"""P1 Triage — DeepSeek: top-k 토픽 선별."""
from __future__ import annotations

import json
import logging

from pipeline.llm import deepseek
from pipeline.models import ScoredArticle, SelectedTopic

log = logging.getLogger(__name__)

SYSTEM = """당신은 한국 AI 개발자 커뮤니티를 위한 기술 블로그 에디터입니다.
후보 기사/논문 목록을 보고 한국 개발자에게 가장 가치 있는 상위 항목을 선별하세요.

평가 기준 (각 0~10점):
- relevance: AI/ML 개발자 실무와의 직접 연관성
- impact: 기술 변화를 일으킬 파급력
- novelty: 기존에 다뤄지지 않은 새로운 내용
- kr_context: 한국 개발 환경(카카오/네이버/토스 등)에 적용 가능성

반드시 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

ANGLES = ("기술심화", "실용적용", "한국맥락", "비교분석")


def p1_triage(articles: list[ScoredArticle], top_k: int = 2) -> list[SelectedTopic]:
    candidates = [
        {
            "id": str(i),
            "title": a.title,
            "url": a.url,
            "source": a.source,
            "published_at": a.published_at,
            "signals": a.signals,
            "composite_score": round(a.composite_score, 3),
            "summary": a.content[:500],
        }
        for i, a in enumerate(articles)
    ]

    prompt = f"""다음 후보 목록을 평가하여 상위 {top_k}개를 선택하세요.

후보 목록:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

응답 형식 (JSON 배열):
[
  {{
    "id": "후보 id",
    "scores": {{"relevance": 0~10, "impact": 0~10, "novelty": 0~10, "kr_context": 0~10}},
    "total": 0~40,
    "angle": "{'" | "'.join(ANGLES)}",
    "reason": "선택 이유 한 줄"
  }}
]"""

    raw = deepseek(prompt, system=SYSTEM, max_tokens=1024, temperature=0.2)

    try:
        selected = _parse_json_array(raw)
    except Exception as e:
        log.error(f"P1 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        # fallback: composite_score 기준 상위 top_k
        selected = [
            {"id": str(i), "scores": {}, "total": 0, "angle": "실용적용", "reason": "fallback"}
            for i in range(min(top_k, len(articles)))
        ]

    results: list[SelectedTopic] = []
    id_map = {str(i): a for i, a in enumerate(articles)}
    for item in selected[:top_k]:
        article = id_map.get(str(item["id"]))
        if not article:
            continue
        angle = item.get("angle", "실용적용")
        if angle not in ANGLES:
            angle = "실용적용"
        results.append(
            SelectedTopic(
                article=article,
                angle=angle,
                p1_scores=item.get("scores", {}),
                reason=item.get("reason", ""),
            )
        )
        log.info(f"P1 선택: [{angle}] {article.title[:50]} (total={item.get('total', '?')})")

    return results


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    # JSON 블록 추출 (```json ... ``` 형식 대응)
    if "```" in text:
        start = text.find("[", text.find("```"))
        end = text.rfind("]") + 1
        text = text[start:end]
    elif not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]") + 1
        text = text[start:end]
    return json.loads(text)
