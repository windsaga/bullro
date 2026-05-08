# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bullro** is an AI-powered Korean tech blog automation pipeline. It collects articles from 7 sources daily, scores and deduplicates them, then runs a 7-stage LLM pipeline to produce and publish blog posts to WordPress.

**Cost target**: ~$0.05/post using NVIDIA API (DeepSeek V4 Pro + GLM-5.1).

## Commands

### Setup
```bash
cp .env.example .env
# Fill in: NVIDIA_API_KEY, WORDPRESS_*, SLACK_WEBHOOK_URL
docker compose build
```

### Run
```bash
# Start scheduler (daily KST 06:00 / UTC 21:00)
docker compose up -d

# One-off manual run
docker compose run --rm pipeline python -m pipeline.main

# Single stage test (from container or venv)
python -c "from pipeline.collector import collect; import asyncio; print(asyncio.run(collect()))"
```

### Local dev (without Docker)
```bash
pip install -r requirements.txt
python -m pipeline.main
```

Logs: `logs/pipeline.log` (Docker volume mount) + stdout.

## Architecture

### Pipeline Stages

`pipeline/main.py` → `run_pipeline()` orchestrates the full flow:

1. **Collect** (`collector.py`) — RSS/API aggregation from 7 sources (Tier1: arXiv, GitHub, official blogs; Tier2: HN, Reddit, HuggingFace, Papers with Code)
2. **Score + Dedup** (`scorer.py`) — NumPy z-score composite ranking, then DeepSeek V4 for LLM-based duplicate detection (threshold 0.75)
3. **P1 Triage** — DeepSeek selects top 1–3 topics (relevance/impact/novelty/kr_context)
4. **P2 Synthesis** — DeepSeek extracts structured facts (claims, data, mechanism, limitations)
5. **P3 Draft** — GLM-5.1 with `thinking=True` (streaming) generates Korean post
6. **P4 Critique** — DeepSeek scores draft on 6-axis rubric; <60 → retry, 60–74 → `pending.json`, ≥75 → publish
7. **P5 Revise** — GLM-5.1 applies critique feedback
8. **P6 Fact-check** — DeepSeek tags unsupported claims
9. **P7 SEO** — DeepSeek generates title candidates, meta description, tags, thumbnail prompt
10. **Image** (`image.py`) — Pollinations.ai FLUX thumbnail (free)
11. **Publish** (`publisher.py`) — WordPress REST API with Application Password auth

### Key Files

| File | Role |
|------|------|
| `pipeline/main.py` | Entry point; `run_pipeline()` + `_process_topic()` per-topic runner |
| `pipeline/config.py` | Env loading, path constants |
| `pipeline/models.py` | 8 dataclasses: `Article`, `ScoredArticle`, `SelectedTopic`, `SynthesizedFacts`, `Draft`, `Critique`, `FactCheckResult`, `SEOMeta`, `Post` |
| `pipeline/llm.py` | NVIDIA API wrapper: `deepseek()` (thinking=False, fast) vs `glm()` (thinking=True, streaming) |
| `pipeline/stages/` | P1–P7 implementations |
| `data/posts.json` | Publish history |
| `data/pending.json` | Retry queue for 60–74 score drafts |
| `data/sources_watchlist.json` | GitHub repos monitored by collector |

### LLM Model Roles

- **DeepSeek V4 Pro**: analysis tasks (scoring, dedup, triage, synthesis, critique, fact-check, SEO)
- **GLM-5.1 thinking**: creative writing tasks (P3 Draft, P5 Revise) — always use streaming

### Data Flow

Articles collected → z-score ranked → LLM deduped → top topics selected → per topic: facts synthesized → Korean draft written → quality gate (score ≥75 to proceed) → fact-checked → SEO metadata → thumbnail → WordPress publish → Slack notification.

## Environment Variables

See `.env.example`. Key variables:

| Variable | Purpose |
|----------|---------|
| `NVIDIA_API_KEY` | DeepSeek V4 Pro + GLM-5.1 via `api.nvidia.com` |
| `WORDPRESS_URL` / `WORDPRESS_USER` / `WORDPRESS_APP_PASSWORD` | REST API publishing |
| `SLACK_WEBHOOK_URL` | Stage notifications |
| `DAILY_POST_COUNT` | Topics to process per run (default: 2) |
| `AUTO_PUBLISH` | `false` = save as draft, `true` = publish immediately |

## `/blog-draft` Skill

Manual post creation via Claude Code. Trigger: `/blog-draft <topic or URL>`. Runs the same DeepSeek/GLM-5.1 pipeline with added review from Codex and Gemini before WordPress upload. See `.claude/skills/blog-draft/SKILL.md`.
