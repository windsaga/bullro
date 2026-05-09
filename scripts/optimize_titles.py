"""기존 포스트 제목을 검색형으로 개선하는 후보를 생성.

사용법:
    python scripts/optimize_titles.py [--apply] [--slug <slug>]

동작:
1. WordPress REST API로 발행된 포스트 목록 직접 조회
2. DeepSeek으로 각 제목의 검색형 개선안 3개 생성
3. 결과를 data/title_suggestions.json에 저장
4. --apply 시 WordPress REST API로 첫 번째 개선안 자동 적용
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.config import cfg
from pipeline.llm import deepseek

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SYSTEM = """당신은 한국 SEO 전문가입니다. 기술 블로그 제목을 검색 유입에 최적화합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 블로그 포스트 제목을 검색형으로 개선하세요.

현재 제목: {title}

검색형 제목 작성 규칙:
- 모델명·도구명·하드웨어명(고유명사)을 제목 앞쪽에 배치
- "방법", "설정", "비교", "오류 해결", "가이드", "설치", "사용법" 중 하나 이상 포함
- 55자 이내
- "~에 대해", "~을 알아보겠습니다" 금지
- 나쁜 예: "AI 코딩 도구의 현황" → 좋은 예: "Claude Code vs Codex CLI 비교"

개선 후보 3개를 JSON으로 반환:
{{
  "candidates": [
    "개선안1 (가장 추천)",
    "개선안2",
    "개선안3"
  ],
  "reason": "개선 이유 한 줄"
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
            params={"status": "publish", "per_page": 100, "page": page},
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


def generate_candidates(title: str) -> dict:
    prompt = PROMPT_TMPL.format(title=title)
    raw = deepseek(prompt, system=SYSTEM, max_tokens=300, temperature=0.4)
    try:
        return _parse_json(raw)
    except Exception as e:
        log.error(f"JSON 파싱 실패 [{title[:40]}]: {e}")
        return {"candidates": [], "reason": "파싱 실패"}


def apply_title_to_wordpress(wp_id: int, new_title: str, cred: str) -> bool:
    try:
        r = requests.post(
            f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts/{wp_id}",
            json={"title": new_title},
            headers={"Authorization": f"Basic {cred}", "Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        log.info(f"  제목 업데이트 완료: {new_title}")
        return True
    except Exception as e:
        log.error(f"  WordPress 업데이트 실패: {e}")
        return False


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
    parser = argparse.ArgumentParser(description="기존 포스트 제목 검색형 개선")
    parser.add_argument("--apply", action="store_true", help="첫 번째 개선안을 WordPress에 자동 적용")
    parser.add_argument("--slug", help="특정 슬러그만 처리")
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
            log.error(f"슬러그 '{args.slug}'를 찾을 수 없음")
            sys.exit(1)

    suggestions = []
    for post in posts:
        wp_id = post.get("id")
        slug = post.get("slug", "")
        title_raw = post.get("title", {})
        title = title_raw.get("rendered", "") if isinstance(title_raw, dict) else str(title_raw)
        title = re.sub(r"<[^>]+>", "", title).strip()
        link = post.get("link", "")

        log.info(f"분석 중: {title[:50]}")
        result = generate_candidates(title)
        candidates = result.get("candidates", [])
        reason = result.get("reason", "")

        entry = {
            "wp_id": wp_id,
            "slug": slug,
            "current_title": title,
            "link": link,
            "candidates": candidates,
            "reason": reason,
        }

        if candidates:
            log.info(f"  추천: {candidates[0]}")
            log.info(f"  이유: {reason}")

        if args.apply and candidates and wp_id:
            if apply_title_to_wordpress(wp_id, candidates[0], cred):
                entry["applied"] = candidates[0]

        suggestions.append(entry)
        time.sleep(3)

    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = cfg.DATA_DIR / "title_suggestions.json"
    output_path.write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"완료: {len(suggestions)}개 포스트 분석 → {output_path}")
    if not args.apply:
        log.info("검토 후 --apply 플래그로 적용하세요.")


if __name__ == "__main__":
    main()
