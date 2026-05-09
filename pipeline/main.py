"""AI 블로그 파이프라인 오케스트레이터.

흐름: collect → score/dedup → P1 triage → [토픽별] P2→P3→P4→gate→P5→P6→P7→image → notify
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.collector import collect_articles
from pipeline.config import cfg
from pipeline.image import generate_thumbnail
from pipeline.models import (
    Critique,
    Draft,
    FactCheckResult,
    Post,
    SEOMeta,
    ScoredArticle,
    SelectedTopic,
    SynthesizedFacts,
)
from pipeline.notifier import notify_slack
from pipeline.publisher import publish_to_wordpress, verify_and_repair_published_post
from pipeline.scorer import score_and_deduplicate
from pipeline.stages.draft_guard import ensure_complete
from pipeline.stages.p1_triage import p1_triage
from pipeline.stages.p2_synthesis import p2_synthesis
from pipeline.stages.p3_draft import p3_draft
from pipeline.stages.p4_critique import p4_critique
from pipeline.stages.p5_revise import p5_revise
from pipeline.stages.p6_factcheck import p6_factcheck
from pipeline.stages.p7_seo import p7_seo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOGS_DIR / "pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def run_pipeline() -> None:
    run_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log.info(f"=== 파이프라인 시작 {run_date} ===")

    # ── STAGE 1: 수집 ─────────────────────────────────────────────
    raw_articles = collect_articles()
    if not raw_articles:
        log.warning("수집된 기사 없음 — 종료")
        return

    # ── STAGE 2: 점수 산정 + 중복 제거 ───────────────────────────
    scored = score_and_deduplicate(
        articles=raw_articles,
        history_path=cfg.POSTS_JSON,
        confidence_threshold=cfg.DEDUP_CONFIDENCE_THRESHOLD,
    )
    top20 = scored[:20]
    if not top20:
        log.warning("중복 제거 후 후보 없음 — 종료")
        return

    log.info(f"top20 확정: {[a.title[:30] for a in top20[:3]]} ...")

    # ── STAGE 3: P1 트리아지 ──────────────────────────────────────
    daily_count = min(cfg.DAILY_POST_COUNT, 3)  # 하드캡: 최대 3개/일
    selected_topics = p1_triage(top20, top_k=daily_count)
    if not selected_topics:
        log.warning("P1 선별 결과 없음 — 종료")
        return

    # ── 토픽별 처리 ───────────────────────────────────────────────
    published_count = 0
    for topic in selected_topics:
        post = _process_topic(topic, run_date)
        if post and post.status == "published":
            published_count += 1

    log.info(f"=== 파이프라인 완료: {published_count}/{len(selected_topics)}편 발행 ===")


def _process_topic(topic: SelectedTopic, run_date: str) -> Optional[Post]:
    title_short = topic.article.title[:40]
    log.info(f"[{title_short}] 처리 시작 (앵글: {topic.angle})")

    try:
        # P2: 팩트 합성 (DeepSeek)
        facts = p2_synthesis(topic)

        # P3: 초안 v1 (GLM-5.1)
        draft_v1 = p3_draft(facts, angle=topic.angle)
        # 잘림 검사: 완결되지 않으면 GLM으로 결론 보완
        completed_content = ensure_complete(draft_v1.content, topic.article.title)
        if completed_content != draft_v1.content:
            draft_v1 = Draft(
                facts=draft_v1.facts,
                content=completed_content,
                model=draft_v1.model,
                version=draft_v1.version,
            )

        # P4: 자체 비평 (DeepSeek)
        critique = p4_critique(draft_v1, facts)
        log.info(f"[{title_short}] P4 점수: {critique.total}/100")

        # ── 품질 분기 게이트 ─────────────────────────────────────
        min_score = cfg.MIN_QUALITY_SCORE
        if min_score > 0:
            if critique.total < 60:
                log.warning(f"[{title_short}] 품질 미달 ({critique.total}) — Slack 알림 후 종료")
                _save_draft_file(draft_v1, run_date, status="low_quality")
                notify_slack(event="low_quality", topic=topic, critique=critique)
                return None

            if 60 <= critique.total < min_score:
                log.info(f"[{title_short}] 60~{min_score-1}점 — P3 재생성 1회 시도")
                draft_v1 = p3_draft(facts, angle=topic.angle, critique_hint=critique)
                critique = p4_critique(draft_v1, facts)
                log.info(f"[{title_short}] P4 재채점: {critique.total}/100")
                if critique.total < min_score:
                    log.info(f"[{title_short}] 재시도 후에도 <{min_score} — 보류 큐 저장")
                    _save_draft_file(draft_v1, run_date, status="pending")
                    _append_pending(topic, draft_v1, critique)
                    notify_slack(event="pending", topic=topic, critique=critique)
                    return None
        else:
            log.info(f"[{title_short}] MIN_QUALITY_SCORE=0 — 품질 게이트 건너뜀 ({critique.total}점)")

        # P5: 수정본 v2 (GLM-5.1)
        draft_v2 = p5_revise(draft_v1, critique)
        # 잘림 검사: P5 수정본도 완결 보장
        completed_v2 = ensure_complete(draft_v2.content, topic.article.title)
        if completed_v2 != draft_v2.content:
            draft_v2 = Draft(
                facts=draft_v2.facts,
                content=completed_v2,
                model=draft_v2.model,
                version=draft_v2.version,
            )

        # P6: 팩트체크 (DeepSeek)
        fact_check = p6_factcheck(draft_v2, facts)
        if fact_check.unsupported_count >= 3:
            draft_v2 = _tag_unsupported_claims(draft_v2, fact_check)
            notify_slack(event="factcheck_warning", topic=topic, fact_check=fact_check)

        # P7: SEO 메타 (DeepSeek)
        seo = p7_seo(draft_v2)

        # 썸네일
        thumbnail_url = generate_thumbnail(seo.thumbnail_prompt)

        # 포스트 조립
        chosen_title = seo.title_candidates[0] if seo.title_candidates else topic.article.title
        # URL 슬러그: P7이 생성한 영문 포커스 키워드 슬러그 우선, 없으면 제목에서 생성
        slug = seo.focus_keyword_slug or _make_slug(chosen_title)
        post = Post(
            draft=draft_v2,
            fact_check=fact_check,
            seo=seo,
            thumbnail_url=thumbnail_url,
            chosen_title=chosen_title,
            slug=slug,
            status="ready",
        )

        # 포커스 키워드 본문 도달 여부 검사 (Yoast SEO rule 4, 5)
        if seo.focus_keyword:
            _check_focus_keyword(draft_v2.content, seo.focus_keyword, title_short)

        # 내부 링크 삽입
        linked_content = _insert_internal_links(
            post.draft.content,
            seo.internal_link_slots,
        )
        if linked_content != post.draft.content:
            post.draft = Draft(
                facts=post.draft.facts,
                content=linked_content,
                model=post.draft.model,
                version=post.draft.version,
            )

        # 파일 저장
        file_path = _save_final_file(post, run_date)
        post.file_path = str(file_path)

        # 발행 결정
        if cfg.AUTO_PUBLISH:
            import base64
            cred = base64.b64encode(
                f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
            ).decode()
            result = publish_to_wordpress(post)
            post.wp_link = result.link
            post.status = "published"
            verify_and_repair_published_post(result.wp_id, cred)
            notify_slack(event="published", post=post, wp_link=result.link)
            log.info(f"[{title_short}] 자동 발행 완료: {result.link}")

            # P8: 공유 콘텐츠 생성 + 자동 투고 (AUTO_PUBLISH=true 시에만)
            from pipeline.stages.p8_share import generate_share_content
            share_files = generate_share_content(post)
            if share_files:
                post.share_files = share_files
        else:
            notify_slack(event="ready", post=post, critique=critique)
            log.info(f"[{title_short}] Slack 알림 전송 완료 (수동 승인 대기)")

        # 게시 이력 기록
        _record_history(post, topic)
        return post

    except Exception as e:
        log.error(f"[{title_short}] 처리 실패: {e}", exc_info=True)
        notify_slack(event="pipeline_error", topic=topic, error=str(e))
        return None


# ── 파일 저장 ──────────────────────────────────────────────────────────────


def _save_draft_file(draft: Draft, run_date: str, status: str) -> Path:
    slug = _make_slug(draft.facts.topic.article.title)
    path = cfg.POSTS_DIR / f"{run_date}-{slug}-{status}.md"
    _ensure_unique_path(path)
    header = _frontmatter(draft, status=status)
    path.write_text(header + "\n\n" + draft.content, encoding="utf-8")
    log.debug(f"초안 저장: {path}")
    return path


def _save_final_file(post: Post, run_date: str) -> Path:
    path = cfg.POSTS_DIR / f"{run_date}-{post.slug}.md"
    _ensure_unique_path(path)

    fact_check_note = ""
    if post.fact_check.unsupported_count > 0:
        fact_check_note = f"\n> ⚠️ 팩트체크: unsupported 주장 {post.fact_check.unsupported_count}건 ([추정] 태그 확인)"

    header = (
        f"---\n"
        f"date: {run_date}\n"
        f"title: {post.chosen_title}\n"
        f"slug: {post.slug}\n"
        f"status: {post.status}\n"
        f"angle: {post.draft.facts.topic.angle}\n"
        f"source_url: {post.draft.facts.topic.article.url}\n"
        f"tags: {post.seo.tags}\n"
        f"thumbnail: {post.thumbnail_url or ''}\n"
        f"meta_description: {post.seo.meta_description}\n"
        f"generated_by: GLM-5.1 (NVIDIA API, thinking)\n"
        f"synthesis_by: DeepSeek V4 Pro (NVIDIA API)\n"
        f"---\n"
    )
    path.write_text(header + fact_check_note + "\n\n" + post.draft.content, encoding="utf-8")
    log.info(f"최종고 저장: {path}")
    return path


def _frontmatter(draft: Draft, status: str) -> str:
    topic = draft.facts.topic
    return (
        f"---\n"
        f"status: {status}\n"
        f"angle: {topic.angle}\n"
        f"source_url: {topic.article.url}\n"
        f"generated_by: GLM-5.1 (NVIDIA API, thinking)\n"
        f"---"
    )


def _ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        new_path = path.parent / f"{stem}-{i}{suffix}"
        if not new_path.exists():
            return new_path
        i += 1


# ── 보류 큐 ───────────────────────────────────────────────────────────────


def _append_pending(topic: SelectedTopic, draft: Draft, critique: Critique) -> None:
    pending_path = cfg.PENDING_JSON
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception:
        pending = []

    pending.append({
        "date": datetime.now(tz=timezone.utc).isoformat(),
        "title": topic.article.title,
        "url": topic.article.url,
        "score": critique.total,
        "weaknesses": critique.weaknesses,
        "improvement_guide": critique.improvement_guide,
    })
    pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 게시 이력 ─────────────────────────────────────────────────────────────


def _record_history(post: Post, topic: SelectedTopic) -> None:
    try:
        posts = json.loads(cfg.POSTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        posts = []

    posts.append({
        "date": datetime.now(tz=timezone.utc).isoformat(),
        "title": post.chosen_title,
        "url": topic.article.url,
        "wp_link": post.wp_link,
        "slug": post.slug,
        "angle": post.draft.facts.topic.angle,
        "status": post.status,
        "tags": post.seo.tags,
    })
    cfg.POSTS_JSON.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    log.debug("게시 이력 업데이트 완료")


# ── 유틸 ──────────────────────────────────────────────────────────────────


def _make_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] or "post"


def _insert_internal_links(content: str, link_slots: list[str]) -> str:
    """P7의 internal_link_slots 키워드로 posts.json 이력을 검색해 관련 포스트 섹션 추가."""
    if not link_slots:
        return content
    try:
        posts = json.loads(cfg.POSTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return content

    published = [p for p in posts if p.get("wp_link") and p.get("status") in ("published", "ready")]
    if not published:
        return content

    matched: list[tuple[str, str]] = []
    seen_links: set[str] = set()
    for slot in link_slots:
        slot_lower = slot.lower()
        for post in published:
            wp_link = post.get("wp_link", "")
            if wp_link in seen_links:
                continue
            tags_text = " ".join(post.get("tags", [])).lower()
            title_text = post.get("title", "").lower()
            if slot_lower in tags_text or slot_lower in title_text:
                matched.append((post["title"], wp_link))
                seen_links.add(wp_link)
                break

    if not matched:
        return content

    link_section = "\n\n---\n\n## 관련 포스트\n\n"
    for title, url in matched[:5]:
        link_section += f"- [{title}]({url})\n"

    return content + link_section


def _check_focus_keyword(content: str, focus_keyword: str, label: str) -> None:
    """Yoast SEO 포커스 키워드 체크:
    - rule 4: 본문 시작 300자 이내 포함 여부
    - rule 5: 본문 전체 포함 여부
    문제가 있으면 경고 로그만 기록 (자동 수정 없음 — LLM 품질 문제로 판단).
    """
    intro = content[:300]
    if focus_keyword not in intro:
        log.warning(f"[{label}] SEO: 포커스 키워드 '{focus_keyword}'가 본문 시작 300자에 없음")
    if focus_keyword not in content:
        log.warning(f"[{label}] SEO: 포커스 키워드 '{focus_keyword}'가 본문 전체에 없음")


def _tag_unsupported_claims(draft: Draft, fact_check: FactCheckResult) -> Draft:
    """unsupported 주장에 [추정] 태그를 추가."""
    content = draft.content
    for claim in fact_check.claims:
        if claim.get("status") == "unsupported":
            text = claim.get("text", "")
            if text and text in content:
                content = content.replace(text, f"{text} [추정]", 1)
    return Draft(
        facts=draft.facts,
        content=content,
        model=draft.model,
        version=draft.version,
    )


if __name__ == "__main__":
    run_pipeline()
