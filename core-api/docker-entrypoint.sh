#!/bin/sh
set -eu

/app/.venv/bin/python -m core_api.bootstrap

exec /app/.venv/bin/uvicorn core_api.main:create_app --factory --host 0.0.0.0 --port 8000
