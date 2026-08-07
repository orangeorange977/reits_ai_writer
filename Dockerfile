# REIT-AI 申报系统生产镜像（步骤 3.7）
# 基础镜像 python:3.12-slim；依赖里 PyMuPDF/lxml/Pillow 官方均提供 cp312 wheel，无需编译。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖（利用 Docker 层缓存：代码变动不重装依赖）；国内服务器走清华 pip 镜像
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 本地 OCR（tesseract + 简体中文语言包）：扫描件免费识别兜底，不依赖付费视觉 API。
# Debian 官方源在国内很慢，先换阿里云镜像源再装。
RUN sed -i 's#deb.debian.org#mirrors.aliyun.com#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# 应用代码（引擎 + 前端 + 模板包）。workspace/ 数据目录由卷挂载提供，不进镜像。
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY templates-packs/ ./templates-packs/

# 容器内监听所有网卡（由 Nginx 反代对外）；worker 数可调，默认 1（2G 内存服务器最稳）
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    WEB_CONCURRENCY=1 \
    DATA_SOURCE_BASE=/app/workspace

RUN mkdir -p /app/workspace

EXPOSE 8000

# 用 uvicorn 命令启动（而非 run_server.py），支持 WEB_CONCURRENCY 多 worker；
# 生成任务状态已落 DB generation_jobs（步骤 3.5），多 worker 下状态仍可查。
CMD uvicorn backend.main:app --host "$APP_HOST" --port "$APP_PORT" --workers "$WEB_CONCURRENCY"
