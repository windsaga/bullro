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
        # GET으로 실제 이미지 생성 트리거 (FLUX 생성에 최대 90초 소요)
        resp = requests.get(url, timeout=90, allow_redirects=True)
        if resp.status_code == 200 and resp.content:
            log.info(f"썸네일 이미지 생성 완료 ({len(resp.content):,} bytes)")
            return url
        else:
            log.warning(f"Pollinations 응답 {resp.status_code}")
            return url
    except Exception as e:
        log.warning(f"썸네일 생성 실패: {e}")
        return None
