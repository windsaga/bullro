"""P7 SEO Meta — DeepSeek: 제목/태그/썸네일 프롬프트 생성."""
from __future__ import annotations

import json
import logging

from pipeline.llm import deepseek
from pipeline.models import Draft, SEOMeta

log = logging.getLogger(__name__)

SYSTEM = """당신은 한국 검색 SEO 전문가입니다.
WordPress + 구글 검색 기준으로 메타 정보를 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 포스트 내용을 분석하여 SEO 메타 정보를 생성하세요.

포스트 내용 (요약):
{draft_summary}

## 포커스 키워드 규칙 (Yoast SEO 기준)
1. focus_keyword: 독자가 검색할 핵심 키워드 (한국어, 2~4단어)
2. focus_keyword_slug: 포커스 키워드의 영어 URL 슬러그 (소문자, 하이픈 구분, 3~6단어)
3. title_candidates: 모든 제목 후보에 focus_keyword를 반드시 포함
4. meta_description: focus_keyword를 앞부분에 포함, 150자 이내, 클릭 유도

출력 (JSON):
{{
  "focus_keyword": "핵심 검색 키워드 (2~4단어, 한국어)",
  "focus_keyword_slug": "focus-keyword-in-english-slug",
  "title_candidates": [
    "제목1 — focus_keyword 포함 필수, 30자 이내",
    "제목2",
    "제목3",
    "제목4",
    "제목5"
  ],
  "meta_description": "focus_keyword로 시작하거나 앞부분 포함, 150자 이내, 클릭 유도",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "internal_link_slots": ["연관 주제 키워드1", "연관 주제 키워드2"],
  "thumbnail_prompt": "FLUX 이미지 생성용 영어 프롬프트. 기술적 시각 요소 포함. 예: 'A futuristic AI chip glowing blue, circuit board background, tech aesthetic, 16:9'"
}}"""


def p7_seo(draft: Draft) -> SEOMeta:
    draft_summary = draft.content[:3000]
    prompt = PROMPT_TMPL.format(draft_summary=draft_summary)

    raw = deepseek(prompt, system=SYSTEM, max_tokens=1024, temperature=0.3)

    try:
        data = _parse_json(raw)
        focus_keyword = data.get("focus_keyword", "")
        focus_keyword_slug = data.get("focus_keyword_slug", "")
        titles = data.get("title_candidates", [])
        meta_desc = data.get("meta_description", "")

        # 포커스 키워드가 제목에 없으면 첫 번째 제목에 append
        if focus_keyword and titles and focus_keyword not in titles[0]:
            log.debug(f"P7: 포커스 키워드 '{focus_keyword}'가 첫 제목에 없음 — append")
            titles[0] = f"{titles[0]} | {focus_keyword}"

        # 포커스 키워드가 메타 설명에 없으면 앞에 삽입
        if focus_keyword and meta_desc and focus_keyword not in meta_desc:
            log.debug(f"P7: 포커스 키워드 '{focus_keyword}'를 메타 설명 앞에 삽입")
            meta_desc = f"{focus_keyword} — {meta_desc}"

        result = SEOMeta(
            title_candidates=titles,
            meta_description=meta_desc,
            tags=data.get("tags", []),
            thumbnail_prompt=data.get("thumbnail_prompt", ""),
            internal_link_slots=data.get("internal_link_slots", []),
            focus_keyword=focus_keyword,
            focus_keyword_slug=focus_keyword_slug,
        )
    except Exception as e:
        log.error(f"P7 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        first_line = draft.content.split("\n")[0].lstrip("# ").strip()
        result = SEOMeta(
            title_candidates=[first_line] if first_line else ["제목 없음"],
            meta_description="",
            tags=[],
            thumbnail_prompt="AI technology concept, digital neural network, blue glowing nodes, dark background",
            internal_link_slots=[],
        )

    log.info(
        f"P7 SEO: 포커스키워드='{result.focus_keyword}', "
        f"슬러그='{result.focus_keyword_slug}', "
        f"제목 {len(result.title_candidates)}개, 태그 {len(result.tags)}개"
    )
    return result


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
