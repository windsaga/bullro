"""WordPress REST API 발행 — Application Password 인증."""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

from pipeline.config import cfg
from pipeline.models import Post

log = logging.getLogger(__name__)


@dataclass
class PublishResult:
    wp_id: int
    link: str
    status: str


def publish_to_wordpress(post: Post, schedule_date: str = "") -> PublishResult:
    """
    WordPress에 포스트 발행.
    schedule_date: ISO8601 문자열 (예: "2026-04-29T09:00:00") — 비어있으면 즉시 발행
    """
    if not cfg.WORDPRESS_URL:
        raise RuntimeError("WORDPRESS_URL 환경변수 미설정")
    if not cfg.WORDPRESS_APP_PASSWORD:
        raise RuntimeError("WORDPRESS_APP_PASSWORD 환경변수 미설정")

    cred = base64.b64encode(
        f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
    ).decode()

    # 본문에서 frontmatter 제거
    body = _strip_frontmatter(post.draft.content)

    payload: dict = {
        "title": post.chosen_title,
        "content": body,
        "status": "future" if schedule_date else "publish",
        "slug": post.slug,
        "categories": [cfg.WORDPRESS_DEFAULT_CATEGORY_ID],
        "tags": post.seo.tags[:10],
        "excerpt": post.seo.meta_description,
    }
    if schedule_date:
        payload["date"] = schedule_date
    if post.thumbnail_url:
        # 썸네일은 Featured Image — WordPress는 먼저 media 업로드 후 featured_media ID 필요
        # v1에서는 본문 상단에 이미지 태그 삽입으로 대체
        payload["content"] = f'<img src="{post.thumbnail_url}" alt="{post.chosen_title}" />\n\n' + body

    data = json.dumps(payload).encode("utf-8")
    endpoint = f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/posts"

    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Basic {cred}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            log.info(f"WordPress 발행 완료: {result.get('link')}")
            return PublishResult(
                wp_id=result.get("id", 0),
                link=result.get("link", ""),
                status=result.get("status", ""),
            )
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        log.error(f"WordPress API 오류 {e.code}: {body_bytes.decode('utf-8', errors='replace')[:500]}")
        raise


def _strip_frontmatter(content: str) -> str:
    """YAML frontmatter (--- ... ---) 제거."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("---", 3)
    if end == -1:
        return content
    return stripped[end + 3:].lstrip()
