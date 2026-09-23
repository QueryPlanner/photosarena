FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN groupadd --gid 10001 photosarena \
    && useradd --uid 10001 --gid photosarena --no-create-home --shell /usr/sbin/nologin photosarena \
    && mkdir -p /app /data \
    && chown photosarena:photosarena /data

WORKDIR /app
COPY app/ /app/app/
COPY photo-manifest.json /app/photo-manifest.json
EXPOSE 8000
USER 10001:10001

CMD ["python", "-m", "app.server"]
