FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /srv/leanfhir
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install . \
    && useradd --system --uid 10001 --no-create-home leanfhir \
    && mkdir /data && chown leanfhir /data

USER leanfhir
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=2)"

# Single worker (required for SQLite). Uvicorn honours WEB_CONCURRENCY for PostgreSQL deployments.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
