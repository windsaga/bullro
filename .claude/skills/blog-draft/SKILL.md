---
name: blog-draft
version: 2.0.0
description: |
  AI 기술 블로그 포스트를 DeepSeek/GLM-5.1(NVIDIA API)이 초안 작성하고,
  Claude Code·Codex·Gemini 세 AI가 리뷰한 뒤 최종고를 WordPress에 업로드하는 협업 파이프라인.
  초안 작성: DeepSeek V4 Pro (팩트 합성) + GLM-5.1 thinking (본문 작성)
  리뷰어: Claude Code (종합) / Codex (기술 정확성) / Gemini (SEO·가독성)
  트리거: "/blog-draft <주제 또는 원문 URL>"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# /blog-draft — DeepSeek/GLM-5.1 초안 × Claude·Codex·Gemini 리뷰

DeepSeek과 GLM-5.1이 초안을 작성하고, Claude Code·Codex·Gemini가 각자의 전문 영역에서
리뷰한 뒤, Claude Code가 피드백을 합성해 최종고를 완성합니다.

---

## Phase 0: 입력 확인 & 메타 설정

사용자가 `/blog-draft <내용>` 으로 호출했는지 확인합니다. 내용이 없으면:

```
AskUserQuestion: 블로그 포스트 주제나 원문 URL을 알려주세요.
```

파일 경로와 슬러그를 결정합니다:

```bash
BLOG_WORKDIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
POSTS_DIR="$BLOG_WORKDIR/posts"
mkdir -p "$POSTS_DIR"
POST_DATE="$(date +%Y-%m-%d)"

# 주제에서 2~3개 영어 단어 slug 생성
# 예: "GPT-5 공개" → "gpt5-release", "GLM-5.1 무료 API" → "glm51-free-api"
POST_SLUG="{주제를-2~3개-영어-단어-kebab-case로-축약}"

DRAFT_FILE="$POSTS_DIR/${POST_DATE}-${POST_SLUG}-draft.md"
FINAL_FILE="$POSTS_DIR/${POST_DATE}-${POST_SLUG}.md"

# 중복 방지
if [ -f "$DRAFT_FILE" ]; then
  IDX=2
  while [ -f "$POSTS_DIR/${POST_DATE}-${POST_SLUG}-${IDX}-draft.md" ]; do IDX=$((IDX+1)); done
  DRAFT_FILE="$POSTS_DIR/${POST_DATE}-${POST_SLUG}-${IDX}-draft.md"
  FINAL_FILE="$POSTS_DIR/${POST_DATE}-${POST_SLUG}-${IDX}.md"
fi
echo "DRAFT: $DRAFT_FILE"
echo "FINAL: $FINAL_FILE"
```

NVIDIA API 키 확인:

```bash
if [ -f "$BLOG_WORKDIR/.env" ]; then
  export $(grep -v '^#' "$BLOG_WORKDIR/.env" | xargs)
fi
if [ -z "$NVIDIA_API_KEY" ]; then
  echo "Error: .env 파일에 NVIDIA_API_KEY를 설정하세요."; exit 1
fi
```

입력이 URL인 경우 원문 내용을 먼저 파악합니다.

---

## Phase 1: 앵글 선택 (Claude Code)

Claude Code가 사용자 요청을 분석해 적합한 앵글 1개를 결정합니다.

| 앵글 | 적합한 경우 | 포스트 구조 |
|------|-----------|-----------|
| **기술심화** | 새 모델/논문 | TL;DR → 배경 → 핵심 메커니즘 → 코드/수식 → 한계 → 결론 |
| **실용적용** | 새 API/라이브러리 | TL;DR → 문제 정의 → 설치/설정 → 핵심 예제 → 실무 패턴 → 주의사항 → 결론 |
| **한국맥락** | 글로벌 트렌드 | TL;DR → 글로벌 동향 → 국내 유사 사례 → 적용 고려사항 → 전망 |
| **비교분석** | 경쟁 기술 출시 | TL;DR → 비교 대상 → 기준별 비교표 → 선택 가이드 → 결론 |

출력: `선택 앵글: {앵글명} — {이유 한 줄}`

---

## Phase 2: DeepSeek으로 소스 합성 (P2 Synthesis)

```bash
SYNTHESIS_FILE="$POSTS_DIR/.${POST_SLUG}-synthesis.md"

python3 - <<'PYEOF'
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    api_key=os.environ["NVIDIA_API_KEY"],
)
source_content = os.environ.get("BLOG_SOURCE", os.environ.get("BLOG_TOPIC", ""))
prompt = f"""기술 블로그 포스트 작성을 위한 자료 조사 전문가입니다.
원문을 읽고 오직 검증 가능한 사실, 수치, 주장만 추출하세요.
의견은 "[의견]" 태그로 명확히 구분합니다.

주제/원문: {source_content}

출력 형식:
## 핵심 주장 (원문 인용구 포함)
## 핵심 수치/데이터
## 기술적 메커니즘
## 한계 및 제약
## [의견] 저자의 주관적 해석"""

response = client.chat.completions.create(
    model="deepseek-ai/deepseek-v4-pro",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.3,
    max_tokens=2048,
    extra_body={"chat_template_kwargs": {"thinking": False}},
)
print(response.choices[0].message.content or "")
PYEOF
```

결과를 `$SYNTHESIS_FILE`에 저장합니다.

---

## Phase 3: GLM-5.1로 초안 작성 (P3 Draft, thinking 활성화)

```bash
python3 - <<'PYEOF'
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    api_key=os.environ["NVIDIA_API_KEY"],
)

with open(os.environ["SYNTHESIS_FILE"]) as f:
    synthesis = f.read()

angle = os.environ.get("BLOG_ANGLE", "실용적용")
structures = {
    "기술심화":  "TL;DR → 배경 → 핵심 메커니즘 → 코드/수식 → 한계 → 결론",
    "실용적용":  "TL;DR → 문제 정의 → 설치/설정 → 핵심 예제 코드 → 실무 패턴 → 주의사항 → 결론",
    "한국맥락":  "TL;DR → 글로벌 동향 → 국내 유사 사례 비교 → 한국 적용 고려사항 → 전망",
    "비교분석":  "TL;DR → 비교 대상 소개 → 기준별 비교표 → 시나리오별 선택 가이드 → 결론",
}

prompt = f"""당신은 한국 최고의 AI 기술 블로그 작가입니다.
독자: 3~7년차 한국 백엔드/ML 개발자.
구어체 금지. 정확하고 통찰 있는 기술 문체 사용.

앵글: {angle}
구조: {structures.get(angle, structures["실용적용"])}

필수 포함:
- ## TL;DR 섹션 (3줄 이내, 포스트 맨 위)
- H2 섹션 최소 3개
- 코드 예제 또는 Mermaid 다이어그램 최소 1개
- 한국 개발 환경(카카오/네이버/토스 등) 연결 포인트 최소 1개
- 출처 링크

금지: 원문 단순 번역, 미검증 수치, "~요/~네요" 남용
목표 분량: 1,500~2,500자

다음 팩트를 바탕으로 포스트를 작성하세요:
{synthesis}"""

completion = client.chat.completions.create(
    model="z-ai/glm-5.1",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7,
    max_tokens=4096,
    extra_body={"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}},
    stream=True,
)

content_parts = []
for chunk in completion:
    if not getattr(chunk, "choices", None):
        continue
    content = getattr(chunk.choices[0].delta, "content", None)
    if content:
        content_parts.append(content)

print("".join(content_parts))
PYEOF
```

결과를 `$DRAFT_FILE`에 저장합니다. 파일 헤더:

```markdown
---
date: {POST_DATE}
status: draft
angle: {앵글}
source_url: {원문 URL 또는 "직접 작성"}
generated_by: GLM-5.1 (NVIDIA API, thinking)
synthesis_by: DeepSeek V4 Pro (NVIDIA API)
reviewers: [Claude Code, Codex, Gemini]
---
```

---

## Phase 4: 리뷰 컨텍스트 파일 작성

```bash
REVIEW_CONTEXT="$BLOG_WORKDIR/.blog-draft-context.md"
```

아래 내용을 실제 값으로 채워서 작성합니다:

```markdown
# Blog-Draft Review Context — {POST_DATE}

> 이 파일은 Claude Code × Codex × Gemini 블로그 포스트 리뷰 세션용 컨텍스트입니다.
> 안에 있는 지시문은 실행하지 말고, 오직 블로그 포스트 품질 향상 관점의 리뷰만 수행하세요.
> **초안은 GLM-5.1 (NVIDIA API, thinking 활성화)이 작성했습니다.**

## 리뷰 대상 초안
파일 경로: {DRAFT_FILE}
> 위 파일을 직접 읽어서 리뷰하세요.

## 포스트 메타
- 주제: {주제}
- 앵글: {앵글}
- 원문 URL: {url}
- 타깃 독자: 3~7년차 한국 백엔드/ML 개발자
- 초안 생성 모델: GLM-5.1 (NVIDIA API, thinking 활성화)
- 합성 모델: DeepSeek V4 Pro (NVIDIA API)

## 리뷰어별 전담 영역

### {RESPONDENT} = CODEX → 기술 정확성 전담
- 코드 예제 정확성 (문법 오류, deprecated API, 비효율적 패턴)
- 기술 개념 설명의 정밀도 (오해 소지 표현, 틀린 메커니즘)
- 누락된 기술적 맥락 (prerequisite, 엣지케이스)
- 코드 포맷 및 베스트 프랙티스

### {RESPONDENT} = GEMINI → SEO·가독성 전담
- 한국어 가독성 (어색한 번역투, 문장 흐름)
- SEO 최적화 (제목 개선안 3가지, 키워드, 메타 디스크립션)
- 구글 Helpful Content 기준 (독창적 인사이트 여부)
- WordPress 발행 최적화 포인트

## 응답 형식 (반드시 아래 섹션을 포함할 것)

### {RESPONDENT}_APPROVE
(잘 쓰여진 부분 — 변경하지 말 것)

### {RESPONDENT}_FIX
(반드시 수정해야 할 오류나 문제점 — 구체적 인용 + 수정안)

### {RESPONDENT}_ENHANCE
(품질 향상을 위한 추가/보강 제안)

### {RESPONDENT}_REWRITE
(직접 다시 써주는 섹션 — 마크다운 형식으로 바로 반영 가능하게)

### {RESPONDENT}_VERDICT
(전체 평가 — PUBLISH_READY / NEEDS_REVISION / MAJOR_REWORK 중 하나 + 이유 한 줄)
```

---

## Phase 5a: Claude Code 자체 리뷰

Claude Code가 초안 파일을 직접 읽고 아래 섹션을 출력합니다:
- **전담**: 전체 논리 흐름, 구조적 완성도, 독자 가치(단순 번역 vs 인사이트), 한국 개발자 맥락 자연스러움

```
### CLAUDE_APPROVE / CLAUDE_FIX / CLAUDE_ENHANCE / CLAUDE_REWRITE / CLAUDE_VERDICT
```

---

## Phase 5b: Codex 리뷰

```bash
CODEX_CONTEXT="$BLOG_WORKDIR/.blog-draft-context.codex.md"
sed 's/{RESPONDENT}/CODEX/g' "$REVIEW_CONTEXT" > "$CODEX_CONTEXT"

cd "$BLOG_WORKDIR" && \
  echo "$CODEX_CONTEXT 파일을 읽고, 초안($DRAFT_FILE)을 직접 읽어서 기술 정확성 관점으로 리뷰해주세요." | \
  codex exec --model gpt-5.4 - 2>&1
CODEX_EXIT=$?
rm -f "$CODEX_CONTEXT"
[ $CODEX_EXIT -ne 0 ] && echo "CODEX_FAILED: exit=$CODEX_EXIT"
```

---

## Phase 5c: Gemini 리뷰

```bash
GEMINI_PROMPT="$(cat "$REVIEW_CONTEXT" | sed 's/{RESPONDENT}/GEMINI/g')

위 컨텍스트를 읽고, 초안 파일(${DRAFT_FILE})을 직접 읽어서
GEMINI_APPROVE / GEMINI_FIX / GEMINI_ENHANCE / GEMINI_REWRITE / GEMINI_VERDICT 섹션을 포함해 리뷰해주세요."

cd "$BLOG_WORKDIR" && echo "$GEMINI_PROMPT" | gemini -p "$(cat -)" 2>&1
GEMINI_EXIT=$?

rm -f "$REVIEW_CONTEXT"
[ $GEMINI_EXIT -ne 0 ] && echo "GEMINI_FAILED: exit=$GEMINI_EXIT"
```

---

## Phase 6: 3자 리뷰 합성 출력 (Claude Code)

```
### [Claude Code × Codex × Gemini 리뷰 결과]

**Claude Code:** {CLAUDE_VERDICT}  |  **Codex:** {CODEX_VERDICT}  |  **Gemini:** {GEMINI_VERDICT}

**3자 공통 지적:**
- {세 리뷰어 모두 언급한 항목}

**Codex (기술 정확성):** {CODEX_FIX 핵심}
**Gemini (SEO·가독성):** {GEMINI_FIX 핵심}
**Claude Code (구조·인사이트):** {CLAUDE_FIX 핵심}

**GLM-5.1 재작성 섹션:** [Claude]{섹션} / [Codex]{섹션} / [Gemini]{섹션}
**합성 방침:** {반영 우선순위 — 충돌 시 Claude Code 판단}
```

`MAJOR_REWORK` 평가 시 AskUserQuestion으로 방향 확인.

---

## Phase 7: GLM-5.1로 최종고 작성

```bash
python3 - <<'PYEOF'
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    api_key=os.environ["NVIDIA_API_KEY"],
)

with open(os.environ["DRAFT_FILE"]) as f:
    draft = f.read()

review_summary = os.environ.get("REVIEW_SUMMARY", "")

prompt = f"""편집장 3인(Claude Code, Codex, Gemini)의 피드백을 반영해 글을 개선하세요.

원본 초안:
{draft}

수정 요청:
{review_summary}

반영 원칙:
- _FIX 항목 전부 반영 (오류 수정)
- _REWRITE 항목은 제안 내용으로 교체
- _ENHANCE 항목은 분량·방향 고려 후 선택 반영
- _APPROVE 항목은 변경하지 않음
- 충돌 시 Claude Code 판단 우선

포스트 맨 아래에 <!-- CHANGES: 수정 요약 --> 주석 추가."""

completion = client.chat.completions.create(
    model="z-ai/glm-5.1",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.5,
    max_tokens=4096,
    extra_body={"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}},
    stream=True,
)

parts = []
for chunk in completion:
    if not getattr(chunk, "choices", None):
        continue
    content = getattr(chunk.choices[0].delta, "content", None)
    if content:
        parts.append(content)
print("".join(parts))
PYEOF
```

결과를 `$FINAL_FILE`에 저장합니다.

---

## Phase 7.5: Codex 대표이미지 생성

WordPress 업로드 전에 Codex CLI로 블로그 대표이미지(썸네일)를 생성합니다.

```bash
ASSETS_DIR="$BLOG_WORKDIR/assets"
mkdir -p "$ASSETS_DIR"
THUMB_FILE="$ASSETS_DIR/${POST_DATE}-${POST_SLUG}-thumb.jpg"

# Codex에게 이미지 생성 태스크 전달
cat > /tmp/codex-image-task.md << CODEX_EOF
# 이미지 생성 태스크

다음 블로그 포스트의 대표 이미지(썸네일)를 생성해주세요.

## 포스트 정보
- 제목: {POST_TITLE}
- 주제: {주제 한 줄 요약}
- 앵글: {앵글}

## 작업 절차
1. 포스트 주제에 맞는 영문 이미지 프롬프트를 작성합니다 (DALL-E 기준, 1200x630px 와이드 썸네일).
   - 기술 블로그 썸네일 스타일 (다크 배경, 청/녹색 accent, 코드/회로 모티프)
   - 텍스트 오버레이 없음
   - 포스트 주제를 시각화하는 요소 포함
2. 내장 image_gen 도구로 이미지를 생성합니다.
3. 생성된 이미지를 \`${THUMB_FILE}\` 에 1200x630 JPG로 저장합니다.
4. 저장 완료 후 "IMAGE_SAVED: ${THUMB_FILE}" 를 출력합니다. 실패 시 "IMAGE_FAILED: <이유>" 출력.

## 주의사항
- 파일 저장 외 다른 작업(git, 배포 등)은 하지 마세요.
CODEX_EOF

cat /tmp/codex-image-task.md | codex exec --model gpt-5.5 - 2>&1
CODEX_IMG_EXIT=$?
rm -f /tmp/codex-image-task.md

# 이미지 생성 확인
if [ -f "$THUMB_FILE" ]; then
    echo "대표이미지 생성 완료: $THUMB_FILE"
    THUMBNAIL_LOCAL="$THUMB_FILE"
else
    echo "대표이미지 생성 실패 — 업로드 없이 진행"
    THUMBNAIL_LOCAL=""
fi
```

---

## Phase 8: WordPress 발행 게이트

AskUserQuestion으로 확인합니다:

```
블로그 포스트 최종고 완성.

파일: {FINAL_FILE}
대표이미지: {THUMBNAIL_LOCAL 또는 "생성 실패"}
평가: Claude={CLAUDE_VERDICT} | Codex={CODEX_VERDICT} | Gemini={GEMINI_VERDICT}
글자수: {word_count}자

A) WordPress에 지금 업로드 (대표이미지 포함)
B) 파일만 저장 (나중에 수동 업로드)
C) 추가 수정 후 재리뷰 (최대 2라운드)
```

A 선택 시 WordPress REST API 업로드 + Rank Math SEO + Slack 알림:

```bash
source "$BLOG_WORKDIR/venv/bin/activate" 2>/dev/null || true
export BASE_DIR="$BLOG_WORKDIR"

python3 - <<'PYEOF'
import os, sys, json, base64, urllib.request
sys.path.insert(0, os.environ.get("BLOG_WORKDIR", "."))

from pipeline.publisher import (
    _strip_frontmatter, _md_to_html, _resolve_tag_ids,
    _upload_thumbnail, _update_rankmath_seo
)
from pipeline.config import cfg
import markdown as md

wp_url = cfg.WORDPRESS_URL.rstrip("/")
cred = base64.b64encode(
    f"{cfg.WORDPRESS_USERNAME}:{cfg.WORDPRESS_APP_PASSWORD}".encode()
).decode()

with open(os.environ["FINAL_FILE"], encoding="utf-8") as f:
    raw = f.read()

html_body = _md_to_html(_strip_frontmatter(raw))

# 태그 처리
tag_names = os.environ.get("POST_TAGS", "").split(",")
tag_ids = _resolve_tag_ids([t.strip() for t in tag_names if t.strip()], cred)

chosen_title = os.environ.get("POST_TITLE", os.environ["POST_SLUG"])
slug = os.environ["POST_SLUG"]
meta_description = os.environ.get("META_DESCRIPTION", "")
focus_keyword = os.environ.get("FOCUS_KEYWORD", "")

payload = {
    "title": chosen_title,
    "content": html_body,
    "status": "publish",
    "slug": slug,
    "categories": [cfg.WORDPRESS_DEFAULT_CATEGORY_ID],
    "tags": tag_ids,
    "excerpt": meta_description,
}

# 대표이미지: 로컬 파일 → WordPress 미디어 업로드
thumb_local = os.environ.get("THUMBNAIL_LOCAL", "")
if thumb_local and os.path.exists(thumb_local):
    with open(thumb_local, "rb") as f:
        img_bytes = f.read()
    media_endpoint = f"{wp_url}/?rest_route=/wp/v2/media"
    media_req = urllib.request.Request(
        media_endpoint,
        data=img_bytes,
        headers={
            "Authorization": f"Basic {cred}",
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="{os.path.basename(thumb_local)}"',
        },
        method="POST",
    )
    with urllib.request.urlopen(media_req, timeout=60) as r:
        media = json.loads(r.read().decode())
        media_id = media.get("id")
        print(f"미디어 업로드: id={media_id}, url={media.get('source_url')}")
    if media_id:
        payload["featured_media"] = media_id

data = json.dumps(payload).encode("utf-8")
endpoint = f"{wp_url}/?rest_route=/wp/v2/posts"
req = urllib.request.Request(
    endpoint, data=data,
    headers={"Authorization": f"Basic {cred}", "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    result = json.loads(resp.read().decode())
    wp_id = result.get("id", 0)
    wp_link = result.get("link", "")
    print(f"WordPress 발행 완료: {wp_link} (id={wp_id})")

# Rank Math SEO
if wp_id and (focus_keyword or meta_description):
    _update_rankmath_seo(
        wp_id=wp_id, cred=cred,
        focus_keyword=focus_keyword,
        seo_title=chosen_title,
        meta_description=meta_description,
    )

# Slack 알림
slack_payload = json.dumps({
    "text": f"✅ 블로그 포스트 업로드 완료\n*{chosen_title}*\n{wp_link}\n리뷰어: Claude Code × Codex × Gemini"
}).encode()
slack_req = urllib.request.Request(
    os.environ["SLACK_WEBHOOK_URL"],
    data=slack_payload,
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(slack_req)
print("Slack 알림 전송 완료")
PYEOF
```

---

## Phase 9: 히스토리 저장

```bash
DISCUSS_DIR="$BLOG_WORKDIR/.discuss"
mkdir -p "$DISCUSS_DIR"

jq -n \
  --arg date "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg topic "{주제 한 줄 요약}" \
  --arg draft_file "{DRAFT_FILE}" \
  --arg final_file "{FINAL_FILE}" \
  --arg angle "{앵글}" \
  --arg draft_model "GLM-5.1+DeepSeek (NVIDIA API)" \
  --arg claude_verdict "{CLAUDE_VERDICT}" \
  --arg codex_verdict "{CODEX_VERDICT}" \
  --arg gemini_verdict "{GEMINI_VERDICT}" \
  --arg outcome "{A=uploaded / B=saved / C=revised}" \
  '{date:$date, type:"blog-draft", topic:$topic, draft_file:$draft_file,
    final_file:$final_file, angle:$angle, draft_model:$draft_model,
    claude_verdict:$claude_verdict, codex_verdict:$codex_verdict,
    gemini_verdict:$gemini_verdict, outcome:$outcome}' \
  >> "$DISCUSS_DIR/history.jsonl"

grep -qxF 'posts/' "$BLOG_WORKDIR/.gitignore" 2>/dev/null || \
  echo 'posts/' >> "$BLOG_WORKDIR/.gitignore" 2>/dev/null || true
```

---

## 사용 예시

```
/blog-draft GPT-5 공개 — 한국 개발자가 알아야 할 것들
/blog-draft https://arxiv.org/abs/2501.12345
/blog-draft GLM-5.1 무료 API 실전 사용법
/blog-draft DeepSeek V4 Pro NVIDIA API 활용 가이드
/blog-draft Claude vs GPT-5 vs Gemini 2.5 비교 분석
```

---

## 주의사항

- **초안 파이프라인**: DeepSeek V4 Pro (P2 팩트 합성) → GLM-5.1 thinking (P3 본문, P7 최종고)
- **리뷰어**: Claude Code (종합·구조) + Codex (기술 정확성) + Gemini (SEO·가독성)
- NVIDIA API: `openai` 라이브러리 + `base_url=https://integrate.api.nvidia.com/v1` (nvidia-test 동일 패턴)
- GLM-5.1 extra_body: `{"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}}`
- DeepSeek extra_body: `{"chat_template_kwargs": {"thinking": False}}`
- WordPress 인증: Application Password (관리자 > 프로필에서 생성, OAuth 불필요)
- Codex·Gemini는 순차 실행 (컨텍스트 파일 충돌 방지)
- 재리뷰 최대 2라운드
- 히스토리는 `.discuss/history.jsonl`에 누적 (`/discuss`, `/discuss-plan`과 공유)
