# Porchlight in one container: nginx for the page, the write-back API on
# loopback beside it. No framework, no Node, no build stage.
FROM debian:13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx python3 python3-yaml ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Every path is stated. api.py derives its own from PORCHLIGHT_DIR, but the
# entrypoint invokes build.py directly, and build.py defaults relative to its
# own location — /app — not to the data volume. Leaving the rest implicit means
# the first start looks for /app/config.yaml, does not find it, and the
# container dies before it ever serves a page.
ENV PORCHLIGHT_DIR=/data \
    PORCHLIGHT_CONFIG=/data/config.yaml \
    PORCHLIGHT_ICONS=/data/icons \
    PORCHLIGHT_BG=/data/bg \
    PORCHLIGHT_OUT=/srv/public \
    PORCHLIGHT_CATALOG=/app/catalog/apps.yaml \
    PORCHLIGHT_PORT=8088

COPY app/ /app/app/
COPY catalog/ /app/catalog/
COPY config.example.yaml /app/config.example.yaml
COPY docker/nginx.conf /etc/nginx/sites-available/default
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data /srv/public

# /data holds your config, icons, backgrounds, backups and token. Mount it, or
# every edit you make dies with the container.
VOLUME ["/data"]
EXPOSE 80
ENTRYPOINT ["/entrypoint.sh"]
