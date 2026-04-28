"""Pollinations.ai 썸네일 이미지 생성 — 무료, 무인증."""
from __future__ import annotations

import logging
import urllib.parse

import requests

log = logging.getLogger(__name__)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"
DEFAULT_PARAMS = "width=1200&height=630&model=flux&nologo=true&seed=42"


def generate_thumbnail(prompt: str, width: int = 1200, height: int = 630) -> str | None:
    """썸네일 이미지 URL 반환. 실패 시 None."""
    if not prompt:
        prompt = "AI technology concept, digital neural network, blue glowing nodes, dark background"

    # Pollinations는 URL 인코딩된 프롬프트를 경로에 직접 삽입
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&model=flux&nologo=true"

    try:
        # HEAD 요청으로 이미지 존재 확인 (실제 다운로드 불필요 — URL 자체가 동적 생성)
        resp = requests.head(url, timeout=30, allow_redirects=True)
        if resp.status_code < 400:
            log.info(f"썸네일 URL 생성 완료")
            return url
        else:
            log.warning(f"Pollinations 응답 {resp.status_code}")
            return url  # URL은 반환 (비동기 생성이라 200이 아닐 수 있음)
    except Exception as e:
        log.warning(f"썸네일 생성 실패: {e}")
        return None
