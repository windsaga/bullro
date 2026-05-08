"""P3 Draft — GLM-5.1 thinking: 초안 작성."""
from __future__ import annotations

import logging
from typing import Optional

from pipeline.llm import glm
from pipeline.models import Critique, Draft, SynthesizedFacts

log = logging.getLogger(__name__)

SYSTEM = """당신은 한국 최고의 AI 기술 블로그 작가입니다.
독자는 3~7년차 한국 백엔드/ML 개발자입니다.
구어체를 피하고 정확하고 통찰 있는 기술 문체를 사용합니다.
"~요/~네요" 남용 금지. 단순 번역 금지. 미검증 수치 사용 금지."""

STRUCTURES = {
    "기술심화": "TL;DR → 배경 → 핵심 메커니즘 → 코드/수식 → 한계 → 결론",
    "실용적용": "TL;DR → 문제 정의 → 설치/설정 → 핵심 예제 코드 → 실무 패턴 → 주의사항 → 결론",
    "한국맥락": "TL;DR → 글로벌 동향 → 국내 유사 사례 비교 → 한국 적용 고려사항 → 전망",
    "비교분석": "TL;DR → 비교 대상 소개 → 기준별 비교표 → 시나리오별 선택 가이드 → 결론",
}

PROMPT_TMPL = """앵글: {angle}
구조: {structure}

필수 포함:
- ## TL;DR 섹션 (3줄 이내, 포스트 맨 위) — 첫 문장에 이 포스트의 핵심 검색 키워드(주제어)를 자연스럽게 포함
- H2(##) 섹션 최소 3개
- 코드 예제 또는 Mermaid 다이어그램 최소 1개
- 한국 개발 환경(카카오/네이버/토스 등) 연결 포인트 최소 1개
- 출처 링크 (원문 URL)

SEO 요구사항:
- TL;DR 첫 문장에 핵심 주제 키워드를 포함하세요 (독자가 검색할 핵심 표현).
- 본문 전체에 핵심 주제 키워드가 자연스럽게 2~5회 분포되도록 작성하세요.

목표 분량: 1,500~2,500자 (한국어 기준){critique_section}

다음 팩트를 바탕으로 포스트를 작성하세요:

{facts}"""


def p3_draft(
    facts: SynthesizedFacts,
    angle: str,
    critique_hint: Optional[Critique] = None,
) -> Draft:
    structure = STRUCTURES.get(angle, STRUCTURES["실용적용"])
    facts_text = _format_facts(facts)

    critique_section = ""
    if critique_hint:
        weaknesses = "\n".join(f"- {w}" for w in critique_hint.weaknesses)
        critique_section = f"\n\n이전 초안의 약점 (반드시 개선):\n{weaknesses}\n개선 지시: {critique_hint.improvement_guide}"

    prompt = PROMPT_TMPL.format(
        angle=angle,
        structure=structure,
        critique_section=critique_section,
        facts=facts_text,
    )

    content = glm(prompt, system=SYSTEM, max_tokens=4096, temperature=0.7)
    log.info(f"P3 초안 생성 완료 ({len(content)}자, 앵글={angle})")

    return Draft(facts=facts, content=content, model="GLM-5.1", version=1)


def _format_facts(facts: SynthesizedFacts) -> str:
    parts = []
    article = facts.topic.article
    parts.append(f"**원문 제목**: {article.title}")
    parts.append(f"**원문 URL**: {article.url}")
    if facts.claims:
        parts.append(f"\n## 핵심 주장\n{facts.claims}")
    if facts.data_points:
        parts.append(f"\n## 핵심 수치/데이터\n{facts.data_points}")
    if facts.mechanism:
        parts.append(f"\n## 기술적 메커니즘\n{facts.mechanism}")
    if facts.limitations:
        parts.append(f"\n## 한계 및 제약\n{facts.limitations}")
    if facts.opinions:
        parts.append(f"\n## [의견] 저자 해석\n{facts.opinions}")
    return "\n".join(parts)
