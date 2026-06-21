# CUDA 13.0-compatible backend image with vLLM kept inside the backend container.
# MinerU VLM inference runs in a separate mineru-openai-server container.
FROM vllm/vllm-openai:v0.21.0
# If you must use CUDA 12.9 instead, switch to:
# FROM vllm/vllm-openai:v0.21.0-cu129

# vllm/vllm-openai images define their own server entrypoint; reset it for FastAPI backend.
ENTRYPOINT []

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/backend/src \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_CACHE=/root/.cache/huggingface \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0

# Embedding/rerank model defaults. docker-compose.yml can override these.
ENV EMBEDDING_MODEL=microsoft/harrier-oss-v1-0.6b \
    LOCAL_EMBEDDINGS_MODEL_PATH=microsoft/harrier-oss-v1-0.6b \
    LOCAL_EMBEDDINGS_DEVICE=cuda \
    LOCAL_EMBEDDINGS_MODEL_DTYPE=float16 \
    EMBEDDING_QUERY_PROMPT_ENABLED=1 \
    EMBEDDING_FORCE_QUERY_PROMPT=1 \
    RERANK_MODEL=Qwen/Qwen3-Reranker-4B \
    LOCAL_RERANKER_MODEL_PATH=Qwen/Qwen3-Reranker-4B \
    LOCAL_RERANKER_DEVICE=cuda \
    LOCAL_RERANKER_MODEL_DTYPE=float16 \
    LOCAL_RERANKER_TOP_K=98 \
    LOCAL_RERANKER_BATCH_SIZE=8 \
    LOCAL_RERANKER_MAX_CHARS_PER_PASSAGE=8192

# Backend uses MinerU CLI as an HTTP client. VLM inference is served by the separate MinerU GPU container.
ENV MINERU_EXEC_MODE=local-cli \
    MINERU_BACKEND=vlm-http-client \
    MINERU_SERVER_URL=http://mineru-openai-server:30000 \
    MINERU_METHOD=auto \
    MINERU_FORMULA=true \
    MINERU_TABLE=true \
    MINERU_IMAGE_ANALYSIS=false \
    MINERU_CLI_TIMEOUT=0

WORKDIR /workspace/backend

RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl ca-certificates \
       build-essential pkg-config \
       libgl1 libgomp1 libglib2.0-0 \
       libnuma1 libnuma-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.docker.txt /workspace/backend/requirements.docker.txt

RUN python -m pip install --timeout 600 -U pip setuptools wheel \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --timeout 600 -r /workspace/backend/requirements.docker.txt \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --timeout 600 hipporag==2.0.0a4 gritlm==1.0.2 --no-deps \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn

COPY . /workspace/backend

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "esg_encoding.api:app", "--host", "0.0.0.0", "--port", "8000"]
