"""Slack Incoming Webhook 알림."""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Any, Optional

from pipeline.config import cfg
from pipeline.models import Critique, FactCheckResult, Post, SelectedTopic

log = logging.getLogger(__name__)


def notify_slack(event: str, **kwargs) -> None:
    """이벤트별 Slack 메시지 발송. webhook URL 미설정 시 로그만 남김."""
    if not cfg.SLACK_WEBHOOK_URL:
        log.info(f"Slack webhook 미설정 — 이벤트 로그: {event}")
        return

    try:
        payload = _build_payload(event, **kwargs)
        _send(payload)
        log.info(f"Slack 알림 전송: {event}")
    except Exception as e:
        log.error(f"Slack 알림 실패 [{event}]: {e}")


def _send(payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        cfg.SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        pass


def _build_payload(event: str, **kwargs) -> dict:
    if event == "ready":
        return _payload_ready(kwargs["post"], kwargs["critique"])
    elif event == "published":
        return _payload_published(kwargs["post"], kwargs.get("wp_link", ""))
    elif event == "share_ready":
        return _payload_share_ready(kwargs["post"], kwargs.get("ko_community_text", ""))
    elif event == "low_quality":
        return _payload_low_quality(kwargs["topic"], kwargs["critique"])
    elif event == "pending":
        return _payload_pending(kwargs["topic"], kwargs["critique"])
    elif event == "factcheck_warning":
        return _payload_factcheck_warning(kwargs["topic"], kwargs["fact_check"])
    elif event == "pipeline_error":
        return _payload_error(kwargs["topic"], kwargs.get("error", ""))
    else:
        return {"text": f"[bullro] 알 수 없는 이벤트: {event}"}


def _payload_ready(post: Post, critique: Critique) -> dict:
    weaknesses = "\n".join(f"{i+1}. {w}" for i, w in enumerate(critique.weaknesses[:3]))
    title = post.chosen_title or "(제목 없음)"
    score = critique.total
    angle = post.draft.facts.topic.angle
    source_url = post.draft.facts.topic.article.url

    return {
        "text": f"📝 새 포스트 초안 준비됨: {title}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📝 새 포스트 초안 준비됨"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*제목 (1안):*\n{title}"},
                    {"type": "mrkdwn", "text": f"*앵글:* {angle}  |  *품질 점수:* {score}/100"},
                    {"type": "mrkdwn", "text": f"*파일:* `{post.file_path}`"},
                    {"type": "mrkdwn", "text": f"*원문:* <{source_url}|링크>"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*P4 지적 사항:*\n{weaknesses}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "✅ 승인 후 WordPress 수동 업로드 | v2+: 1시간 무반응 시 자동 게시"},
                ],
            },
        ],
    }


def _payload_published(post: Post, wp_link: str) -> dict:
    return {
        "text": f"✅ 블로그 포스트 발행 완료: {post.chosen_title}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ *발행 완료*\n제목: {post.chosen_title}\n링크: <{wp_link}|{wp_link}>",
                },
            }
        ],
    }


def _payload_share_ready(post: Post, ko_community_text: str) -> dict:
    title = post.chosen_title or "(제목 없음)"
    text_block = ko_community_text[:800] if ko_community_text else "공유 콘텐츠 생성 실패"
    return {
        "text": f"📢 공유 콘텐츠 준비됨: {title}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📢 커뮤니티 공유 콘텐츠 준비됨"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*포스트:* <{post.wp_link}|{title}>\n*공유 파일:* `posts/share/`",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*국내 커뮤니티용:*\n```{text_block}```",
                },
            },
        ],
    }


def _payload_low_quality(topic: SelectedTopic, critique: Critique) -> dict:
    weaknesses = "\n".join(f"- {w}" for w in critique.weaknesses[:3])
    return {
        "text": f"⚠️ 품질 미달 ({critique.total}/100): {topic.article.title[:50]}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"⚠️ *품질 미달 — 사람 검토 필요*\n"
                        f"*제목:* {topic.article.title[:60]}\n"
                        f"*점수:* {critique.total}/100\n"
                        f"*약점:*\n{weaknesses}"
                    ),
                },
            }
        ],
    }


def _payload_pending(topic: SelectedTopic, critique: Critique) -> dict:
    return {
        "text": f"🔄 보류 큐 추가 ({critique.total}/100): {topic.article.title[:50]}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🔄 *보류 큐 추가* (2회 시도 후 <75점)\n"
                        f"*제목:* {topic.article.title[:60]}\n"
                        f"*최종 점수:* {critique.total}/100\n"
                        f"*개선 지시:* {critique.improvement_guide[:200]}"
                    ),
                },
            }
        ],
    }


def _payload_factcheck_warning(topic: SelectedTopic, fact_check: FactCheckResult) -> dict:
    return {
        "text": f"🔍 팩트체크 경고: unsupported {fact_check.unsupported_count}건",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🔍 *팩트체크 경고*\n"
                        f"*제목:* {topic.article.title[:60]}\n"
                        f"*unsupported 주장:* {fact_check.unsupported_count}건\n"
                        f"포스트에 [추정] 태그가 자동 추가됩니다."
                    ),
                },
            }
        ],
    }


def _payload_error(topic: SelectedTopic, error: str) -> dict:
    return {
        "text": f"❌ 파이프라인 오류: {topic.article.title[:40]}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"❌ *파이프라인 오류*\n"
                        f"*제목:* {topic.article.title[:60]}\n"
                        f"*오류:* `{error[:300]}`"
                    ),
                },
            }
        ],
    }
