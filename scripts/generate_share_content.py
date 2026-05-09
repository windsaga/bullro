"""기존 발행된 WordPress 포스트에 대해 X 공유 콘텐츠를 소급 생성.

사용법:
    python scripts/generate_share_content.py [--slug <slug>] [--limit N]

동작:
1. WordPress REST API로 발행된 포스트 목록 직접 조회
2. posts/share/{slug}.x.md가 이미 있으면 스킵
3. DeepSeek으로 X 공유문 생성 후 posts/share/ 저장
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.config import cfg
from pipeline.llm import deepseek

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SYSTEM = """당신은 기술 블로그 콘텐츠 마케터입니다.
블로그 포스트를 읽고 X(Twitter) 공유문을 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 블로그 포스트에 대한 X(Twitter) 공유문을 생성하세요.

포스트 제목: {title}
포스트 링크: {link}

포스트 내용 (요약):
{content}

X(Twitter) 공유문을 JSON으로 반환하세요:

{{
  "twitter": "한국어 트윗 (260자 이내). 첫 줄: 핵심 결과+수치. ▸ 포인트 3개. 빈 줄 후 링크 {link}"
}}"""


def _cred() -> str:
    return base64.b64encode(
        f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
    ).decode()


def get_all_posts(cred: str) -> list[dict]:
    """WordPress REST API로 발행된 모든 포스트 조회."""
    posts = []
    page = 1
    while True:
        r = requests.get(
            f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts",
            params={"status": "publish", "per_page": 100, "page": page, "context": "edit"},
            headers={"Authorization": f"Basic {cred}"},
            timeout=30,
        )
        if not r.ok or not r.json():
            break
        posts.extend(r.json())
        if len(r.json()) < 100:
            break
        page += 1
    return posts


def strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def already_generated(slug: str) -> bool:
    return bool(list(cfg.SHARE_DIR.glob(f"*{slug[:30]}*.x.md")))


def generate_and_save(title: str, wp_link: str, content: str, slug: str) -> str | None:
    """DeepSeek으로 X 공유문 생성 후 저장. 저장된 파일 경로 반환."""
    prompt = PROMPT_TMPL.format(title=title, link=wp_link, content=content[:2000])
    raw = deepseek(prompt, system=SYSTEM, max_tokens=400, temperature=0.5)

    try:
        data = _parse_json(raw)
    except Exception as e:
        log.error(f"JSON 파싱 실패 [{title[:40]}]: {e}")
        return None

    twitter_text = data.get("twitter", "")
    if not twitter_text:
        log.error(f"twitter 필드 없음 [{title[:40]}]")
        return None

    cfg.SHARE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    path = cfg.SHARE_DIR / f"{today}-{slug[:40]}.x.md"
    path.write_text(f"# X / Twitter\n\n{twitter_text}\n", encoding="utf-8")
    return str(path)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        start = text.find("{", text.find("```"))
        end = text.rfind("}") + 1
        text = text[start:end]
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}") + 1
        text = text[start:end]
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description="기존 포스트 X 공유 콘텐츠 소급 생성")
    parser.add_argument("--slug", help="특정 슬러그만 처리")
    parser.add_argument("--limit", type=int, default=0, help="처리할 최대 포스트 수 (0=전체)")
    args = parser.parse_args()

    if not cfg.WORDPRESS_URL or not cfg.WORDPRESS_APP_PASSWORD:
        log.error("WORDPRESS_URL / WORDPRESS_APP_PASSWORD 환경변수 미설정")
        sys.exit(1)

    cred = _cred()
    log.info("WordPress 포스트 목록 조회 중...")
    posts = get_all_posts(cred)
    log.info(f"총 {len(posts)}개 발행 포스트 조회됨")

    if args.slug:
        posts = [p for p in posts if p.get("slug") == args.slug]
        if not posts:
            log.error(f"슬러그 '{args.slug}' 포스트를 찾을 수 없음")
            sys.exit(1)

    if args.limit > 0:
        posts = posts[:args.limit]

    generated = 0
    skipped = 0
    for post in posts:
        slug = post.get("slug", "")
        title = post.get("title", {}).get("rendered", "") or post.get("slug", "")
        link = post.get("link", "")

        if already_generated(slug):
            log.info(f"[스킵] {title[:40]} — 공유 파일 이미 존재")
            skipped += 1
            continue

        log.info(f"생성 중: {title[:50]}")
        raw_content = post.get("content", {}).get("raw", "")
        content = strip_html(raw_content)
        if not content:
            log.warning("  본문 없음 — 스킵")
            continue

        saved = generate_and_save(title, link, content, slug)
        if saved:
            log.info(f"  저장: {Path(saved).name}")
            generated += 1
        time.sleep(3)

    log.info(f"완료: {generated}개 생성, {skipped}개 스킵")


if __name__ == "__main__":
    main()
