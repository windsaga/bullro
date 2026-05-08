#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  AI 블로그 파이프라인 Deployer"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "============================================"

# ── Docker Compose 래퍼 (v1/v2 자동 감지) ──────────────────────────
compose() {
    if sudo docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD="sudo docker compose"
        sudo docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="sudo docker-compose"
        sudo docker-compose "$@"
    else
        echo "ERROR: Docker Compose를 찾을 수 없습니다."
        exit 1
    fi
}

# ── 사용법 ─────────────────────────────────────────────────────────
usage() {
    cat <<EOF
사용법:
  ./run.sh [옵션]

옵션:
  --no-pull   git pull 생략 (빠른 재배포)
  --test      파이프라인 1회 즉시 실행 후 종료 (스케줄러 미시작)
  -h, --help  도움말

예시:
  ./run.sh                  # 전체 배포 (pull → build → 스케줄러 시작)
  ./run.sh --no-pull        # git pull 없이 재배포
  ./run.sh --test           # 지금 당장 한 번 실행 (테스트용)
  ./run.sh --test --no-pull # pull 없이 즉시 실행
EOF
}

# ── 옵션 파싱 ──────────────────────────────────────────────────────
DO_PULL=1
TEST_MODE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --no-pull) DO_PULL=0 ;;
        --test)    TEST_MODE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: 알 수 없는 옵션: $1"; usage; exit 1 ;;
    esac
    shift
done

# ── Step 1: .env 확인 ──────────────────────────────────────────────
echo ""
echo "[1/5] .env 파일 확인..."
if [ ! -f .env ]; then
    echo "ERROR: .env 파일이 없습니다. .env.example을 복사하고 값을 채워 주세요."
    echo "  cp .env.example .env"
    exit 1
fi

# 필수 키 체크
for key in NVIDIA_API_KEY WORDPRESS_URL WORDPRESS_USERNAME WORDPRESS_APP_PASSWORD; do
    if ! grep -q "^${key}=.\+" .env 2>/dev/null; then
        echo "ERROR: .env에 ${key} 가 설정되지 않았습니다."
        exit 1
    fi
done
echo "  .env OK"

# ── Step 2: 디렉토리 초기화 ────────────────────────────────────────
echo ""
echo "[2/5] 디렉토리 확인..."
mkdir -p data posts logs
# JSON DB 초기화 (없을 때만)
[ -f data/posts.json ]   || echo "[]" > data/posts.json
[ -f data/pending.json ] || echo "[]" > data/pending.json
[ -f data/sources_watchlist.json ] || echo "[]" > data/sources_watchlist.json
echo "  data/ posts/ logs/ OK"

# ── Step 3: 코드 업데이트 ──────────────────────────────────────────
echo ""
if [ $DO_PULL -eq 1 ]; then
    echo "[3/5] 최신 코드 가져오기..."
    git pull
else
    echo "[3/5] git pull 생략 (--no-pull)"
fi

# ── Step 4: Docker 이미지 빌드 ─────────────────────────────────────
echo ""
echo "[4/5] Docker 이미지 빌드..."
if ! compose build; then
    echo "빌드 실패. 재시도 중 (--no-cache)..."
    compose build --no-cache
fi

# ── Step 5: 실행 ───────────────────────────────────────────────────
echo ""
if [ $TEST_MODE -eq 1 ]; then
    echo "[5/5] 파이프라인 1회 즉시 실행 (--test 모드)..."
    compose run --rm pipeline python -m pipeline.main
    echo ""
    echo "============================================"
    echo "  테스트 실행 완료"
    echo "============================================"
    echo ""
    echo "생성된 포스트 확인:"
    echo "  ls posts/"
    echo ""
    echo "로그 확인:"
    echo "  cat logs/pipeline.log"
else
    echo "[5/5] 서비스 시작 (스케줄러 포함)..."
    compose down 2>/dev/null || true
    compose up -d --build --force-recreate

    echo ""
    echo "============================================"
    echo "  배포 완료! 매일 KST 06:00 자동 실행"
    echo "============================================"
    echo ""
    compose ps
    echo ""
    echo "수동 즉시 실행:"
    echo "  ./run.sh --test --no-pull"
    echo ""
    echo "로그 확인:"
    echo "  ${COMPOSE_CMD:-sudo docker compose} logs -f pipeline"
    echo "  tail -f logs/pipeline.log"
    echo ""
    echo "중지:"
    echo "  ${COMPOSE_CMD:-sudo docker compose} down"
fi
