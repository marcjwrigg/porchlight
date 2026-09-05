# Porchlight in one container: nginx for the page, the write-back API on
# loopback beside it. No framework, no Node, no build stage.
FROM debian:13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx python3 python3-yaml ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PORCHLIGHT_DIR=/data \
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
