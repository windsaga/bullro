"""NVIDIA API 클라이언트 — DeepSeek V4 Pro + GLM-5.1."""
from __future__ import annotations

import logging
import time

from openai import OpenAI

from pipeline.config import cfg

log = logging.getLogger(__name__)

DEEPSEEK_MODEL = "deepseek-ai/deepseek-v4-pro"
GLM_MODEL = "z-ai/glm-5.1"

# DeepSeek: 분석/검증용 — 빠름, 120초면 충분
# GLM-5.1:  thinking 활성화 — 응답에 300~600초 소요
_client_fast: OpenAI | None = None   # DeepSeek용
_client_slow: OpenAI | None = None   # GLM-5.1용


def _get_client(slow: bool = False) -> OpenAI:
    global _client_fast, _client_slow
    if slow:
        if _client_slow is None:
            _client_slow = OpenAI(
                base_url=cfg.NVIDIA_BASE_URL,
                api_key=cfg.NVIDIA_API_KEY,
                timeout=600.0,   # GLM thinking: 최대 10분
                max_retries=0,
            )
        return _client_slow
    else:
        if _client_fast is None:
            _client_fast = OpenAI(
                base_url=cfg.NVIDIA_BASE_URL,
                api_key=cfg.NVIDIA_API_KEY,
                timeout=120.0,   # DeepSeek: 2분이면 충분
                max_retries=0,
            )
        return _client_fast


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e)
    return "429" in msg or "Too Many Requests" in msg or "rate limit" in msg.lower()


def _call_with_retry(fn, retries: int = 5, backoff: float = 10.0):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt == retries - 1:
                raise
            # 429 Rate Limit: 60s 베이스로 지수 백오프, 최대 120s
            # 그 외 오류(timeout 등): 10s 베이스
            if _is_rate_limit(e):
                wait = min(60.0 * (2 ** attempt), 120.0)
                log.warning(
                    f"NVIDIA API Rate Limit (시도 {attempt+1}/{retries}) — {wait:.0f}초 대기 후 재시도"
                )
            else:
                wait = backoff * (2 ** attempt)
                log.warning(
                    f"NVIDIA API 오류 (시도 {attempt+1}/{retries}): {e} — {wait:.0f}초 대기"
                )
            time.sleep(wait)


def deepseek(
    prompt: str,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """분석·분류·검증용. thinking 비활성화로 속도 우선."""
    client = _get_client(slow=False)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call():
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"thinking": False}},
        )
        return resp.choices[0].message.content or ""

    log.debug(f"DeepSeek 호출 (max_tokens={max_tokens})")
    result = _call_with_retry(_call)
    log.debug(f"DeepSeek 응답 {len(result)}자")
    return result


def glm(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """창작·작문용. thinking 활성화 + 스트리밍."""
    client = _get_client(slow=True)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call():
        completion = client.chat.completions.create(
            model=GLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}},
            stream=True,
        )
        parts: list[str] = []
        for chunk in completion:
            if not getattr(chunk, "choices", None):
                continue
            content = getattr(chunk.choices[0].delta, "content", None)
            if content:
                parts.append(content)
        return "".join(parts)

    log.debug(f"GLM-5.1 호출 (max_tokens={max_tokens}, thinking=True)")
    result = _call_with_retry(_call)
    log.debug(f"GLM-5.1 응답 {len(result)}자")
    return result
