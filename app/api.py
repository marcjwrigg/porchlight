#!/usr/bin/env python3
"""Porchlight's write-back API. Runs behind nginx at /api/.

    GET  /api/config                          -> {ok, config}
    PUT  /api/config                          -> validate, write, rebuild
    POST /api/asset?kind=icon|bg&name=<file>  -> store an uploaded image

Binds **127.0.0.1 only**; nginx is the sole way in. Every mutating call needs
`X-Edit-Token` matching /opt/porchlight/data/token, compared with `hmac.compare_digest`.
That is a real gate, not decoration: this endpoint rewrites a file and executes
a build, and a home network is typically one flat L2 with no client isolation.

Deliberately stdlib + PyYAML, and nothing else. A launcher whose editor drags in
a web framework has stopped being the thing that keeps working when everything
else is down.

**Saving rewrites config.yaml and its comments do not survive** — a YAML
round-trip through a UI cannot keep them. That is why per-entry rationale lives
in a `note:` field, which is data and does round-trip. Anything that must be
remembered about a service belongs in `note`, not in a `#` comment.
"""
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

HERE = pathlib.Path(__file__).resolve().parent
# Code and data are separate: the code is whatever you checked out, the data is
# yours. An upgrade replaces one and must never touch the other.
DATA_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_DIR", HERE.parent))
CONFIG = pathlib.Path(os.environ.get("PORCHLIGHT_CONFIG", DATA_DIR / "config.yaml"))
ICON_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_ICONS", DATA_DIR / "icons"))
BG_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_BG", DATA_DIR / "bg"))
BACKUP_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_BACKUP", DATA_DIR / "backup"))
TOKEN_FILE = pathlib.Path(os.environ.get("PORCHLIGHT_TOKEN", DATA_DIR / "token"))
BUILD = HERE / "build.py"

PORT = int(os.environ.get("PORCHLIGHT_PORT", "8088"))
# Set PORCHLIGHT_OPEN=1 to run with no token at all. Deliberately an env var
# rather than a config setting: turning off the only lock on a write endpoint
# should be something you did on purpose to the host, not something a stray save
# from the browser can do to itself.
OPEN = os.environ.get("PORCHLIGHT_OPEN", "").lower() in ("1", "true", "yes")

MAX_CONFIG = 512 * 1024
# Per kind, because they are not the same thing. An icon is a logo and 2 MB is
# already generous; a background is a photograph and a straight-off-the-camera
# JPG is routinely 8-15 MB. One shared 4 MB cap rejected real wallpapers.
MAX_ASSET = {"icon": 2 * 1024 * 1024, "bg": 24 * 1024 * 1024}
KEEP_BACKUPS = 20

SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
ALLOWED_EXT = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".avif"}

HEADER = """\
# Managed by the launcher's editor as well as by hand — see the Porchlight README.
#
# THE EDITOR REWRITES THIS FILE, AND COMMENTS DO NOT SURVIVE A SAVE. Anything
# that must be remembered about an entry goes in its `note:`, which is data and
# does round-trip (and shows as the tile's tooltip). A `#` comment here lasts
# only until the next save from the UI.
"""


def token():
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def clean_name(raw, fallback="upload"):
    name = SAFE_NAME.sub("-", (raw or "").strip()).strip("-.") or fallback
    return name[:64]


def split_upload_name(raw):
    """Split an uploaded filename into (slug, extension).

    The extension comes off FIRST and the stem is truncated after. Doing it the
    other way round silently ate the `.jpg` off any filename over 64 characters
    — which a stock-photo wallpaper comfortably is — and the upload then failed
    an extension check for a file that had a perfectly good extension.
    """
    raw = (raw or "").strip()
    stem, dot, ext = raw.rpartition(".")
    if not dot:
        stem, ext = raw, ""
    ext = "." + SAFE_NAME.sub("", ext).lower() if ext else ""
    return clean_name(stem, "upload").lower(), ext


def bg_name(raw, data):
    """Name a background by its truncated stem PLUS a hash of its contents.

    Truncating the stem alone is not enough. Two wallpapers from the same stock
    set share a long prefix, collapse to an identical 64-character name, and the
    second upload lands on the first one's filename — `settings.background`
    never changes, the URL never changes, and the browser shows the cached
    original. It looks exactly like "the background cannot be changed".

    A content hash makes the name unique per image, and cache-busting for free.
    Re-uploading the same file stays idempotent, which is what you want.
    """
    stem, ext = split_upload_name(raw)
    return f"{stem[:40].rstrip('-.')}-{hashlib.sha256(data).hexdigest()[:8]}", ext


def validate(cfg):
    """Reject anything that is not the shape the generator can render.

    The API is the only writer that is not a person reading the file, so this is
    where a malformed save is stopped — after it reaches disk the page is
    already broken for everyone.
    """
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")

    settings = cfg.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    # Missing keys take the default rather than failing: a caller that only
    # wants to change the groups should not have to restate the whole page.
    fallback = {"title": "", "background": "", "favicon": "",
                "layout": "flat", "sort": "az", "size": "m"}
    out_settings = {}
    for key, default in fallback.items():
        val = settings.get(key, default)
        if val is None:
            val = default
        if not isinstance(val, str):
            raise ValueError(f"settings.{key} must be a string")
        out_settings[key] = (val or default if key in ("layout", "sort", "size") else val)[:512]
    for key, default, ceiling in (("gapX", 8, 96), ("gapY", 8, 96),
                                  ("padX", 32, 400), ("padY", 28, 400),
                                  ("bgDim", 72, 100)):
        try:
            out_settings[key] = max(0, min(ceiling, int(settings.get(key, default))))
        except (TypeError, ValueError):
            raise ValueError(f"settings.{key} must be a number")
    if out_settings["layout"] not in ("flat", "grouped"):
        raise ValueError("settings.layout must be flat or grouped")
    if out_settings["sort"] not in ("az", "curated"):
        raise ValueError("settings.sort must be az or curated")
    if out_settings["size"] not in ("s", "m", "l"):
        raise ValueError("settings.size must be s, m or l")

    groups = cfg.get("groups")
    if not isinstance(groups, list) or len(groups) > 40:
        raise ValueError("groups must be a list of at most 40")
    out_groups = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            raise ValueError("each group needs a name")
        services = group.get("services") or []
        if not isinstance(services, list) or len(services) > 200:
            raise ValueError("services must be a list of at most 200")
        out_services = []
        for svc in services:
            if not isinstance(svc, dict):
                raise ValueError("each service must be an object")
            name = (svc.get("name") or "").strip()
            if not name:
                raise ValueError("every service needs a name")
            entry = {"name": name[:80], "href": (svc.get("href") or "").strip()[:2048]}
            for key in ("icon", "icon_url", "note"):
                val = (svc.get(key) or "").strip()
                if val:
                    entry[key] = val[:2048]
            out_services.append(entry)
        out_groups.append({"name": group["name"].strip()[:80], "services": out_services})
    return {"settings": out_settings, "groups": out_groups}


def write_config(cfg):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        # Millisecond suffix: two saves inside the same second are easy to do
        # from a script, and a collision silently loses the earlier backup.
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
        shutil.copy2(CONFIG, BACKUP_DIR / f"services-{stamp}.yaml")
        old = sorted(BACKUP_DIR.glob("services-*.yaml"))
        for stale in old[:-KEEP_BACKUPS]:
            stale.unlink()
    body = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=100)
    # Atomic: a half-written config.yaml is a launcher that will not build.
    tmp = CONFIG.with_suffix(".yaml.tmp")
    tmp.write_text(HEADER + "\n" + body)
    tmp.replace(CONFIG)


def rebuild():
    env = dict(os.environ)
    env.setdefault("PORCHLIGHT_CONFIG", str(CONFIG))
    env.setdefault("PORCHLIGHT_ICONS", str(ICON_DIR))
    env.setdefault("PORCHLIGHT_BG", str(BG_DIR))
    proc = subprocess.run(
        [sys.executable, str(BUILD)], env=env, capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "build failed").strip()[-800:])
    return proc.stdout


class Handler(BaseHTTPRequestHandler):
    server_version = "porchlight"

    def log_message(self, fmt, *args):  # quieter journal; nginx has the access log
        sys.stderr.write("api: " + fmt % args + "\n")

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        if OPEN:
            return True
        want = token()
        got = self.headers.get("X-Edit-Token", "")
        if not want or not hmac.compare_digest(want, got):
            self.reply(401, {"ok": False, "error": "Token rejected"})
            return False
        return True

    def body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            self.reply(
                413,
                {
                    "ok": False,
                    "error": f"file is {length / 1048576:.1f} MB; the limit here is "
                    f"{limit // 1048576} MB",
                },
            )
            return None
        return self.rfile.read(length)

    def do_GET(self):
        if self.path.rstrip("/") != "/api/config":
            return self.reply(404, {"ok": False, "error": "no such endpoint"})
        try:
            cfg = yaml.safe_load(CONFIG.read_text()) or {}
        except Exception as exc:
            return self.reply(500, {"ok": False, "error": str(exc)})
        self.reply(200, {"ok": True, "config": cfg})

    def do_PUT(self):
        if self.path.rstrip("/") != "/api/config":
            return self.reply(404, {"ok": False, "error": "no such endpoint"})
        if not self.authed():
            return
        raw = self.body(MAX_CONFIG)
        if raw is None:
            return
        try:
            cfg = validate(json.loads(raw.decode()))
        except Exception as exc:
            return self.reply(400, {"ok": False, "error": str(exc)})
        try:
            write_config(cfg)
            rebuild()
        except Exception as exc:
            return self.reply(500, {"ok": False, "error": str(exc)})
        self.reply(200, {"ok": True})

    def do_POST(self):
        path, _, query = self.path.partition("?")
        if path.rstrip("/") != "/api/asset":
            return self.reply(404, {"ok": False, "error": "no such endpoint"})
        if not self.authed():
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        kind = params.get("kind", "icon")
        if kind not in ("icon", "bg"):
            return self.reply(400, {"ok": False, "error": "kind must be icon or bg"})
        raw = self.body(MAX_ASSET[kind])
        if raw is None:
            return
        slug, ext = (
            bg_name(params.get("name"), raw)
            if kind == "bg"
            else split_upload_name(params.get("name"))
        )
        if ext not in ALLOWED_EXT:
            return self.reply(
                400,
                {
                    "ok": False,
                    "error": f"{ext or 'no extension'} is not an image type — "
                    f"expected one of {' '.join(sorted(ALLOWED_EXT))}",
                },
            )
        target_dir = ICON_DIR if kind == "icon" else BG_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        if kind == "icon":
            # One file per slug: an upload replaces whatever extension was there
            # before, so a slug can never resolve to two different images.
            for stale in target_dir.glob(f"{slug}.*"):
                stale.unlink()
        if kind == "bg":
            # One background at a time. Keeping every wallpaper ever tried would
            # quietly fill a 4 GB rootfs, and nothing references the old ones.
            for stale in target_dir.iterdir():
                if stale.is_file():
                    stale.unlink()
        dest = target_dir / f"{slug}{ext}"
        dest.write_bytes(raw)
        try:
            rebuild()
        except Exception as exc:
            return self.reply(500, {"ok": False, "error": str(exc)})
        self.reply(200, {"ok": True, "slug": slug, "file": dest.name, "bytes": len(raw)})


def main():
    if OPEN:
        # Loud, every start, in the journal. An unauthenticated write endpoint
        # that rewrites the page and runs a build is a fine thing to choose and
        # a terrible thing to forget you chose.
        print(
            "porchlight: PORCHLIGHT_OPEN is set — ANYONE who can reach this "
            "server can rewrite your links and upload files. Do not expose it.",
            file=sys.stderr,
            flush=True,
        )
    elif not token():
        sys.exit(f"no edit token at {TOKEN_FILE} — refusing to start with writes ungated")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"porchlight api on 127.0.0.1:{PORT}, config {CONFIG}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
