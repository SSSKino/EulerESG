# MinerU GPU service image for CUDA 13.0-compatible environments.
# This follows MinerU's Docker guidance: vllm/vllm-openai:v0.21.0 is the CUDA 13.0-compatible base.
FROM vllm/vllm-openai:v0.21.0
# If your host driver only supports CUDA 12.9, switch to:
# FROM vllm/vllm-openai:v0.21.0-cu129

ENTRYPOINT []

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_CACHE=/root/.cache/huggingface \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    MINERU_MODEL_SOURCE=local

WORKDIR /workspace/mineru

RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl ca-certificates \
       build-essential pkg-config \
       libgl1 libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --timeout 600 -U pip setuptools wheel \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --timeout 600 mineru \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn

EXPOSE 30000 7860 8000 8002

ENTRYPOINT []
CMD ["/bin/bash", "-lc", "mineru-openai-server --host ${MINERU_OPENAI_HOST:-0.0.0.0} --port ${MINERU_OPENAI_PORT:-30000} --gpu-memory-utilization ${MINERU_GPU_MEMORY_UTILIZATION:-0.45}; status=$?; echo '[mineru] mineru-openai-server exited with code' $status '; entering sleep infinity'; exec sleep infinity"]
