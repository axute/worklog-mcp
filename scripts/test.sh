#!/bin/sh
# Runs the test suite in a python:3.12-slim container (the host may have an
# older Python). Dependencies are cached in a Docker volume between runs.
#   ./scripts/test.sh [pytest args]
set -e
cd "$(dirname "$0")/.."
exec docker run --rm \
  -v "$PWD":/src -w /src \
  -v worklog-mcp-venv:/venv \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  python:3.12-slim sh -c '
    [ -x /venv/bin/python ] || python -m venv /venv
    if ! cmp -s pyproject.toml /venv/.pyproject 2>/dev/null; then
      /venv/bin/pip install -q -e ".[test]" && cp pyproject.toml /venv/.pyproject
    fi
    /venv/bin/pip install -q --no-deps -e . >/dev/null
    exec /venv/bin/pytest -q "$@"' -- "$@"
