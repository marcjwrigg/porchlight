#!/bin/sh
set -e
mkdir -p /data/icons /data/bg /data/backup
[ -f /data/config.yaml ] || cp /app/config.example.yaml /data/config.yaml
python3 /app/app/build.py
nginx
exec python3 /app/app/api.py
