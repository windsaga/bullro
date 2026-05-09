from __future__ import annotations

import os
from pathlib import Path


def _require(key: str) -> str:
    val = os.getenv(key, "")
    if not val:
        raise RuntimeError(f"환경변수 {key} 가 설정되지 않았습니다.")
    return val


class Config:
    # NVIDIA API
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"

    # Anthropic API (Claude fallback)
    ANTHROPIC_API_KEY: str = ""

    # GitHub
    GITHUB_TOKEN: str = ""

    # WordPress
    WORDPRESS_URL: str = ""
    WORDPRESS_USERNAME: str = ""
    WORDPRESS_APP_PASSWORD: str = ""
    WORDPRESS_DEFAULT_CATEGORY_ID: int = 61
    WORDPRESS_CATEGORY_AI_ML: int = 61
    WORDPRESS_CATEGORY_DEV_TOOLS: int = 62
    WORDPRESS_CATEGORY_PAPER_REVIEW: int = 63
    # 시리즈 카테고리 (WordPress에서 미리 생성 후 ID를 .env에 설정, 0이면 비활성화)
    WORDPRESS_CATEGORY_LOCAL_LLM: int = 0
    WORDPRESS_CATEGORY_AI_DEVTOOLS: int = 0
    WORDPRESS_CATEGORY_AI_AUTOMATION: int = 0

    # Slack
    SLACK_WEBHOOK_URL: str = ""
    SLACK_CHANNEL: str = "#blog-review"

    # Reddit (선택 — praw 자동 투고)
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "bullro-bot/1.0"
    REDDIT_USERNAME: str = ""
    REDDIT_PASSWORD: str = ""
    REDDIT_SUBREDDIT: str = "LocalLLaMA"
    AUTO_POST_REDDIT: bool = False   # true이고 크레덴셜 있을 때만 자동 투고

    # Twitter/X (선택 — tweepy 자동 투고)
    TWITTER_API_KEY: str = ""
    TWITTER_API_SECRET: str = ""
    TWITTER_ACCESS_TOKEN: str = ""
    TWITTER_ACCESS_SECRET: str = ""
    AUTO_POST_TWITTER: bool = False  # true이고 크레덴셜 있을 때만 자동 투고

    # Semantic Scholar (optional)
    SEMANTIC_SCHOLAR_API_KEY: str = ""

    # 파이프라인 동작
    DAILY_POST_COUNT: int = 2
    AUTO_PUBLISH: bool = False          # v2에서 True로 변경
    DEDUP_CONFIDENCE_THRESHOLD: float = 0.75  # DeepSeek 중복 판정 confidence 임계값
    MIN_QUALITY_SCORE: int = 75         # 0이면 품질 게이트 무시

    # 경로
    BASE_DIR: Path = Path("/app")
    DATA_DIR: Path = BASE_DIR / "data"
    POSTS_DIR: Path = BASE_DIR / "posts"
    SHARE_DIR: Path = BASE_DIR / "posts" / "share"
    LOGS_DIR: Path = BASE_DIR / "logs"
    POSTS_JSON: Path = DATA_DIR / "posts.json"
    PENDING_JSON: Path = DATA_DIR / "pending.json"
    SOURCES_WATCHLIST: Path = DATA_DIR / "sources_watchlist.json"

    def __init__(self) -> None:
        self.NVIDIA_API_KEY = _require("NVIDIA_API_KEY")
        self.NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", self.NVIDIA_BASE_URL)
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

        self.WORDPRESS_URL = os.getenv("WORDPRESS_URL", "").rstrip("/")
        self.WORDPRESS_USERNAME = os.getenv("WORDPRESS_USERNAME", "")
        self.WORDPRESS_APP_PASSWORD = os.getenv("WORDPRESS_APP_PASSWORD", "")
        self.WORDPRESS_DEFAULT_CATEGORY_ID = int(
            os.getenv("WORDPRESS_DEFAULT_CATEGORY_ID", "61")
        )
        self.WORDPRESS_CATEGORY_AI_ML = int(os.getenv("WORDPRESS_CATEGORY_AI_ML", "61"))
        self.WORDPRESS_CATEGORY_DEV_TOOLS = int(os.getenv("WORDPRESS_CATEGORY_DEV_TOOLS", "62"))
        self.WORDPRESS_CATEGORY_PAPER_REVIEW = int(os.getenv("WORDPRESS_CATEGORY_PAPER_REVIEW", "63"))
        self.WORDPRESS_CATEGORY_LOCAL_LLM = int(os.getenv("WORDPRESS_CATEGORY_LOCAL_LLM", "0"))
        self.WORDPRESS_CATEGORY_AI_DEVTOOLS = int(os.getenv("WORDPRESS_CATEGORY_AI_DEVTOOLS", "0"))
        self.WORDPRESS_CATEGORY_AI_AUTOMATION = int(os.getenv("WORDPRESS_CATEGORY_AI_AUTOMATION", "0"))

        self.SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
        self.SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#blog-review")

        self.REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
        self.REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", self.REDDIT_USER_AGENT)
        self.REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
        self.REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
        self.REDDIT_SUBREDDIT = os.getenv("REDDIT_SUBREDDIT", "LocalLLaMA")
        self.AUTO_POST_REDDIT = os.getenv("AUTO_POST_REDDIT", "false").lower() == "true"

        self.TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
        self.TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
        self.TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
        self.TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")
        self.AUTO_POST_TWITTER = os.getenv("AUTO_POST_TWITTER", "false").lower() == "true"

        self.SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

        self.DAILY_POST_COUNT = int(os.getenv("DAILY_POST_COUNT", "2"))
        self.AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "false").lower() == "true"
        self.DEDUP_CONFIDENCE_THRESHOLD = float(
            os.getenv("DEDUP_CONFIDENCE_THRESHOLD", "0.75")
        )
        # 0으로 설정하면 품질 게이트 무시하고 무조건 발행
        self.MIN_QUALITY_SCORE = int(os.getenv("MIN_QUALITY_SCORE", "75"))

        base = Path(os.getenv("BASE_DIR", "/app"))
        self.BASE_DIR = base
        self.DATA_DIR = base / "data"
        self.POSTS_DIR = base / "posts"
        self.SHARE_DIR = base / "posts" / "share"
        self.LOGS_DIR = base / "logs"
        self.POSTS_JSON = self.DATA_DIR / "posts.json"
        self.PENDING_JSON = self.DATA_DIR / "pending.json"
        self.SOURCES_WATCHLIST = self.DATA_DIR / "sources_watchlist.json"

        for d in (self.DATA_DIR, self.POSTS_DIR, self.SHARE_DIR, self.LOGS_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # 초기 JSON 파일 생성
        for p, default in (
            (self.POSTS_JSON, []),
            (self.PENDING_JSON, []),
        ):
            if not p.exists():
                p.write_text("[]", encoding="utf-8")

        if not self.SOURCES_WATCHLIST.exists():
            self.SOURCES_WATCHLIST.write_text("[]", encoding="utf-8")


cfg = Config()
