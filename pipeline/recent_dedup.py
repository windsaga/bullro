"""최근 이력 기반 중복 주제 필터.

P1이 선정한 토픽을 posts.json / pending.json 이력과 비교하여
최근 DEDUP_DAYS 일 이내에 다룬 주제를 제거한다.

체크 순서:
  1. URL 완전 일치 → 즉시 제거
  2. 제목 Jaccard 유사도 >= SIMILARITY_THRESHOLD → 제거
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.models import SelectedTopic

log = logging.getLogger(__name__)

DEDUP_DAYS = 14            # 최근 14일 이내 이력과 비교
SIMILARITY_THRESHOLD = 0.25  # Jaccard 유사도 임계값

# 제목 토큰화 시 제외할 불용어
_STOPWORDS = {
    # English
    "the", "a", "an", "of", "in", "is", "it", "to", "and", "or", "for",
    "how", "why", "what", "with", "from", "by", "on", "at", "as", "be",
    "are", "was", "were", "has", "have", "will", "can", "new", "vs",
    # Korean
    "이", "가", "을", "를", "의", "에", "서", "로", "와", "과", "도",
    "는", "은", "그", "저", "한", "하는", "하고", "있는", "위한", "대한",
    "통해", "기반", "방법", "사용", "활용", "적용", "소개", "관련", "위해",
    "통한", "대해", "따른", "대한", "통한",
}


def filter_recent_duplicates(
    topics: list[SelectedTopic],
    posts_path: Path,
    pending_path: Path,
    days: int = DEDUP_DAYS,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[SelectedTopic]:
    """최근 이력과 중복되는 토픽을 제거하고 남은 목록을 반환한다."""
    recent = _load_recent_history(posts_path, pending_path, days)
    if not recent:
        return topics

    kept: list[SelectedTopic] = []
    for topic in topics:
        url = topic.article.url
        title = topic.article.title

        matched = _find_match(url, title, recent, threshold)
        if matched:
            log.warning(
                "최근 이력 중복 드롭: [%s] → 이전 글: [%s] (%.0f일 전, 유사도=%.2f)",
                title[:40],
                matched["title"][:40],
                matched["days_ago"],
                matched["similarity"],
            )
        else:
            kept.append(topic)

    log.info("최근 이력 필터: %d개 → %d개 (제거 %d개)", len(topics), len(kept), len(topics) - len(kept))
    return kept


# ── 내부 ──────────────────────────────────────────────────────────────────


def _load_recent_history(posts_path: Path, pending_path: Path, days: int) -> list[dict]:
    """posts.json + pending.json에서 최근 N일 이내 항목을 로드한다."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    items: list[dict] = []

    for path, source in [(posts_path, "posts"), (pending_path, "pending")]:
        if not path.exists():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            for e in entries:
                raw_date = e.get("date", "")
                if not raw_date:
                    continue
                try:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if dt >= cutoff:
                    items.append({
                        "title": e.get("title", ""),
                        "url": e.get("url", ""),
                        "date": dt,
                        "source": source,
                    })
        except Exception as ex:
            log.debug("이력 로드 실패 [%s]: %s", path.name, ex)

    log.debug("최근 %d일 이력: %d건 (posts+pending)", days, len(items))
    return items


def _find_match(
    url: str,
    title: str,
    history: list[dict],
    threshold: float,
) -> dict | None:
    """URL 일치 또는 제목 유사도가 threshold 이상인 이력 항목을 반환한다."""
    now = datetime.now(tz=timezone.utc)
    tokens_new = _tokenize(title)

    for item in history:
        days_ago = (now - item["date"]).days

        # 1. URL 완전 일치
        if url and item["url"] and url.split("?")[0].rstrip("/") == item["url"].split("?")[0].rstrip("/"):
            return {"title": item["title"], "days_ago": days_ago, "similarity": 1.0}

        tokens_old = _tokenize(item["title"])
        sim = _jaccard(tokens_new, tokens_old)

        # 2. Jaccard 유사도가 threshold 이상
        if sim >= threshold:
            return {"title": item["title"], "days_ago": days_ago, "similarity": sim}

        # 3. 핵심 식별자(제품명·버전, 4자 이상) 공유 + 최소 유사도 충족
        #    예: "GPT-5 공개" vs "GPT-5 출시" → {gpt-5} 공유, Jaccard 0.11
        shared_distinctive = {t for t in tokens_new & tokens_old if len(t) >= 4}
        if shared_distinctive and sim >= 0.10:
            return {"title": item["title"], "days_ago": days_ago, "similarity": sim}

    return None


def _tokenize(title: str) -> set[str]:
    """제목을 소문자 단어 집합으로 변환한다 (불용어·단음절 제거).

    버전 번호(3.5, gpt-4)와 하이픈 복합어는 하나의 토큰으로 유지한다.
    """
    # 버전/복합어 포함 토큰 추출: "gpt-4", "3.5", "llama-3.1" 등
    words = re.findall(r"[a-zA-Z0-9가-힣]+(?:[.\-][a-zA-Z0-9가-힣]+)*", title.lower())
    return {w for w in words if len(w) >= 2 and w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
