FROM pytorch/pytorch:2.5.0-cuda12.1-cudnn9-runtime


ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/backend/src

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

RUN python -m pip install -U pip setuptools wheel \
 && pip install -r /workspace/requirements.docker.txt \
 && pip install hipporag==2.0.0a4 --no-deps

COPY . /workspace

EXPOSE 8000

CMD ["python","-m","uvicorn","esg_encoding.api:app","--host","0.0.0.0","--port","8000"]
