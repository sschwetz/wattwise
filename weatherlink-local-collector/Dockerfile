FROM python:3.12-alpine

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY weatherlink_local_collector ./weatherlink_local_collector
RUN uv sync --frozen --no-dev

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "weatherlink_local_collector.main"]
