"""주제 웹 검색 — Tavily API(우선) / DuckDuckGo(무료 폴백).

P1이 선정한 주제에 대해 추가 자료를 수집하여 P2 합성의 입력으로 제공한다.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from pipeline.config import cfg
from pipeline.models import SelectedTopic

log = logging.getLogger(__name__)

RESEARCH_CHAR_LIMIT = 12000  # P2에 넘길 최대 글자 수
RESULTS_PER_QUERY = 5


# ── 공개 인터페이스 ────────────────────────────────────────────────────────


def research_topic(topic: SelectedTopic) -> str:
    """주제에 대해 웹 검색하고 연구 자료를 문자열로 반환한다."""
    queries = _build_queries(topic)
    log.info("웹검색 시작: %s (쿼리 %d개)", topic.article.title[:40], len(queries))

    results: list[dict] = []
    for q in queries:
        hits = _search(q)
        results.extend(hits)

    # URL 기준 중복 제거
    seen: set[str] = set()
    unique = [r for r in results if not (r["url"] in seen or seen.add(r["url"]))]  # type: ignore[func-returns-value]

    if not unique:
        log.warning("웹검색 결과 없음 — 원문 컨텐츠로 폴백")
        return ""

    formatted = _format_research(unique, topic)
    log.info("웹검색 완료: %d건 수집, %d자", len(unique), len(formatted))
    return formatted


# ── 쿼리 생성 ─────────────────────────────────────────────────────────────


def _build_queries(topic: SelectedTopic) -> list[str]:
    """앵글에 맞는 검색 쿼리 2개를 생성한다."""
    title = topic.article.title
    angle = topic.angle

    suffix_map = {
        "기술심화": "technical explanation how it works architecture",
        "실용적용": "tutorial implementation example use case",
        "한국맥락": "한국 적용 사례 국내 현황",
        "비교분석": "vs comparison alternatives benchmark",
    }
    suffix = suffix_map.get(angle, "overview review")

    return [title, f"{title} {suffix}"]


# ── 검색 실행 ─────────────────────────────────────────────────────────────


def _search(query: str) -> list[dict]:
    """Tavily 우선, 실패 시 DuckDuckGo로 폴백."""
    if cfg.TAVILY_API_KEY:
        results = _search_tavily(query)
        if results:
            return results
    return _search_duckduckgo(query)


def _search_tavily(query: str) -> list[dict]:
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": cfg.TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": RESULTS_PER_QUERY,
                "include_raw_content": False,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in data.get("results", [])
        ]
    except Exception as e:
        log.debug("Tavily 검색 실패 [%s]: %s", query[:40], e)
        return []


def _search_duckduckgo(query: str) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=RESULTS_PER_QUERY))
        return [
            {
                "title": h.get("title", ""),
                "url": h.get("href", ""),
                "content": h.get("body", ""),
            }
            for h in hits
        ]
    except ImportError:
        log.warning("duckduckgo-search 미설치 — pip install duckduckgo-search")
        return []
    except Exception as e:
        log.debug("DuckDuckGo 검색 실패 [%s]: %s", query[:40], e)
        return []


# ── 포맷팅 ────────────────────────────────────────────────────────────────


def _format_research(results: list[dict], topic: SelectedTopic) -> str:
    """검색 결과를 P2 합성에 적합한 마크다운 형식으로 변환한다."""
    lines: list[str] = [
        f"# 웹 검색 자료: {topic.article.title}",
        f"앵글: {topic.angle}",
        f"원문 출처: {topic.article.url}",
        "",
    ]

    for i, r in enumerate(results, 1):
        lines.append(f"## 자료 {i}: {r['title']}")
        lines.append(f"출처: {r['url']}")
        lines.append("")
        lines.append(r["content"][:2000])
        lines.append("")

    combined = "\n".join(lines)
    return combined[:RESEARCH_CHAR_LIMIT]
