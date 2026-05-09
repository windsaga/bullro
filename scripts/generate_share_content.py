"""기존 발행된 WordPress 포스트에 대해 공유 콘텐츠를 소급 생성.

사용법:
    python scripts/generate_share_content.py [--slug <slug>] [--limit N]

동작:
1. data/posts.json에서 published 포스트 목록 조회
2. posts/share/{slug}.*.md가 이미 있으면 스킵
3. WordPress REST API로 포스트 본문 조회
4. DeepSeek으로 4종 공유 콘텐츠 생성 후 posts/share/ 저장
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.config import cfg
from pipeline.llm import deepseek

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SYSTEM = """당신은 기술 블로그 콘텐츠 마케터입니다.
블로그 포스트를 읽고 커뮤니티 채널별 공유문을 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 블로그 포스트에 대한 커뮤니티 배포용 공유문을 생성하세요.

포스트 제목: {title}
포스트 링크: {link}

포스트 내용 (요약):
{content}

4가지 채널별 공유문을 JSON으로 반환하세요:

{{
  "reddit": {{
    "title": "영어 검색형 제목 (60자 이내, 실험 결과/설정값/벤치마크 중심)",
    "body": "TL;DR:\\n- 핵심 포인트1 (영어)\\n- 핵심 포인트2\\n- 핵심 포인트3\\n\\nPractical takeaway: 실무 포인트 (영어)\\nMain caveat: 주의사항 (영어)\\n\\nLink: {link}"
  }},
  "hn": {{
    "title": "영어 HN 제목 (80자 이내)",
    "comment": "HN Ask/Show 첫 댓글용 영어 본문 (150자 이내)"
  }},
  "twitter": "한국어 트윗 (260자 이내). 핵심 결과+수치 첫 줄, ▸ 포인트 3개, 빈 줄 후 링크.",
  "ko_community": "한국어 커뮤니티 공유문. 형식: 제목: / 핵심 요약: / 실무 적용 포인트: / 주의할 점: / 링크:"
}}"""


def _cred() -> str:
    return base64.b64encode(
        f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
    ).decode()


def fetch_post_content(wp_link: str, cred: str) -> str:
    """WordPress REST API로 포스트 본문 텍스트 조회."""
    slug = wp_link.rstrip("/").split("/")[-1]
    try:
        r = requests.get(
            f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts",
            params={"slug": slug, "context": "edit"},
            headers={"Authorization": f"Basic {cred}"},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json()
        if not posts:
            return ""
        raw = posts[0].get("content", {}).get("raw", "")
        import re
        return re.sub(r"<[^>]+>", "", raw).strip()[:2000]
    except Exception as e:
        log.warning(f"포스트 조회 실패 [{wp_link}]: {e}")
        return ""


def already_generated(slug: str) -> bool:
    """posts/share/ 에 해당 slug 파일이 이미 있는지 확인."""
    share_dir = cfg.SHARE_DIR
    return any(share_dir.glob(f"*{slug[:30]}*.md"))


def generate_and_save(title: str, wp_link: str, content: str, slug: str) -> list[str]:
    """DeepSeek으로 공유 콘텐츠 생성 후 저장. 저장된 파일 목록 반환."""
    prompt = PROMPT_TMPL.format(title=title, link=wp_link, content=content)
    raw = deepseek(prompt, system=SYSTEM, max_tokens=1200, temperature=0.5)

    try:
        data = _parse_json(raw)
    except Exception as e:
        log.error(f"JSON 파싱 실패 [{title[:40]}]: {e}")
        return []

    share_dir = cfg.SHARE_DIR
    share_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().strftime("%Y-%m-%d")
    slug_short = slug[:40]
    base = share_dir / f"{today}-{slug_short}"
    saved: list[str] = []

    reddit = data.get("reddit", {})
    if reddit:
        path = Path(f"{base}.reddit.md")
        path.write_text(
            f"# Reddit\n\n**Title:** {reddit.get('title', '')}\n\n{reddit.get('body', '')}\n",
            encoding="utf-8",
        )
        saved.append(str(path))

    hn = data.get("hn", {})
    if hn:
        path = Path(f"{base}.hn.md")
        path.write_text(
            f"# Hacker News\n\n**Title:** {hn.get('title', '')}\n\n**Comment:**\n{hn.get('comment', '')}\n",
            encoding="utf-8",
        )
        saved.append(str(path))

    twitter = data.get("twitter", "")
    if twitter:
        path = Path(f"{base}.x.md")
        path.write_text(f"# X / Twitter\n\n{twitter}\n", encoding="utf-8")
        saved.append(str(path))

    ko_community = data.get("ko_community", "")
    if ko_community:
        path = Path(f"{base}.ko-community.md")
        path.write_text(
            f"# 국내 커뮤니티 (OKKY / 클리앙)\n\n{ko_community}\n",
            encoding="utf-8",
        )
        saved.append(str(path))

    return saved


def _parse_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        start = text.find("{", text.find("```"))
        end = text.rfind("}") + 1
        text = text[start:end]
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}") + 1
        text = text[start:end]
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description="기존 포스트 공유 콘텐츠 소급 생성")
    parser.add_argument("--slug", help="특정 슬러그만 처리")
    parser.add_argument("--limit", type=int, default=0, help="처리할 최대 포스트 수 (0=전체)")
    args = parser.parse_args()

    if not cfg.WORDPRESS_URL or not cfg.WORDPRESS_APP_PASSWORD:
        log.error("WORDPRESS_URL / WORDPRESS_APP_PASSWORD 환경변수 미설정")
        sys.exit(1)

    try:
        posts = json.loads(cfg.POSTS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"posts.json 읽기 실패: {e}")
        sys.exit(1)

    published = [p for p in posts if p.get("status") == "published" and p.get("wp_link")]

    if args.slug:
        published = [p for p in published if p.get("slug") == args.slug]
        if not published:
            log.error(f"슬러그 '{args.slug}' 포스트를 찾을 수 없음")
            sys.exit(1)

    if args.limit > 0:
        published = published[:args.limit]

    log.info(f"처리 대상: {len(published)}개 포스트")
    cred = _cred()

    generated = 0
    skipped = 0
    for post in published:
        slug = post.get("slug", "")
        title = post.get("title", "")
        wp_link = post.get("wp_link", "")

        if already_generated(slug):
            log.info(f"[스킵] {title[:40]} — 공유 파일 이미 존재")
            skipped += 1
            continue

        log.info(f"생성 중: {title[:50]}")
        content = fetch_post_content(wp_link, cred)
        if not content:
            log.warning(f"  본문 조회 실패 — 스킵")
            continue

        saved = generate_and_save(title, wp_link, content, slug)
        log.info(f"  {len(saved)}개 파일 저장: {[Path(f).name for f in saved]}")
        generated += 1
        time.sleep(3)

    log.info(f"완료: {generated}개 생성, {skipped}개 스킵")


if __name__ == "__main__":
    main()
