"""잘린 WordPress 포스트를 GLM으로 감지하고 결론 보완.

사용법:
    python scripts/repair_truncated_posts.py [--dry-run]

동작:
1. WordPress REST API로 모든 발행 포스트 조회
2. 각 포스트의 raw content 끝을 GPT/GLM으로 잘림 여부 판단
3. 잘린 포스트는 GLM으로 결론 생성 후 WordPress에 PATCH
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
from pipeline.llm import glm, deepseek

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# 정상 종결 패턴 (있으면 완결로 판단)
COMPLETION_SIGNALS = [
    r'결론',
    r'마치며',
    r'정리하면',
    r'전망',
    r'참고\s*(자료|링크)',
    r'출처',
    r'References',
    r'마무리',
]

# 한국어 정상 문장 종결 어미 (문장 끝에 이 패턴이 있으면 완결된 문장)
KO_SENTENCE_END = re.compile(
    r'[다요오세].{0,2}\s*$|[.!?。」』]\s*$',
    re.UNICODE,
)


def _cred() -> str:
    return base64.b64encode(
        f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
    ).decode()


def get_all_posts(cred: str) -> list[dict]:
    """WordPress에서 발행된 모든 포스트 목록 조회."""
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
    """HTML 태그 제거, 텍스트만 추출."""
    return re.sub(r'<[^>]+>', '', html).strip()


def is_truncated(raw_content: str) -> bool:
    """포스트 raw content(HTML)가 잘렸는지 판단."""
    text = strip_html(raw_content).strip()
    if not text:
        return False

    # 내용이 너무 짧으면 잘린 것으로 의심
    if len(text) < 500:
        return True

    # HTML 코드블록이 닫히지 않은 채로 끝나는 경우 (마크다운 → HTML 변환 후)
    # <pre> 블록 내용이 , 또는 { 로 끝나면 코드 잘림
    html_trimmed = raw_content.strip()
    if re.search(r'<code[^>]*>[^<]*[,{]\s*$', html_trimmed, re.DOTALL):
        return True
    # Markdown 코드펜스가 닫히지 않은 경우
    if re.search(r'```[a-z]*\n[^`]+$', html_trimmed, re.DOTALL):
        return True

    # 결론성 단어가 있으면 완결 (전체 텍스트에서 검색)
    for sig in COMPLETION_SIGNALS:
        if re.search(sig, text, re.IGNORECASE):
            return False

    # 마지막 600자에서 문장 종결 여부 판단
    tail = text[-600:]
    # 한국어 정상 종결 어미 또는 구두점으로 끝나면 완결
    if KO_SENTENCE_END.search(tail):
        return False

    return True


def generate_conclusion(post_title: str, existing_content_text: str) -> str:
    """DeepSeek으로 잘린 포스트의 결론 섹션 생성.
    GLM thinking 모드는 max_tokens를 소진해 빈 응답이 나올 수 있어 DeepSeek 사용.
    """
    context_tail = existing_content_text[-1500:] if len(existing_content_text) > 1500 else existing_content_text

    prompt = f"""다음은 한국 AI 기술 블로그 포스트입니다. 포스트가 중간에 잘려 결론이 없습니다.

포스트 제목: {post_title}

포스트 마지막 내용 (잘린 부분):
{context_tail}

---
위 내용을 이어받아 **결론/마무리 섹션을 Markdown으로 작성**하세요.

요구사항:
- ## 결론 또는 ## 마치며 헤딩으로 시작
- 3~5문장으로 핵심 내용 요약
- 독자에게 실무 적용 포인트 제시
- 마지막에 ## 참고 자료 섹션 포함
- 400~600자 분량
- 잘린 앞 내용과 자연스럽게 이어지도록 작성"""

    result = deepseek(prompt, max_tokens=1024, temperature=0.5)
    return result


def repair_post(post: dict, cred: str, dry_run: bool = False) -> bool:
    """단일 포스트 복구. True=수정됨."""
    wp_id = post["id"]
    title = post.get("title", {}).get("rendered", "")
    raw_content = post.get("content", {}).get("raw", "")
    link = post.get("link", "")

    log.info(f"[{wp_id}] {title[:50]} — 검사 중...")

    if not is_truncated(raw_content):
        log.info(f"[{wp_id}] 정상 완결 — 스킵")
        return False

    log.warning(f"[{wp_id}] 잘림 감지! {link}")

    if dry_run:
        log.info(f"[DRY-RUN] 업데이트 생략 (GLM 호출 없음)")
        return True

    text_content = strip_html(raw_content)
    conclusion_md = generate_conclusion(title, text_content)

    if not conclusion_md.strip():
        log.error(f"[{wp_id}] 결론 생성 실패")
        return False

    # Markdown → HTML (간단 변환)
    conclusion_html = _simple_md_to_html(conclusion_md)
    new_content = raw_content.rstrip() + "\n\n" + conclusion_html

    log.info(f"[{wp_id}] 결론 {len(conclusion_md)}자 생성 완료")


    try:
        r = requests.post(
            f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts/{wp_id}",
            json={"content": new_content},
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        r.raise_for_status()
        log.info(f"[{wp_id}] WordPress 업데이트 완료: {link}")
        return True
    except Exception as e:
        log.error(f"[{wp_id}] 업데이트 실패: {e}")
        return False


def _simple_md_to_html(md: str) -> str:
    """기본 Markdown → HTML (## h2, **bold**, 단락)."""
    lines = md.split('\n')
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('## '):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith('### '):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith('- '):
            html_lines.append(f"<li>{_inline_md(stripped[2:])}</li>")
        elif stripped == '':
            html_lines.append('')
        else:
            html_lines.append(f"<p>{_inline_md(stripped)}</p>")
    return '\n'.join(html_lines)


def _inline_md(text: str) -> str:
    """**bold** 변환."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)


def main():
    parser = argparse.ArgumentParser(description="잘린 WordPress 포스트 자동 복구")
    parser.add_argument('--dry-run', action='store_true', help="실제 업데이트 없이 시뮬레이션")
    parser.add_argument('--slug', help="특정 슬러그만 복구 (예: llm-vibecoding-development-paradigm)")
    args = parser.parse_args()

    if not cfg.WORDPRESS_URL or not cfg.WORDPRESS_APP_PASSWORD:
        log.error("WORDPRESS_URL / WORDPRESS_APP_PASSWORD 환경변수 미설정")
        sys.exit(1)

    cred = _cred()
    log.info("WordPress 포스트 목록 조회 중...")
    posts = get_all_posts(cred)
    log.info(f"총 {len(posts)}개 포스트 조회됨")

    if args.slug:
        posts = [p for p in posts if p.get("slug") == args.slug]
        if not posts:
            log.error(f"슬러그 '{args.slug}'를 찾을 수 없음")
            sys.exit(1)

    repaired = 0
    for post in posts:
        try:
            if repair_post(post, cred, dry_run=args.dry_run):
                repaired += 1
            time.sleep(2)  # API 부하 방지
        except Exception as e:
            log.error(f"포스트 처리 오류 [{post.get('id')}]: {e}")

    log.info(f"완료: {repaired}/{len(posts)}개 포스트 복구됨")


if __name__ == "__main__":
    main()
