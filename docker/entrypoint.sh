#!/bin/sh
set -e
mkdir -p /data/icons /data/bg /data/backup
[ -f /data/config.yaml ] || cp /app/config.example.yaml /data/config.yaml
[ -s /data/token ] || head -c 24 /dev/urandom | base64 | tr -d '=+/' > /data/token
chmod 600 /data/token
echo "porchlight: edit token is $(cat /data/token)"
python3 /app/app/build.py
nginx
exec python3 /app/app/api.py
