# bullro — AI 기술 블로그 자동화 파이프라인

> 크롤링 → 주제 선택 → 초안 → 품질 게이트 → WordPress 발행까지 자동화.  
> DeepSeek V4 Pro + GLM-5.1 (NVIDIA API) 기반, 포스트당 ~$0.05.

---

## 아키텍처

```
NAS Docker cron (매일 06:00 KST)
        │
        ▼
  Tier1/2 수집 ──────────────────────────────────────
  arxiv / GitHub / 공식 블로그 / HN / Reddit / HF / PwC
        │
        ▼
  복합 신호 점수 (z-score) + 중복 제거 (sentence-transformers)
        │ top 20
        ▼
  P1 Triage (DeepSeek) ── top 1~3 토픽 선별
        │
        ▼ (토픽별 반복)
  P2 Synthesis  (DeepSeek)     팩트 구조화
  P3 Draft      (GLM-5.1 🧠)  초안 v1
  P4 Critique   (DeepSeek)     루브릭 채점 0~100
        ├─ < 60  → Slack 경고 알림
        ├─ 60~74 → P3 재시도 1회 → 재채점 → 보류 큐
        └─ ≥ 75  →
  P5 Revise     (GLM-5.1 🧠)  수정본 v2
  P6 Fact-Check (DeepSeek)     unsupported 주장에 [추정] 태그
  P7 SEO Meta   (DeepSeek)     제목 5안 / 태그 / 썸네일 프롬프트
        │
        ▼
  Pollinations.ai 썸네일 (무료 FLUX)
        │
        ▼
  Slack 알림 → 수동 승인 (v1) / 자동 발행 (v2+, AUTO_PUBLISH=true)
        │
        ▼
  WordPress REST API 발행
```

---

## 모델 역할 분담

| 모델 | 역할 | 스테이지 |
|------|------|---------|
| **DeepSeek V4 Pro** | 분석·분류·검증 (thinking 비활성화, 빠름) | P1, P2, P4, P6, P7 |
| **GLM-5.1** | 창작·작문 (thinking 활성화 🧠) | P3, P5 |

---

## 파일 구조

```
bullro/
├── pipeline/
│   ├── main.py              오케스트레이터 (전체 흐름 연결)
│   ├── config.py            환경변수 & 경로 관리
│   ├── models.py            스테이지 간 데이터 계약 (8개 dataclass)
│   ├── llm.py               NVIDIA API 클라이언트
│   ├── collector.py         Tier1/2 소스 수집
│   ├── scorer.py            z-score 점수 + 중복 제거
│   ├── image.py             Pollinations.ai 썸네일
│   ├── publisher.py         WordPress REST API
│   ├── notifier.py          Slack Incoming Webhook
│   └── stages/
│       ├── p1_triage.py     DeepSeek: top-k 토픽 선별
│       ├── p2_synthesis.py  DeepSeek: 팩트 구조화
│       ├── p3_draft.py      GLM-5.1: 초안 작성
│       ├── p4_critique.py   DeepSeek: 루브릭 채점
│       ├── p5_revise.py     GLM-5.1: 수정본
│       ├── p6_factcheck.py  DeepSeek: 팩트체크
│       └── p7_seo.py        DeepSeek: SEO 메타
├── data/
│   ├── posts.json           게시 이력 DB
│   ├── pending.json         보류 큐 (60~74점)
│   └── sources_watchlist.json  GitHub 저장소 목록 (30개 기본)
├── .claude/skills/
│   └── blog-draft/SKILL.md  /blog-draft 스킬 (수동 포스트용)
├── plan/
│   └── 2026-04-28-ai-blog-pipeline.md  설계 문서
├── Dockerfile
├── docker-compose.yml
├── scheduler.ini            ofelia cron 설정
└── requirements.txt
```

---

## 빠른 시작

### 1. 환경 설정

```bash
cp .env.example .env
# .env 편집: NVIDIA_API_KEY, WORDPRESS_*, SLACK_WEBHOOK_URL 입력
```

### 2. NAS Docker 실행

```bash
docker compose build
docker compose up -d
```

ofelia가 **매일 UTC 21:00 (KST 06:00)** 에 파이프라인을 자동 실행합니다.

### 3. 수동 즉시 실행

```bash
docker compose run --rm pipeline python -m pipeline.main
```

---

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `NVIDIA_API_KEY` | ✅ | NVIDIA API 키 (`nvapi-...`) |
| `WORDPRESS_URL` | ✅ | WordPress 사이트 URL |
| `WORDPRESS_USERNAME` | ✅ | WordPress 관리자 계정 |
| `WORDPRESS_APP_PASSWORD` | ✅ | Application Password (관리자 > 프로필) |
| `SLACK_WEBHOOK_URL` | 권장 | Incoming Webhook URL |
| `GITHUB_TOKEN` | 선택 | GitHub API PAT (소스 수집 강화) |
| `DAILY_POST_COUNT` | 선택 | 일 최대 포스트 수 (기본: 2) |
| `AUTO_PUBLISH` | 선택 | `true` 시 75점 이상 자동 발행 (기본: false) |

---

## `/blog-draft` 스킬 (수동 포스트)

Claude Code CLI에서 특정 주제/URL로 즉시 포스트 작성:

```
/blog-draft GPT-5 공개 — 한국 개발자가 알아야 할 것들
/blog-draft https://arxiv.org/abs/2501.12345
/blog-draft GLM-5.1 NVIDIA API 무료 사용법
```

DeepSeek이 팩트를 합성하고 GLM-5.1이 초안을 작성합니다.  
Claude Code + Codex + Gemini가 3자 리뷰 후 최종고를 WordPress에 업로드합니다.

---

## 품질 루브릭

| 차원 | 가중치 |
|------|--------|
| 사실 정확성 | 30점 |
| 새로운 관점 | 20점 |
| 기술 깊이 | 15점 |
| 가독성 | 15점 |
| 독창성 | 10점 |
| SEO 구조 | 10점 |

- **≥ 75점**: 발행 (`AUTO_PUBLISH=true` 시 자동, 기본은 Slack 알림)
- **60~74점**: P3 재생성 1회 → 재채점 → 그래도 미달 시 보류 큐
- **< 60점**: Slack 즉시 경고, 자동 발행 불가

---

## 비용

| 항목 | 비용 |
|------|------|
| NVIDIA API (포스트 30편/월) | ~$3~5 |
| NAS 전기세 | ~2,000원 |
| Pollinations.ai 이미지 | 무료 |
| sentence-transformers (로컬) | 무료 |
| **합계** | **~1.5~2만원/월** |

---

## 로드맵

- **v1 (현재)**: 파이프라인 작동 + Slack 알림 → 수동 승인 후 발행
- **v2**: `AUTO_PUBLISH=true` — 75점+ 자동 발행, Slack App 버튼 인터랙션
- **v3**: GA4/Search Console 피드백 → 루브릭 가중치 자동 보정

---

## 라이선스

MIT
