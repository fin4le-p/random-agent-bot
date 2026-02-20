FROM python:3.13-slim

WORKDIR /app

# pip の余計なキャッシュを使わない
ENV PIP_NO_CACHE_DIR=1

# 依存関係のインストール
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# ソースコード一式コピー
COPY . /app

# Token は compose 側で渡す
ENV DISCORD_TOKEN=""

# Botを起動
CMD ["python", "main.py"]
