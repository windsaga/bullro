"""복합 신호 점수 산정 + DeepSeek 기반 중복 제거."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np

from pipeline.llm import deepseek
from pipeline.models import Article, ScoredArticle

log = logging.getLogger(__name__)

# 신호 가중치
W_HN = 0.4
W_REDDIT = 0.3
W_HF = 0.2
W_GITHUB = 0.1

DEDUP_BATCH_SIZE = 20
HISTORY_LIMIT = 60

DEDUP_SYSTEM = """당신은 AI 기술 블로그의 중복 주제 검수자입니다.
후보 기사가 기존 게시글 또는 같은 후보 목록의 더 높은 점수 후보와 사실상 같은 주제인지 판단하세요.

중복으로 볼 조건:
- 같은 논문/제품/모델/릴리즈/사건을 다룸
- 제목 표현만 다르고 핵심 뉴스와 독자에게 줄 내용이 거의 같음
- 후속 기사라도 새 정보가 거의 없고 기존 게시글과 같은 글감임

중복으로 보지 않을 조건:
- 같은 회사/모델군이어도 릴리즈, 벤치마크, 적용 사례, 비판처럼 초점이 다름
- 기존 글과 이어지는 후속 소식이지만 새 데이터나 의사결정 포인트가 뚜렷함

반드시 JSON만 출력하세요."""


def score_and_deduplicate(
    articles: list[Article],
    history_path: Path,
    confidence_threshold: float = 0.75,
) -> list[ScoredArticle]:
    """점수 산정 → DeepSeek 중복 제거 → composite_score 내림차순 반환."""

    # 1. 신호 수집
    hn_pts = [float(a.signals.get("hn_points", 0)) for a in articles]
    reddit_sc = [float(a.signals.get("reddit_score", 0)) for a in articles]
    hf_up = [float(a.signals.get("hf_upvotes", 0)) for a in articles]
    github_sd = [float(a.signals.get("github_star_delta", 0)) for a in articles]

    # 2. z-score 정규화
    z_hn = _zscore(hn_pts)
    z_reddit = _zscore(reddit_sc)
    z_hf = _zscore(hf_up)

    scored: list[ScoredArticle] = []
    for i, a in enumerate(articles):
        composite = (
            W_HN * z_hn[i]
            + W_REDDIT * z_reddit[i]
            + W_HF * z_hf[i]
            + W_GITHUB * math.log1p(github_sd[i])
        )
        scored.append(ScoredArticle(
            url=a.url,
            title=a.title,
            content=a.content,
            source=a.source,
            published_at=a.published_at,
            signals=a.signals,
            composite_score=composite,
        ))

    # 3. 점수 내림차순 정렬
    scored.sort(key=lambda x: x.composite_score, reverse=True)

    # 4. DeepSeek 중복 제거 — top 30만 대상으로 한정 (API 호출 절약)
    # 어차피 top 20이 목표이므로 하위 기사는 dedup 불필요
    dedup_candidates = scored[:30]
    deduped = _deduplicate_with_deepseek(
        dedup_candidates,
        history_path=history_path,
        confidence_threshold=confidence_threshold,
    )
    # top 30 밖의 기사는 dedup 없이 뒤에 붙임
    deduped_urls = {a.url for a in deduped}
    remainder = [a for a in scored[30:] if a.url not in deduped_urls]
    deduped = deduped + remainder

    log.info(f"점수 산정: {len(scored)}건 → 중복 제거 후 {len(deduped)}건")
    return deduped


def _zscore(values: list[float]) -> list[float]:
    arr = np.array(values, dtype=float)
    std = arr.std()
    if std == 0:
        return [0.0] * len(values)
    return ((arr - arr.mean()) / std).tolist()


def _deduplicate_with_deepseek(
    scored: list[ScoredArticle],
    history_path: Path,
    confidence_threshold: float,
) -> list[ScoredArticle]:
    history = _load_history_posts(history_path)
    accepted: list[ScoredArticle] = []

    for start in range(0, len(scored), DEDUP_BATCH_SIZE):
        batch = scored[start:start + DEDUP_BATCH_SIZE]
        duplicate_ids = _find_duplicate_ids(
            batch=batch,
            accepted=accepted,
            history=history,
            confidence_threshold=confidence_threshold,
            id_offset=start,
        )
        for i, article in enumerate(batch, start=start):
            if str(i) in duplicate_ids:
                continue
            accepted.append(article)

    return accepted


def _find_duplicate_ids(
    batch: list[ScoredArticle],
    accepted: list[ScoredArticle],
    history: list[dict],
    confidence_threshold: float,
    id_offset: int,
) -> set[str]:
    candidates = [
        {
            "id": str(id_offset + i),
            "title": a.title,
            "url": a.url,
            "source": a.source,
            "published_at": a.published_at,
            "composite_score": round(a.composite_score, 3),
            "summary": a.content[:600],
        }
        for i, a in enumerate(batch)
    ]
    prior_candidates = [
        {
            "id": f"selected-{i}",
            "title": a.title,
            "url": a.url,
            "source": a.source,
            "summary": a.content[:300],
        }
        for i, a in enumerate(accepted[-HISTORY_LIMIT:])
    ]

    prompt = f"""후보 기사 중 중복인 항목만 골라 JSON 배열로 반환하세요.

confidence가 {confidence_threshold:.2f} 이상일 때만 중복으로 판정하세요.
후보 목록은 composite_score 내림차순입니다. 후보끼리 중복이면 더 낮은 점수 후보를 duplicate로 표시하세요.

기존 게시글:
{json.dumps(history[-HISTORY_LIMIT:], ensure_ascii=False, indent=2)}

이미 유지하기로 한 이번 실행 후보:
{json.dumps(prior_candidates, ensure_ascii=False, indent=2)}

검토할 후보:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

응답 형식:
[
  {{
    "id": "중복 후보 id",
    "matched_with": "기존 게시글 제목 또는 후보 id",
    "confidence": 0.0,
    "reason": "한 줄 이유"
  }}
]"""

    try:
        raw = deepseek(prompt, system=DEDUP_SYSTEM, max_tokens=1536, temperature=0.0)
        items = _parse_json_array(raw)
    except Exception as e:
        log.warning(f"DeepSeek 중복 탐지 실패 — 해당 배치 통과: {e}")
        return set()

    duplicate_ids: set[str] = set()
    candidate_ids = {c["id"] for c in candidates}
    for item in items:
        item_id = str(item.get("id", ""))
        confidence = float(item.get("confidence", 0))
        if item_id in candidate_ids and confidence >= confidence_threshold:
            log.debug(
                "중복 제거: %s (matched=%s, confidence=%.2f)",
                item_id,
                item.get("matched_with", ""),
                confidence,
            )
            duplicate_ids.add(item_id)
    return duplicate_ids


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if "```" in text:
        start = text.find("[", text.find("```"))
        end = text.rfind("]") + 1
        text = text[start:end]
    elif not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]") + 1
        text = text[start:end]
    return json.loads(text)


def _load_history_posts(history_path: Path) -> list[dict]:
    if not history_path.exists():
        return []
    try:
        posts = json.loads(history_path.read_text(encoding="utf-8"))
        return [
            {
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "slug": p.get("slug", ""),
                "angle": p.get("angle", ""),
                "tags": p.get("tags", []),
                "status": p.get("status", ""),
                "date": p.get("date", ""),
            }
            for p in posts
            if p.get("title") or p.get("url")
        ]
    except Exception as e:
        log.warning(f"게시 이력 로드 실패: {e}")
        return []
