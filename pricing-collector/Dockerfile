FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY pricing_collector ./pricing_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "pricing_collector.main"]
