FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 (sentence-transformers 의존성)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# sentence-transformers 모델 사전 다운로드 (컨테이너 시작 시간 단축)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY pipeline/ ./pipeline/

CMD ["python", "-m", "pipeline.main"]
