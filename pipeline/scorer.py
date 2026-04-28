"""복합 신호 점수 산정 + sentence-transformers 기반 중복 제거."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np

from pipeline.models import Article, ScoredArticle

log = logging.getLogger(__name__)

# 신호 가중치
W_HN = 0.4
W_REDDIT = 0.3
W_HF = 0.2
W_GITHUB = 0.1

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
            log.info("sentence-transformers 모델 로드 완료")
        except ImportError:
            log.warning("sentence-transformers 미설치 — 중복 탐지 건너뜀")
    return _embedder


def score_and_deduplicate(
    articles: list[Article],
    history_path: Path,
    similarity_threshold: float = 0.75,
) -> list[ScoredArticle]:
    """점수 산정 → 기존 게시글과 유사도 중복 제거 → composite_score 내림차순 반환."""

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

    # 4. 임베딩 + 중복 제거
    embedder = _get_embedder()
    if embedder is None:
        log.warning("임베딩 불가 — 중복 탐지 없이 진행")
        return scored

    history_embeddings = _load_history_embeddings(history_path)

    texts = [f"{a.title} {a.content[:200]}" for a in scored]
    embeddings = embedder.encode(texts, normalize_embeddings=True)

    for i, a in enumerate(scored):
        a.embedding = embeddings[i].tolist()

    # 기존 게시글과 유사도 비교
    filtered: list[ScoredArticle] = []
    for a in scored:
        emb = np.array(a.embedding)
        is_dup = False
        for hist_emb in history_embeddings:
            similarity = float(np.dot(emb, np.array(hist_emb)))
            if similarity >= similarity_threshold:
                log.debug(f"중복 제거: {a.title[:40]} (similarity={similarity:.3f})")
                is_dup = True
                break
        if not is_dup:
            filtered.append(a)

    # 새로 선택된 항목들끼리도 중복 제거
    deduped: list[ScoredArticle] = []
    deduped_embs: list[np.ndarray] = []
    for a in filtered:
        emb = np.array(a.embedding)
        is_dup = any(
            float(np.dot(emb, e)) >= similarity_threshold
            for e in deduped_embs
        )
        if not is_dup:
            deduped.append(a)
            deduped_embs.append(emb)

    log.info(f"점수 산정: {len(scored)}건 → 중복 제거 후 {len(deduped)}건")
    return deduped


def _zscore(values: list[float]) -> list[float]:
    arr = np.array(values, dtype=float)
    std = arr.std()
    if std == 0:
        return [0.0] * len(values)
    return ((arr - arr.mean()) / std).tolist()


def _load_history_embeddings(history_path: Path) -> list[list[float]]:
    if not history_path.exists():
        return []
    try:
        posts = json.loads(history_path.read_text(encoding="utf-8"))
        return [p["embedding"] for p in posts if p.get("embedding")]
    except Exception as e:
        log.warning(f"게시 이력 로드 실패: {e}")
        return []
