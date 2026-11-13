FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WORKLOG_DB=/data/worklog.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && useradd --uid 99 --gid 100 --no-create-home --shell /usr/sbin/nologin worklog \
 && mkdir -p /data && chown 99:100 /data

# 99:100 = nobody:users on Unraid, so files in the data volume get the usual owner
USER 99:100
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "-m", "worklog.server"]
