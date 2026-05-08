"""WordPress REST API 발행 — Application Password 인증."""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

import requests

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
        media_id = _upload_thumbnail(post.thumbnail_url, cred, post.chosen_title)
        if media_id:
            payload["featured_media"] = media_id
        else:
            # 업로드 실패 시 본문 상단 이미지 태그로 대체
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


def _upload_thumbnail(thumbnail_url: str, cred: str, title: str) -> int | None:
    """썸네일을 WordPress Media API로 업로드하고 media ID 반환. 실패 시 None."""
    try:
        img_resp = requests.get(thumbnail_url, timeout=30)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
        content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()

        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title[:40])
        filename = f"{safe_title}.jpg"
        endpoint = f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/media"

        req = urllib.request.Request(
            endpoint,
            data=img_bytes,
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            media_id = result.get("id")
            log.info(f"썸네일 업로드 완료: media_id={media_id}")
            return media_id
    except Exception as e:
        log.warning(f"썸네일 WordPress 업로드 실패 (본문 이미지 태그로 대체): {e}")
        return None


def _strip_frontmatter(content: str) -> str:
    """YAML frontmatter (--- ... ---) 제거."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("---", 3)
    if end == -1:
        return content
    return stripped[end + 3:].lstrip()
