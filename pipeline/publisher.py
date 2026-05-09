"""WordPress REST API 발행 — Application Password 인증."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
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

    # Markdown → HTML 변환 (cred 전달 → Mermaid 블록을 WP 미디어로 업로드)
    raw_md = _strip_frontmatter(post.draft.content)
    html_body = _md_to_html(raw_md, cred=cred)

    # 태그 이름 → WordPress 태그 ID 변환
    tag_ids = _resolve_tag_ids(post.seo.tags[:10], cred)

    category_id = _determine_category(post.chosen_title, post.seo.tags)
    category_ids = [category_id]
    series_cat = _series_category_id(post.seo.series)
    if series_cat:
        category_ids.append(series_cat)

    payload: dict = {
        "title": post.chosen_title,
        "content": html_body,
        "status": "future" if schedule_date else "publish",
        "slug": post.slug,
        "categories": category_ids,
        "tags": tag_ids,
        "excerpt": post.seo.meta_description,
    }
    if schedule_date:
        payload["date"] = schedule_date

    # 썸네일 업로드 (실패 시 본문 삽입 없이 로그만 남김 — 이미지 중복 방지)
    if post.thumbnail_url:
        media_id = _upload_thumbnail(post.thumbnail_url, cred, post.chosen_title)
        if media_id:
            payload["featured_media"] = media_id

    data = json.dumps(payload).encode("utf-8")
    endpoint = f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts"

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
            wp_id = result.get("id", 0)
            log.info(f"WordPress 발행 완료: {result.get('link')}")

            # Rank Math SEO 메타 업데이트 (포커스 키워드, SEO 제목, 메타 설명)
            if wp_id and (post.seo.focus_keyword or post.seo.meta_description):
                _update_rankmath_seo(
                    wp_id=wp_id,
                    cred=cred,
                    focus_keyword=post.seo.focus_keyword,
                    seo_title=post.chosen_title,
                    meta_description=post.seo.meta_description,
                )

            return PublishResult(
                wp_id=wp_id,
                link=result.get("link", ""),
                status=result.get("status", ""),
            )
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        log.error(f"WordPress API 오류 {e.code}: {body_bytes.decode('utf-8', errors='replace')[:500]}")
        raise


# ── Markdown → HTML ───────────────────────────────────────────────────────────


def _clean_llm_markdown(text: str) -> str:
    """GLM 스트리밍 아티팩트 제거.

    GLM-5.1이 출력하는 두 가지 패턴을 정리:
    1. 줄 시작 > (이전 비어있지 않은 줄의 연속으로 보이는 블록인용 아티팩트)
       예: "VRAM 용\\n>량별" → "VRAM 용량별"
    2. 한국어 단어 사이에 남아있는 > 문자
       예: "고려>하는" → "고려하는"

    코드블록 내부는 손대지 않음.
    """
    lines = text.split('\n')
    out: list[str] = []
    in_code = False

    for line in lines:
        if line.strip().startswith('```'):
            in_code = not in_code

        if not in_code and line.startswith('>') and out and out[-1].strip():
            prev = out[-1].rstrip()
            if re.match(r'^#{1,6}\s', prev):
                # > 가 헤딩 직후에 오면 새 문단으로 처리 (헤딩과 합치지 않음)
                body = line[1:]
                if body.startswith(' '):
                    body = body[1:]
                out.append(body)
            else:
                continuation = line[1:]
                sep = '' if not continuation or continuation[0].isspace() else ' '
                out[-1] = prev + sep + continuation
        else:
            out.append(line)

    joined = '\n'.join(out)
    # 한국어 글자 사이 > 제거 (예: 고려>하는 → 고려하는)
    joined = re.sub(r'([가-힣])\s*>\s*([가-힣])', r'\1\2', joined)
    return joined


def _md_to_html(content: str, cred: str = "") -> str:
    """Markdown을 WordPress용 HTML로 변환."""
    # LLM 아티팩트 주석 제거 (P6 팩트체크가 삽입하는 <!-- CHANGES: ... -->)
    content = re.sub(r'\s*<!--\s*CHANGES:.*?-->\s*', '', content, flags=re.DOTALL)
    # Mermaid 코드 블록 → 이미지로 변환 (변환 후 일반 코드블록이 되도록 처리)
    content = _convert_mermaid_blocks(content, cred)
    cleaned = _clean_llm_markdown(content)
    html = md.markdown(
        cleaned,
        extensions=[
            "fenced_code",   # ```python ... ``` 코드블록
            "tables",        # Markdown 테이블
            "toc",           # 목차 앵커
            # nl2br 제거: LLM이 출력한 단일 \n을 <br>로 변환하면
            # 줄바꿈된 단어가 분리되는 문제 발생
        ],
    )
    return _verify_html_structure(html)


def _convert_mermaid_blocks(content: str, cred: str = "") -> str:
    """Mermaid 코드 블록을 mermaid.ink 이미지로 변환.
    변환 실패 시 원본 코드 블록 유지."""
    def replace_block(m: re.Match) -> str:
        mermaid_code = m.group(1).strip()
        try:
            encoded = base64.urlsafe_b64encode(mermaid_code.encode()).decode()
            img_url = f"https://mermaid.ink/img/{encoded}?bgColor=white"
            r = requests.get(img_url, timeout=30)
            if r.status_code != 200 or len(r.content) < 500:
                log.warning(f"Mermaid 변환 실패 (status={r.status_code}), 코드 블록 유지")
                return m.group(0)

            content_type = r.headers.get("Content-Type", "image/png").split(";")[0]
            filename = "mermaid-diagram.png"

            if cred:
                r2 = requests.post(
                    f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/media",
                    data=r.content,
                    headers={
                        "Authorization": f"Basic {cred}",
                        "Content-Type": content_type,
                        "Content-Disposition": f'attachment; filename="{filename}"',
                    },
                    timeout=30,
                )
                if r2.ok:
                    wp_url = r2.json().get("source_url", img_url)
                    log.info(f"Mermaid → WordPress 미디어 업로드: {wp_url}")
                else:
                    wp_url = img_url
            else:
                wp_url = img_url

            return (
                f'\n\n<figure class="wp-block-image">'
                f'<img src="{wp_url}" alt="다이어그램" style="max-width:100%"/>'
                f'</figure>\n\n'
            )
        except Exception as e:
            log.warning(f"Mermaid 변환 오류: {e} — 코드 블록 유지")
            return m.group(0)

    return re.sub(
        r'```mermaid\n(.*?)```',
        replace_block,
        content,
        flags=re.DOTALL,
    )


# Section heading prefixes GLM uses across all angles
_SECTION_PREFIXES = (
    "TL;DR", "문제 정의:", "배경:", "핵심 메커니즘:", "기술적 메커니즘:", "코드/수식:",
    "한계:", "결론:", "설치/설정:", "핵심 예제 코드:", "실무 패턴:", "주의사항:",
    "글로벌 동향:", "국내 유사 사례:", "한국 적용:", "전망:",
    "비교 대상:", "기준별 비교:", "시나리오별 선택:", "주요 발견:",
)

# 한국 tech 블로그 본문 첫 문장 시작 패턴
_BODY_STARTERS = re.compile(
    r'(?:최근|현재|이번|지난|해당|본\s|이는|이를|이후|그러나|하지만|따라서|즉,|또한|'
    r'Windows|Linux|macOS|Python|Docker|Kubernetes|GitHub|Hugging|'
    r'이\s글|본\s포스트|본\s정보|본\s연구|이\s모델|이\s도구)'
)


def _split_h2_heading_body(inner: str) -> tuple[str, str] | None:
    """h2 내용을 (heading_text, body_html)로 분리. 실패 시 None."""
    text = re.sub(r'<[^>]+>', '', inner)

    # TL;DR
    if inner.startswith("TL;DR "):
        return "TL;DR", inner[6:].lstrip()

    for pfx in _SECTION_PREFIXES:
        if not text.startswith(pfx):
            continue
        rest_text = text[len(pfx):].lstrip()

        # 1순위: 본문 시작 단어로 정확히 분리
        m = _BODY_STARTERS.search(rest_text)
        if m and m.start() > 3:
            subtitle = rest_text[:m.start()].rstrip()
            starter = m.group(0).split()[0]
            idx = inner.find(starter, len(pfx))
            if idx > 0:
                return (pfx + " " + subtitle).rstrip(), inner[idx:].lstrip()

        # 2순위: fallback — 5번째 어절 뒤에서 분리
        words = rest_text.split()
        if len(words) > 5:
            n = 5
            subtitle = ' '.join(words[:n])
            idx = inner.find(words[n], len(pfx))
            if idx > 0:
                return (pfx + " " + subtitle).rstrip(), inner[idx:].lstrip()

    return None


def _verify_html_structure(html: str) -> str:
    """h2 내 과도한 텍스트(heading+body 병합 버그) 감지 및 자동 수정. 수정된 HTML 반환."""
    def try_repair(m: re.Match) -> str:
        attrs = m.group(1)
        inner = m.group(2)
        text = re.sub(r'<[^>]+>', '', inner)

        if len(text) <= 80:
            return m.group(0)

        result = _split_h2_heading_body(inner)
        if result:
            heading, body = result
            body = re.sub(r'\s*---\s*$', '', body).strip()
            attrs = "" if len(attrs) > 120 else attrs
            log.info(f"[HTML 수정] '{heading[:50]}' | {len(body)}자 본문 분리")
            return f'<h2{attrs}>{heading}</h2>\n<p>{body}</p>'

        log.warning(f"[HTML 수정 실패] h2 {len(text)}자: {text[:80]}")
        return m.group(0)

    return re.sub(r'<h2([^>]*)>(.*?)</h2>', try_repair, html, flags=re.DOTALL)


def verify_and_repair_published_post(wp_id: int, cred: str) -> bool:
    """발행된 포스트의 HTML 구조 검증 및 자동 수정. True=수정 완료."""
    endpoint = f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/posts/{wp_id}"
    try:
        r = requests.get(
            endpoint, params={"context": "edit"},
            headers={"Authorization": f"Basic {cred}"}, timeout=15,
        )
        r.raise_for_status()
        raw = r.json().get("content", {}).get("raw", "")
    except Exception as e:
        log.warning(f"[발행 후 검증] 포스트 {wp_id} 조회 실패: {e}")
        return False

    repaired = _verify_html_structure(raw)
    if repaired == raw:
        log.info(f"[발행 후 검증] 포스트 {wp_id}: HTML 구조 정상")
        return False

    try:
        r2 = requests.post(
            endpoint,
            json={"content": repaired},
            headers={"Authorization": f"Basic {cred}", "Content-Type": "application/json"},
            timeout=30,
        )
        r2.raise_for_status()
        log.info(f"[발행 후 검증] 포스트 {wp_id}: HTML 자동 수정 완료")
        return True
    except Exception as e:
        log.warning(f"[발행 후 검증] 포스트 {wp_id}: 수정 업로드 실패: {e}")
        return False


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
    base = f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/tags"

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
        img_resp = requests.get(thumbnail_url, timeout=90)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
        content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()

        # HTML 오류 페이지를 이미지로 잘못 업로드하는 것 방지
        if not content_type.startswith("image/"):
            log.warning(f"썸네일 Content-Type 비정상: {content_type} — 업로드 스킵")
            return None
        if len(img_bytes) < 1024:
            log.warning(f"썸네일 파일 크기 너무 작음 ({len(img_bytes)}B) — 업로드 스킵")
            return None

        filename = _safe_media_filename(title, content_type)
        endpoint = f"{cfg.WORDPRESS_URL}/?rest_route=/wp/v2/media"

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
        log.warning(f"썸네일 WordPress 업로드 실패: {e}")
        return None


def _safe_media_filename(title: str, content_type: str) -> str:
    """HTTP 헤더에 안전한 ASCII 미디어 파일명 생성."""
    stem = title.lower()
    stem = re.sub(r"[^a-z0-9\s_-]", "", stem)
    stem = re.sub(r"[\s_]+", "-", stem).strip("-")
    if not stem:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
        stem = f"thumbnail-{digest}"
    else:
        stem = stem[:48].strip("-") or "thumbnail"

    ext_by_type = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    ext = ext_by_type.get(content_type.lower(), "jpg")
    return f"{stem}.{ext}"


# ── Rank Math SEO ─────────────────────────────────────────────────────────────


def _update_rankmath_seo(
    wp_id: int,
    cred: str,
    focus_keyword: str,
    seo_title: str,
    meta_description: str,
) -> None:
    """Rank Math REST API로 포스트 SEO 메타 업데이트.

    설정되는 필드:
    - rank_math_focus_keyword : Rank Math 포커스 키워드
    - rank_math_title         : Rank Math SEO 제목 (검색 결과 표시용)
    - rank_math_description   : Rank Math 메타 설명 (검색 결과 스니펫)
    """
    endpoint = f"{cfg.WORDPRESS_URL}/?rest_route=/rankmath/v1/updateMeta"
    meta: dict = {}
    if focus_keyword:
        meta["rank_math_focus_keyword"] = focus_keyword
    if seo_title:
        meta["rank_math_title"] = seo_title
    if meta_description:
        meta["rank_math_description"] = meta_description

    payload = json.dumps({
        "objectType": "post",
        "objectID": wp_id,
        "meta": meta,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Basic {cred}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("success"):
                log.info(f"Rank Math SEO 메타 업데이트 완료 (post_id={wp_id}, keyword='{focus_keyword}')")
            else:
                log.warning(f"Rank Math SEO 업데이트 응답 이상: {body}")
    except Exception as e:
        log.warning(f"Rank Math SEO 업데이트 실패 (발행은 완료): {e}")


# ── 유틸 ─────────────────────────────────────────────────────────────────────


def _determine_category(title: str, tags: list[str]) -> int:
    """제목+태그 키워드 기반으로 카테고리 ID 결정."""
    text = (title + " " + " ".join(tags)).lower()

    paper_kw = {"논문", "arxiv", "paper", "survey", "연구", "실험", "벤치마크", "benchmark",
                "제안", "리뷰", "review", "학습", "training", "fine-tuning", "rlhf"}
    tool_kw  = {"라이브러리", "프레임워크", "framework", "library", "sdk", "cli", "플랫폼",
                "platform", "릴리즈", "release", "오픈소스", "open-source", "도구", "tool",
                "extension", "plugin", "패키지", "package", "vscode", "cursor", "copilot"}

    if any(k in text for k in paper_kw):
        return cfg.WORDPRESS_CATEGORY_PAPER_REVIEW
    if any(k in text for k in tool_kw):
        return cfg.WORDPRESS_CATEGORY_DEV_TOOLS
    return cfg.WORDPRESS_CATEGORY_AI_ML


def _series_category_id(series: str | None) -> int | None:
    """시리즈명 → WordPress 카테고리 ID. 0이면 None 반환."""
    mapping = {
        "로컬 LLM 실험실": cfg.WORDPRESS_CATEGORY_LOCAL_LLM,
        "AI 개발도구 워크플로우": cfg.WORDPRESS_CATEGORY_AI_DEVTOOLS,
        "AI 블로그 자동화": cfg.WORDPRESS_CATEGORY_AI_AUTOMATION,
    }
    if not series:
        return None
    cat_id = mapping.get(series, 0)
    return cat_id if cat_id > 0 else None


def _strip_frontmatter(content: str) -> str:
    """YAML frontmatter (--- ... ---) 제거."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("---", 3)
    if end == -1:
        return content
    return stripped[end + 3:].lstrip()
