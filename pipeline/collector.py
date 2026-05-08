"""Tier1/2 소스 수집 — arxiv, GitHub, 공식 블로그 RSS, HN, Reddit, HuggingFace, PwC."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests

from pipeline.config import cfg
from pipeline.models import Article

log = logging.getLogger(__name__)

OFFICIAL_BLOGS = [
    ("https://openai.com/blog/rss/", "blog"),
    ("https://www.anthropic.com/rss.xml", "blog"),
    ("https://blog.google/technology/ai/rss/", "blog"),
    ("https://ai.meta.com/blog/rss/", "blog"),
    ("https://huggingface.co/blog/feed.xml", "blog"),
]

ARXIV_FEEDS = [
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.CL",
    "https://rss.arxiv.org/rss/cs.LG",
]

HN_ALGOLIA = "https://hn.algolia.com/api/v1/search?tags=story&query=AI+LLM&hitsPerPage=30&numericFilters=points>50"

REDDIT_SUBS = [
    "https://www.reddit.com/r/LocalLLaMA/.json?limit=25&sort=hot",
    "https://www.reddit.com/r/MachineLearning/.json?limit=25&sort=hot",
]

HF_PAPERS = "https://huggingface.co/papers"
PWC_RSS = "https://paperswithcode.com/latest.xml"

HEADERS = {"User-Agent": cfg.REDDIT_USER_AGENT}


def collect_articles() -> list[Article]:
    """전체 소스에서 수집 후 합산. 실패한 소스는 건너뜀."""
    articles: list[Article] = []

    sources = [
        ("arxiv", _collect_arxiv),
        ("blogs", _collect_official_blogs),
        ("hn", _collect_hn),
        ("reddit", _collect_reddit),
        ("hf", _collect_huggingface),
        ("pwc", _collect_pwc),
    ]
    if cfg.GITHUB_TOKEN:
        sources.append(("github", _collect_github))

    for name, fn in sources:
        try:
            batch = fn()
            log.info(f"수집 [{name}]: {len(batch)}건")
            articles.extend(batch)
        except Exception as e:
            log.warning(f"수집 실패 [{name}]: {e}")

    seen: set[str] = set()
    unique: list[Article] = []
    for a in articles:
        key = a.url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(a)

    log.info(f"전체 수집: {len(articles)}건 → 중복 제거 후 {len(unique)}건")
    return unique


# ── Tier 1 ────────────────────────────────────────────────────────────────


def _collect_arxiv() -> list[Article]:
    articles = []
    for feed_url in ARXIV_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:15]:
            content = getattr(entry, "summary", "") or getattr(entry, "description", "")
            articles.append(Article(
                url=entry.get("link", ""),
                title=entry.get("title", ""),
                content=content,
                source="arxiv",
                published_at=_parse_date(entry),
                signals={},
            ))
        time.sleep(1)
    return articles


def _collect_official_blogs() -> list[Article]:
    articles = []
    for feed_url, source in OFFICIAL_BLOGS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                content = _entry_content(entry)
                articles.append(Article(
                    url=entry.get("link", ""),
                    title=entry.get("title", ""),
                    content=content,
                    source=source,
                    published_at=_parse_date(entry),
                    signals={},
                ))
            time.sleep(0.5)
        except Exception as e:
            log.debug(f"블로그 RSS 실패 {feed_url}: {e}")
    return articles


def _load_stars_cache() -> dict[str, int]:
    cache_path = cfg.DATA_DIR / "github_stars_cache.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_stars_cache(cache: dict[str, int]) -> None:
    cache_path = cfg.DATA_DIR / "github_stars_cache.json"
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _collect_github() -> list[Article]:
    """sources_watchlist.json의 GitHub 저장소 최근 릴리즈 + star delta 수집."""
    watchlist_path = cfg.SOURCES_WATCHLIST
    if not watchlist_path.exists():
        return []
    repos = json.loads(watchlist_path.read_text(encoding="utf-8"))
    if not repos:
        return []

    headers = {"Authorization": f"token {cfg.GITHUB_TOKEN}", "User-Agent": "bullro-bot/1.0"}
    stars_cache = _load_stars_cache()
    new_cache: dict[str, int] = dict(stars_cache)

    articles = []
    for repo in repos[:50]:
        try:
            # repo 기본 정보 (star 수)
            repo_resp = requests.get(
                f"https://api.github.com/repos/{repo}", headers=headers, timeout=10
            )
            star_count = 0
            if repo_resp.status_code == 200:
                star_count = repo_resp.json().get("stargazers_count", 0)
            new_cache[repo] = star_count
            # 처음 수집 시(캐시 없음) delta=0, 이후 실행부터 실제 증가분 반영
            star_delta = max(0, star_count - stars_cache.get(repo, star_count))

            # 최근 릴리즈 정보
            rel_resp = requests.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                headers=headers,
                timeout=10,
            )
            if rel_resp.status_code != 200:
                time.sleep(0.3)
                continue
            data = rel_resp.json()
            if not data.get("tag_name"):
                time.sleep(0.3)
                continue

            articles.append(Article(
                url=data.get("html_url", ""),
                title=f"{repo} {data['tag_name']} 릴리즈",
                content=data.get("body", "")[:2000],
                source="github",
                published_at=data.get("published_at", ""),
                signals={"github_star_delta": star_delta},
            ))
            time.sleep(0.3)
        except Exception as e:
            log.debug(f"GitHub 수집 실패 {repo}: {e}")

    _save_stars_cache(new_cache)
    log.info(f"GitHub star 캐시 갱신: {len(new_cache)}개 레포")
    return articles


# ── Tier 2 ────────────────────────────────────────────────────────────────


def _collect_hn() -> list[Article]:
    try:
        resp = requests.get(HN_ALGOLIA, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"HN 수집 실패: {e}")
        return []

    articles = []
    for hit in data.get("hits", []):
        articles.append(Article(
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            title=hit.get("title", ""),
            content=hit.get("story_text") or hit.get("title", ""),
            source="hn",
            published_at=hit.get("created_at", ""),
            signals={
                "hn_points": hit.get("points", 0),
                "hn_comments": hit.get("num_comments", 0),
            },
        ))
    return articles


def _collect_reddit() -> list[Article]:
    articles = []
    for sub_url in REDDIT_SUBS:
        try:
            resp = requests.get(sub_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for post in data.get("data", {}).get("children", []):
                d = post.get("data", {})
                if d.get("is_self") and not d.get("selftext"):
                    continue
                articles.append(Article(
                    url=d.get("url", ""),
                    title=d.get("title", ""),
                    content=d.get("selftext", "")[:2000] or d.get("title", ""),
                    source="reddit",
                    published_at=datetime.fromtimestamp(
                        d.get("created_utc", 0), tz=timezone.utc
                    ).isoformat(),
                    signals={
                        "reddit_score": d.get("score", 0),
                        "upvote_ratio": d.get("upvote_ratio", 0.5),
                    },
                ))
            time.sleep(1)
        except Exception as e:
            log.warning(f"Reddit 수집 실패 {sub_url}: {e}")
    return articles


def _collect_huggingface() -> list[Article]:
    """HuggingFace Daily Papers 스크래핑."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(HF_PAPERS, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except ImportError:
        log.warning("beautifulsoup4 미설치 — HF 수집 건너뜀")
        return []
    except Exception as e:
        log.warning(f"HF 수집 실패: {e}")
        return []

    articles = []
    for card in soup.select("article.overview-card-wrapper")[:20]:
        link = card.select_one("a")
        title_el = card.select_one("h3") or card.select_one("h2")
        upvote_el = card.select_one("[data-target='upvotes']") or card.select_one(".font-bold")

        href = link["href"] if link else ""
        title = title_el.get_text(strip=True) if title_el else ""
        upvotes = 0
        if upvote_el:
            try:
                upvotes = int(upvote_el.get_text(strip=True))
            except ValueError:
                pass

        if not title:
            continue
        url = f"https://huggingface.co{href}" if href.startswith("/") else href
        articles.append(Article(
            url=url,
            title=title,
            content=title,
            source="hf",
            published_at=datetime.now(tz=timezone.utc).isoformat(),
            signals={"hf_upvotes": upvotes},
        ))
    return articles


def _collect_pwc() -> list[Article]:
    try:
        feed = feedparser.parse(PWC_RSS)
    except Exception as e:
        log.warning(f"PwC RSS 실패: {e}")
        return []

    articles = []
    for entry in feed.entries[:10]:
        articles.append(Article(
            url=entry.get("link", ""),
            title=entry.get("title", ""),
            content=getattr(entry, "summary", ""),
            source="pwc",
            published_at=_parse_date(entry),
            signals={},
        ))
    return articles


# ── 유틸 ──────────────────────────────────────────────────────────────────


def _parse_date(entry) -> str:
    for field in ("published", "updated"):
        val = getattr(entry, field, None)
        if val:
            return val
    return datetime.now(tz=timezone.utc).isoformat()


def _entry_content(entry) -> str:
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].get("value", "")
    return getattr(entry, "summary", "") or getattr(entry, "description", "")
