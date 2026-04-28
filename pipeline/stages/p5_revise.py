"""P5 Revision — GLM-5.1 thinking: critique 반영 수정본."""
from __future__ import annotations

import logging

from pipeline.llm import glm
from pipeline.models import Critique, Draft

log = logging.getLogger(__name__)

SYSTEM = """당신은 편집장의 비평을 반영해 글을 개선하는 작가입니다.
비평에서 지적된 약점을 모두 수정하되, 글의 전체 구조를 유지합니다.
수정하지 않은 부분은 그대로 유지하세요.
포스트 맨 아래에 <!-- CHANGES: 수정 내용 요약 --> 주석을 추가하세요."""

PROMPT_TMPL = """원본 포스트:
{draft}

---
편집장 채점 결과:
- 총점: {total}/100
- 약점:
{weaknesses}

- 수정 지시: {improvement_guide}

위 약점들을 모두 반영한 개선본을 작성하세요.
반드시 약점에서 지적된 부분을 구체적으로 수정하세요."""


def p5_revise(draft_v1: Draft, critique: Critique) -> Draft:
    weaknesses_text = "\n".join(f"  {i+1}. {w}" for i, w in enumerate(critique.weaknesses))

    prompt = PROMPT_TMPL.format(
        draft=draft_v1.content[:4000],
        total=critique.total,
        weaknesses=weaknesses_text,
        improvement_guide=critique.improvement_guide,
    )

    content = glm(prompt, system=SYSTEM, max_tokens=4096, temperature=0.5)
    log.info(f"P5 수정본 생성 완료 ({len(content)}자)")

    return Draft(
        facts=draft_v1.facts,
        content=content,
        model="GLM-5.1",
        version=2,
    )
