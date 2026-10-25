FROM python:3.12-alpine

# Alpine ships no timezone database, and core_api uses ZoneInfo for site timezones.
RUN apk add --no-cache tzdata

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY core_api ./core_api
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000
# bootstrap runs migrations before uvicorn starts, so allow a long start period.
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=5 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
ENTRYPOINT ["/docker-entrypoint.sh"]
