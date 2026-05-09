"""초안 완결성 검사 + 잘림 복구.

P3/P5 GLM 출력이 max_tokens 한도나 타임아웃으로 잘렸을 때
자동으로 감지하고 GLM으로 이어 쓰게 한다.
"""
from __future__ import annotations

import logging
import re

from pipeline.llm import glm

log = logging.getLogger(__name__)

# 정상 종결을 의미하는 Markdown 키워드
_CONCLUSION_PATTERNS = re.compile(
    r'(결론|마치며|마무리|정리하면|전망|참고\s*(자료|링크)|References|출처)',
    re.IGNORECASE,
)

# 한국어 문장 종결 어미 (문장 맨 끝에서 확인)
_KO_SENTENCE_END = re.compile(
    r'[다요오세시]\s*\.?\s*$',
    re.UNICODE,
)

# 영문/기호 정상 종결
_PUNCT_END = re.compile(r'[.!?。」』]\s*$')

# 코드블록 펜스 패턴
_CODE_FENCE = re.compile(r'^```', re.MULTILINE)


def is_complete(content: str) -> bool:
    """Markdown 초안이 완결됐는지 판단. True = 완결."""
    text = content.strip()

    if len(text) < 600:
        log.warning(f"초안 너무 짧음 ({len(text)}자) — 잘린 것으로 판단")
        return False

    # 코드블록 펜스 홀수 개 → 닫히지 않은 블록 존재
    fences = _CODE_FENCE.findall(text)
    if len(fences) % 2 != 0:
        log.warning("코드블록 미닫힘 — 잘린 것으로 판단")
        return False

    # 결론 키워드가 있으면 완결
    if _CONCLUSION_PATTERNS.search(text):
        return True

    # 마지막 300자에서 문장 종결 어미 확인
    tail = text[-300:]
    if _KO_SENTENCE_END.search(tail) or _PUNCT_END.search(tail):
        return True

    log.warning("종결 어미/키워드 없음 — 잘린 것으로 판단")
    return False


def ensure_complete(content: str, topic_title: str, max_retries: int = 1) -> str:
    """잘린 초안을 GLM으로 이어 쓴 뒤 반환.
    이미 완결된 경우 그대로 반환.
    """
    if is_complete(content):
        return content

    for attempt in range(1, max_retries + 1):
        log.info(f"초안 잘림 감지 — 결론 보완 시도 {attempt}/{max_retries} ('{topic_title[:30]}')")
        tail = content[-1200:]  # 마지막 1200자를 컨텍스트로
        completion = _generate_completion(topic_title, tail)

        if not completion.strip():
            log.warning(f"결론 보완 응답 비어있음 (시도 {attempt})")
            continue

        merged = content.rstrip() + "\n\n" + completion.strip()
        if is_complete(merged):
            log.info(f"초안 결론 보완 완료 (+{len(completion)}자)")
            return merged

        log.warning(f"보완 후에도 완결 미달 (시도 {attempt})")

    log.error(f"초안 결론 보완 실패 — 원본 반환 ('{topic_title[:30]}')")
    return content


def _generate_completion(topic_title: str, context_tail: str) -> str:
    prompt = f"""다음은 한국 AI 기술 블로그 포스트의 마지막 부분입니다.
포스트가 중간에 잘려 결론이 없습니다.

포스트 제목: {topic_title}

포스트 끝 부분:
{context_tail}

---
위 내용을 자연스럽게 이어받아 **결론/마무리 섹션(Markdown)**을 작성하세요.

요구사항:
- ## 결론 또는 ## 마치며 헤딩으로 시작
- 3~5문장으로 핵심 요약 + 실무 적용 포인트
- 마지막에 ## 참고 자료 섹션 (원문 링크 제외하고 간단히)
- 400~700자 분량
- 앞 내용과 자연스럽게 연결"""

    return glm(prompt, max_tokens=2048, temperature=0.6)
