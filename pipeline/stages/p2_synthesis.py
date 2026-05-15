"""P2 Source Synthesis — DeepSeek: 팩트 구조화."""
from __future__ import annotations

import logging

from pipeline.llm import deepseek
from pipeline.models import SelectedTopic, SynthesizedFacts

log = logging.getLogger(__name__)

SYSTEM = """기술 블로그 포스트 작성을 위한 자료 조사 전문가입니다.
원문을 읽고 오직 검증 가능한 사실, 수치, 주장만 추출하세요.
의견이나 해석은 반드시 "[의견]" 태그로 명확히 구분합니다.
각 주장에는 원문 인용구를 포함하세요."""

PROMPT_TMPL = """다음 원문을 분석하여 구조화된 팩트를 추출하세요.

제목: {title}
URL: {url}
원문 내용:
{content}

반드시 아래 마크다운 섹션 형식으로 출력하세요:
## 핵심 주장
## 핵심 수치/데이터
## 기술적 메커니즘
## 한계 및 제약
## [의견] 저자의 주관적 해석"""

SECTION_KEYS = {
    "## 핵심 주장": "claims",
    "## 핵심 수치/데이터": "data_points",
    "## 기술적 메커니즘": "mechanism",
    "## 한계 및 제약": "limitations",
    "## [의견] 저자의 주관적 해석": "opinions",
}


def p2_synthesis(topic: SelectedTopic) -> SynthesizedFacts:
    article = topic.article

    # 웹검색 자료가 있으면 우선 사용, 없으면 수집 시 가져온 내용으로 폴백
    if topic.web_research:
        content_truncated = topic.web_research
        log.info("P2: 웹검색 자료 사용 (%d자)", len(content_truncated))
    else:
        content_truncated = article.content[:6000]
        log.info("P2: 수집 원문 사용 (%d자)", len(content_truncated))

    prompt = PROMPT_TMPL.format(
        title=article.title,
        url=article.url,
        content=content_truncated,
    )

    raw = deepseek(prompt, system=SYSTEM, max_tokens=2048, temperature=0.2)
    log.debug(f"P2 원문 응답 {len(raw)}자")

    sections = _parse_sections(raw)
    return SynthesizedFacts(
        topic=topic,
        claims=sections.get("claims", ""),
        data_points=sections.get("data_points", ""),
        mechanism=sections.get("mechanism", ""),
        limitations=sections.get("limitations", ""),
        opinions=sections.get("opinions", ""),
        raw=article.content,
    )


def _parse_sections(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_key: str | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        matched = None
        for heading, key in SECTION_KEYS.items():
            if line.strip().startswith(heading.strip()):
                matched = key
                break

        if matched:
            if current_key and buffer:
                result[current_key] = "\n".join(buffer).strip()
            current_key = matched
            buffer = []
        elif current_key:
            buffer.append(line)

    if current_key and buffer:
        result[current_key] = "\n".join(buffer).strip()

    return result
