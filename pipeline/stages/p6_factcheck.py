"""P6 Fact-Check — DeepSeek: 주장 vs 원문 entailment."""
from __future__ import annotations

import json
import logging

from pipeline.llm import deepseek
from pipeline.models import Draft, FactCheckResult, SynthesizedFacts

log = logging.getLogger(__name__)

SYSTEM = """당신은 팩트체커입니다.
포스트의 각 주장을 원문과 대조하여 검증합니다.
원문에서 exact quote를 제시할 수 없으면 반드시 "unsupported"로 표시합니다.
false-positive를 줄이기 위해 반드시 원문 exact quote를 제시해야 합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요.

검증 카테고리:
- supported: 원문에서 exact quote로 확인됨
- inferred: 원문에서 합리적으로 추론 가능 (quote 불가)
- unsupported: 원문에 근거 없음 → 포스트에서 삭제 또는 [추정] 태그 필요"""

PROMPT_TMPL = """원문 팩트:
{facts}

---
검증할 포스트 (주요 주장 추출):
{draft}

---
포스트에서 사실적 주장(수치, 기술 특성, 성능 비교 등)을 추출하고 검증하세요.
최대 10개의 핵심 주장을 선택하세요.

응답 형식 (JSON):
{{
  "claims": [
    {{
      "text": "포스트의 주장 (원문 인용)",
      "status": "supported|inferred|unsupported",
      "quote": "원문 근거 인용 or null"
    }}
  ]
}}"""


def p6_factcheck(draft: Draft, facts: SynthesizedFacts) -> FactCheckResult:
    facts_summary = f"""{facts.claims}

{facts.data_points}

원문: {facts.raw[:2000]}"""

    prompt = PROMPT_TMPL.format(
        facts=facts_summary[:3000],
        draft=draft.content[:3000],
    )

    raw = deepseek(prompt, system=SYSTEM, max_tokens=2048, temperature=0.1)

    try:
        data = _parse_json(raw)
        claims = data.get("claims", [])
    except Exception as e:
        log.error(f"P6 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        claims = []

    unsupported = sum(1 for c in claims if c.get("status") == "unsupported")
    log.info(f"P6 팩트체크: {len(claims)}개 주장, unsupported={unsupported}건")

    return FactCheckResult(
        draft=draft,
        claims=claims,
        unsupported_count=unsupported,
    )


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
