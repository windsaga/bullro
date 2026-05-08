"""WordPress REST API 발행 — Application Password 인증."""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

import markdown as md
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

    # Markdown → HTML 변환
    raw_md = _strip_frontmatter(post.draft.content)
    html_body = _md_to_html(raw_md)

    # 태그 이름 → WordPress 태그 ID 변환
    tag_ids = _resolve_tag_ids(post.seo.tags[:10], cred)

    payload: dict = {
        "title": post.chosen_title,
        "content": html_body,
        "status": "future" if schedule_date else "publish",
        "slug": post.slug,
        "categories": [cfg.WORDPRESS_DEFAULT_CATEGORY_ID],
        "tags": tag_ids,
        "excerpt": post.seo.meta_description,
    }
    if schedule_date:
        payload["date"] = schedule_date

    # 썸네일 업로드
    if post.thumbnail_url:
        media_id = _upload_thumbnail(post.thumbnail_url, cred, post.chosen_title)
        if media_id:
            payload["featured_media"] = media_id
        else:
            # 업로드 실패 시 본문 상단 이미지 태그로 대체
            payload["content"] = (
                f'<img src="{post.thumbnail_url}" alt="{post.chosen_title}" />\n\n'
                + html_body
            )

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


# ── Markdown → HTML ───────────────────────────────────────────────────────────


def _md_to_html(content: str) -> str:
    """Markdown을 WordPress용 HTML로 변환."""
    return md.markdown(
        content,
        extensions=[
            "fenced_code",   # ```python ... ``` 코드블록
            "tables",        # Markdown 테이블
            "toc",           # 목차 앵커
            "nl2br",         # 줄바꿈 보존
        ],
    )


# ── 태그 ID 변환 ──────────────────────────────────────────────────────────────


def _resolve_tag_ids(tag_names: list[str], cred: str) -> list[int]:
    """태그 이름 목록 → WordPress 태그 ID 목록. 없는 태그는 자동 생성."""
    ids = []
    for name in tag_names:
        tag_id = _get_or_create_tag(name.strip(), cred)
        if tag_id:
            ids.append(tag_id)
    return ids


def _get_or_create_tag(name: str, cred: str) -> int | None:
    """태그 검색 후 없으면 생성, ID 반환."""
    if not name:
        return None
    headers = {
        "Authorization": f"Basic {cred}",
        "Content-Type": "application/json",
    }
    base = f"{cfg.WORDPRESS_URL}/wp-json/wp/v2/tags"

    # 1. 검색
    try:
        resp = requests.get(base, params={"search": name, "per_page": 5}, headers=headers, timeout=10)
        resp.raise_for_status()
        for tag in resp.json():
            if tag.get("name", "").lower() == name.lower():
                return tag["id"]
    except Exception as e:
        log.debug(f"태그 검색 실패 [{name}]: {e}")
        return None

    # 2. 없으면 생성
    try:
        resp = requests.post(base, json={"name": name}, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as e:
        log.warning(f"태그 생성 실패 [{name}]: {e}")
        return None


# ── 썸네일 업로드 ─────────────────────────────────────────────────────────────


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


# ── 유틸 ─────────────────────────────────────────────────────────────────────


def _strip_frontmatter(content: str) -> str:
    """YAML frontmatter (--- ... ---) 제거."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("---", 3)
    if end == -1:
        return content
    return stripped[end + 3:].lstrip()
