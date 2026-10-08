FROM python:3.14-slim AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --prefix=/install -r requirements.lock

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system soundpick \
    && useradd --system --gid soundpick --home-dir /app soundpick \
    && mkdir -p /app/data \
    && chown soundpick:soundpick /app /app/data
WORKDIR /app
COPY --from=dependencies /install /usr/local
COPY --chown=soundpick:soundpick app ./app
USER soundpick
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
