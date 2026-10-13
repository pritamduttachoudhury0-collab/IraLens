FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IRALENS_API_HOST=0.0.0.0 \
    IRALENS_API_PORT=8000 \
    IRALENS_HOME=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY iralens ./iralens
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir . \
    && useradd --system --uid 10001 --create-home iralens \
    && mkdir -p /data \
    && chown -R iralens:iralens /data

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()" || exit 1
ENTRYPOINT ["iralens-api"]
