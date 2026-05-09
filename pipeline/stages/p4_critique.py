"""P4 Self-Critique — DeepSeek: 루브릭 채점."""
from __future__ import annotations

import json
import logging

from pipeline.llm import deepseek
from pipeline.models import Critique, Draft, SynthesizedFacts

log = logging.getLogger(__name__)

SYSTEM = """당신은 까다로운 기술 블로그 편집장입니다.
아래 글을 루브릭 기준으로 채점하고 반드시 약점 3개를 찾아내세요.
"전반적으로 좋습니다" 같은 총평은 절대 쓰지 마세요.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요.

루브릭:
- 사실정확성 (30): 원문과 다른 주장, 수치 오류 → 오류 없음=30 / 1건=20 / 2건=10 / 3+건=0
- 새로운관점 (20): 단순 번역 수준인가, 한국 맥락이 있는가
- 기술깊이 (15): 메커니즘 설명, 코드/수식 포함 여부
- 가독성 (15): 단락 길이, 헤딩 구조, 코드블록 포맷
- 독창성 (10): 기존 유사 글과 차별점
- SEO구조 (10): TL;DR, H2 3개+, 메타 설명 구조

기술 품질 체크 (weaknesses/improvement_guide에 반영):
독자가 바로 실행할 수 있는 요소(설치 명령어, 설정 파일, Docker Compose, 벤치마크 수치,
오류 해결 방법, 비용 비교, 하드웨어별 권장 설정)가 하나도 없으면 weakness로 지적하세요."""

PROMPT_TMPL = """원문 팩트:
{facts}

---
작성된 포스트:
{draft}

---
위 포스트를 채점하세요.
Find at least 3 weaknesses. Do not say 'overall good'.

응답 형식 (JSON):
{{
  "scores": {{
    "사실정확성": 0~30,
    "새로운관점": 0~20,
    "기술깊이": 0~15,
    "가독성": 0~15,
    "독창성": 0~10,
    "SEO구조": 0~10
  }},
  "total": 0~100,
  "weaknesses": ["약점1 (구체적 인용 포함)", "약점2", "약점3"],
  "improvement_guide": "P5에서 반영할 구체적 수정 지시 (200자 이내)"
}}"""


def p4_critique(draft: Draft, facts: SynthesizedFacts) -> Critique:
    facts_summary = _summarize_facts(facts)
    prompt = PROMPT_TMPL.format(
        facts=facts_summary,
        draft=draft.content[:8000],
    )

    raw = deepseek(prompt, system=SYSTEM, max_tokens=1024, temperature=0.1)

    try:
        data = _parse_json(raw)
        scores = data.get("scores", {})
        total = data.get("total", sum(scores.values()))
        weaknesses = data.get("weaknesses", [])
        improvement_guide = data.get("improvement_guide", "")
    except Exception as e:
        log.error(f"P4 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        scores = {}
        total = 50
        weaknesses = ["파싱 실패 — 수동 검토 필요"]
        improvement_guide = ""

    # 약점이 3개 미만이면 경고
    if len(weaknesses) < 3:
        log.warning(f"P4 약점 {len(weaknesses)}개만 반환 (3개 기대)")
        while len(weaknesses) < 3:
            weaknesses.append("추가 약점 미제시")

    log.info(f"P4 채점: {total}/100 (약점 {len(weaknesses)}개)")
    return Critique(
        draft=draft,
        scores=scores,
        total=total,
        weaknesses=weaknesses[:5],
        improvement_guide=improvement_guide,
    )


def _summarize_facts(facts: SynthesizedFacts) -> str:
    return f"""{facts.claims[:800]}

{facts.data_points[:400]}

원문: {facts.topic.article.url}"""


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
