FROM pytorch/pytorch:2.5.0-cuda12.1-cudnn9-runtime


ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/backend/src

# Embedding/rerank model defaults. docker-compose.yml can override these.
ENV EMBEDDING_MODEL=microsoft/harrier-oss-v1-0.6b \
    LOCAL_EMBEDDINGS_DEVICE=cuda \
    EMBEDDING_MODEL_DTYPE=float32 \
    EMBEDDING_QUERY_PROMPT_ENABLED=1 \
    EMBEDDING_FORCE_QUERY_PROMPT=1 \
    RERANK_MODEL=microsoft/harrier-oss-v1-0.6b \
    LOCAL_RERANKER_DEVICE=cuda \
    RERANK_MODEL_DTYPE=float32 \
    LOCAL_RERANKER_USE_FP16=0 \
    LOCAL_RERANKER_TOP_K=46 \
    LOCAL_RERANKER_BATCH_SIZE=64 \
    LOCAL_RERANKER_EMBED_BATCH_SIZE=64 \
    LOCAL_RERANKER_MAX_CHARS_PER_PASSAGE=4096


# MinerU report extraction defaults. docker-compose.yml starts mineru-openai-server.
# Backend uses the MinerU CLI as an HTTP client; VLM inference runs in the GPU MinerU service.
ENV MINERU_EXEC_MODE=local-cli \
    MINERU_BACKEND=vlm-http-client \
    MINERU_SERVER_URL=http://mineru-openai-server:30000 \
    MINERU_METHOD=auto \
    MINERU_FORMULA=true \
    MINERU_TABLE=true \
    MINERU_IMAGE_ANALYSIS=false

WORKDIR /workspace

RUN echo "deb https://mirrors.aliyun.com/ubuntu/ jammy main restricted universe multiverse" > /etc/apt/sources.list \
    && echo "deb https://mirrors.aliyun.com/ubuntu/ jammy-updates main restricted universe multiverse" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.aliyun.com/ubuntu/ jammy-security main restricted universe multiverse" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    build-essential pkg-config \
    libgl1 libgomp1 \
    libnuma1 libnuma-dev \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.docker.txt /workspace/requirements.docker.txt

# RUN python -m pip install -U pip setuptools wheel \
#  && pip install -r /workspace/requirements.docker.txt \
#  && pip install hipporag==2.0.0a4 --no-deps

RUN python -m pip install --timeout 600 -U pip setuptools wheel \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
 && pip install --timeout 600 -r /workspace/requirements.docker.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
 && pip install --timeout 600 hipporag==2.0.0a4 --no-deps \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

COPY . /workspace

EXPOSE 8000

CMD ["python","-m","uvicorn","esg_encoding.api:app","--host","0.0.0.0","--port","8000"]
