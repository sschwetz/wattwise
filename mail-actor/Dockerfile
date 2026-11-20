FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mail_actor ./mail_actor
RUN uv sync --frozen --no-dev

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "mail_actor.main"]
