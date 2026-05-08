from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Article:
    url: str
    title: str
    content: str
    source: str          # "arxiv" | "github" | "blog" | "hn" | "reddit" | "hf" | "pwc"
    published_at: str    # ISO8601
    signals: dict = field(default_factory=dict)
    # {"hn_points": 340, "reddit_score": 80, "hf_upvotes": 12, "github_star_delta": 50}


@dataclass
class ScoredArticle(Article):
    composite_score: float = 0.0


@dataclass
class SelectedTopic:
    article: ScoredArticle
    angle: str           # "기술심화" | "실용적용" | "한국맥락" | "비교분석"
    p1_scores: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class SynthesizedFacts:
    topic: SelectedTopic
    claims: str          # ## 핵심 주장
    data_points: str     # ## 핵심 수치/데이터
    mechanism: str       # ## 기술적 메커니즘
    limitations: str     # ## 한계 및 제약
    opinions: str        # ## [의견] 저자의 주관적 해석
    raw: str = ""        # 전체 원문 (P6 팩트체크용)


@dataclass
class Draft:
    facts: SynthesizedFacts
    content: str
    model: str           # "GLM-5.1" | "DeepSeek-V4-Pro"
    version: int = 1     # 1=P3 초안, 2=P5 수정본


@dataclass
class Critique:
    draft: Draft
    scores: dict = field(default_factory=dict)
    total: int = 0
    weaknesses: list[str] = field(default_factory=list)
    improvement_guide: str = ""


@dataclass
class FactCheckResult:
    draft: Draft
    claims: list[dict] = field(default_factory=list)
    # [{"text": ..., "status": "supported|inferred|unsupported", "quote": ...}]
    unsupported_count: int = 0


@dataclass
class SEOMeta:
    title_candidates: list[str] = field(default_factory=list)
    meta_description: str = ""
    tags: list[str] = field(default_factory=list)
    thumbnail_prompt: str = ""
    internal_link_slots: list[str] = field(default_factory=list)
    focus_keyword: str = ""          # 한국어 포커스 키워드 (예: "로컬 LLM 추천 2026")
    focus_keyword_slug: str = ""     # URL용 영문 슬러그 (예: "local-llm-comparison-2026")


@dataclass
class Post:
    draft: Draft
    fact_check: FactCheckResult
    seo: SEOMeta
    thumbnail_url: Optional[str]
    chosen_title: str
    slug: str
    status: str = "ready"   # "ready" | "pending_review" | "published" | "rejected"
    wp_link: str = ""
    file_path: str = ""
