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

## Phase 7.3: DeepSeek SEO 메타 생성 + 카테고리 결정

최종고를 기반으로 SEO 메타데이터(제목 후보·키워드·태그·썸네일 프롬프트·시리즈)를 생성하고
WordPress 카테고리를 자동 결정합니다.

```bash
SEO_JSON_FILE="$POSTS_DIR/.${POST_SLUG}-seo.json"

python3 - <<'PYEOF'
import os, json
from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    api_key=os.environ["NVIDIA_API_KEY"],
)

with open(os.environ["FINAL_FILE"], encoding="utf-8") as f:
    final_content = f.read()

draft_summary = final_content[:3000]

SYSTEM = """당신은 한국 검색 SEO 전문가입니다.
WordPress + 구글 검색 기준으로 메타 정보를 작성합니다.
오직 JSON만 출력하고 다른 텍스트는 쓰지 마세요."""

prompt = f"""다음 포스트 내용을 분석하여 SEO 메타 정보를 생성하세요.

포스트 내용 (요약):
{draft_summary}

## 포커스 키워드 규칙
1. focus_keyword: 독자가 검색할 핵심 키워드 (한국어, 2~4단어)
2. focus_keyword_slug: 포커스 키워드의 영어 URL 슬러그 (소문자, 하이픈, 3~6단어)
3. title_candidates: 모든 제목 후보에 focus_keyword를 반드시 포함, 55자 이내
4. meta_description: focus_keyword를 앞부분에 포함, 90~150자, 클릭 유도

## 검색형 제목 규칙
- 모델명·도구명 등 고유명사를 제목 앞쪽에 배치
- "방법", "설정", "비교", "설치", "사용법", "가이드" 중 하나 이상 포함
- "~에 대해", "~을 알아보겠습니다" 같은 리포트 투 금지

## 카테고리 분류 (category_type 필드)
- "paper_review": 논문·arxiv·연구·벤치마크·학습·fine-tuning 관련
- "dev_tools": 라이브러리·프레임워크·SDK·CLI·오픈소스·플랫폼·릴리즈 관련
- "ai_ml": 위 두 카테고리에 해당하지 않는 AI/ML 일반

## 시리즈 분류 (series 필드)
아래 중 하나만 선택, 해당 없으면 null:
- "로컬 LLM 실험실": VRAM·GPU·llama.cpp·vLLM·quantization·gguf·로컬 모델 실행
- "AI 개발도구 워크플로우": Codex CLI·Claude Code·Gemini CLI·IDE 자동화
- "AI 블로그 자동화": 파이프라인·WordPress·Rank Math·블로그 자동화

## 썸네일 프롬프트 규칙
- DALL-E 기준 영문, 1200x630 와이드 썸네일
- 다크 배경, 청/녹색 accent, 코드/회로 모티프
- 텍스트 오버레이 없음
- 포스트 주제를 시각화하는 기술적 요소 포함

## OG/Twitter 소셜 태그 규칙
- og_title: 검색 결과보다 클릭·공유 유도에 최적화, 60자 이내, 이모지 사용 가능
- og_description: 카카오·링크드인·페이스북 공유 시 표시, 80~120자, 이점 중심
- (Twitter Card는 og 값을 재사용하므로 별도 필드 불필요)

출력 (JSON):
{{
  "focus_keyword": "핵심 검색 키워드 (2~4단어, 한국어)",
  "focus_keyword_slug": "focus-keyword-in-english-slug",
  "title_candidates": ["제목1", "제목2", "제목3", "제목4", "제목5"],
  "meta_description": "90~150자, 클릭 유도",
  "og_title": "소셜 공유용 제목 (60자 이내, 이모지 허용)",
  "og_description": "소셜 공유용 설명 (80~120자, 이점 중심)",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "category_type": "ai_ml",
  "series": null,
  "thumbnail_prompt": "영문 FLUX/DALL-E 이미지 프롬프트"
}}"""

response = client.chat.completions.create(
    model="deepseek-ai/deepseek-v4-pro",
    messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ],
    temperature=0.3,
    max_tokens=1024,
    extra_body={"chat_template_kwargs": {"thinking": False}},
)
raw = response.choices[0].message.content or "{}"

# JSON 파싱
import re
text = raw.strip()
if "```" in text:
    start = text.find("{", text.find("```"))
    end = text.rfind("}") + 1
    text = text[start:end]
elif not text.startswith("{"):
    start = text.find("{")
    end = text.rfind("}") + 1
    text = text[start:end]

data = json.loads(text)
with open(os.environ["SEO_JSON_FILE"], "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(json.dumps(data, ensure_ascii=False, indent=2))
PYEOF
```

SEO JSON 파일을 읽어 아래 환경 변수를 설정합니다:

```bash
# SEO JSON → 환경변수 (python3 우선, 없으면 node 폴백)
_json_field() {
  local file="$1" key="$2" default="${3:-}"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import json; d=json.load(open('$file')); v=d.get('$key'); print(v if v else '$default')"
  else
    node -e "const d=require('$file'); const v=d['$key']; process.stdout.write(String(v||'$default'));"
  fi
}

FOCUS_KEYWORD="$(_json_field "$SEO_JSON_FILE" focus_keyword)"
FOCUS_KEYWORD_SLUG="$(_json_field "$SEO_JSON_FILE" focus_keyword_slug)"
META_DESCRIPTION="$(_json_field "$SEO_JSON_FILE" meta_description)"
POST_TAGS="$(
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import json; d=json.load(open('$SEO_JSON_FILE')); print(','.join(d.get('tags',[])))"
  else
    node -e "const d=require('$SEO_JSON_FILE'); process.stdout.write((d.tags||[]).join(','));"
  fi
)"
CATEGORY_TYPE="$(_json_field "$SEO_JSON_FILE" category_type "ai_ml")"
POST_SERIES="$(_json_field "$SEO_JSON_FILE" series "")"
THUMBNAIL_PROMPT="$(_json_field "$SEO_JSON_FILE" thumbnail_prompt "AI technology concept, dark background, blue circuit board, neural network nodes")"
OG_TITLE="$(_json_field "$SEO_JSON_FILE" og_title)"
OG_DESCRIPTION="$(_json_field "$SEO_JSON_FILE" og_description)"

# title_candidates 목록 출력
if command -v python3 >/dev/null 2>&1; then
  python3 -c "
import json
d = json.load(open('$SEO_JSON_FILE'))
print('=== SEO 제목 후보 ===')
for i, t in enumerate(d.get('title_candidates', []), 1):
    print(f'{i}. {t}')
print(f'포커스 키워드: {d.get(\"focus_keyword\",\"\")}')
print(f'카테고리: {d.get(\"category_type\",\"\")} | 시리즈: {d.get(\"series\",\"없음\")}')
print(f'태그: {\" | \".join(d.get(\"tags\",[]))}')
"
else
  node -e "
const d = require('$SEO_JSON_FILE');
console.log('=== SEO 제목 후보 ===');
(d.title_candidates||[]).forEach((t,i) => console.log((i+1)+'. '+t));
console.log('포커스 키워드: '+(d.focus_keyword||''));
console.log('카테고리: '+(d.category_type||'')+' | 시리즈: '+(d.series||'없음'));
console.log('태그: '+(d.tags||[]).join(' | '));
"
fi
```

AskUserQuestion으로 제목을 선택합니다 (title_candidates 목록 기반). 선택 후:

```bash
POST_TITLE="{사용자가 선택한 제목}"
# POST_SLUG는 기존 값 유지하거나 focus_keyword_slug 반영
# POST_SLUG="${FOCUS_KEYWORD_SLUG:-$POST_SLUG}"
```

---

## Phase 7.5: Codex CLI 대표이미지 생성

Phase 7.3에서 생성한 `THUMBNAIL_PROMPT`를 사용해 Codex CLI로 대표이미지를 생성합니다.
Codex 실패 시 Pollinations.ai(FLUX)로 자동 폴백합니다.

```bash
ASSETS_DIR="$BLOG_WORKDIR/assets"
mkdir -p "$ASSETS_DIR"
THUMB_FILE="$ASSETS_DIR/${POST_DATE}-${POST_SLUG}-thumb.jpg"

echo "=== 대표이미지 생성 (Codex CLI) ==="
echo "프롬프트: $THUMBNAIL_PROMPT"

# Codex CLI에게 DALL-E API로 이미지 생성 후 저장하는 Python 코드 실행 위임
cat > /tmp/codex-image-task.md << CODEX_EOF
# 이미지 생성 태스크

블로그 대표 이미지(1200x630 JPG)를 DALL-E API로 생성하여 지정 경로에 저장하세요.

## 입력
- 이미지 프롬프트: ${THUMBNAIL_PROMPT}
- 저장 경로: ${THUMB_FILE}
- 크기: 1792x1024 (DALL-E 3 와이드, 저장 시 1200x630으로 리사이즈)

## 작업 절차
1. OpenAI Python SDK로 DALL-E 3 이미지 생성 (`model="dall-e-3"`, `size="1792x1024"`, `quality="standard"`)
2. 생성된 이미지 URL에서 bytes 다운로드
3. Pillow(PIL)로 1200x630 리사이즈 후 JPG 저장 (`${THUMB_FILE}`)
4. 성공 시 마지막 줄에 "IMAGE_SAVED: ${THUMB_FILE}" 출력
5. 실패 시 마지막 줄에 "IMAGE_FAILED: <이유>" 출력

## 주의
- Pillow 없으면 urllib로 원본 크기 그대로 저장 (리사이즈 스킵)
- 파일 저장 외 다른 작업(git, 배포 등) 금지
CODEX_EOF

codex --approval-mode full-auto "$(cat /tmp/codex-image-task.md)" 2>&1
CODEX_IMG_EXIT=$?
rm -f /tmp/codex-image-task.md

# Codex 결과 확인 후 실패 시 Pollinations.ai 폴백
if [ -f "$THUMB_FILE" ] && [ "$(stat -c%s "$THUMB_FILE" 2>/dev/null || echo 0)" -gt 1024 ]; then
    echo "대표이미지 생성 완료 (Codex): $THUMB_FILE"
    THUMBNAIL_LOCAL="$THUMB_FILE"
else
    echo "Codex 이미지 생성 실패 — Pollinations.ai(FLUX) 폴백 시도"
    python3 - <<'PYEOF'
import os, urllib.parse, urllib.request, sys

prompt = os.environ.get("THUMBNAIL_PROMPT",
    "AI technology concept, digital neural network, blue glowing nodes, dark background")
thumb_file = os.environ["THUMB_FILE"]

encoded = urllib.parse.quote(prompt)
url = (
    f"https://image.pollinations.ai/prompt/{encoded}"
    f"?width=1200&height=630&model=flux&nologo=true&seed=42"
)
print(f"Pollinations URL: {url}")
try:
    req = urllib.request.Request(url, headers={"User-Agent": "bullro-blog/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
    if len(data) > 1024:
        with open(thumb_file, "wb") as f:
            f.write(data)
        print(f"POLLINATIONS_SAVED: {thumb_file} ({len(data):,} bytes)")
    else:
        print(f"POLLINATIONS_FAILED: 응답 크기 너무 작음 ({len(data)}B)")
except Exception as e:
    print(f"POLLINATIONS_FAILED: {e}")
PYEOF

    if [ -f "$THUMB_FILE" ] && [ "$(stat -c%s "$THUMB_FILE" 2>/dev/null || echo 0)" -gt 1024 ]; then
        echo "대표이미지 생성 완료 (Pollinations): $THUMB_FILE"
        THUMBNAIL_LOCAL="$THUMB_FILE"
    else
        echo "대표이미지 생성 실패 (Codex + Pollinations 모두 실패) — 이미지 없이 발행"
        THUMBNAIL_LOCAL=""
    fi
fi
```

---

## Phase 8: WordPress 발행 게이트

AskUserQuestion으로 확인합니다:

```
블로그 포스트 최종고 완성.

파일: {FINAL_FILE}
제목: {POST_TITLE}
포커스 키워드: {FOCUS_KEYWORD}
카테고리: {CATEGORY_TYPE} | 시리즈: {POST_SERIES 또는 "없음"}
태그: {POST_TAGS}
메타 설명: {META_DESCRIPTION}
대표이미지: {THUMBNAIL_LOCAL 또는 "생성 실패"}
평가: Claude={CLAUDE_VERDICT} | Codex={CODEX_VERDICT} | Gemini={GEMINI_VERDICT}
글자수: {word_count}자

A) WordPress에 지금 업로드 (대표이미지 + SEO 포함)
B) 파일만 저장 (나중에 수동 업로드)
C) 추가 수정 후 재리뷰 (최대 2라운드)
```

A 선택 시 WordPress REST API 업로드 + Rank Math SEO + Slack 알림:

```bash
source "$BLOG_WORKDIR/venv/bin/activate" 2>/dev/null || true
export BASE_DIR="$BLOG_WORKDIR"

# ── Markdown → HTML 변환 (python3 → marked CLI 순으로 폴백) ──────────────────

_md_to_html_file() {
  local src="$1" dst="$2"

  # frontmatter(--- ... ---) 및 CHANGES 주석 제거
  local line2
  line2=$(grep -n '^---$' "$src" | sed -n '2p' | cut -d: -f1)
  if [ -n "$line2" ]; then
    tail -n +$((line2+1)) "$src"
  else
    cat "$src"
  fi | grep -v '<!-- CHANGES:' > /tmp/_post_body.md

  # 1순위: python3 + pipeline.publisher._md_to_html
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<PYEOF > "$dst"
import os, sys
sys.path.insert(0, os.environ.get("BLOG_WORKDIR", "."))
from pipeline.publisher import _md_to_html
with open("/tmp/_post_body.md", encoding="utf-8") as f:
    print(_md_to_html(f.read()))
PYEOF
    if [ -s "$dst" ]; then echo "HTML변환: python3+pipeline"; return 0; fi
  fi

  # 2순위: marked CLI
  if ! command -v marked >/dev/null 2>&1; then
    echo "marked 미설치 — npm install -g marked 시도"
    npm install -g marked --silent 2>/dev/null || true
  fi
  if command -v marked >/dev/null 2>&1; then
    marked /tmp/_post_body.md -o "$dst"
    if [ -s "$dst" ]; then echo "HTML변환: marked CLI"; return 0; fi
  fi

  # 3순위: node 인라인 (marked 모듈)
  if command -v node >/dev/null 2>&1; then
    node -e "
const fs = require('fs');
const md = fs.readFileSync('/tmp/_post_body.md', 'utf8');
// 기본 변환: 코드블록·헤딩·리스트·표·볼드·이탤릭·링크
let html = md
  .replace(/\`\`\`(\w*)\n([\s\S]*?)\`\`\`/g, '<pre><code class=\"language-\$1\">\$2</code></pre>')
  .replace(/^#{6}\s(.+)/gm, '<h6>\$1</h6>')
  .replace(/^#{5}\s(.+)/gm, '<h5>\$1</h5>')
  .replace(/^#{4}\s(.+)/gm, '<h4>\$1</h4>')
  .replace(/^#{3}\s(.+)/gm, '<h3>\$1</h3>')
  .replace(/^#{2}\s(.+)/gm, '<h2>\$1</h2>')
  .replace(/^#{1}\s(.+)/gm, '<h1>\$1</h1>')
  .replace(/\*\*(.+?)\*\*/g, '<strong>\$1</strong>')
  .replace(/\*(.+?)\*/g, '<em>\$1</em>')
  .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href=\"\$2\">\$1</a>')
  .replace(/^\|(.+)\|$/gm, (m) => '<tr>' + m.split('|').slice(1,-1).map(c => '<td>' + c.trim() + '</td>').join('') + '</tr>')
  .replace(/^---+$/gm, '<hr>')
  .replace(/^- (.+)/gm, '<li>\$1</li>')
  .replace(/^(\d+)\. (.+)/gm, '<li>\$2</li>')
  .replace(/\n\n/g, '</p><p>');
fs.writeFileSync('$dst', '<p>' + html + '</p>');
" && echo "HTML변환: node 인라인" && return 0
  fi

  echo "ERROR: HTML 변환 방법 없음 — python3/marked/node 모두 실패"; return 1
}

HTML_FILE="/tmp/_post_body.html"
_md_to_html_file "$FINAL_FILE" "$HTML_FILE" || exit 1
HTML_SIZE=$(wc -c < "$HTML_FILE")
echo "HTML 크기: ${HTML_SIZE} bytes"
if [ "$HTML_SIZE" -lt 200 ]; then
  echo "ERROR: HTML 변환 결과가 너무 작음 (${HTML_SIZE}B) — 발행 중단"
  exit 1
fi

# ── WordPress 업로드 (node 사용, python3 없는 환경 대응) ──────────────────────

node - <<'JSEOF'
const fs   = require('fs');
const https = require('https');
const http  = require('http');
const path  = require('path');

const WP_URL   = process.env.WORDPRESS_URL.replace(/\/$/, '');
const USER     = process.env.WORDPRESS_USERNAME;
const PASS     = process.env.WORDPRESS_APP_PASSWORD;
const CRED     = Buffer.from(`${USER}:${PASS}`).toString('base64');

const title       = process.env.POST_TITLE    || process.env.POST_SLUG;
const slug        = process.env.POST_SLUG;
const excerpt     = process.env.META_DESCRIPTION || '';
const focusKw     = process.env.FOCUS_KEYWORD    || '';
const ogTitle     = process.env.OG_TITLE         || '';
const ogDesc      = process.env.OG_DESCRIPTION   || '';
const tagIds      = (process.env.WP_TAG_IDS || '').split(',').filter(Boolean).map(Number);
const categoryIds = (process.env.WP_CATEGORY_IDS || process.env.WORDPRESS_DEFAULT_CATEGORY_ID || '61')
                      .split(',').filter(Boolean).map(Number);
const thumbLocal  = process.env.THUMBNAIL_LOCAL  || '';
const slackUrl    = process.env.SLACK_WEBHOOK_URL || '';

const htmlBody = fs.readFileSync('/tmp/_post_body.html', 'utf8');

function request(urlStr, method, headers, body) {
  return new Promise((resolve, reject) => {
    const u   = new URL(urlStr);
    const lib = u.protocol === 'https:' ? https : http;
    const opts = {
      hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search, method,
      headers: { ...headers, 'Content-Length': body ? Buffer.byteLength(body) : 0 },
      rejectUnauthorized: false,
    };
    const req = lib.request(opts, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function getOrCreateTag(name) {
  const search = await request(
    `${WP_URL}/?rest_route=/wp/v2/tags&search=${encodeURIComponent(name)}&per_page=5`,
    'GET', { Authorization: `Basic ${CRED}` }
  );
  const tags = JSON.parse(search.body);
  const found = tags.find(t => t.name.toLowerCase() === name.toLowerCase());
  if (found) return found.id;
  const create = await request(
    `${WP_URL}/?rest_route=/wp/v2/tags`, 'POST',
    { Authorization: `Basic ${CRED}`, 'Content-Type': 'application/json' },
    JSON.stringify({ name })
  );
  return JSON.parse(create.body).id;
}

async function uploadThumb(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return { id: null, url: '' };
  const img = fs.readFileSync(filePath);
  if (img.length < 1024) return { id: null, url: '' };
  const res = await request(
    `${WP_URL}/?rest_route=/wp/v2/media`, 'POST',
    {
      Authorization: `Basic ${CRED}`,
      'Content-Type': 'image/jpeg',
      'Content-Disposition': `attachment; filename="${path.basename(filePath)}"`,
    },
    img
  );
  const m = JSON.parse(res.body);
  console.log(`대표이미지 업로드: id=${m.id}, url=${m.source_url || ''}`);
  return { id: m.id || null, url: m.source_url || '' };
}

async function updateRankMath(wpId, thumbUrl) {
  if (!focusKw && !excerpt) return;
  const meta = {};
  if (focusKw)            meta.rank_math_focus_keyword   = focusKw;
  if (title)              meta.rank_math_title            = title;
  if (excerpt)            meta.rank_math_description      = excerpt;
  const effOgTitle = ogTitle || title;
  const effOgDesc  = ogDesc  || excerpt;
  if (effOgTitle)  meta.rank_math_og_title         = effOgTitle;
  if (effOgDesc)   meta.rank_math_og_description   = effOgDesc;
  if (thumbUrl)    meta.rank_math_og_image          = thumbUrl;
  if (effOgTitle || effOgDesc || thumbUrl) {
    meta.rank_math_twitter_title       = effOgTitle;
    meta.rank_math_twitter_description = effOgDesc;
    if (thumbUrl) meta.rank_math_twitter_image = thumbUrl;
  } else {
    meta.rank_math_twitter_use_og = 1;
  }
  await request(
    `${WP_URL}/?rest_route=/rankmath/v1/updateMeta`, 'POST',
    { Authorization: `Basic ${CRED}`, 'Content-Type': 'application/json' },
    JSON.stringify({ objectType: 'post', objectID: wpId, meta })
  );
  console.log(`Rank Math SEO 업데이트 완료 (keyword='${focusKw}', og_image=${thumbUrl ? '있음' : '없음'})`);
}

(async () => {
  // 태그 ID 수집
  const tagNames = (process.env.POST_TAGS || '').split(',').filter(Boolean).map(s => s.trim());
  const resolvedTagIds = tagIds.length ? tagIds
    : await Promise.all(tagNames.map(getOrCreateTag)).catch(() => []);

  // 대표이미지 업로드
  const { id: mediaId, url: thumbUrl } = await uploadThumb(thumbLocal);

  // 포스트 페이로드
  const payload = {
    title, content: htmlBody, status: 'publish',
    slug, categories: categoryIds, tags: resolvedTagIds, excerpt,
  };
  if (mediaId) payload.featured_media = mediaId;

  const res = await request(
    `${WP_URL}/?rest_route=/wp/v2/posts`, 'POST',
    { Authorization: `Basic ${CRED}`, 'Content-Type': 'application/json' },
    JSON.stringify(payload)
  );
  const post = JSON.parse(res.body);
  const wpId   = post.id   || 0;
  const wpLink = post.link || '';
  console.log(`WordPress 발행 완료: ${wpLink} (id=${wpId})`);
  console.log(`카테고리: ${categoryIds} | 태그 수: ${resolvedTagIds.length} | 시리즈: ${process.env.POST_SERIES || '없음'}`);

  // Rank Math SEO
  if (wpId) await updateRankMath(wpId, thumbUrl);

  // Slack 알림
  if (slackUrl && wpId) {
    await request(slackUrl, 'POST',
      { 'Content-Type': 'application/json' },
      JSON.stringify({ text: `✅ 블로그 포스트 업로드 완료\n*${title}*\n${wpLink}\n키워드: ${focusKw} | 카테고리: ${categoryIds}\n리뷰어: Claude Code × Codex × Gemini` })
    );
    console.log('Slack 알림 전송 완료');
  }
})().catch(e => { console.error('발행 실패:', e.message); process.exit(1); });
JSEOF
```

> **환경별 HTML 변환 우선순위:**
> 1. `python3` + `pipeline.publisher._md_to_html` (Mermaid 변환·LLM 아티팩트 제거 포함)
> 2. `marked` CLI (`npm install -g marked` 자동 시도)
> 3. `node` 인라인 정규식 변환 (기본 Markdown 요소만)
>
> **WordPress 업로드는 항상 `node`로 처리** — python3 없는 환경에서도 동작.

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
  --arg post_title "${POST_TITLE}" \
  --arg focus_keyword "${FOCUS_KEYWORD}" \
  --arg category_type "${CATEGORY_TYPE}" \
  --arg post_series "${POST_SERIES}" \
  --arg thumbnail_local "${THUMBNAIL_LOCAL}" \
  '{date:$date, type:"blog-draft", topic:$topic, draft_file:$draft_file,
    final_file:$final_file, angle:$angle, draft_model:$draft_model,
    claude_verdict:$claude_verdict, codex_verdict:$codex_verdict,
    gemini_verdict:$gemini_verdict, outcome:$outcome,
    post_title:$post_title, focus_keyword:$focus_keyword,
    category_type:$category_type, post_series:$post_series,
    thumbnail_local:$thumbnail_local}' \
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
- **SEO 메타**: Phase 7.3에서 DeepSeek이 제목 후보 5개·포커스 키워드·메타 설명·태그·카테고리·시리즈·썸네일 프롬프트 생성
- **카테고리**: `_determine_category(title, tags)` 자동 결정 + 시리즈 카테고리 추가 (publisher.py와 동일 로직)
- **대표이미지**: Codex CLI (`codex --approval-mode full-auto`) → 실패 시 Pollinations.ai(FLUX) 자동 폴백
- **Rank Math SEO**: `focus_keyword` + `seo_title` + `meta_description` 업데이트 (발행 후 자동)
- NVIDIA API: `openai` 라이브러리 + `base_url=https://integrate.api.nvidia.com/v1`
- GLM-5.1 extra_body: `{"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": True}}`
- DeepSeek extra_body: `{"chat_template_kwargs": {"thinking": False}}`
- WordPress 인증: Application Password (관리자 > 프로필에서 생성, OAuth 불필요)
- Codex·Gemini는 순차 실행 (컨텍스트 파일 충돌 방지)
- 재리뷰 최대 2라운드
- 히스토리는 `.discuss/history.jsonl`에 누적 (`/discuss`, `/discuss-plan`과 공유)
