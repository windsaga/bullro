---
created: 2026-04-28
status: updated
version: v5
participants: [Claude, Codex, Gemini]
topic: AI 기술 블로그 자동화 파이프라인 — 사람 개입 최소화 + 품질 극대화
---

# 플랜: AI 기술 블로그 자동화 파이프라인 v5

> **전략 원칙**: 완전 자동화를 한 번에 구축하지 않는다. v1 → v2 → v3 단계적으로 사람을 뺀다.
> **비용 목표**: NVIDIA API 실비 수준 (DeepSeek/GLM-5.1, 포스트당 ~$0.05 예상)
> **인터벤션 목표**: 품질 기준 미달 시에만 Slack 알림 → 선택적 개입

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  NAS Docker cron (매일 06:00 KST)                           │
│  docker-compose + ofelia 스케줄러                           │
└──────────┬──────────────────────────────────────────────────┘
           ▼
┌──────────────────────┐  ┌──────────────────────┐
│ Tier1 수집 (무료)    │  │ Tier2 수집 (무료)    │
│ arxiv / GitHub /     │  │ HN / Reddit /        │
│ 공식 블로그 RSS      │  │ HuggingFace Daily /  │
│                      │  │ Papers with Code     │
└──────────┬───────────┘  └───────────┬──────────┘
           └──────────┬───────────────┘
                      ▼
           ┌──────────────────────────┐
           │ 복합 신호 점수 산출      │  ← 수집 신호 기반 순수 코드
           │ cross-source z-score     │
           │ + 중복 제거 (DeepSeek)   │
           └──────────┬───────────────┘
                      ▼  top 20 후보
           ┌──────────────────────────┐
           │ P1: Triage               │  ← DeepSeek V4 Pro (배치)
           │ 관련성/영향력/신선도 평가 │
           └──────────┬───────────────┘
                      ▼  top 1~3 토픽
           ┌──────────────────────────┐
           │ 게시 이력 중복 검사      │  ← DeepSeek confidence < 0.75만 통과
           └──────────┬───────────────┘
                      ▼
       ┌──────────────┴───────────────┐
       ▼ (토픽당 반복)                │
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │  P2: Source Synthesis (DeepSeek V4 Pro)             │
  │      원문 + 관련 자료 → 구조화된 팩트 추출          │
  │           ↓                                         │
  │  P3: Multi-Angle Draft (GLM-5.1 + thinking)         │
  │      앵글 1개 선택 → 초안 v1 생성                  │
  │           ↓                                         │
  │  P4: Self-Critique (DeepSeek V4 Pro)                │
  │      루브릭 채점 + 약점 3개 의무 지적               │
  │           ↓                                         │
  │    점수 60~74? → P3 재생성 1회 (최대 1회)          │
  │    점수 < 60?  → Slack 알림 (사람 개입)             │
  │    점수 ≥ 75?  ↓                                    │
  │  P5: Revision (GLM-5.1 + thinking)                  │
  │      critique 반영 → 초안 v2                        │
  │           ↓                                         │
  │  P6: Fact-Check (DeepSeek V4 Pro)                   │
  │      주장 → 원문 entailment 검사                    │
  │      unsupported claim → 경고 태그                  │
  │           ↓                                         │
  │  P7: SEO Meta (DeepSeek V4 Pro)                     │
  │      제목 5안 / 메타 디스크립션 / 내부 링크 슬롯    │
  │           ↓                                         │
  │  이미지 생성 (Pollinations.ai)                      │
  │      썸네일 + Mermaid 다이어그램                    │
  └─────────────────────────────────────────────────────┘
                      ▼
           ┌──────────────────────────┐
           │ WordPress REST API 업로드 │
           │ (예약 발행, 1편/일)       │
           └──────────┬───────────────┘
                      ▼
           ┌──────────────────────────┐
           │ GA4 + NAS 로컬 JSON 기록 │
           │ (월 1회 루브릭 가중치    │
           │  수동 보정)              │
           └──────────────────────────┘
```

---

## 프로젝트 파일 구조

```
bullro/                              ← NAS Docker 컨테이너 루트
├── pipeline/
│   ├── __init__.py
│   ├── config.py                    ← 환경변수 로딩 & 상수
│   ├── models.py                    ← 스테이지 간 데이터 클래스 (타입 계약)
│   ├── collector.py                 ← Tier1/2 소스 수집
│   ├── scorer.py                    ← 복합 신호 점수 + 중복 제거
│   ├── llm.py                       ← NVIDIA API 클라이언트 (DeepSeek/GLM-5.1)
│   ├── stages/
│   │   ├── p1_triage.py             ← DeepSeek: top-3 토픽 선별
│   │   ├── p2_synthesis.py          ← DeepSeek: 팩트 구조화
│   │   ├── p3_draft.py              ← GLM-5.1 thinking: 초안 작성
│   │   ├── p4_critique.py           ← DeepSeek: 루브릭 채점
│   │   ├── p5_revise.py             ← GLM-5.1 thinking: 수정본
│   │   ├── p6_factcheck.py          ← DeepSeek: 팩트체크
│   │   └── p7_seo.py                ← DeepSeek: SEO 메타
│   ├── image.py                     ← Pollinations.ai 썸네일
│   ├── publisher.py                 ← WordPress REST API 업로드
│   ├── notifier.py                  ← Slack Incoming Webhook 알림
│   └── main.py                      ← 진입점 (오케스트레이터)
├── data/                            ← NAS 볼륨 마운트 (컨테이너 재시작 후에도 유지)
│   ├── posts.json                   ← 게시 이력 DB (url, title, tags, score)
│   ├── pending.json                 ← 보류 큐 (score 60~74, 사람 검토 대기)
│   └── sources_watchlist.json       ← GitHub repo 100개 목록
├── posts/                           ← 생성된 초안/최종고 임시 보관
├── logs/
├── Dockerfile
├── docker-compose.yml
├── scheduler.ini                    ← ofelia cron 설정
├── requirements.txt
├── .env                             ← NAS에서 직접 관리 (gitignore)
├── .env.example
└── plan/
    └── 2026-04-28-ai-blog-pipeline.md
```

---

## 필요 환경변수 목록

```bash
# .env.example

# NVIDIA API (DeepSeek V4 Pro + GLM-5.1)
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1

# GitHub (소스 수집용 — DB는 NAS 로컬)
GITHUB_TOKEN=                        # PAT: public_repo 스코프

# WordPress REST API
WORDPRESS_URL=https://your-blog.com
WORDPRESS_USERNAME=
WORDPRESS_APP_PASSWORD=              # 관리자 > 프로필 > Application Passwords
WORDPRESS_DEFAULT_CATEGORY_ID=1      # 카테고리 ID

# Slack
SLACK_WEBHOOK_URL=                   # Incoming Webhook URL
SLACK_CHANNEL=#blog-review           # 알림 채널

# Reddit (선택 — v2)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=bullro-bot/1.0

# Semantic Scholar (선택 — 인용수 보강)
SEMANTIC_SCHOLAR_API_KEY=            # 무료, rate limit 완화용
```

---

## 소스 수집 전략

### Tier 1 — 1차 소스 (신뢰도 최고)

| 소스 | 수집 방법 | 신호 지표 | 비용 |
|------|----------|----------|------|
| arxiv (cs.AI/cs.CL/cs.LG) | RSS + Atom API | Semantic Scholar 인용수 | 0원 |
| GitHub Releases (주요 100개 repo) | GitHub API (5000 req/h) | star delta 24h | 0원 |
| 공식 블로그 (OpenAI/Anthropic/Google/Meta) | RSS | - | 0원 |

### Tier 2 — 2차 소스 (커뮤니티 신호)

| 소스 | 수집 방법 | 신호 지표 | 비용 |
|------|----------|----------|------|
| HuggingFace Daily Papers | 스크래핑 (일 1회, 1초 간격) | upvote 수 | 0원 |
| Hacker News | Algolia API (무제한) | points + comments | 0원 |
| Reddit (r/LocalLLaMA, r/ML) | `.json` suffix (60 req/min) | score + upvote_ratio | 0원 |
| Papers with Code | RSS | code repo stars | 0원 |

> **Tier 3 제외**: X/Twitter API 유료화, Discord 스크래핑 ToS 위반 — 수집 비용 대비 품질 기여 낮음

### 복합 신호 점수식

```python
score = (0.4 * z(hn_points)
       + 0.3 * z(reddit_score)
       + 0.2 * z(hf_upvotes)
       + 0.1 * log1p(github_star_delta))
```

z-score 정규화로 소스 간 스케일 차이 제거.
**크로스 소스 교차 검증 우선**: arxiv 논문이 HN 100+ points **AND** HF Daily Papers 등재 시 우선 선발.

---

## 7단계 LLM 파이프라인 (P1~P7)

| 단계 | 모델 | 목적 | 출력 |
|------|------|------|------|
| **P1 Triage** | **DeepSeek V4 Pro** | 후보 20개 → 상위 3개 선별. 관련성/영향력/한국 개발자 관련도 0-10 | JSON 배치 |
| **P2 Source Synthesis** | **DeepSeek V4 Pro** | 원문 + 관련 자료 multi-doc → 구조화된 팩트만 추출 | structured facts |
| **P3 Draft** | **GLM-5.1 + thinking** | 앵글 1개 선택 → 초안 v1. thinking 활성화 (`enable_thinking: True`) | 본문 v1 |
| **P4 Self-Critique** | **DeepSeek V4 Pro** | 루브릭 채점 + **약점 3개 의무** (칭찬 금지 지시) | score + critique |
| **P5 Revise** | **GLM-5.1 + thinking** | critique 반영 → 초안 v2. **1회만** (2회부터 hallucination 증가) | 본문 v2 |
| **P6 Fact-Check** | **DeepSeek V4 Pro** | v2 주장 → 원문 entailment 검사 ("exact quote 인용 불가 = unsupported") | flagged claims |
| **P7 SEO Meta** | **DeepSeek V4 Pro** | 제목 5안 / 메타 디스크립션 / 내부 링크 슬롯 / 태그 | metadata |

> **모델 선택 근거**: DeepSeek = 분석·분류·검증 (빠름, thinking 불필요) / GLM-5.1 = 창작·작문 (thinking으로 품질 향상)
> **NVIDIA API 호출 방식**: `openai` 라이브러리 + `base_url=https://integrate.api.nvidia.com/v1` (nvidia-test 코드 패턴 동일)

### 앵글 선택 (P1에서 1개만 라우팅)

| 앵글 | 적합 토픽 | 구조 |
|------|----------|------|
| **기술 심화** | 새 모델/논문 | 메커니즘 → 수식/코드 → 한계 |
| **실용 적용** | 새 API/라이브러리 | 설치 → 예제 → 실무 패턴 |
| **한국 시장 맥락** | 글로벌 트렌드 | 해외 동향 → 국내 적용 사례 → 전망 |
| **비교 분석** | 경쟁 기술 출시 | A vs B 기준표 → 선택 가이드 |

> 4개 앵글을 모두 쓰지 않는다. 토큰 4배 소모 대비 독자 가치 1.5배 미만.

---

## 프롬프트 상세 설계 (P1~P7)

### P1 — Triage (Flash, 배치)

```
[시스템]
당신은 한국 AI 개발자 커뮤니티를 위한 기술 블로그 에디터입니다.
아래 후보 기사/논문 목록을 보고, 한국 개발자에게 가장 가치 있는 상위 3개를 선별하세요.

평가 기준 (각 0~10점):
- relevance: AI/ML 개발자 실무와의 직접 연관성
- impact: 기술 변화를 일으킬 파급력
- novelty: 기존에 다뤄지지 않은 새로운 내용
- kr_context: 한국 개발 환경(카카오/네이버/토스 등)에 적용 가능성

[유저]
다음 후보 목록을 JSON으로 평가하세요:
{{candidates_json}}

응답 형식:
{"selected": [{"id": "...", "scores": {...}, "total": 0~40, "angle": "기술심화|실용적용|한국맥락|비교분석", "reason": "한 줄 이유"}]}
```

### P2 — Source Synthesis (Flash)

```
[시스템]
기술 블로그 포스트 작성을 위한 자료 조사 전문가입니다.
원문을 읽고 오직 검증 가능한 사실, 수치, 주장만 추출하세요.
의견이나 해석은 "[의견]" 태그로 명확히 구분합니다.

[유저]
다음 원문을 분석하여 구조화된 팩트를 추출하세요:

제목: {{title}}
URL: {{url}}
원문 내용: {{content}}

출력 형식:
## 핵심 주장 (원문 인용구 포함)
## 핵심 수치/데이터
## 기술적 메커니즘
## 한계 및 제약
## [의견] 저자의 주관적 해석
```

### P3 — Draft (Pro + thinking)

```
[시스템]
당신은 한국 최고의 AI 기술 블로그 작가입니다.
독자는 3~7년차 한국 백엔드/ML 개발자입니다.
구어체를 피하고, 정확하고 통찰 있는 기술 문체를 사용합니다.

앵글: {{angle}}

앵글별 구조:
- 기술심화: TL;DR → 배경 → 핵심 메커니즘 → 수식/코드 → 실험 결과 → 한계 → 결론
- 실용적용: TL;DR → 문제 정의 → 설치/설정 → 핵심 예제 코드 → 실무 패턴 → 주의사항 → 결론
- 한국맥락: TL;DR → 글로벌 동향 → 국내 유사 사례 비교 → 한국 적용 시 고려사항 → 전망
- 비교분석: TL;DR → 비교 대상 소개 → 기준별 비교표 → 선택 가이드 (시나리오별) → 결론

필수 포함:
- TL;DR 섹션 (3줄 이내)
- 코드 예제 또는 Mermaid 다이어그램 최소 1개
- H2 섹션 최소 3개
- 출처 링크

[유저]
다음 자료를 바탕으로 {{angle}} 앵글의 블로그 포스트를 작성하세요:
{{synthesized_facts}}

목표 분량: 1500~2500자 (한국어 기준)
```

### P4 — Self-Critique (Flash)

```
[시스템]
당신은 까다로운 기술 블로그 편집장입니다.
아래 글을 루브릭 기준으로 채점하고 반드시 약점 3개를 찾아내세요.
"전반적으로 좋습니다" 같은 총평은 절대 쓰지 마세요.

루브릭:
- 사실 정확성 (30): 원문과 다른 주장, 수치 오류
- 새로운 관점 (20): 단순 번역 수준인가, 한국 맥락이 있는가
- 기술 깊이 (15): 메커니즘 설명, 코드/수식 포함 여부
- 가독성 (15): 단락 길이, 헤딩 구조, 코드블록 포맷
- 독창성 (10): 기존 유사 글과 차별점
- SEO 구조 (10): TL;DR, H2 3개+, alt text, 메타 설명

[유저]
원문 팩트: {{facts}}
작성된 포스트: {{draft}}

응답 형식:
{"scores": {"사실정확성": 0~30, "새로운관점": 0~20, ...}, "total": 0~100,
 "weaknesses": ["약점1 (구체적 인용)", "약점2", "약점3"],
 "improvement_guide": "P5에서 반영할 구체적 수정 지시"}
```

### P5 — Revision (Pro)

```
[시스템]
당신은 P4 비평을 반영하여 글을 개선하는 작가입니다.
비평에서 지적된 약점을 모두 수정하되, 글의 전체 구조를 유지합니다.
수정하지 않은 부분은 그대로 유지하세요.

[유저]
원본 포스트:
{{draft_v1}}

P4 비평 결과:
{{critique}}

위 약점들을 반영한 개선본을 작성하세요.
수정 사항을 포스트 맨 아래에 <!-- CHANGES: 수정내용 --> 형태로 주석 추가.
```

### P6 — Fact-Check (Flash)

```
[시스템]
당신은 팩트체커입니다.
아래 포스트의 각 주장을 원문과 대조하여 검증합니다.
원문에서 정확히 인용할 수 없으면 "unsupported"로 표시합니다.
35% false-positive를 줄이기 위해 반드시 원문 exact quote를 제시해야 합니다.

검증 카테고리:
- supported: 원문에서 exact quote로 확인됨
- inferred: 원문에서 합리적으로 추론 가능 (quote 불가)
- unsupported: 원문에 근거 없음 → 포스트에서 삭제 또는 [추정] 태그

[유저]
원문 팩트: {{facts}}
검증할 포스트: {{draft_v2}}

응답: {"claims": [{"text": "주장", "status": "supported|inferred|unsupported", "quote": "원문 인용 or null"}]}
```

### P7 — SEO Meta (Flash)

```
[시스템]
당신은 한국 검색 SEO 전문가입니다.
WordPress + 구글 검색 기준으로 메타 정보를 작성합니다.

[유저]
포스트 내용: {{final_draft}}

출력 (JSON):
{
  "title_candidates": ["제목1 (30자 이내)", "제목2", "제목3", "제목4", "제목5"],
  "meta_description": "150자 이내, 핵심 키워드 포함",
  "tags": ["태그1", "태그2", ... (최대 10개)"],
  "internal_link_slots": ["관련 주제 키워드1", "관련 주제 키워드2"],
  "thumbnail_prompt": "Pollinations.ai에 전달할 영어 이미지 생성 프롬프트 (구체적, 기술적 시각 요소 포함)"
}
```

---

## Slack 검토 게이트 UX

### 알림 메시지 포맷 (Incoming Webhook + Block Kit)

```json
{
  "text": "📝 새 포스트 초안 준비됨",
  "blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": "📝 새 포스트 초안 준비됨"}},
    {"type": "section", "fields": [
      {"type": "mrkdwn", "text": "*제목 (1안):*\nGPT-5 공개: 한국 개발자가 알아야 할 5가지 변화"},
      {"type": "mrkdwn", "text": "*앵글:* 실용적용  |  *품질 점수:* 82/100"},
      {"type": "mrkdwn", "text": "*소스:* arxiv + HN 340pts  |  *예상 발행:* 내일 09:00"}
    ]},
    {"type": "section", "text": {"type": "mrkdwn",
      "text": "*TL;DR 미리보기:*\n> OpenAI가 GPT-5를 공개했다. 컨텍스트 윈도우 200만 토큰..."}},
    {"type": "section", "text": {"type": "mrkdwn",
      "text": "*P4 지적 사항:*\n1. 벤치마크 수치 출처 불명확 → 원문 재확인 필요\n2. 코드 예제 없음 → 실용 적용 앵글 미흡\n3. H2 섹션 2개뿐 → SEO 구조 미충족"}},
    {"type": "actions", "elements": [
      {"type": "button", "text": {"type": "plain_text", "text": "✅ 자동 게시"}, "style": "primary", "value": "approve"},
      {"type": "button", "text": {"type": "plain_text", "text": "✏️ 수정 요청"}, "value": "revise"},
      {"type": "button", "text": {"type": "plain_text", "text": "❌ 폐기"}, "style": "danger", "value": "reject"}
    ]}
  ]
}
```

> **v1 단순화**: Slack Incoming Webhook만 사용 시 Actions 버튼이 작동하지 않음.
> 버튼 인터랙션은 Slack App (OAuth) 필요. **v1에서는 텍스트 알림만 보내고 파일을 보고 수동 업로드.**
> **v2에서** Slack App + `/approve` 슬래시 커맨드로 업그레이드.

### 분기 처리 로직

```
점수 ≥ 75 + 1시간 무반응 (v2+)  →  자동 게시 진행
점수 ≥ 75 + 수동 승인           →  즉시 게시
점수 ≥ 75 + 수정 요청           →  수정 요청 텍스트 대기 후 P3 재시작
점수 ≥ 75 + 폐기 선택           →  폐기 (posts.json에 rejected 기록)
점수 60~74                       →  "품질 미달 - 검토 필요" Slack 별도 알림
점수 < 60                        →  Slack 즉각 알림, 자동 게시 불가
```

> **v1에서는 자동 게시 없음**: Slack 알림 후 사람이 직접 파일 확인 후 수동 업로드.
> **v2부터**: 75점 이상 + 1시간 무반응 시 자동 게시 활성화.

---

## 품질 루브릭 (0~100점)

| 차원 | 가중치 | 자동 측정 기준 |
|------|--------|--------------|
| **사실 정확성** | 30 | unsupported claim 수: 0건=30 / 1건=20 / 2건=10 / 3+건=0 |
| **새로운 관점** | 20 | 한국 맥락/실무 적용/비교 포함 여부 (체크리스트) |
| **기술 깊이** | 15 | 메커니즘 설명 + 코드/수식 존재 여부 |
| **가독성** | 15 | 단락 5줄 초과율, H2/H3 비율, 코드블록 여부 |
| **독창성** | 10 | DeepSeek 중복 판정 confidence < 0.75 |
| **SEO 구조** | 10 | TL;DR / H2 3개+ / alt text / 메타 디스크립션 |

### 자동 분기 게이트

```
점수 ≥ 75  →  자동 게시 (사람 개입 없음)
점수 60~74 →  P3 재생성 1회 → 재채점 → 그래도 < 75면 보류 큐
점수 < 60  →  Slack 즉시 알림 (사람이 폐기 or 수동 수정)
```

> **LLM 자기 채점 편향 주의**: "점수"보다 "약점 3개 명시" critique가 더 신뢰할 만함.
> P4 프롬프트에 반드시 포함: *"Find at least 3 weaknesses. Do not say 'overall good'."*

---

## API 호출 수 & Free Tier 계산

### 포스트 1개 기준 호출 수

| 단계 | 모델 | 호출 수 | 누적 Flash | 누적 Pro |
|------|------|--------|-----------|----------|
| P1 Triage | Flash | 1 | 1 | 0 |
| P2 Synthesis | Flash | 1 | 2 | 0 |
| P3 Draft | Pro | 1 | 2 | 1 |
| P4 Critique | Flash | 1 | 3 | 1 |
| P3 재생성 (확률 30%) | Pro | 0.3 | 3 | 1.3 |
| P4 재채점 (조건부) | Flash | 0.3 | 3.3 | 1.3 |
| P5 Revise | Pro | 1 | 3.3 | 2.3 |
| P6 Fact-check | Flash | 1 | 4.3 | 2.3 |
| P7 SEO | Flash | 1 | **5.3** | **2.3** |

### Free Tier 일일 처리 가능량

| 모델 | Free Tier (RPD) | 안전 마진 70% | 포스트당 호출 | **일 최대 포스트** |
|------|----------------|--------------|-------------|-----------------|
| Flash (2.5) | 1,500 | 1,050 | 5.3 | ~198편 |
| Pro (2.5) | 50 | 35 | 2.3 | **~15편** ← 병목 |

**Pro가 병목. 일 1~2편 목표면 여유 충분.**
일 5편 이상 원하면 P3/P5를 Flash로 강등하고 thinking budget 증가로 보완.

### 포스트당 처리 시간

RPM 제약 (Pro 5 RPM, Flash 15 RPM) + thinking 지연 → **약 5~8분/포스트**

---

## 이미지 & 시각 자료 전략

| 유형 | 도구 | 비용 | 용도 |
|------|------|------|------|
| 썸네일 이미지 | **Pollinations.ai** | 0원 | FLUX 기반, 워터마크 없음, 인증 불필요 |
| 아키텍처 다이어그램 | **Mermaid.js** | 0원 | 코드 블록으로 삽입, Tistory 렌더링 |
| 비교표 | Markdown 테이블 | 0원 | 자동 생성 |
| 스크린샷 | 원문 링크 + 출처 명시 | 0원 | 저작권 안전 |

> **Codex / DALL-E 이미지 생성 불가**: Codex CLI는 텍스트 전용. gpt-image-1 사용 시 별도 OpenAI API 키 필요 (이미지당 ~$0.04 유료). 구독과 무관.

---

## 확정 기술 스택

| 레이어 | 선택 | 비용 | 근거 |
|--------|------|------|------|
| 파이프라인 런타임 | **Python 3.12** | 무료 | 세밀한 전처리 제어 |
| LLM — 분석/검증 | **DeepSeek V4 Pro** (NVIDIA API) | ~$0.03/포스트 | 빠름, P1·P2·P4·P6·P7 담당 |
| LLM — 작성/수정 | **GLM-5.1** (NVIDIA API) | ~$0.05/포스트 | thinking 활성화, P3·P5 담당 |
| 이미지 | **Pollinations.ai + Mermaid** | **무료** | 무제한 무료 |
| 소스 수집 | feedparser, PyGitHub, requests | **무료** | - |
| 중복 탐지 | DeepSeek V4 Pro (NVIDIA API) | API 비용에 포함 | 게시 이력/후보 간 주제 중복 판정 |
| **인프라/스케줄러** | **NAS Docker + ofelia** | 전기세 수준 | 외부 의존 없음, 온프레미스 |
| DB | **NAS 로컬 JSON** (볼륨 마운트) | **무료** | 컨테이너 재시작 시에도 유지 |
| 블로그 | **WordPress** | 호스팅비 | REST API, AdSense, 플러그인 |
| 알림 | **Slack Incoming Webhook** | **무료** | 업무 채널 통합, 버튼 인터랙션 |
| 뉴스레터 | **Mailchimp** free | **무료** (500명까지) | |
| 애널리틱스 | GA4 + GSC | **무료** | |
| **월 합계 (API)** | | **~$3~5/월** | 포스트 30편 기준 |

### 도구 역할 분담

| 도구 | 역할 |
|------|------|
| **DeepSeek V4 Pro** | 파이프라인 분석·분류·검증 엔진 (P1/P2/P4/P6/P7) |
| **GLM-5.1** | 파이프라인 작문 엔진 (P3/P5, thinking 활성화) |
| **Claude Code** | `/blog-draft` 리뷰어 #1 + 오케스트레이션 |
| **Codex CLI** | `/blog-draft` 리뷰어 #2 (기술 정확성) + 코드 유지보수 |
| **Gemini CLI** | `/blog-draft` 리뷰어 #3 (SEO·가독성) |
| **Claude Pro** | 프롬프트 R&D, 월 1회 품질 감사 (웹 UI, 직접 투입 불가) |

## 인프라: NAS Docker 구성

```yaml
# docker-compose.yml
services:
  pipeline:
    build: .
    container_name: ai-blog-pipeline
    env_file: .env
    volumes:
      - ./data:/app/data        # JSON DB (posts.json, pending.json)
      - ./posts:/app/posts      # 생성된 포스트 임시 보관
      - ./logs:/app/logs
    restart: unless-stopped
    networks:
      - blog-net

  scheduler:
    image: mcuadros/ofelia:latest
    container_name: blog-scheduler
    depends_on:
      - pipeline
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./scheduler.ini:/etc/ofelia/config.ini
    restart: unless-stopped
    networks:
      - blog-net

networks:
  blog-net:
```

```ini
# scheduler.ini
[job-exec "daily-pipeline"]
schedule  = 0 6 * * *          # 매일 06:00 KST (UTC 21:00 전날)
container = ai-blog-pipeline
command   = python main.py
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline/ ./pipeline/
CMD ["python", "pipeline/main.py"]
```

---

## 단계별 실행 계획 (v1 → v3)

### v1: 기반 구축 (2주) — 사람 검토 유지

목표: 파이프라인이 하루 1~2편 초안을 만들고 Slack으로 전달. 게시는 사람이 승인.

- [ ] Tier 1 소스 수집 스크립트 (arxiv + GitHub + 공식 블로그 RSS)
- [ ] 복합 신호 점수 산출 코드
- [ ] NVIDIA API 클라이언트 모듈 (nvidia-test 패턴 참고)
- [ ] P1(Triage·DeepSeek) + P2(Synthesis·DeepSeek) + P3(Draft·GLM-5.1) 구현
- [ ] P7(SEO Meta·DeepSeek) + Pollinations.ai 이미지 생성
- [ ] Slack Incoming Webhook 알림 (미리보기 + 승인 링크)
- [ ] WordPress REST API 업로드 연동 (Application Password 인증)
- [ ] NAS Docker Compose + ofelia 스케줄러 설정

### v2: 자동 품질 게이트 (4주) — 사람 개입 조건부

목표: ≥75점 포스트는 완전 자동 게시. 사람은 <60점 알림에만 반응.

- [ ] Tier 2 소스 추가 (HN + Reddit + HuggingFace + PwC)
- [ ] P4(Self-Critique·DeepSeek) + P5(Revise·GLM-5.1) + P6(Fact-Check·DeepSeek) 구현
- [ ] 루브릭 자동 채점 + 분기 게이트 로직
- [ ] DeepSeek 기반 중복 탐지 (NAS JSON DB)
- [ ] 내부 링크 자동 삽입 (과거 포스트 DB 조회)
- [ ] 재시도 로직 (NVIDIA API rate limit 백오프)

### v3: 피드백 루프 (8주~) — 자기 개선

목표: GA4/GSC 데이터로 루브릭 가중치를 자동 보정.

- [ ] GA4 API + Search Console API 연동
- [ ] 포스트별 CTR/체류시간 → 성과 DB 기록
- [ ] 고성과 포스트 패턴 추출 → 프롬프트 개선 자동화
- [ ] 시리즈/연재 일관성을 위한 RAG (게시 이력 → P2에 주입)
- [ ] 루브릭 가중치 월 1회 자동 조정

---

## 오케스트레이터 설계 (`pipeline/main.py` + `models.py`)

> **이 섹션이 전체 파이프라인의 연결 설계입니다.**
> 크롤링 → 주제 선택 → 초안 → 3자 리뷰(blog-draft 스킬) → 완성본의 흐름이
> 어떻게 자동으로 연결되는지 코드 수준으로 정의합니다.

### 스테이지 간 데이터 계약 (`models.py`)

```python
from dataclasses import dataclass, field
from typing import Optional

# 수집 단계
@dataclass
class Article:
    url: str
    title: str
    content: str          # 원문 전체 텍스트
    source: str           # "arxiv" | "hn" | "reddit" | "github" | "blog"
    published_at: str     # ISO8601
    signals: dict         # {"hn_points": 340, "reddit_score": 80, ...}

# 점수 산정 단계
@dataclass
class ScoredArticle(Article):
    composite_score: float = 0.0

# P1 트리아지 단계
@dataclass
class SelectedTopic:
    article: ScoredArticle
    angle: str            # "기술심화" | "실용적용" | "한국맥락" | "비교분석"
    p1_scores: dict       # {"relevance": 8, "impact": 9, ...}
    reason: str

# P2 합성 단계
@dataclass
class SynthesizedFacts:
    topic: SelectedTopic
    claims: str           # 마크다운 블록
    data_points: str
    mechanism: str
    limitations: str
    opinions: str

# P3/P5 초안 단계
@dataclass
class Draft:
    facts: SynthesizedFacts
    content: str          # 마크다운 본문
    model: str            # "GLM-5.1" | "DeepSeek-V4-Pro"
    version: int          # 1 = P3 초안, 2 = P5 수정본

# P4 비평 단계
@dataclass
class Critique:
    draft: Draft
    scores: dict          # {"사실정확성": 25, "새로운관점": 16, ...}
    total: int            # 0~100
    weaknesses: list[str] # 반드시 3개
    improvement_guide: str

# P6 팩트체크 단계
@dataclass
class FactCheckResult:
    draft: Draft
    claims: list[dict]    # [{"text": ..., "status": "supported|inferred|unsupported", "quote": ...}]
    unsupported_count: int

# P7 SEO 단계
@dataclass
class SEOMeta:
    title_candidates: list[str]    # 5개
    meta_description: str
    tags: list[str]
    thumbnail_prompt: str
    internal_link_slots: list[str]

# 최종 발행 단위
@dataclass
class Post:
    draft: Draft
    fact_check: FactCheckResult
    seo: SEOMeta
    thumbnail_url: Optional[str]
    chosen_title: str
    slug: str
    status: str  # "ready" | "pending_review" | "published" | "rejected"
```

### 오케스트레이터 흐름 (`main.py`)

```python
import json, logging
from datetime import datetime
from pathlib import Path

from pipeline.config import cfg
from pipeline.collector import collect_articles
from pipeline.scorer import score_and_deduplicate
from pipeline.stages.p1_triage import p1_triage
from pipeline.stages.p2_synthesis import p2_synthesis
from pipeline.stages.p3_draft import p3_draft
from pipeline.stages.p4_critique import p4_critique
from pipeline.stages.p5_revise import p5_revise
from pipeline.stages.p6_factcheck import p6_factcheck
from pipeline.stages.p7_seo import p7_seo
from pipeline.image import generate_thumbnail
from pipeline.publisher import publish_to_wordpress
from pipeline.notifier import notify_slack

log = logging.getLogger(__name__)

def run_pipeline():
    run_date = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== 파이프라인 시작 {run_date} ===")

    # ── STAGE 1: 수집 ──────────────────────────────────────────────
    raw_articles = collect_articles()          # Tier1 + Tier2 수집
    log.info(f"수집: {len(raw_articles)}건")

    # ── STAGE 2: 점수 산정 + 중복 제거 ────────────────────────────
    scored = score_and_deduplicate(
        articles=raw_articles,
        history_path=cfg.POSTS_JSON,          # 기존 게시글/후보 주제 중복 비교
        confidence_threshold=0.75,
    )
    top20 = scored[:20]
    log.info(f"점수 산정 후 top20 후보 확정")

    # ── STAGE 3: P1 트리아지 (DeepSeek) ───────────────────────────
    selected_topics: list[SelectedTopic] = p1_triage(top20, top_k=cfg.DAILY_POST_COUNT)
    log.info(f"P1 선별 완료: {[t.article.title[:30] for t in selected_topics]}")

    results = []
    for topic in selected_topics:
        post = process_topic(topic, run_date)
        if post:
            results.append(post)

    log.info(f"=== 파이프라인 완료: {len(results)}편 처리 ===")


def process_topic(topic: SelectedTopic, run_date: str) -> "Post | None":
    """단일 토픽 → 완성 포스트. 실패 시 None 반환."""

    log.info(f"[{topic.article.title[:40]}] 처리 시작 (앵글: {topic.angle})")

    try:
        # ── P2: 팩트 합성 (DeepSeek) ───────────────────────────────
        facts = p2_synthesis(topic)

        # ── P3: 초안 작성 (GLM-5.1 thinking) ──────────────────────
        draft_v1 = p3_draft(facts, angle=topic.angle)

        # ── P4: 자체 비평 (DeepSeek) ───────────────────────────────
        critique = p4_critique(draft_v1, facts)
        log.info(f"P4 점수: {critique.total}/100")

        # ── 품질 분기 게이트 ────────────────────────────────────────
        if critique.total < 60:
            _handle_low_quality(topic, draft_v1, critique)
            return None

        if 60 <= critique.total < 75:
            # P3 재시도 1회
            log.info("점수 60~74: P3 재생성 시도")
            draft_v1 = p3_draft(facts, angle=topic.angle, critique_hint=critique)
            critique = p4_critique(draft_v1, facts)
            log.info(f"P4 재채점: {critique.total}/100")
            if critique.total < 75:
                _handle_pending(topic, draft_v1, critique)
                return None

        # ── P5: 수정본 (GLM-5.1 thinking) ──────────────────────────
        draft_v2 = p5_revise(draft_v1, critique)

        # ── P6: 팩트체크 (DeepSeek) ────────────────────────────────
        fact_check = p6_factcheck(draft_v2, facts)
        if fact_check.unsupported_count >= 3:
            log.warning(f"unsupported claim {fact_check.unsupported_count}건 → Slack 경고")
            notify_slack(event="factcheck_warning", topic=topic, fact_check=fact_check)

        # ── P7: SEO 메타 (DeepSeek) ────────────────────────────────
        seo = p7_seo(draft_v2)

        # ── 썸네일 생성 (Pollinations.ai) ──────────────────────────
        thumbnail_url = generate_thumbnail(seo.thumbnail_prompt)

        # ── 포스트 조립 ────────────────────────────────────────────
        post = Post(
            draft=draft_v2,
            fact_check=fact_check,
            seo=seo,
            thumbnail_url=thumbnail_url,
            chosen_title=seo.title_candidates[0],
            slug=_make_slug(seo.title_candidates[0]),
            status="ready",
        )

        # ── 초안 파일 저장 ─────────────────────────────────────────
        _save_post_file(post, run_date)

        # ── 발행 결정 ─────────────────────────────────────────────
        _publish_or_notify(post, critique)

        # ── 게시 이력 기록 ─────────────────────────────────────────
        _record_history(post, topic)

        return post

    except Exception as e:
        log.error(f"처리 실패: {e}")
        notify_slack(event="pipeline_error", topic=topic, error=str(e))
        return None


def _handle_low_quality(topic, draft, critique):
    """점수 < 60: Slack 즉시 알림, 자동 게시 불가."""
    _save_post_file_draft(draft, status="low_quality")
    notify_slack(event="low_quality", topic=topic, critique=critique)


def _handle_pending(topic, draft, critique):
    """두 번 시도 후에도 < 75: 보류 큐 저장."""
    _save_post_file_draft(draft, status="pending")
    pending = json.loads(Path(cfg.PENDING_JSON).read_text() or "[]")
    pending.append({"title": topic.article.title, "date": datetime.now().isoformat(),
                    "score": critique.total, "weaknesses": critique.weaknesses})
    Path(cfg.PENDING_JSON).write_text(json.dumps(pending, ensure_ascii=False, indent=2))
    notify_slack(event="pending", topic=topic, critique=critique)


def _publish_or_notify(post: Post, critique: Critique):
    """v1: Slack 알림 후 수동 승인. v2+: 75점+ 1시간 무반응 시 자동 발행."""
    notify_slack(event="ready", post=post, critique=critique)
    # v2에서 아래 활성화:
    # if cfg.AUTO_PUBLISH and critique.total >= 75:
    #     result = publish_to_wordpress(post)
    #     notify_slack(event="published", post=post, wp_link=result.link)
```

### 전체 데이터 흐름 요약

```
cron (매일 06:00)
    │
    ▼
collect_articles()  → List[Article]
    │
    ▼
score_and_deduplicate()  → List[ScoredArticle] top20
    │
    ▼
p1_triage()  → List[SelectedTopic]  (DeepSeek)
    │
    └─ 토픽별 반복 ─────────────────────────────────────┐
                                                        │
    ▼                                                   │
p2_synthesis(topic)  → SynthesizedFacts  (DeepSeek)   │
    │                                                   │
    ▼                                                   │
p3_draft(facts, angle)  → Draft v1  (GLM-5.1)         │
    │                                                   │
    ▼                                                   │
p4_critique(draft_v1, facts)  → Critique  (DeepSeek)  │
    │                                                   │
    ├─ total < 60  →  Slack 경고알림, 종료              │
    │                                                   │
    ├─ 60~74  →  p3_draft 재시도 1회 → p4_critique      │
    │              │                                    │
    │              └─ 재채점 < 75  →  pending.json 저장 │
    │                                                   │
    └─ total ≥ 75  ─────────────────────────────────── │
         │                                              │
         ▼                                              │
    p5_revise(draft_v1, critique)  → Draft v2  (GLM)  │
         │                                              │
         ▼                                              │
    p6_factcheck(draft_v2, facts)  → FactCheckResult  │
         │                                              │
         ▼                                              │
    p7_seo(draft_v2)  → SEOMeta  (DeepSeek)            │
         │                                              │
         ▼                                              │
    generate_thumbnail(seo.thumbnail_prompt)            │
         │                                              │
         ▼                                              │
    Post 조립 → posts/{date}-{slug}.md 저장             │
         │                                              │
         ▼                                              │
    Slack 알림 (v1: 수동 승인 대기)                      │
    [v2+] critique.total ≥ 75 → WordPress 자동 발행 ◄──┘
```

### 두 시스템의 관계

| | 자동화 파이프라인 (`main.py`) | `/blog-draft` 스킬 |
|--|------------------------------|-------------------|
| **트리거** | ofelia cron (매일 06:00) | 사람이 `/blog-draft <주제>` 입력 |
| **소스** | Tier1/2 자동 수집 | 사람이 직접 주제/URL 제공 |
| **초안** | DeepSeek P2 + GLM-5.1 P3 (자동) | 동일 (자동) |
| **리뷰** | P4 자체 채점만 (자동) | Claude Code + Codex + Gemini (3자) |
| **발행** | v1: Slack 알림 후 수동 / v2+: 자동 | WordPress 즉시 or 예약 |
| **용도** | 매일 1~2편 자동 생산 | 특정 주제 심층 포스트 요청 시 |

> **두 시스템은 독립적이며 보완 관계**: 파이프라인이 놓친 트렌드 토픽을 `/blog-draft`로 수동 보충.

---

## 해결해야 할 기술적 난제

| 난제 | 위험도 | 대응 방법 |
|------|--------|----------|
| **Gemini free tier 한도 변동** | 높음 | Flash → Flash-Lite fallback 체인, 모델별 한도 자동 감지 |
| **Self-critique 점수 인플레이션** | 높음 | "Find 3 weaknesses, no positive summary" 강제 지시 |
| **Fact-check false positive (~35%)** | 중간 | "exact quote 인용 필수" → 인용 불가 = unsupported 강제 |
| **중복 탐지 임계값 튜닝** | 중간 | 0.75 시작 → 실측 후 조정 (너무 낮으면 반복, 너무 높으면 reject 폭증) |
| **HuggingFace/Reddit 스크래핑 차단** | 중간 | User-Agent 명시 + 1초 간격 + ETag 캐시 |
| **WordPress REST API 연결 실패** | 중간 | Application Password 재발급 절차 문서화, retry 3회 + Slack 알림 |
| **구글 AI 대량생산 패턴 탐지** | 높음 | 글 길이 정규분포 샘플링, H2 개수 무작위화, 이미지/Mermaid 필수 |
| **장기 시리즈 일관성 부재** | 낮음 | v3에서 RAG로 해결 (v1/v2는 단독 포스트만) |

---

## 수익화 전략

### 월 운영 비용 vs 수익

| 항목 | 비용 |
|------|------|
| NVIDIA API (DeepSeek/GLM-5.1, 포스트 30편) | **~$3~5/월** (≈ 4,500~7,500원) |
| NAS 전기세 | ~2,000원/월 |
| 도메인 (선택) | ~1만원/월 |
| WordPress 호스팅 | 별도 (이미 있으면 0원) |
| **총 비용** | **~1.5~2만원/월** |

| 수익 경로 | 조건 | 예상 (6개월 후) |
|----------|------|----------------|
| Google AdSense | 월 1만 PV 이상 | 5~30만원 |
| 카카오 AdFit (보조) | 국내 트래픽 집중 시 | 1~5만원 |
| 제휴마케팅 (AWS/쿠팡) | 도구 추천 포스트 | 1~10만원 |
| 뉴스레터 유료화 | 구독자 500명+ | 0~50만원 |

> **현실적 수익화 경로**: 광고 수익보다 **개발자 브랜드 → 강의/컨설팅/스폰서십** 이 6~12개월 후 더 빠르게 발생. 런타임 비용 0원이므로 손익분기 즉시 달성.

---

## 검증 기준

| 시점 | 기준 |
|------|------|
| v1 완료 (2주) | 파이프라인이 하루 2건 이상 Slack에 초안 전달 |
| v2 완료 (4주) | ≥75점 포스트 70% 이상 자동 게시 |
| Month 2 | 첫 20편 발행, 구글 색인 100% |
| Month 3 | 월 500+ PV, AdSense 신청 |
| Month 6 | 수익 > 도메인 비용 |

---

## 리뷰 변경 로그

- **2026-04-28 v1** — Gemini 리뷰 반영: Tistory 확정, Python 확정, Insight 프롬프트 모드 추가
- **2026-04-28 v2** — 무료티어 재설계: Gemini API + Pollinations.ai + Discord + GitHub JSON
- **2026-04-28 v3** — 심층 연구 반영
  - 7단계 LLM 파이프라인 (P1~P7) 설계
  - 품질 루브릭 6차원 + 자동 분기 게이트
  - API 호출 수 계산 (Pro 병목 확인, 일 1~2편 기준 free tier 여유)
  - Tier 1+2 크로스 소스 신호 점수식
  - v1→v2→v3 단계적 자동화 로드맵
  - 8개 기술 난제 목록화
- **2026-04-28 v4** — 플랜 완성 (누락 섹션 4개 추가)
  - 프로젝트 파일 구조, 환경변수 목록, P1~P7 프롬프트 전문, Discord UX
- **2026-04-28 v5.2** — 오케스트레이터 연결 설계 추가
  - `models.py` 스테이지 간 데이터 계약 (Article→ScoredArticle→SelectedTopic→SynthesizedFacts→Draft→Critique→FactCheckResult→SEOMeta→Post)
  - `main.py` 오케스트레이터 전체 흐름 (collect→score→P1→P2→P3→P4→gate→P5→P6→P7→image→publish)
  - P4 품질 분기 게이트 코드 (< 60 / 60~74 재시도 / ≥75 통과)
  - 파일 구조 NAS Docker 기준으로 재정비 (stages/ 서브디렉토리 추가)
  - 자동화 파이프라인 vs `/blog-draft` 스킬 역할 비교표
- **2026-04-28 v5.1** — 잔여 참조 정정
  - Discord UX → Slack Block Kit 포맷으로 교체 (v1 단순화 안내 추가)
  - P7 "Tistory + 구글" → "WordPress + 구글"
  - 기술 난제: Tistory OAuth → WordPress REST API 연결 실패로 교체
  - 비용표: API 0원 → NVIDIA API 실비 반영 (~$3~5/월)
- **2026-04-28 v5** — 핵심 설계 변경
  - LLM 엔진: Gemini free API → **DeepSeek V4 Pro + GLM-5.1 (NVIDIA API)**
  - 모델 역할: DeepSeek=분석/검증(P1/2/4/6/7) / GLM-5.1=작문(P3/5, thinking 활성화)
  - 블로그 플랫폼: Tistory → **WordPress** (REST API + Application Password)
  - 알림: Discord → **Slack Incoming Webhook**
  - 인프라: GitHub Actions → **NAS Docker + ofelia** (온프레미스)
  - 중복 탐지: DeepSeek 주제 중복 판정
  - blog-draft 초안 작성자: Claude → **DeepSeek/GLM-5.1**, 리뷰어: Claude Code + Codex + Gemini

## 리뷰 요약

| 항목 | Codex | Gemini | Advisor (Opus) |
|------|-------|--------|----------------|
| 전체 평가 | (응답 실패) | APPROVE_WITH_CHANGES | 단계적 구축 권장 |
| 핵심 우려 | — | 이미지 부재, AdSense 어려움 | Self-critique 인플레이션, Pro 호출 병목 |
| 핵심 제안 | — | Tistory, Insight 프롬프트 | 1회 critique 루프, 크로스 소스 점수식 |
