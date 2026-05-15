"""P8 Share — 발행 후 X(Twitter) 5편 스레드 생성 + Slack 전송."""
from __future__ import annotations

import json
import logging
from datetime import date

from pipeline.config import cfg
from pipeline.llm import deepseek
from pipeline.models import Post

log = logging.getLogger(__name__)

SYSTEM = """당신은 기술 블로그 콘텐츠 마케터입니다.
블로그 포스트를 읽고 X(Twitter) 스레드를 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

PROMPT_TMPL = """다음 블로그 포스트를 X(Twitter) 5편 스레드로 변환하세요.

포스트 제목: {title}
포스트 링크: {link}
포스트 요약:
{content}

작성 규칙:
- 총 5편, 각 280자 이내
- 1편: 핵심 키워드 포함 + 문제 제기 (URL 금지)
- 2편: 왜 이 주제가 중요한지 (URL 금지)
- 3편: 실무 예시 또는 명령어 (URL 금지)
- 4편: 운영 팁 / 비용 최적화 / 주의사항 (URL 금지)
- 5편: 핵심 요약 한 줄 + URL ({link})
- 개발자 타겟, 실무 회고 스타일 (광고 문구 금지)
- 문장은 짧게, 불필요한 해시태그 금지, 이모지 최대 1개

JSON 형식:
{{
  "threads": [
    "1편 내용",
    "2편 내용",
    "3편 내용",
    "4편 내용",
    "5편 내용 + {link}"
  ]
}}"""


def generate_share_content(post: Post) -> list[str]:
    """X 스레드 5편 생성, 저장, Slack 전송, (옵션) Twitter 자동 투고."""
    if not post.wp_link:
        log.warning("P8: wp_link 없음 — 공유 콘텐츠 생략")
        return []

    prompt = PROMPT_TMPL.format(
        title=post.chosen_title,
        link=post.wp_link,
        content=post.draft.content[:2000],
    )

    raw = deepseek(prompt, system=SYSTEM, max_tokens=2000, temperature=0.5)

    try:
        data = _parse_json(raw)
        threads: list[str] = data.get("threads", [])
        if not threads:
            raise ValueError("threads 배열 없음")
    except Exception as e:
        log.error(f"P8 JSON 파싱 실패: {e}\n응답: {raw[:300]}")
        return []

    # 280자 초과 편은 잘라냄
    threads = [t[:280] for t in threads[:5]]

    saved = _save_share_file(post, threads)
    log.info("P8: X 스레드 %d편 저장 → posts/share/", len(threads))

    if cfg.AUTO_POST_TWITTER and _twitter_ready():
        _post_thread_to_twitter(threads)

    _notify_slack_share(post, threads)

    return saved


# ── 파일 저장 ──────────────────────────────────────────────────────────────


def _save_share_file(post: Post, threads: list[str]) -> list[str]:
    if not threads:
        return []

    share_dir = cfg.SHARE_DIR
    share_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().strftime("%Y-%m-%d")
    slug = post.slug[:40]
    path = share_dir / f"{today}-{slug}.x.md"

    lines = ["# X / Twitter 스레드\n"]
    for i, t in enumerate(threads, 1):
        lines.append(f"## {i}편\n\n{t}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return [str(path)]


# ── Twitter 자동 투고 ──────────────────────────────────────────────────────


def _twitter_ready() -> bool:
    return bool(
        cfg.TWITTER_API_KEY and cfg.TWITTER_API_SECRET
        and cfg.TWITTER_ACCESS_TOKEN and cfg.TWITTER_ACCESS_SECRET
    )


def _post_thread_to_twitter(threads: list[str]) -> None:
    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=cfg.TWITTER_API_KEY,
            consumer_secret=cfg.TWITTER_API_SECRET,
            access_token=cfg.TWITTER_ACCESS_TOKEN,
            access_token_secret=cfg.TWITTER_ACCESS_SECRET,
        )
        prev_id = None
        for i, text in enumerate(threads, 1):
            kwargs = {"text": text}
            if prev_id:
                kwargs["in_reply_to_tweet_id"] = prev_id
            resp = client.create_tweet(**kwargs)
            prev_id = resp.data["id"]
            log.info("P8 Twitter %d편 투고: tweet_id=%s", i, prev_id)
    except ImportError:
        log.error("P8 Twitter: tweepy 미설치 — pip install tweepy")
    except Exception as e:
        log.error("P8 Twitter 투고 실패: %s", e)


# ── Slack 알림 ────────────────────────────────────────────────────────────


def _notify_slack_share(post: Post, threads: list[str]) -> None:
    from pipeline.notifier import notify_slack
    notify_slack(event="share_ready", post=post, threads=threads)


# ── JSON 파싱 ─────────────────────────────────────────────────────────────


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
