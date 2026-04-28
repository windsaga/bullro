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

출력 (JSON):
{{
  "title_candidates": [
    "제목1 (30자 이내, 핵심 키워드 포함)",
    "제목2",
    "제목3",
    "제목4",
    "제목5"
  ],
  "meta_description": "150자 이내, 핵심 키워드 포함, 클릭 유도",
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
        result = SEOMeta(
            title_candidates=data.get("title_candidates", []),
            meta_description=data.get("meta_description", ""),
            tags=data.get("tags", []),
            thumbnail_prompt=data.get("thumbnail_prompt", ""),
            internal_link_slots=data.get("internal_link_slots", []),
        )
    except Exception as e:
        log.error(f"P7 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        # fallback: 제목만 추출
        first_line = draft.content.split("\n")[0].lstrip("# ").strip()
        result = SEOMeta(
            title_candidates=[first_line] if first_line else ["제목 없음"],
            meta_description="",
            tags=[],
            thumbnail_prompt="AI technology concept, digital neural network, blue glowing nodes, dark background",
            internal_link_slots=[],
        )

    log.info(f"P7 SEO: 제목 {len(result.title_candidates)}개, 태그 {len(result.tags)}개")
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
