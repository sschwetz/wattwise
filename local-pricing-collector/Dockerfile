FROM python:3.12-alpine

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY local_pricing_collector ./local_pricing_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "local_pricing_collector.main"]
