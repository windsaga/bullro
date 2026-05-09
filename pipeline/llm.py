"""NVIDIA API 클라이언트 — DeepSeek V4 Pro + GLM-5.1.
NVIDIA 일일 한도(429) 소진 시 Claude API(Anthropic)로 자동 폴백.
"""
from __future__ import annotations

import logging
import threading
import time

import anthropic
from openai import OpenAI

from pipeline.config import cfg

# NVIDIA 무료 티어: 5 RPM 제한 → 호출 간 최소 13초 간격 자체 적용
_last_call_time: float = 0.0
_rate_lock = threading.Lock()
MIN_CALL_INTERVAL = 13.0  # seconds (60s / 5RPM + 1s 버퍼)

log = logging.getLogger(__name__)

DEEPSEEK_MODEL = "deepseek-ai/deepseek-v4-pro"
GLM_MODEL = "z-ai/glm-5.1"

# Claude 폴백 모델
CLAUDE_HAIKU = "claude-haiku-4-5"    # DeepSeek 대체: 분석/검증 (빠름, 저렴)
CLAUDE_SONNET = "claude-sonnet-4-6"  # GLM 대체: 작문/창작 (품질)

# DeepSeek: 분석/검증용 — 빠름, 120초면 충분
# GLM-5.1:  thinking 활성화 — 응답에 300~600초 소요
_client_fast: OpenAI | None = None   # DeepSeek용
_client_slow: OpenAI | None = None   # GLM-5.1용
_anthropic_client: anthropic.Anthropic | None = None
_nvidia_exhausted: bool = False      # True가 되면 이후 모든 호출을 Claude로 직행


class RateLimitError(Exception):
    """NVIDIA API 일일 한도 소진 — 모든 재시도 후에도 429가 지속될 때."""


def _rate_limit_wait() -> None:
    """API 호출 전 최소 간격 보장 — 429 사전 방지."""
    global _last_call_time
    with _rate_lock:
        elapsed = time.time() - _last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            wait = MIN_CALL_INTERVAL - elapsed
            log.debug(f"자체 rate limit: {wait:.1f}초 대기")
            time.sleep(wait)
        _last_call_time = time.time()


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


def _get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        if not cfg.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "NVIDIA API 한도 소진 + ANTHROPIC_API_KEY 미설정 — 폴백 불가"
            )
        _anthropic_client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    return _anthropic_client


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e)
    return "429" in msg or "Too Many Requests" in msg or "rate limit" in msg.lower()


def _is_server_error(e: Exception) -> bool:
    """504/502/503 같은 서버측 일시 오류."""
    msg = str(e)
    return any(code in msg for code in ("504", "502", "503", "Gateway", "upstream"))


def _call_with_retry(fn, retries: int = 5, backoff: float = 10.0, rate_limit_retries: int = 2):
    """NVIDIA API 호출 + 재시도.
    - 일반 오류: retries회까지 재시도
    - 429: rate_limit_retries회만 재시도 후 즉시 RateLimitError (Claude 폴백)
    - 504/502/503 지속: retries 소진 시 RateLimitError로 변환 → Claude 폴백
    """
    rl_count = 0  # 429 연속 횟수
    server_err_count = 0  # 5xx 연속 횟수
    for attempt in range(retries):
        _rate_limit_wait()
        try:
            return fn()
        except Exception as e:
            if _is_rate_limit(e):
                rl_count += 1
                if rl_count >= rate_limit_retries:
                    raise RateLimitError(
                        f"NVIDIA API 429 × {rl_count}회 — 일일 한도 소진으로 판단, Claude 폴백"
                    ) from e
                wait = 60.0
                log.warning(
                    f"NVIDIA API Rate Limit (429 횟수 {rl_count}/{rate_limit_retries}) — {wait:.0f}초 대기 후 재시도"
                )
                time.sleep(wait)
            elif _is_server_error(e):
                server_err_count += 1
                if attempt == retries - 1:
                    raise RateLimitError(
                        f"NVIDIA API 서버 오류 {server_err_count}회 연속 — Claude 폴백"
                    ) from e
                wait = backoff * (2 ** attempt)
                log.warning(
                    f"NVIDIA API 서버 오류 (시도 {attempt+1}/{retries}): {e} — {wait:.0f}초 대기"
                )
                time.sleep(wait)
            else:
                rl_count = 0
                server_err_count = 0
                if attempt == retries - 1:
                    raise
                wait = backoff * (2 ** attempt)
                log.warning(
                    f"NVIDIA API 오류 (시도 {attempt+1}/{retries}): {e} — {wait:.0f}초 대기"
                )
                time.sleep(wait)


def _claude_fallback(
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    model: str,
) -> str:
    """Claude API 폴백 호출 (Anthropic SDK, 스트리밍)."""
    client = _get_anthropic_client()
    messages: list[dict] = [{"role": "user", "content": prompt}]

    log.info(f"Claude 폴백 호출: model={model}, max_tokens={max_tokens}")

    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        messages=messages,
        temperature=temperature,
    ) as stream:
        result = stream.get_final_message()

    text = ""
    for block in result.content:
        if hasattr(block, "text"):
            text += block.text

    log.info(f"Claude 폴백 응답 {len(text)}자 (model={model})")
    return text


def deepseek(
    prompt: str,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """분석·분류·검증용. thinking 비활성화로 속도 우선.
    NVIDIA 429 소진 시 claude-haiku-4-5로 자동 폴백. 이후 호출은 바로 Claude 직행.
    """
    global _nvidia_exhausted

    if _nvidia_exhausted:
        log.debug("NVIDIA 소진 상태 — Claude Haiku 직행")
        return _claude_fallback(prompt, system, max_tokens, temperature, CLAUDE_HAIKU)

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
    try:
        result = _call_with_retry(_call)
    except RateLimitError:
        _nvidia_exhausted = True
        log.warning("NVIDIA DeepSeek 한도 소진 → Claude Haiku 폴백 (이후 모든 호출 Claude 직행)")
        result = _claude_fallback(prompt, system, max_tokens, temperature, CLAUDE_HAIKU)
    log.debug(f"응답 {len(result)}자")
    return result


def glm(
    prompt: str,
    system: str = "",
    max_tokens: int = 8192,
    temperature: float = 0.7,
) -> str:
    """창작·작문용. thinking 활성화 + 스트리밍.
    NVIDIA 429 소진 시 claude-sonnet-4-6으로 자동 폴백. 이후 호출은 바로 Claude 직행.
    """
    global _nvidia_exhausted

    if _nvidia_exhausted:
        log.debug("NVIDIA 소진 상태 — Claude Sonnet 직행")
        return _claude_fallback(prompt, system, max_tokens, temperature, CLAUDE_SONNET)

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
    try:
        result = _call_with_retry(_call)
    except RateLimitError:
        _nvidia_exhausted = True
        log.warning("NVIDIA GLM-5.1 한도 소진 → Claude Sonnet 폴백 (이후 모든 호출 Claude 직행)")
        result = _claude_fallback(prompt, system, max_tokens, temperature, CLAUDE_SONNET)
    log.debug(f"응답 {len(result)}자")
    return result
