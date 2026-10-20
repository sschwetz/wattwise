FROM python:3.12-alpine

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Install dependencies only first (no project code yet) so this layer caches
# independently of application changes, then install the project itself once
# the code is present. Both are --no-dev so pytest/respx never land in the
# runtime image.
RUN uv sync --frozen --no-dev --no-install-project

COPY envoy_collector ./envoy_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

# Run the venv's interpreter directly rather than "uv run" — uv run
# re-resolves/re-syncs the project at every container start, which would
# require network access on every restart and can reintroduce dev
# dependencies into a supposedly production image.
CMD ["/app/.venv/bin/python", "-m", "envoy_collector.main"]
