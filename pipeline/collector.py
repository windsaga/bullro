"""소스 수집 — 공식 블로그 RSS, GitHub 릴리즈, YouTube 키워드/채널."""
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

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_KEYWORDS = [
    "claude code tutorial",
    "LLM benchmark 2026",
    "AI agent development",
    "large language model explained",
    "GPT codex developer",
]
YOUTUBE_DAYS = 7
YOUTUBE_MAX_PER_KEYWORD = 10
YOUTUBE_MIN_DURATION = "medium"  # medium=4~20분, long=20분+ (숏츠·밈 제거)

YOUTUBE_CHANNELS_PATH = cfg.DATA_DIR / "youtube_channels.json"
YOUTUBE_CHANNEL_DAYS = 7   # 채널에서 최근 N일 영상만 수집

HEADERS = {"User-Agent": cfg.REDDIT_USER_AGENT}


def collect_articles() -> list[Article]:
    """전체 소스에서 수집 후 합산. 실패한 소스는 건너뜀."""
    articles: list[Article] = []

    sources = [
        ("blogs", _collect_official_blogs),
    ]
    if cfg.GITHUB_TOKEN:
        sources.append(("github", _collect_github))
    if cfg.YOUTUBE_API_KEY:
        sources.append(("youtube", _collect_youtube))
        sources.append(("youtube_ch", _collect_youtube_channels))

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
        # YouTube는 ?v= 파라미터가 식별자이므로 전체 URL을 키로 사용
        # 그 외 소스는 UTM 등 트래킹 파라미터 제거
        if a.source == "youtube":
            key = a.url.rstrip("/")
        else:
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


# ── YouTube ───────────────────────────────────────────────────────────────


def _collect_youtube() -> list[Article]:
    """YouTube Data API v3로 최근 7일 AI 관련 영상 수집."""
    from datetime import timedelta
    api_key = cfg.YOUTUBE_API_KEY
    published_after = (
        datetime.now(tz=timezone.utc) - timedelta(days=YOUTUBE_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    video_ids: list[str] = []
    snippets: dict[str, dict] = {}

    for keyword in YOUTUBE_KEYWORDS:
        try:
            resp = requests.get(
                YOUTUBE_SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": keyword,
                    "type": "video",
                    "publishedAfter": published_after,
                    "maxResults": YOUTUBE_MAX_PER_KEYWORD,
                    "order": "viewCount",
                    "videoDuration": YOUTUBE_MIN_DURATION,
                    "relevanceLanguage": "en",
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                vid_id = item["id"]["videoId"]
                if vid_id not in snippets:
                    video_ids.append(vid_id)
                    snippets[vid_id] = item["snippet"]
            time.sleep(0.5)
        except Exception as e:
            log.debug(f"YouTube 검색 실패 [{keyword}]: {e}")

    if not video_ids:
        return []

    # 조회수 일괄 조회 (50개씩 배치)
    stats: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = requests.get(
                YOUTUBE_VIDEOS_URL,
                params={"part": "statistics", "id": ",".join(batch), "key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                stats[item["id"]] = item.get("statistics", {})
        except Exception as e:
            log.debug(f"YouTube 통계 조회 실패: {e}")

    articles: list[Article] = []
    seen: set[str] = set()
    for vid_id in video_ids:
        if vid_id in seen:
            continue
        seen.add(vid_id)
        snippet = snippets[vid_id]
        views = int(stats.get(vid_id, {}).get("viewCount", 0))
        articles.append(
            Article(
                url=f"https://www.youtube.com/watch?v={vid_id}",
                title=snippet.get("title", ""),
                content=snippet.get("description", "")[:2000],
                source="youtube",
                published_at=snippet.get("publishedAt", ""),
                signals={"youtube_views": views},
            )
        )

    return articles


def _collect_youtube_channels() -> list[Article]:
    """youtube_channels.json 채널의 최근 영상을 수집한다.

    search API(100유닛) 대신 playlistItems API(1유닛)를 사용해 쿼터를 아낀다.
    """
    from datetime import timedelta

    if not YOUTUBE_CHANNELS_PATH.exists():
        log.debug("youtube_channels.json 없음 — 채널 수집 건너뜀")
        return []

    try:
        channels = json.loads(YOUTUBE_CHANNELS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("youtube_channels.json 로드 실패: %s", e)
        return []

    api_key = cfg.YOUTUBE_API_KEY
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=YOUTUBE_CHANNEL_DAYS)).isoformat()

    video_ids: list[str] = []
    channel_of: dict[str, str] = {}   # video_id → channel name

    for ch in channels:
        ch_id = ch.get("channel_id", "")
        ch_name = ch.get("name", ch_id)
        if not ch_id:
            continue

        # 채널의 업로드 플레이리스트 ID 조회 (UC... → UU...)
        uploads_playlist = "UU" + ch_id[2:]

        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={
                    "part": "snippet",
                    "playlistId": uploads_playlist,
                    "maxResults": 10,
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                snippet = item.get("snippet", {})
                published = snippet.get("publishedAt", "")
                if published < cutoff:
                    continue
                vid_id = snippet.get("resourceId", {}).get("videoId", "")
                if vid_id and vid_id not in channel_of:
                    video_ids.append(vid_id)
                    channel_of[vid_id] = ch_name
            time.sleep(0.3)
        except Exception as e:
            log.debug("채널 수집 실패 [%s]: %s", ch_name, e)

    if not video_ids:
        return []

    # 조회수 일괄 조회
    stats: dict[str, dict] = {}
    snippets_map: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = requests.get(
                YOUTUBE_VIDEOS_URL,
                params={"part": "statistics,snippet", "id": ",".join(batch), "key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                stats[item["id"]] = item.get("statistics", {})
                snippets_map[item["id"]] = item.get("snippet", {})
        except Exception as e:
            log.debug("채널 영상 통계 조회 실패: %s", e)

    articles: list[Article] = []
    for vid_id in video_ids:
        snippet = snippets_map.get(vid_id, {})
        views = int(stats.get(vid_id, {}).get("viewCount", 0))
        ch_name = channel_of.get(vid_id, "")
        articles.append(
            Article(
                url=f"https://www.youtube.com/watch?v={vid_id}",
                title=snippet.get("title", ""),
                content=f"[{ch_name}] " + snippet.get("description", "")[:1800],
                source="youtube",
                published_at=snippet.get("publishedAt", ""),
                signals={"youtube_views": views, "channel": ch_name},
            )
        )

    return articles
