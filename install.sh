#!/usr/bin/env bash
# Install Porchlight on a Debian/Ubuntu host. Run from a checkout, as root:
#
#   sudo ./install.sh
#
# Installs nginx + python3-yaml, lays the app out under /opt/porchlight, and
# starts a systemd unit for the write-back API. Re-running upgrades the code and
# rewrites the unit and the nginx site; it never touches your config.yaml, your
# icons or your backgrounds.
#
# Everything lives under one directory so uninstalling is `rm -rf /opt/porchlight`
# plus two files, and backing up is one tarball.
set -euo pipefail

PREFIX=${PREFIX:-/opt/porchlight}
USER=${PORCHLIGHT_USER:-porchlight}
PORT=${PORCHLIGHT_PORT:-8088}
HTTP_PORT=${PORCHLIGHT_HTTP_PORT:-80}
SRC=$(cd "$(dirname "$0")" && pwd)

# Root, not sudo: a minimal Debian image does not ship sudo, and this is very
# often run in a container shell that is root already.
[[ $EUID -eq 0 ]] || { echo "run me as root — 'sudo -i' first, then ./install.sh"; exit 1; }

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx python3 python3-yaml >/dev/null

echo "==> user and layout under $PREFIX"
id -u "$USER" >/dev/null 2>&1 || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin "$USER"
mkdir -p "$PREFIX"/{app,catalog,data/{icons,bg,backup},public}

echo "==> code"
install -m 644 "$SRC/app/build.py" "$SRC/app/api.py" "$PREFIX/app/"
install -m 644 "$SRC/catalog/apps.yaml" "$PREFIX/catalog/apps.yaml"

echo "==> config"
# Seeded once, then yours. An upgrade must never overwrite a live config.
if [[ ! -f $PREFIX/data/config.yaml ]]; then
  install -m 644 "$SRC/config.example.yaml" "$PREFIX/data/config.yaml"
  echo "   seeded from config.example.yaml"
else
  echo "   keeping the existing $PREFIX/data/config.yaml"
fi

chown -R "$USER:$USER" "$PREFIX"

echo "==> first build"
runuser -u "$USER" -- env \
  PORCHLIGHT_CONFIG="$PREFIX/data/config.yaml" \
  PORCHLIGHT_ICONS="$PREFIX/data/icons" \
  PORCHLIGHT_BG="$PREFIX/data/bg" \
  PORCHLIGHT_OUT="$PREFIX/public" \
  PORCHLIGHT_CATALOG="$PREFIX/catalog/apps.yaml" \
  python3 "$PREFIX/app/build.py" ${PORCHLIGHT_PREFETCH:+--prefetch}

echo "==> systemd unit"
cat >/etc/systemd/system/porchlight.service <<UNIT
[Unit]
Description=Porchlight write-back API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
Group=${USER}
WorkingDirectory=${PREFIX}
Environment=PORCHLIGHT_DIR=${PREFIX}/data
Environment=PORCHLIGHT_OUT=${PREFIX}/public
Environment=PORCHLIGHT_CATALOG=${PREFIX}/catalog/apps.yaml
Environment=PORCHLIGHT_PORT=${PORT}
ExecStart=/usr/bin/python3 ${PREFIX}/app/api.py
Restart=always
RestartSec=3
# It writes exactly two directories. Saying so means a mistake in the API cannot
# become a mistake anywhere else on the box.
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=${PREFIX}/data ${PREFIX}/public

[Install]
WantedBy=multi-user.target
UNIT

echo "==> nginx site"
cat >/etc/nginx/sites-available/porchlight <<CONF
server {
    listen ${HTTP_PORT} default_server;
    listen [::]:${HTTP_PORT} default_server;
    server_name _;

    root ${PREFIX}/public;
    index index.html;

    # Compression matters most when the page is reached over a tunnel or a slow
    # link, where the round trips dominate. SVG marks compress hard.
    gzip on;
    gzip_vary on;
    gzip_comp_level 6;
    gzip_min_length 512;
    gzip_types text/html text/css application/javascript application/json image/svg+xml;

    # Caching is per-location on purpose. `add_header` at server level applied
    # `no-store` to every asset too, so a browser re-fetched every icon - and a
    # 24 MB background - on each visit. On a LAN that is invisible; through a
    # tunnel it is the whole of the page load.
    #
    # nginx does NOT merge add_header across levels: a location declaring one
    # discards every inherited one. Each location below therefore sets its own.

    # The page is regenerated wholesale on every save. Caching it guarantees
    # someone stares at a stale grid wondering why their edit did nothing.
    location = / {
        add_header Cache-Control "no-store" always;
        try_files /index.html =404;
    }
    location = /index.html {
        add_header Cache-Control "no-store" always;
    }

    # Backgrounds are content-hashed on upload (stem-<sha256>.jpg), so a
    # different image is a different filename. Safe to pin hard, and these are
    # by far the largest thing on the page.
    location /bg/ {
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        try_files \$uri =404;
    }

    # Logos are content-hashed on upload like backgrounds, so pin them too.
    location /logo/ {
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        try_files \$uri =404;
    }

    # Icons are named by slug, so --refresh can replace one in place. Revalidate
    # daily rather than pinning for a year.
    location /icons/ {
        add_header Cache-Control "public, max-age=86400" always;
        try_files \$uri =404;
    }

    # Changes only when porchlight itself is upgraded.
    location = /catalog.json {
        add_header Cache-Control "public, max-age=3600" always;
    }

    location / {
        add_header Cache-Control "no-store" always;
        try_files \$uri \$uri/ =404;
    }

    # The write path. The API binds loopback, so this proxy is the only way in.
    # There is no authentication beyond that — do not put this on the internet
    # without auth in front of it.
    location /api/ {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        # Above the API's own per-kind caps, so an oversized upload is refused
        # by the API with a readable JSON reason rather than by nginx with an
        # HTML page the editor cannot parse.
        client_max_body_size 32m;
    }

    access_log off;
}
CONF
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/porchlight /etc/nginx/sites-enabled/porchlight
# This script has no `set -e`, so the test has to gate the restart explicitly.
# Restarting on a config nginx has just rejected takes the page down and leaves
# nothing serving - worse than aborting with the old config still live.
if ! nginx -t; then
    echo
    echo "  nginx rejected the generated config (above). The site link has been"
    echo "  written but nginx has NOT been restarted, so whatever was serving"
    echo "  before is still serving. Fix the config and re-run."
    exit 1
fi

systemctl daemon-reload
systemctl enable -q nginx porchlight
systemctl restart nginx porchlight

echo
echo "  Porchlight is up:  http://$(hostname -I 2>/dev/null | awk '{print $1}'):${HTTP_PORT}/"
echo
echo "  Open the page, click the sliders icon, then 'Edit page…' to add services."
echo "  Anyone who can reach it can edit it, so keep it off the open internet."
