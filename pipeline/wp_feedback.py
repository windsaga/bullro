"""WordPress 인기글 기반 토픽 힌트 생성.

Jetpack Stats API 우선, 없으면 댓글 수 기준으로 폴백.
scorer._wp_popular_boost 에서 한 번만 호출되고 캐시됨.
"""
from __future__ import annotations

import base64
import logging
from collections import Counter
from functools import lru_cache

import requests

from pipeline.config import cfg

log = logging.getLogger(__name__)

_WP_TIMEOUT = 10
WP_BOOST_CAP = 0.3  # WP 피드백 최대 부스트 상한


def _auth_headers() -> dict:
    token = base64.b64encode(
        f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


@lru_cache(maxsize=1)
def get_popular_topic_hints() -> list[dict]:
    """WordPress 인기글 카테고리/태그를 키워드 힌트로 반환.

    반환 형식: [{"keywords": [...], "weight": float}]
    scorer._interest_boost 와 동일한 포맷이라 재사용 가능.
    """
    if not (cfg.WORDPRESS_URL and cfg.WORDPRESS_USERNAME and cfg.WORDPRESS_APP_PASSWORD):
        return []

    posts = _try_jetpack_top_posts() or _get_posts_by_comment_count()
    if not posts:
        return []

    return _posts_to_hints(posts)


# ── WordPress 데이터 조회 ──────────────────────────────────────────────────


def _try_jetpack_top_posts() -> list[dict] | None:
    """Jetpack Stats API로 이번 달 인기글 조회. 실패 시 None 반환."""
    try:
        resp = requests.get(
            f"{cfg.WORDPRESS_URL}/wp-json/jetpack/v4/top-posts",
            params={"period": "month", "num": 20},
            headers=_auth_headers(),
            timeout=_WP_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        posts_data = resp.json().get("posts", [])
        if not posts_data:
            return None
        view_map = {str(p["post_id"]): p.get("views", 0) for p in posts_data if p.get("post_id")}
        posts = _fetch_posts_meta(list(view_map.keys()))
        for p in posts:
            p["_views"] = view_map.get(str(p.get("id", "")), 0)
        log.info(f"Jetpack Stats: 인기글 {len(posts)}건 로드")
        return posts
    except Exception as e:
        log.debug(f"Jetpack Stats 조회 실패 (폴백 진행): {e}")
        return None


def _get_posts_by_comment_count() -> list[dict] | None:
    """REST API 댓글 수 기준 인기글 조회 (Jetpack 폴백)."""
    try:
        resp = requests.get(
            f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/posts",
            params={
                "per_page": 20,
                "orderby": "comment_count",
                "order": "desc",
                "_fields": "id,categories,tags",
            },
            headers=_auth_headers(),
            timeout=_WP_TIMEOUT,
        )
        resp.raise_for_status()
        posts = resp.json()
        log.info(f"WP REST API(댓글수 기준): 인기글 {len(posts)}건 로드")
        return posts
    except Exception as e:
        log.debug(f"WP 인기글 조회 실패: {e}")
        return None


def _fetch_posts_meta(post_ids: list[str]) -> list[dict]:
    """post_id 목록의 categories/tags 메타 조회."""
    result = []
    for pid in post_ids:
        try:
            resp = requests.get(
                f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/posts/{pid}",
                params={"_fields": "id,categories,tags"},
                headers=_auth_headers(),
                timeout=_WP_TIMEOUT,
            )
            if resp.status_code == 200:
                result.append(resp.json())
        except Exception:
            pass
    return result


# ── 힌트 변환 ─────────────────────────────────────────────────────────────


def _posts_to_hints(posts: list[dict]) -> list[dict]:
    """포스트 목록 → scorer 호환 keyword hint 리스트 변환."""
    cat_ids: set[int] = set()
    tag_ids: set[int] = set()
    for post in posts:
        cat_ids.update(post.get("categories", []))
        tag_ids.update(post.get("tags", []))

    cat_names = _fetch_term_names("categories", list(cat_ids))
    tag_names = _fetch_term_names("tags", list(tag_ids))

    total = max(len(posts), 1)
    cat_counter: Counter = Counter()
    tag_counter: Counter = Counter()
    for post in posts:
        for cid in post.get("categories", []):
            if name := cat_names.get(cid):
                cat_counter[name] += 1
        for tid in post.get("tags", []):
            if name := tag_names.get(tid):
                tag_counter[name] += 1

    hints: list[dict] = []
    for name, count in cat_counter.most_common(5):
        hints.append({"keywords": [name.lower()], "weight": round(count / total, 2)})
    for name, count in tag_counter.most_common(10):
        # 태그는 카테고리보다 가중치 0.8배
        hints.append({"keywords": [name.lower()], "weight": round(count / total * 0.8, 2)})

    log.info(f"WP 인기글 힌트 생성: 카테고리 {len(cat_counter)}개, 태그 {len(tag_counter)}개")
    return hints


def _fetch_term_names(taxonomy: str, ids: list[int]) -> dict[int, str]:
    """카테고리/태그 ID → 이름 매핑 일괄 조회."""
    if not ids:
        return {}
    try:
        resp = requests.get(
            f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/{taxonomy}",
            params={"include": ",".join(str(i) for i in ids), "_fields": "id,name", "per_page": 100},
            headers=_auth_headers(),
            timeout=_WP_TIMEOUT,
        )
        resp.raise_for_status()
        return {item["id"]: item["name"] for item in resp.json()}
    except Exception as e:
        log.debug(f"term 이름 조회 실패 [{taxonomy}]: {e}")
        return {}
