#!/usr/bin/env bash
# Install Porchlight on a Debian/Ubuntu host. Run from a checkout, as root:
#
#   sudo ./install.sh
#
# Installs nginx + python3-yaml, lays the app out under /opt/porchlight, and
# starts a systemd unit for the write-back API. Re-running upgrades the code and
# rewrites the unit and the nginx site; it never touches your config.yaml, your
# icons, your backgrounds or your token.
#
# Everything lives under one directory so uninstalling is `rm -rf /opt/porchlight`
# plus two files, and backing up is one tarball.
set -euo pipefail

PREFIX=${PREFIX:-/opt/porchlight}
USER=${PORCHLIGHT_USER:-porchlight}
PORT=${PORCHLIGHT_PORT:-8088}
HTTP_PORT=${PORCHLIGHT_HTTP_PORT:-80}
SRC=$(cd "$(dirname "$0")" && pwd)

[[ $EUID -eq 0 ]] || { echo "run me as root (sudo ./install.sh)"; exit 1; }

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

echo "==> edit token"
# Generated here so it never travels. The API refuses to start without one
# rather than exposing an ungated write endpoint.
if [[ ! -s $PREFIX/data/token ]]; then
  head -c 24 /dev/urandom | base64 | tr -d '=+/' >"$PREFIX/data/token"
  echo "   generated a new token"
fi
chmod 600 "$PREFIX/data/token"
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
# It writes two directories and reads one token. Saying so means a mistake in
# the API cannot become a mistake anywhere else on the box.
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

    # The page is regenerated wholesale on every save and is a few KB. Caching
    # it guarantees someone stares at a stale grid wondering why their edit did
    # nothing.
    add_header Cache-Control "no-store" always;

    location / {
        try_files \$uri \$uri/ =404;
    }

    # The write path. The API binds loopback, so this is the only way in and
    # the token check is the gate that matters.
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
nginx -t

systemctl daemon-reload
systemctl enable -q nginx porchlight
systemctl restart nginx porchlight

echo
echo "  Porchlight is up:  http://$(hostname -I 2>/dev/null | awk '{print $1}'):${HTTP_PORT}/"
echo "  Edit token:        $(cat "$PREFIX"/data/token)"
echo
echo "  Open the page, click the sliders icon, then 'Edit page…' to add services."
