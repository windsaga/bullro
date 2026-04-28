"""NVIDIA API 클라이언트 — DeepSeek V4 Pro + GLM-5.1."""
from __future__ import annotations

import logging
import time

from openai import OpenAI

from pipeline.config import cfg

log = logging.getLogger(__name__)

DEEPSEEK_MODEL = "deepseek-ai/deepseek-v4-pro"
GLM_MODEL = "z-ai/glm-5.1"

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=cfg.NVIDIA_BASE_URL,
            api_key=cfg.NVIDIA_API_KEY,
            timeout=120.0,
            max_retries=0,
        )
    return _client


def _call_with_retry(fn, retries: int = 3, backoff: float = 10.0):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            log.warning(f"NVIDIA API 오류 (시도 {attempt+1}/{retries}): {e} — {wait:.0f}초 대기")
            time.sleep(wait)


def deepseek(
    prompt: str,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """분석·분류·검증용. thinking 비활성화로 속도 우선."""
    client = _get_client()
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
    client = _get_client()
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
