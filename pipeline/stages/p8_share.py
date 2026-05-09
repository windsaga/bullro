"""P8 Share — 발행 후 커뮤니티 배포용 공유 콘텐츠 생성 + 자동 투고."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from pipeline.config import cfg
from pipeline.llm import deepseek
from pipeline.models import Post

log = logging.getLogger(__name__)

SYSTEM = """당신은 기술 블로그 콘텐츠 마케터입니다.
블로그 포스트를 읽고 커뮤니티 채널별 공유문을 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 블로그 포스트에 대한 X(Twitter) 공유문을 생성하세요.

포스트 제목: {title}
포스트 링크: {link}

포스트 내용 (요약):
{content}

X(Twitter) 공유문을 JSON으로 반환하세요:

{{
  "twitter": "한국어 트윗 (260자 이내). 첫 줄: 핵심 결과+수치. ▸ 포인트 3개. 빈 줄 후 링크 {link}"
}}"""


def generate_share_content(post: Post) -> list[str]:
    """공유 콘텐츠 생성, 저장, (옵션) Reddit/Twitter 자동 투고.

    Returns:
        저장된 파일 경로 목록
    """
    if not post.wp_link:
        log.warning("P8: wp_link 없음 — 공유 콘텐츠 생략")
        return []

    content_summary = post.draft.content[:2000]
    prompt = PROMPT_TMPL.format(
        title=post.chosen_title,
        link=post.wp_link,
        content=content_summary,
    )

    raw = deepseek(prompt, system=SYSTEM, max_tokens=1200, temperature=0.5)

    try:
        data = _parse_json(raw)
    except Exception as e:
        log.error(f"P8 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        return []

    twitter_text = data.get("twitter", "")
    saved_files = _save_share_file(post, twitter_text)
    log.info(f"P8: X 공유문 저장 → posts/share/")

    if cfg.AUTO_POST_TWITTER and _twitter_ready():
        _post_to_twitter(twitter_text)

    _notify_slack_share(post, twitter_text)

    return saved_files


def _save_share_file(post: Post, twitter_text: str) -> list[str]:
    if not twitter_text:
        return []

    share_dir = cfg.SHARE_DIR
    share_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().strftime("%Y-%m-%d")
    slug = post.slug[:40]
    path = share_dir / f"{today}-{slug}.x.md"
    path.write_text(f"# X / Twitter\n\n{twitter_text}\n", encoding="utf-8")
    return [str(path)]


def _twitter_ready() -> bool:
    return bool(
        cfg.TWITTER_API_KEY and cfg.TWITTER_API_SECRET
        and cfg.TWITTER_ACCESS_TOKEN and cfg.TWITTER_ACCESS_SECRET
    )


def _post_to_twitter(tweet_text: str) -> None:
    if not tweet_text:
        log.warning("P8 Twitter: 본문 없음 — 스킵")
        return
    text = tweet_text[:280]
    try:
        import tweepy  # type: ignore
        client = tweepy.Client(
            consumer_key=cfg.TWITTER_API_KEY,
            consumer_secret=cfg.TWITTER_API_SECRET,
            access_token=cfg.TWITTER_ACCESS_TOKEN,
            access_token_secret=cfg.TWITTER_ACCESS_SECRET,
        )
        response = client.create_tweet(text=text)
        log.info(f"P8 Twitter 투고 완료: tweet_id={response.data['id']}")
    except ImportError:
        log.error("P8 Twitter: tweepy 패키지 미설치 — pip install tweepy")
    except Exception as e:
        log.error(f"P8 Twitter 투고 실패: {e}")


def _notify_slack_share(post: Post, twitter_text: str) -> None:
    from pipeline.notifier import notify_slack
    notify_slack(event="share_ready", post=post, twitter_text=twitter_text)


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
