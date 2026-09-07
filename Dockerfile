# syntax=docker/dockerfile:1
###############################################################################
# Telegram AI Auto-Reply
###############################################################################
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    TZ=UTC

WORKDIR /app

# curl  - used by the container HEALTHCHECK below.
# tzdata - python:*-slim ships no IANA timezone database, so zoneinfo could not
#          resolve Asia/Kolkata and every schedule silently ran on UTC. The
#          tzdata *wheel* in requirements.txt also covers this; both are kept so
#          the timezone works whether or not the wheel install is skipped.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a non-root user: nothing here needs privileges.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

CMD ["python", "main.py"]
