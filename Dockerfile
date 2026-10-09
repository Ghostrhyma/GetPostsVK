# Debian 13 (trixie): системный Python 3.13 из apt, зависимости - в venv.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tini tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv \
 && useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bot

WORKDIR /srv/bot

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY healthcheck.py ./

USER bot

# У бота нет веб-сервера: поллер обновляет heartbeat-файл, healthcheck проверяет его возраст
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/srv/bot/healthcheck.py"]

# tini - PID 1: корректно передаёт SIGTERM и подбирает завершившиеся процессы
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app"]
