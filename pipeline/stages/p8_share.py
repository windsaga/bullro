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

PROMPT_TMPL = """다음 블로그 포스트에 대한 커뮤니티 배포용 공유문을 생성하세요.

포스트 제목: {title}
포스트 링크: {link}

포스트 내용 (요약):
{content}

4가지 채널별 공유문을 JSON으로 반환하세요. 각 채널의 톤과 형식을 정확히 따르세요.

{{
  "reddit": {{
    "title": "영어 검색형 제목 (60자 이내, 실험 결과/설정값/벤치마크 중심)",
    "body": "TL;DR:\\n- 핵심 포인트1 (영어)\\n- 핵심 포인트2\\n- 핵심 포인트3\\n\\nPractical takeaway: 실무 적용 포인트 1문장 (영어)\\nMain caveat: 주의사항 1문장 (영어)\\n\\nLink: {link}"
  }},
  "hn": {{
    "title": "영어 HN 제목 (80자 이내, 기술 관찰/설계 trade-off/구현 디테일 중심)",
    "comment": "HN Ask/Show 첫 댓글용 영어 본문 (150자 이내, 기술적 관찰과 구체적 trade-off)"
  }},
  "twitter": "한국어 트윗 (260자 이내). 첫 줄: 핵심 결과+수치. ▸ 포인트 3개. 빈 줄 후 링크.",
  "ko_community": "한국어 커뮤니티 공유문. 아래 형식 준수:\\n제목: <검색형 제목>\\n핵심 요약:\\n- <1줄>\\n- <1줄>\\n- <1줄>\\n실무 적용 포인트: <1-2문장>\\n주의할 점: <1문장>\\n링크: {link}"
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

    saved_files = _save_share_files(post, data)
    log.info(f"P8: 공유 콘텐츠 {len(saved_files)}개 저장 → posts/share/")

    if cfg.AUTO_POST_REDDIT and _reddit_ready():
        _post_to_reddit(data.get("reddit", {}))

    if cfg.AUTO_POST_TWITTER and _twitter_ready():
        _post_to_twitter(data.get("twitter", ""))

    _notify_slack_share(post, data)

    return saved_files


def _save_share_files(post: Post, data: dict) -> list[str]:
    share_dir = cfg.SHARE_DIR
    share_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().strftime("%Y-%m-%d")
    slug = post.slug[:40]
    base = share_dir / f"{today}-{slug}"

    saved: list[str] = []

    reddit = data.get("reddit", {})
    if reddit:
        content = (
            f"# Reddit\n\n"
            f"**Title:** {reddit.get('title', '')}\n\n"
            f"{reddit.get('body', '')}\n"
        )
        path = Path(f"{base}.reddit.md")
        path.write_text(content, encoding="utf-8")
        saved.append(str(path))

    hn = data.get("hn", {})
    if hn:
        content = (
            f"# Hacker News\n\n"
            f"**Title:** {hn.get('title', '')}\n\n"
            f"**Comment:**\n{hn.get('comment', '')}\n"
        )
        path = Path(f"{base}.hn.md")
        path.write_text(content, encoding="utf-8")
        saved.append(str(path))

    twitter = data.get("twitter", "")
    if twitter:
        path = Path(f"{base}.x.md")
        path.write_text(f"# X / Twitter\n\n{twitter}\n", encoding="utf-8")
        saved.append(str(path))

    ko_community = data.get("ko_community", "")
    if ko_community:
        path = Path(f"{base}.ko-community.md")
        path.write_text(
            f"# 국내 커뮤니티 (OKKY / 클리앙)\n\n{ko_community}\n",
            encoding="utf-8",
        )
        saved.append(str(path))

    return saved


def _reddit_ready() -> bool:
    return bool(cfg.REDDIT_CLIENT_ID and cfg.REDDIT_USERNAME and cfg.REDDIT_PASSWORD)


def _twitter_ready() -> bool:
    return bool(
        cfg.TWITTER_API_KEY and cfg.TWITTER_API_SECRET
        and cfg.TWITTER_ACCESS_TOKEN and cfg.TWITTER_ACCESS_SECRET
    )


def _post_to_reddit(reddit_data: dict) -> None:
    title = reddit_data.get("title", "")
    body = reddit_data.get("body", "")
    if not title or not body:
        log.warning("P8 Reddit: title/body 없음 — 스킵")
        return
    try:
        import praw  # type: ignore
        reddit = praw.Reddit(
            client_id=cfg.REDDIT_CLIENT_ID,
            client_secret=cfg.REDDIT_CLIENT_SECRET,
            username=cfg.REDDIT_USERNAME,
            password=cfg.REDDIT_PASSWORD,
            user_agent=cfg.REDDIT_USER_AGENT,
        )
        subreddit = reddit.subreddit(cfg.REDDIT_SUBREDDIT)
        submission = subreddit.submit(title=title, selftext=body)
        log.info(f"P8 Reddit 투고 완료: {submission.url}")
    except ImportError:
        log.error("P8 Reddit: praw 패키지 미설치 — pip install praw")
    except Exception as e:
        log.error(f"P8 Reddit 투고 실패: {e}")


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


def _notify_slack_share(post: Post, data: dict) -> None:
    from pipeline.notifier import notify_slack
    ko_text = data.get("ko_community", "")
    notify_slack(event="share_ready", post=post, ko_community_text=ko_text)


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
