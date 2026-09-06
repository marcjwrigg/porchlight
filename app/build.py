#!/usr/bin/env python3
"""Generate Porchlight's index.html and fetch the icons it needs.

    python3 app/build.py            # build, fetching any icon not already local
    python3 app/build.py --refresh  # re-fetch every icon
    python3 app/build.py --prefetch # also fetch every icon in the catalogue

One copy runs in two places — from a checkout while you are working on it, and
on the server, where the write-back API invokes it after an edit. There is no
second implementation of the template to keep in step.

    PORCHLIGHT_CONFIG    path to config.yaml       (default: ./config.yaml)
    PORCHLIGHT_ICONS     icon cache directory      (default: ./icons)
    PORCHLIGHT_BG        background directory      (default: ./bg)
    PORCHLIGHT_OUT       document root to write    (default: ./public)
    PORCHLIGHT_CATALOG   catalogue yaml            (default: ./catalog/apps.yaml)

Output is one self-contained HTML file plus a directory of images. No build
toolchain, no framework, no bundler, and no network at render time. That last
one is the point: a launcher is what you reach for when other things are
broken, so it must not depend on any of them.
"""
import html
import json
import os
import pathlib
import shutil
import sys
import re
import urllib.parse
import urllib.request

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = pathlib.Path(os.environ.get("PORCHLIGHT_CONFIG", ROOT / "config.yaml"))
ICON_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_ICONS", ROOT / "icons"))
BG_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_BG", ROOT / "bg"))
OUT_DIR = pathlib.Path(os.environ.get("PORCHLIGHT_OUT", ROOT / "public"))
CATALOG = pathlib.Path(os.environ.get("PORCHLIGHT_CATALOG", ROOT / "catalog" / "apps.yaml"))
ICON_PNG = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{}.png"
ICON_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{}.svg"

IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$")


def normalise_href(raw):
    """Give a bare host a scheme, so the link is absolute.

    The API does this on save, but a hand-edited config.yaml never goes through
    the API — and `href: plex.example.com` is a *relative* reference that the
    browser resolves against the launcher itself. You click Plex and land on
    `http://launcher/plex.example.com`. Normalising here too means a config
    written by hand behaves the same as one written by the editor.
    """
    href = (raw or "").strip()
    if not href or "://" in href or href.startswith(("/", "#", "mailto:")):
        return href
    host = href.split("/", 1)[0]
    if IPV4.match(host):
        return "http://" + href
    port = host.rpartition(":")[2] if ":" in host else ""
    if port.isdigit() and port != "443":
        return "http://" + href
    return "https://" + href


# Lettered fallback tiles get a stable colour derived from the name, so a
# service does not change colour when its neighbours are edited.
PALETTE = ["#4f7cac", "#7a5c9e", "#3f8f6f", "#b0703c", "#a4515f", "#4a6fa5"]

# A backing shape behind an icon, for marks that vanish into the page. Tailscale
# and Vaultwarden are near-black; on a dark background they read as empty space.
# Two keywords cover almost every case, and anything else is taken as a CSS
# colour so an odd one can be matched exactly.
PLATES = {"light": "#ffffff", "dark": "#1c1e21"}


def plate_colour(value):
    v = (value or "").strip()
    return PLATES.get(v.lower(), v) if v else ""

DEFAULTS = {
    "title": "",
    "background": "",
    "favicon": "",
    "layout": "flat",
    "sort": "az",
    "size": 88,
    "gapX": 8,
    "gapY": 8,
    "padX": 32,
    "padY": 28,
    "bgDim": 72,
    "bgTint": "dark",
}


def fetch_icon(slug, refresh, url=None):
    """Pull one icon into the icon cache and return its filename.

    `url` overrides the dashboard-icons CDN, which is how a first-party app gets
    its own mark: ArmoryHub and homepage both serve theirs and no upstream icon
    set has ever heard of them. The extension comes from the URL, so a PNG is
    fine — SVG is preferred where it exists, not required.
    """
    ext = pathlib.PurePosixPath(urllib.parse.urlparse(url or "x.svg").path).suffix or ".svg"
    dest = ICON_DIR / f"{slug}{ext}"
    if dest.exists() and not refresh:
        return dest.name
    # An icon uploaded through the editor is already on disk under some other
    # extension; find it rather than going to the network for a slug the CDN has
    # never heard of.
    if not refresh:
        for existing in sorted(ICON_DIR.glob(f"{slug}.*")):
            return existing.name
    url = url or ICON_CDN.format(slug)
    print(f"    fetching {dest.name}  <- {url}")
    with urllib.request.urlopen(url, timeout=20) as r:
        if r.status != 200:
            raise SystemExit(f"{url} returned HTTP {r.status}")
        dest.write_bytes(r.read())
    return dest.name


def initials(name):
    # "AdGuard · dns01" -> "AD". Two characters keeps a fallback tile the same
    # visual weight as an icon.
    return "".join(c for c in name if c.isalnum())[:2].upper() or "?"


def colour_for(name):
    return PALETTE[sum(map(ord, name)) % len(PALETTE)]


def tile(svc, group, order, files, pos):
    name, href, slug = svc["name"], normalise_href(svc.get("href")), svc.get("icon")
    icon_file = files.get(slug) if slug else None
    if icon_file:
        art = f'<img src="icons/{html.escape(icon_file)}" alt="" loading="lazy">'
        # Only for real artwork: a lettered tile already has its own backing.
        plate = plate_colour(svc.get("plate"))
        if plate:
            art = (
                f'<span class="plate" style="background:'
                f'{html.escape(plate, quote=True)}">{art}</span>'
            )
    else:
        art = (
            f'<span class="letters" style="background:{colour_for(name)}">'
            f"{html.escape(initials(name))}</span>"
        )
    # `note` is where per-entry rationale lives, because the editor rewrites
    # config.yaml and YAML comments do not survive a save. It surfaces as the
    # tile's tooltip so the reason is where the thing is.
    note = svc.get("note") or ""
    title_attr = f' title="{html.escape(note, quote=True)}"' if note else ""
    return (
        # target=_blank: the launcher is a place you come back to, so a click
        # must not replace it. rel is mandatory, not decoration — without
        # noopener the opened page gets a handle on this one via window.opener.
        f'      <a class="tile" target="_blank" rel="noopener noreferrer"'
        f'{title_attr}'
        f' href="{html.escape(href)}"'
        f' data-group="{html.escape(group)}"'
        f' data-order="{order}"'
        f' data-pos="{pos}"'
        f' data-name="{html.escape(name.lower())}">\n'
        f'        <span class="art">{art}</span>\n'
        f'        <span class="label">{html.escape(name)}</span>\n'
        f"      </a>\n"
    )


def write_catalog(prefetch):
    """Publish the app catalogue next to the page, and optionally pre-fetch it.

    The picker loads this on demand, not on page load — 200 entries of JSON is
    not something a launcher should pay for on every visit just in case someone
    edits it. `--prefetch` pulls every catalogue icon into the local cache so an
    air-gapped install has artwork for anything a user might add later; without
    it, only icons actually in use are stored and the picker's thumbnails come
    from the CDN while you are choosing.
    """
    if not CATALOG.exists():
        print("==> catalogue: none found, the picker will offer custom entries only")
        return []
    data = yaml.safe_load(CATALOG.read_text()) or {}
    cats = data.get("categories") or []
    (OUT_DIR / "catalog.json").write_text(json.dumps(cats, separators=(",", ":")))
    count = sum(len(c.get("apps") or []) for c in cats)
    print(f"==> catalogue: {count} apps -> {OUT_DIR / 'catalog.json'}")
    if prefetch:
        for cat in cats:
            for app in cat.get("apps") or []:
                url = app.get("url") or (
                    ICON_PNG if app.get("ext") == "png" else ICON_CDN
                ).format(app["icon"])
                try:
                    fetch_icon(app["icon"], False, url)
                except Exception as exc:
                    print(f"    !! {app['icon']}: {exc}")
    return cats


def main():
    refresh = "--refresh" in sys.argv[1:]
    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    settings = dict(DEFAULTS, **(cfg.get("settings") or {}))
    groups = cfg.get("groups") or []

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("==> icons")
    wanted = {}
    for group in groups:
        for svc in group.get("services") or []:
            if svc.get("icon"):
                wanted[svc["icon"]] = svc.get("icon_url")
    files = {}
    for slug in sorted(wanted):
        try:
            files[slug] = fetch_icon(slug, refresh, wanted[slug])
        except Exception as exc:
            # A missing icon must never fail a build — the tile falls back to
            # letters and the page still ships. Losing one mark is a blemish;
            # losing the launcher because a CDN blipped is an outage.
            print(f"    !! {slug}: {exc} — falling back to a lettered tile")
    for name, src in (("icons", ICON_DIR), ("bg", BG_DIR)):
        dst = OUT_DIR / name
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
    print(f"    {len(files)} icons -> {OUT_DIR / 'icons'}")

    catalog = write_catalog(refresh or "--prefetch" in sys.argv[1:])

    print("==> page")
    flat, order, group_names = [], 0, []
    for group in groups:
        group_names.append(group["name"])
        for svc in group.get("services") or []:
            flat.append((svc, group["name"], order))
            order += 1

    # `pos` is the one-grid order, which is genuinely a different thing from the
    # grouped order — a flat wall of icons is not the groups concatenated, and
    # forcing one to imply the other is what made arranging it awkward. A config
    # without any `pos` falls back to the grouped sequence, so nothing written
    # before this existed changes behaviour.
    positions = {id(svc): svc.get("pos") for svc, _, _ in flat}
    if any(v is not None for v in positions.values()):
        ordered = sorted(
            flat, key=lambda t: (t[0].get("pos") is None, t[0].get("pos", 0), t[2])
        )
    else:
        ordered = flat
    pos_of = {id(t[0]): i for i, t in enumerate(ordered)}
    # Ship the configured default view as the markup itself, so the page is
    # correct before a single line of script runs.
    shown = sorted(flat, key=lambda t: t[0]["name"].lower()) if settings["sort"] == "az" else ordered
    tiles = "".join(
        tile(svc, grp, idx, files, pos_of[id(svc)]) for svc, grp, idx in shown
    )

    bg = settings.get("background") or ""
    bg_css = ""
    if bg:
        url = bg if "://" in bg else f"bg/{bg}"
        bg_css = (
            "body::before { content:''; position:fixed; inset:0; z-index:-1;"
            f" background:url('{html.escape(url, quote=True)}') center/cover no-repeat;"
            " }\n"
            "body::after { content:''; position:fixed; inset:0; z-index:-1;"
            " background:rgba(var(--scrim),var(--bg-dim)); }\n"
        )

    title = settings.get("title") or ""
    header_title = (
        f'    <h1>{html.escape(title)}</h1>\n' if title else ""
    )

    boot = json.dumps(
        {
            "groups": group_names,
            "defaults": {
                k: settings[k]
                for k in (
                    "layout", "sort", "size", "gapX", "gapY",
                    "padX", "padY", "bgDim", "bgTint",
                )
            },
            "hasBg": bool(bg),
            "config": cfg,
            "iconFiles": files,
        },
        separators=(",", ":"),
    )

    page = (
        "<!doctype html>\n"
        "<!-- Generated by build-page.py from config.yaml.\n"
        "     Do not edit this file: the next build overwrites it. -->\n"
        '<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{html.escape(title or 'Porchlight')}</title>\n"
        + (
            f'  <link rel="icon" href="{html.escape(settings["favicon"], quote=True)}">\n'
            if settings.get("favicon")
            else ""
        )
        + f"  <style>{CSS}{bg_css}{EDITOR_CSS}  </style>\n"
        "</head>\n<body>\n"
        f'  <script>window.__BOOT__ = {boot};</script>\n'
        "  <header>\n" + header_title + CONTROLS + "  </header>\n"
        '  <main id="main">\n    <div class="grid">\n'
        + tiles
        + "    </div>\n  </main>\n"
        + EDITOR_HTML
        + "  <script>"
        + JS
        + EDITOR_JS
        + "  </script>\n</body>\n</html>\n"
    )
    (OUT_DIR / "index.html").write_text(page)
    print(f"    {len(flat)} tiles -> {OUT_DIR / 'index.html'}")
    return 0


CSS = """
:root {
  color-scheme: dark;
  --art: 88px; --cell: 150px; --gap-x: 8px; --gap-y: 8px; --pad-x: 32px; --pad-y: 28px;
  --bg-dim: .72;
  --bg: #2b2d31; --panel: #34373d; --line: #43474e; --dim: #8f949b;
  --fg: #e6e6e6; --label: #cfd3d8; --muted: #b9bec5; --quiet: #565b63;
  --press: #4a4f57; --link: #9fb6d4; --shadow: rgba(0,0,0,.45);
  /* The scrim laid over a background image, as raw channels so --bg-dim can
     supply the alpha. Flipping it is the whole of the light tint. */
  --scrim: 24, 25, 28;
}

/* A white scrim over a photo with white labels on top is unreadable, so the
   tint is not just the overlay — it flips the text and panels with it. With no
   background image set it is simply a light theme, which is a fair thing to
   want anyway. */
body.light {
  --bg: #eceef1; --panel: #ffffff; --line: #d3d7dd; --dim: #5f656e;
  --fg: #1b1d21; --label: #2c3036; --muted: #4a505a; --quiet: #8a9099;
  --press: #d7dbe2; --link: #2f5d94; --shadow: rgba(0,0,0,.18);
  --scrim: 250, 250, 250;
}
body.light .seg button[aria-pressed="true"] { color: #16181c; }
body.light .letters { color: #fff; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh;
  /* Bottom keeps a little more room than the top so the last row is not
     jammed against the viewport edge on a short page. */
  padding: var(--pad-y) var(--pad-x) calc(var(--pad-y) + 40px);
  background: var(--bg); color: var(--fg);
  font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
h1 { margin: 0 0 1.5rem; font-size: 1.3rem; font-weight: 600; }

/* The launcher is tiles. Layout is a preference you set once, so it lives
   behind one quiet button rather than occupying the top of every visit. */
#prefs { position: fixed; top: .9rem; right: 1.1rem; z-index: 40; }
#prefs-btn {
  display: none; appearance: none; border: 0; background: transparent;
  color: var(--quiet); width: 34px; height: 34px; padding: 6px; border-radius: 9px;
  cursor: pointer; transition: color .12s, background .12s;
}
#prefs-btn.on { display: block; }
#prefs-btn:hover, #prefs-btn[aria-expanded="true"] { color: var(--fg); background: var(--panel); }
#prefs-btn svg { width: 100%; height: 100%; display: block; }

/* Hidden until the script enables it: a dead toggle is worse than a missing one. */
#controls {
  display: none; position: absolute; top: 40px; right: 0;
  flex-direction: column; gap: .75rem; white-space: nowrap; min-width: 250px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: .9rem 1rem; box-shadow: 0 12px 32px var(--shadow);
}
#controls.open { display: flex; }
/* Specificity matters here: a bare `.bg-only { display:none }` loses to the
   `.ctl { display:flex }` rule further down, and the backdrop slider then shows
   with no background image to dim. */
#controls .bg-only { display: none; }
#prefs.has-bg #controls .bg-only { display: flex; }
.ctl { display: flex; align-items: center; gap: 1rem; justify-content: space-between; }
.ctl > span:first-child {
  font-size: .66rem; letter-spacing: .12em; text-transform: uppercase; color: var(--dim);
}
.seg { display: flex; background: var(--bg); border-radius: 8px; padding: 2px; }
.seg button {
  appearance: none; border: 0; background: transparent; color: var(--muted);
  font: inherit; font-size: .78rem; padding: .3rem .65rem; border-radius: 6px; cursor: pointer;
}
.seg button:hover { color: var(--fg); }
.seg button[aria-pressed="true"] { background: var(--press); color: #fff; }
.ctl input[type=range] { width: 120px; accent-color: #7f8792; }
.ctl-sep { height: 1px; background: var(--line); margin: .15rem 0; }
.linkish {
  appearance: none; border: 0; background: transparent; color: var(--link);
  font: inherit; font-size: .8rem; padding: 0; cursor: pointer; text-align: left;
}
.linkish:hover { filter: brightness(1.25); }

h2 {
  margin: 2.25rem 0 1rem; font-size: .7rem; font-weight: 600;
  letter-spacing: .14em; text-transform: uppercase; color: var(--dim);
}
main > h2:first-child { margin-top: 0; }
.grid {
  display: grid; column-gap: var(--gap-x); row-gap: var(--gap-y);
  grid-template-columns: repeat(auto-fill, minmax(var(--cell), 1fr));
}
.tile {
  position: relative;
  display: flex; flex-direction: column; align-items: center; gap: .85rem;
  padding: 1.4rem .5rem 1.15rem; border-radius: 14px;
  text-decoration: none; color: inherit;
  border: 1px solid transparent; transition: background .12s, border-color .12s;
}
.tile:hover, .tile:focus-visible { background: var(--panel); border-color: var(--line); outline: none; }
.art { display: flex; align-items: center; justify-content: center; width: var(--art); height: var(--art); }
.art img { width: 100%; height: 100%; object-fit: contain; }
.plate {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%; border-radius: 18%; padding: 11%;
}
.plate img { width: 100%; height: 100%; object-fit: contain; }
.letters {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%; border-radius: 18%;
  font-size: calc(var(--art) / 3); font-weight: 600; color: #fff;
}
.label { font-size: .85rem; text-align: center; line-height: 1.25; color: var(--label); overflow-wrap: anywhere; }
"""

EDITOR_CSS = """
/* ── editor ─────────────────────────────────────────────────────────────── */
body.editing { padding-top: calc(var(--pad-y) + 44px); }
body.editing .tile { cursor: grab; }
#editbar {
  display: none; position: fixed; top: 0; left: 0; right: 0; z-index: 50;
  align-items: center; gap: .6rem; padding: .6rem 1.1rem;
  background: #24262a; border-bottom: 1px solid var(--line);
}
body.editing #editbar { display: flex; }
#editbar .who {
  font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--dim);
}
#ed-layout { margin-right: auto; }
.btn {
  appearance: none; font: inherit; font-size: .8rem; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: #e6e6e6;
  padding: .35rem .8rem; border-radius: 8px;
}
.btn:hover { background: #3d4149; }
.btn.primary { background: #3f6f9e; border-color: #4b81b6; }
.btn.primary:hover { background: #497fb4; }
.btn.danger { color: #e79a9a; }
.btn:disabled { opacity: .5; cursor: default; }

.grouphead { display: flex; align-items: center; gap: .5rem; margin: 2.25rem 0 1rem; }
main > .grouphead:first-child { margin-top: 0; }
.grouphead input {
  font: inherit; font-size: .7rem; font-weight: 600; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim);
  background: transparent; border: 1px dashed var(--line); border-radius: 6px;
  padding: .25rem .5rem; min-width: 12rem;
}
.grouphead input:focus { outline: none; border-color: #6b7280; color: #e6e6e6; }
.mini {
  appearance: none; border: 1px solid var(--line); background: var(--panel);
  color: #b9bec5; width: 26px; height: 26px; border-radius: 7px; cursor: pointer;
  font-size: .8rem; line-height: 1;
}
.mini:hover { color: #fff; background: #3d4149; }
.tile.drop-before { box-shadow: inset 3px 0 0 #5b8fc7; }
.tile.dragging { opacity: .35; }
.tile .badge {
  position: absolute; top: 6px; right: 6px; display: none; gap: 4px;
}
body.editing .tile:hover .badge { display: flex; }
.tile .badge button {
  appearance: none; border: 0; border-radius: 6px; width: 22px; height: 22px;
  background: #1f2124cc; color: #cfd3d8; cursor: pointer; font-size: .7rem; line-height: 1;
}
.tile .badge button:hover { background: #000; color: #fff; }
.addtile {
  display: flex; align-items: center; justify-content: center;
  min-height: 120px; border: 1px dashed var(--line); border-radius: 14px;
  color: var(--dim); cursor: pointer; font-size: 1.5rem; background: transparent;
}
.addtile:hover { color: #fff; border-color: #6b7280; }

dialog {
  border: 1px solid var(--line); border-radius: 14px; padding: 0;
  background: var(--panel); color: #e6e6e6; width: min(460px, 92vw);
}
dialog::backdrop { background: rgba(0,0,0,.55); }
dialog form { padding: 1.15rem 1.25rem; display: flex; flex-direction: column; gap: .85rem; }
dialog h3 { margin: 0; font-size: .95rem; }
.field { display: flex; flex-direction: column; gap: .3rem; }
.field > label { font-size: .66rem; letter-spacing: .12em; text-transform: uppercase; color: var(--dim); }
.field input[type=text], .field select {
  font: inherit; font-size: .85rem; color: #e6e6e6;
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: .45rem .6rem;
}
.field input:focus, .field select:focus { outline: none; border-color: #6b7280; }
.hint { font-size: .72rem; color: var(--dim); line-height: 1.35; }
.hint code { font-size: .95em; }
.row { display: flex; gap: .6rem; align-items: center; }
.row .grow { flex: 1; }
.preview {
  width: 48px; height: 48px; flex: 0 0 48px;
  display: flex; align-items: center; justify-content: center;
}
.preview img { width: 100%; height: 100%; object-fit: contain; }
.preview .letters { border-radius: 18%; font-size: 1rem; }
.preview .plate { border-radius: 18%; padding: 10%; }
#f-platec {
  width: 34px; height: 30px; padding: 2px; cursor: pointer;
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
}
.actions { display: flex; gap: .5rem; justify-content: flex-end; margin-top: .3rem; }
.actions .grow { margin-right: auto; }
/* ── catalogue picker ───────────────────────────────────────────────────── */
dialog.wide { width: min(560px, 94vw); }
#pick-search {
  font: inherit; font-size: .9rem; color: #e6e6e6; width: 100%;
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
  padding: .5rem .7rem;
}
#pick-search:focus { outline: none; border-color: #6b7280; }
#pick-list { max-height: 46vh; overflow-y: auto; margin: 0 -.35rem; padding: 0 .35rem; }
#pick-list h4 {
  margin: .9rem 0 .35rem; font-size: .62rem; font-weight: 600;
  letter-spacing: .14em; text-transform: uppercase; color: var(--dim);
}
#pick-list h4:first-child { margin-top: 0; }
.pick {
  display: flex; align-items: center; gap: .7rem; width: 100%;
  appearance: none; border: 0; background: transparent; color: inherit;
  font: inherit; font-size: .85rem; text-align: left; cursor: pointer;
  padding: .35rem .5rem; border-radius: 8px;
}
.pick:hover, .pick:focus-visible { background: #3d4149; outline: none; }
.pick img { width: 22px; height: 22px; object-fit: contain; flex: 0 0 22px; }
.pick .port { margin-left: auto; color: var(--dim); font-size: .75rem; }
.pick .fallback {
  width: 22px; height: 22px; flex: 0 0 22px; border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  font-size: .6rem; font-weight: 600; color: #fff;
}
#pick-empty { color: var(--dim); font-size: .82rem; padding: .8rem .5rem; }
#toast {
  position: fixed; bottom: 1.25rem; left: 50%; transform: translateX(-50%);
  background: #1f2124; border: 1px solid var(--line); border-radius: 10px;
  padding: .5rem .9rem; font-size: .82rem; display: none; z-index: 60;
}
#toast.on { display: block; }
"""

GEAR = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"><path d="M4 6h9M19 6h1M4 12h1M11 12h9M4 18h9M19 18h1"/>'
    '<circle cx="16" cy="6" r="2.2"/><circle cx="8" cy="12" r="2.2"/>'
    '<circle cx="16" cy="18" r="2.2"/></svg>'
)

CONTROLS = f"""    <div id="prefs">
      <button id="prefs-btn" aria-expanded="false" aria-controls="controls"
              aria-label="Layout options" title="Layout options">{GEAR}</button>
      <div id="controls">
        <label class="ctl"><span>Layout</span><span class="seg">
          <button data-key="layout" data-value="flat" aria-pressed="true">One grid</button>
          <button data-key="layout" data-value="grouped" aria-pressed="false">Grouped</button>
        </span></label>
        <label class="ctl"><span>Sort</span><span class="seg">
          <button data-key="sort" data-value="az" aria-pressed="true">A&ndash;Z</button>
          <button data-key="sort" data-value="curated" aria-pressed="false">Curated</button>
        </span></label>
        <label class="ctl"><span>Size</span>
          <input type="range" id="size" min="24" max="260" step="2"></label>
        <label class="ctl"><span>Gap X</span>
          <input type="range" id="gapx" min="0" max="300" step="2"></label>
        <label class="ctl"><span>Gap Y</span>
          <input type="range" id="gapy" min="0" max="300" step="2"></label>
        <label class="ctl"><span>Margin X</span>
          <input type="range" id="padx" min="0" max="800" step="4"></label>
        <label class="ctl"><span>Margin Y</span>
          <input type="range" id="pady" min="0" max="800" step="4"></label>
        <label class="ctl"><span>Tint</span><span class="seg">
          <button data-key="bgTint" data-value="dark" aria-pressed="true">Dark</button>
          <button data-key="bgTint" data-value="light" aria-pressed="false">Light</button>
        </span></label>
        <label class="ctl bg-only"><span>Backdrop</span>
          <input type="range" id="bgdim" min="0" max="100" step="1"></label>
        <div class="ctl-sep"></div>
        <button class="linkish" id="reset-prefs">Reset to page defaults</button>
        <button class="linkish" id="edit-open">Edit page&hellip;</button>
      </div>
    </div>
"""

EDITOR_HTML = """  <div id="editbar">
    <span class="who">Editing</span>
    <span class="seg" id="ed-layout">
      <button type="button" data-v="grouped" aria-pressed="true">Grouped</button>
      <button type="button" data-v="flat" aria-pressed="false">One grid</button>
    </span>
    <button class="btn" id="ed-add-service">Add service</button>
    <button class="btn" id="ed-add-group">Add group</button>
    <button class="btn" id="ed-settings">Page settings</button>
    <button class="btn" id="ed-cancel">Cancel</button>
    <button class="btn primary" id="ed-save">Save</button>
  </div>
  <dialog id="dlg"></dialog>
  <div id="toast"></div>
"""

# Reordering only — the viewer script never invents a tile, so a bug in it can
# hide a link but can never fabricate one that is not in config.yaml.
JS = """
var BOOT = window.__BOOT__;
var PREF = 'porchlight.prefs';
// Legacy configs used s/m/l. Map them once on read so an old config keeps
// working and is written back as a number on the next save.
var LEGACY = { s: 56, m: 88, l: 120 };
var state = Object.assign({}, BOOT.defaults);
try { Object.assign(state, JSON.parse(localStorage.getItem(PREF) || '{}')); } catch (e) {}

var main = document.getElementById('main');
var tiles = [].slice.call(main.querySelectorAll('.tile'));

function applyView() {
  if (typeof state.size === 'string') state.size = LEGACY[state.size] || 88;
  var root = document.documentElement.style;
  root.setProperty('--art', state.size + 'px');
  // The tile is the icon plus its label and padding. Deriving the column width
  // rather than storing it means one slider cannot produce a broken grid.
  root.setProperty('--cell', Math.max(72, state.size + 62) + 'px');
  root.setProperty('--gap-x', state.gapX + 'px');
  root.setProperty('--gap-y', state.gapY + 'px');
  root.setProperty('--pad-x', state.padX + 'px');
  root.setProperty('--pad-y', state.padY + 'px');
  // A scrim over the background image. 0 shows the photo untouched; 100 hides
  // it completely. Labels have to stay readable over whatever gets uploaded,
  // and only the person looking at it can judge that.
  root.setProperty('--bg-dim', (state.bgDim / 100).toFixed(2));
  document.body.classList.toggle('light', state.bgTint === 'light');

  if (document.body.classList.contains('editing')) return;

  var byName = function (a, b) { return a.dataset.name.localeCompare(b.dataset.name); };
  var curated = state.layout === 'grouped'
    // Inside a group, the group's own order is what you arranged.
    ? function (a, b) { return (+a.dataset.order) - (+b.dataset.order); }
    // One grid has its own arrangement, independent of the groups.
    : function (a, b) { return (+a.dataset.pos) - (+b.dataset.pos); };
  var sorted = tiles.slice().sort(state.sort === 'az' ? byName : curated);

  main.textContent = '';
  if (state.layout === 'grouped') {
    BOOT.groups.forEach(function (g) {
      var mine = sorted.filter(function (t) { return t.dataset.group === g; });
      if (!mine.length) return;
      var h = document.createElement('h2');
      h.textContent = g;
      var grid = document.createElement('div');
      grid.className = 'grid';
      mine.forEach(function (t) { grid.appendChild(t); });
      main.appendChild(h);
      main.appendChild(grid);
    });
  } else {
    var grid = document.createElement('div');
    grid.className = 'grid';
    sorted.forEach(function (t) { grid.appendChild(t); });
    main.appendChild(grid);
  }
  syncControls();
}

function syncControls() {
  document.querySelectorAll('#controls .seg button').forEach(function (b) {
    b.setAttribute('aria-pressed', state[b.dataset.key] === b.dataset.value);
  });
  document.getElementById('size').value = state.size;
  document.getElementById('gapx').value = state.gapX;
  document.getElementById('gapy').value = state.gapY;
  document.getElementById('padx').value = state.padX;
  document.getElementById('pady').value = state.padY;
  document.getElementById('bgdim').value = state.bgDim;
  try { localStorage.setItem(PREF, JSON.stringify(state)); } catch (e) {}
}

var panel = document.getElementById('controls');
var prefsBtn = document.getElementById('prefs-btn');

panel.addEventListener('click', function (ev) {
  var b = ev.target.closest('.seg button');
  if (!b) return;
  state[b.dataset.key] = b.dataset.value;
  applyView();
});
document.getElementById('size').addEventListener('input', function () {
  state.size = +this.value; applyView();
});
document.getElementById('gapx').addEventListener('input', function () {
  state.gapX = +this.value; applyView();
});
document.getElementById('gapy').addEventListener('input', function () {
  state.gapY = +this.value; applyView();
});
document.getElementById('padx').addEventListener('input', function () {
  state.padX = +this.value; applyView();
});
document.getElementById('pady').addEventListener('input', function () {
  state.padY = +this.value; applyView();
});
document.getElementById('bgdim').addEventListener('input', function () {
  state.bgDim = +this.value; applyView();
});
document.getElementById('reset-prefs').addEventListener('click', function () {
  state = Object.assign({}, BOOT.defaults);
  try { localStorage.removeItem(PREF); } catch (e) {}
  applyView();
});

function openPanel(yes) {
  panel.classList.toggle('open', yes);
  prefsBtn.setAttribute('aria-expanded', yes);
}
prefsBtn.addEventListener('click', function (ev) {
  ev.stopPropagation();
  openPanel(!panel.classList.contains('open'));
});
document.addEventListener('click', function (ev) {
  if (!document.getElementById('prefs').contains(ev.target)) openPanel(false);
});
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Escape') openPanel(false);
});

prefsBtn.classList.add('on');
document.getElementById('prefs').classList.toggle('has-bg', !!BOOT.hasBg);
applyView();
"""

# The editor is the only part of this page that talks to a server, and it does
# so only on an explicit Save. Everything up to that point is local: a failed
# or refused write loses nothing that was not already on screen.
EDITOR_JS = """
(function () {
  var cfg = null;          // working copy, only while editing
  var dlg = document.getElementById('dlg');

  function toast(msg, ms) {
    var t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('on'); }, ms || 2600);
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function esc(s) { return String(s == null ? '' : s); }

  function api(method, path, body, isRaw) {
    var headers = {};
    if (!isRaw) headers['Content-Type'] = 'application/json';
    return fetch(path, { method: method, headers: headers, body: isRaw ? body : JSON.stringify(body) })
      .then(function (r) {
        // nginx answers some failures itself, in HTML — parsing that as JSON
        // would report a syntax error instead of the actual reason.
        return r.text().then(function (body) {
          var j = null;
          try { j = JSON.parse(body); } catch (e) {}
          if (!j) throw new Error(r.status === 413
            ? 'File too large for the upload limit'
            : 'HTTP ' + r.status + ' ' + r.statusText);
          if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
          return j;
        });
      });
  }

  function iconSrc(svc) {
    if (!svc.icon) return null;
    if (svc.icon_url && svc.icon_url.indexOf('://') > -1 && !BOOT.iconFiles[svc.icon]) return svc.icon_url;
    var f = BOOT.iconFiles[svc.icon];
    return f ? 'icons/' + f : null;
  }
  function initials(n) { return (String(n).replace(/[^a-z0-9]/gi, '').slice(0, 2) || '?').toUpperCase(); }

  // Same rules as normalise_href() in build.py and api.py. Applied on blur so
  // you SEE what will be saved: a bare host is a relative reference, and a link
  // to 'plex.example.com' otherwise sends you to launcher/plex.example.com —
  // no error, just the wrong place.
  function normaliseHref(raw) {
    var h = (raw || '').trim();
    if (!h || h.indexOf('://') > -1 || /^[\/#]|^mailto:/.test(h)) return h;
    var host = h.split('/')[0];
    if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?$/.test(host)) return 'http://' + h;
    var port = host.indexOf(':') > -1 ? host.split(':').pop() : '';
    if (/^\d+$/.test(port) && port !== '443') return 'http://' + h;
    return 'https://' + h;
  }
  function colourFor(n) {
    var P = ['#4f7cac', '#7a5c9e', '#3f8f6f', '#b0703c', '#a4515f', '#4a6fa5'], s = 0;
    for (var i = 0; i < n.length; i++) s += n.charCodeAt(i);
    return P[s % P.length];
  }

  // ── rendering the editable board ──────────────────────────────────────
  var PLATES = { light: '#ffffff', dark: '#1c1e21' };
  function plateColour(v) {
    v = (v || '').trim();
    return v ? (PLATES[v.toLowerCase()] || v) : '';
  }

  function art(svc) {
    var src = iconSrc(svc);
    if (src) {
      var img = '<img src="' + esc(src) + '" alt="">';
      var pc = plateColour(svc.plate);
      if (pc) img = '<span class="plate" style="background:' + esc(pc) + '">' + img + '</span>';
      return '<span class="art">' + img + '</span>';
    }
    return '<span class="art"><span class="letters" style="background:' + colourFor(svc.name) +
           '">' + initials(svc.name) + '</span></span>';
  }

  // Editing follows the view. Arranging one grid by shuffling groups was the
  // awkward part: the two orders are independent, so the editor now offers both
  // and writes whichever you touched.
  var editLayout = 'grouped';
  var toldAboutSort = false;

  // Dragging a tile while the page sorts A-Z is work that gets thrown away on
  // the next render. Rather than let that happen quietly, arranging anything
  // switches to your own order — both the saved default and, because a browser
  // preference wins over the default, this browser's own setting.
  function useCuratedOrder() {
    if (cfg.settings.sort === 'curated' && state.sort === 'curated') return;
    cfg.settings.sort = 'curated';
    state.sort = 'curated';
    try { localStorage.setItem(PREF, JSON.stringify(state)); } catch (e) {}
    if (!toldAboutSort) {
      toldAboutSort = true;
      toast('Sorting switched from A\u2013Z to your own order', 4000);
    }
  }

  function flatten() {
    var out = [];
    cfg.groups.forEach(function (g, gi) {
      (g.services || []).forEach(function (svc, si) { out.push({ gi: gi, si: si, svc: svc }); });
    });
    if (out.some(function (e) { return e.svc.pos != null; })) {
      out.sort(function (a, b) {
        var ap = a.svc.pos == null ? 1e9 : a.svc.pos;
        var bp = b.svc.pos == null ? 1e9 : b.svc.pos;
        return ap - bp;
      });
    }
    return out;
  }

  function renumber(list) { list.forEach(function (e, i) { e.svc.pos = i; }); }

  function renderFlat() {
    var main = document.getElementById('main');
    main.textContent = '';
    var list = flatten();
    renumber(list);
    var grid = document.createElement('div');
    grid.className = 'grid';

    list.forEach(function (entry, index) {
      var el = document.createElement('div');
      el.className = 'tile';
      el.draggable = true;
      el.innerHTML = art(entry.svc) +
        '<span class="label">' + esc(entry.svc.name) + '</span>' +
        '<span class="badge"><button data-act="edit" title="Edit">&#9998;</button>' +
        '<button data-act="del" title="Remove">&times;</button></span>';
      el.addEventListener('click', function (ev) {
        var b = ev.target.closest('button');
        if (b && b.dataset.act === 'del') {
          cfg.groups[entry.gi].services.splice(entry.si, 1);
          renumber(flatten());
          render();
          return;
        }
        editService(entry.gi, entry.si);
      });
      el.addEventListener('dragstart', function (ev) {
        ev.dataTransfer.setData('text/plain', String(index));
        ev.dataTransfer.effectAllowed = 'move';
        el.classList.add('dragging');
      });
      el.addEventListener('dragend', function () { el.classList.remove('dragging'); });
      el.addEventListener('dragover', function (ev) { ev.preventDefault(); el.classList.add('drop-before'); });
      el.addEventListener('dragleave', function () { el.classList.remove('drop-before'); });
      el.addEventListener('drop', function (ev) {
        ev.preventDefault(); ev.stopPropagation();
        el.classList.remove('drop-before');
        var from = parseInt(ev.dataTransfer.getData('text/plain'), 10);
        if (isNaN(from) || from === index) return;
        var moved = list.splice(from, 1)[0];
        list.splice(from < index ? index - 1 : index, 0, moved);
        renumber(list);
        useCuratedOrder();
        render();
      });
      grid.appendChild(el);
    });

    var add = document.createElement('button');
    add.className = 'addtile'; add.type = 'button'; add.textContent = '+';
    add.title = 'Add a service';
    add.addEventListener('click', function () { pickApp(0); });
    grid.appendChild(add);

    grid.addEventListener('dragover', function (ev) { ev.preventDefault(); });
    grid.addEventListener('drop', function (ev) {
      ev.preventDefault();
      var from = parseInt(ev.dataTransfer.getData('text/plain'), 10);
      if (isNaN(from)) return;
      list.push(list.splice(from, 1)[0]);
      renumber(list);
      useCuratedOrder();
      render();
    });
    main.appendChild(grid);
  }

  function render() {
    if (editLayout === 'flat') return renderFlat();
    return renderGrouped();
  }

  function renderGrouped() {
    var main = document.getElementById('main');
    main.textContent = '';
    cfg.groups.forEach(function (g, gi) {
      var head = document.createElement('div');
      head.className = 'grouphead';
      head.innerHTML =
        '<input value="' + esc(g.name).replace(/"/g, '&quot;') + '" aria-label="Group name">' +
        '<button class="mini" data-act="gup" title="Move group up">&uarr;</button>' +
        '<button class="mini" data-act="gdown" title="Move group down">&darr;</button>' +
        '<button class="mini" data-act="gdel" title="Delete group">&times;</button>';
      head.querySelector('input').addEventListener('input', function () { g.name = this.value; });
      head.addEventListener('click', function (ev) {
        var b = ev.target.closest('button'); if (!b) return;
        if (b.dataset.act === 'gup' && gi > 0) swapGroup(gi, gi - 1);
        if (b.dataset.act === 'gdown' && gi < cfg.groups.length - 1) swapGroup(gi, gi + 1);
        if (b.dataset.act === 'gdel') {
          if ((g.services || []).length && !confirm('Delete "' + g.name + '" and its ' +
              g.services.length + ' service(s)?')) return;
          cfg.groups.splice(gi, 1); render();
        }
      });
      main.appendChild(head);

      var grid = document.createElement('div');
      grid.className = 'grid';
      grid.dataset.gi = gi;
      (g.services || []).forEach(function (svc, si) {
        grid.appendChild(tileEl(svc, gi, si));
      });
      var add = document.createElement('button');
      add.className = 'addtile'; add.type = 'button'; add.textContent = '+';
      add.title = 'Add a service to ' + g.name;
      add.addEventListener('click', function () { pickApp(gi); });
      grid.appendChild(add);
      wireDrop(grid);
      main.appendChild(grid);
    });
  }

  function swapGroup(a, b) {
    var t = cfg.groups[a]; cfg.groups[a] = cfg.groups[b]; cfg.groups[b] = t;
    useCuratedOrder();
    render();
  }

  function tileEl(svc, gi, si) {
    var el = document.createElement('div');
    el.className = 'tile';
    el.draggable = true;
    el.dataset.gi = gi; el.dataset.si = si;
    el.innerHTML = art(svc) +
      '<span class="label">' + esc(svc.name) + '</span>' +
      '<span class="badge"><button data-act="edit" title="Edit">&#9998;</button>' +
      '<button data-act="del" title="Remove">&times;</button></span>';
    el.addEventListener('click', function (ev) {
      var b = ev.target.closest('button');
      if (b && b.dataset.act === 'del') { cfg.groups[gi].services.splice(si, 1); render(); return; }
      editService(gi, si);
    });
    el.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/plain', gi + ':' + si);
      ev.dataTransfer.effectAllowed = 'move';
      el.classList.add('dragging');
    });
    el.addEventListener('dragend', function () { el.classList.remove('dragging'); });
    el.addEventListener('dragover', function (ev) { ev.preventDefault(); el.classList.add('drop-before'); });
    el.addEventListener('dragleave', function () { el.classList.remove('drop-before'); });
    el.addEventListener('drop', function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      el.classList.remove('drop-before');
      move(ev.dataTransfer.getData('text/plain'), +el.dataset.gi, +el.dataset.si);
    });
    return el;
  }

  function wireDrop(grid) {
    grid.addEventListener('dragover', function (ev) { ev.preventDefault(); });
    grid.addEventListener('drop', function (ev) {
      ev.preventDefault();
      move(ev.dataTransfer.getData('text/plain'), +grid.dataset.gi, -1);
    });
  }

  function move(from, tgi, tsi) {
    if (!from) return;
    var p = from.split(':'), fgi = +p[0], fsi = +p[1];
    var svc = cfg.groups[fgi].services.splice(fsi, 1)[0];
    if (!svc) { render(); return; }
    var dest = cfg.groups[tgi].services;
    if (fgi === tgi && tsi > fsi) tsi--;
    dest.splice(tsi < 0 ? dest.length : tsi, 0, svc);
    useCuratedOrder();
    render();
  }

  // ── dialogs ───────────────────────────────────────────────────────────
  function field(label, id, value, hint, type, placeholder) {
    return '<div class="field"><label for="' + id + '">' + label + '</label>' +
      '<input type="' + (type || 'text') + '" id="' + id + '" value="' +
      esc(value).replace(/"/g, '&quot;') + '"' +
      (placeholder ? ' placeholder="' + esc(placeholder).replace(/"/g, '&quot;') + '"' : '') +
      '>' +
      (hint ? '<span class="hint">' + hint + '</span>' : '') + '</div>';
  }

  // ── catalogue picker ──────────────────────────────────────────────────
  // 200-odd known self-hosted apps with a verified icon and a usual port, so
  // the common case is two clicks and the uncommon one is still a blank form.
  var catalog = null;
  var ICON_CDN_SVG = 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/';
  var ICON_CDN_PNG = 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/';

  function catalogIconUrl(app) {
    // Prefer a copy already on this server: an install run with --prefetch is
    // fully offline, and there is no reason to go out for what is local.
    var local = BOOT.iconFiles[app.icon];
    if (local) return 'icons/' + local;
    if (app.url) return app.url;
    return (app.ext === 'png' ? ICON_CDN_PNG : ICON_CDN_SVG) + app.icon +
           (app.ext === 'png' ? '.png' : '.svg');
  }

  function pickApp(gi) {
    dlg.className = 'wide';
    dlg.innerHTML = '<form method="dialog">' +
      '<h3>Add a service</h3>' +
      '<input id="pick-search" type="text" placeholder="Search 200+ known apps\u2026" autofocus>' +
      '<div id="pick-list"><div id="pick-empty">Loading catalogue\u2026</div></div>' +
      '<div class="actions">' +
        '<button class="btn grow" type="button" id="pick-custom">Something else\u2026</button>' +
        '<button class="btn" value="cancel">Cancel</button>' +
      '</div></form>';

    var list = dlg.querySelector('#pick-list');
    var search = dlg.querySelector('#pick-search');

    function draw(q) {
      q = (q || '').trim().toLowerCase();
      var html = '', hits = 0;
      (catalog || []).forEach(function (cat) {
        var apps = (cat.apps || []).filter(function (a) {
          return !q || a.name.toLowerCase().indexOf(q) > -1 ||
                 cat.name.toLowerCase().indexOf(q) > -1;
        });
        if (!apps.length) return;
        html += '<h4>' + esc(cat.name) + '</h4>';
        apps.forEach(function (a) {
          hits++;
          html += '<button type="button" class="pick" data-icon="' + esc(a.icon) + '"' +
            ' data-name="' + esc(a.name).replace(/"/g, '&quot;') + '"' +
            ' data-ext="' + esc(a.ext || '') + '" data-port="' + (a.port || '') + '"' +
            ' data-url="' + esc(a.url || '') + '">' +
            '<img src="' + catalogIconUrl(a) + '" alt="" loading="lazy" ' +
            'data-letters="' + initials(a.name) + '">' +
            '<span>' + esc(a.name) + '</span>' +
            (a.port ? '<span class="port">:' + a.port + '</span>' : '') +
            '</button>';
        });
      });
      list.innerHTML = hits ? html
        : '<div id="pick-empty">Nothing matches \u2014 use <b>Something else\u2026</b> ' +
          'to add it by hand.</div>';
      // A CDN thumbnail can 404, or be unreachable on an offline install. Swap
      // it for letters rather than leaving a broken-image glyph in the list.
      // Attached as a listener, not an inline onerror attribute: that attribute
      // has to survive three layers of quoting, and got it wrong once already
      // in a way that took the whole script out.
      list.querySelectorAll('img[data-letters]').forEach(function (img) {
        img.addEventListener('error', function () {
          var span = document.createElement('span');
          span.className = 'fallback';
          span.style.background = '#4a4f57';
          span.textContent = img.dataset.letters;
          img.replaceWith(span);
        });
      });
    }

    function ready() { draw(search.value); }
    if (catalog) ready();
    else {
      fetch('catalog.json')
        .then(function (r) { return r.json(); })
        .then(function (j) { catalog = j; ready(); })
        .catch(function () {
          catalog = [];
          list.innerHTML = '<div id="pick-empty">No catalogue on this install. ' +
            'Use <b>Something else\u2026</b>.</div>';
        });
    }

    search.addEventListener('input', function () { draw(this.value); });
    list.addEventListener('click', function (ev) {
      var b = ev.target.closest('.pick');
      if (!b) return;
      var pre = { name: b.dataset.name, icon: b.dataset.icon, port: b.dataset.port };
      if (b.dataset.url) pre.icon_url = b.dataset.url;
      else if (b.dataset.ext === 'png') pre.icon_url = ICON_CDN_PNG + b.dataset.icon + '.png';
      dlg.returnValue = 'cancel';
      dlg.close();
      editService(gi, -1, pre);
    });
    dlg.querySelector('#pick-custom').addEventListener('click', function () {
      dlg.returnValue = 'cancel';
      dlg.close();
      editService(gi, -1, {});
    });
    dlg.onclose = null;
    dlg.showModal();
  }

  function editService(gi, si, pre) {
    var isNew = si < 0;
    pre = pre || {};
    var svc = isNew
      ? { name: pre.name || '', href: '', icon: pre.icon || '', icon_url: pre.icon_url || '' }
      : clone(cfg.groups[gi].services[si]);
    // The catalogue knows the usual port but not your host, so it becomes a
    // placeholder rather than a value — a prefilled URL that is wrong for
    // everyone is worse than an empty one.
    var urlHint = pre.port ? 'http://192.168.1.10:' + pre.port : 'https://example.lan';
    var opts = cfg.groups.map(function (g, i) {
      return '<option value="' + i + '"' + (i === gi ? ' selected' : '') + '>' + esc(g.name) + '</option>';
    }).join('');
    dlg.className = '';
    dlg.innerHTML = '<form method="dialog">' +
      '<h3>' + (isNew ? 'Add service' : 'Edit service') + '</h3>' +
      field('Name', 'f-name', svc.name) +
      field('URL', 'f-href', svc.href,
            (pre.port ? esc(pre.name) + ' usually listens on port ' + pre.port + '. ' : '') +
            'Opens in a new tab. A bare host gets https:// added \u2014 or http:// for an ' +
            'IP address or a non-443 port.',
            'text', urlHint) +
      '<div class="field"><label for="f-group">Group</label><select id="f-group">' + opts + '</select></div>' +
      '<div class="field"><label>Icon</label><div class="row">' +
        '<span class="preview" id="f-prev"></span>' +
        '<input class="grow" type="text" id="f-icon" placeholder="slug, e.g. sonarr" value="' +
          esc(svc.icon).replace(/"/g, '&quot;') + '">' +
      '</div>' +
      '<span class="hint">A slug from dashboard-icons, or leave blank for a lettered tile. ' +
      'Check it reads on a dark background — several upstream marks are near-black and have a ' +
      '<code>-light</code> variant.</span></div>' +
      field('Icon URL', 'f-iconurl', svc.icon_url || '',
            'Only for a mark upstream does not carry. Fetched on save and shipped with the page.') +
      field('Note', 'f-note', svc.note || '',
            'Why this entry is the way it is. Shows as the tile tooltip, and survives a save \u2014 ' +
            'a YAML comment does not.') +
      '<div class="field"><label for="f-upload">Or upload</label>' +
        '<input type="file" id="f-upload" accept="image/*"></div>' +
      '<div class="field"><label>Icon plate</label><div class="row">' +
        '<span class="seg" id="f-plate">' +
          '<button type="button" data-v="">None</button>' +
          '<button type="button" data-v="light">Light</button>' +
          '<button type="button" data-v="dark">Dark</button>' +
        '</span>' +
        '<input type="color" id="f-platec" title="Custom colour" value="' +
          (/^#[0-9a-f]{6}$/i.test(svc.plate || '') ? svc.plate : '#ffffff') + '">' +
      '</div><span class="hint">A shape behind the icon, for marks that vanish into ' +
      'the page \u2014 Tailscale and Vaultwarden are near-black.</span></div>' +
      '<div class="actions">' +
        (isNew ? '' : '<button class="btn danger grow" value="delete">Delete</button>') +
        '<button class="btn" value="cancel">Cancel</button>' +
        '<button class="btn primary" value="ok">Done</button>' +
      '</div></form>';

    var plate = svc.plate || '';
    function syncPlate() {
      dlg.querySelectorAll('#f-plate button').forEach(function (b) {
        b.setAttribute('aria-pressed', b.dataset.v === plate);
      });
    }

    function paint() {
      // Build the preview directly rather than reusing art(): that wraps the
      // image in .art, which is sized by the tile grid's custom property and
      // collapses to nothing inside a dialog.
      var probe = { name: dlg.querySelector('#f-name').value || '?',
                    icon: dlg.querySelector('#f-icon').value.trim(),
                    icon_url: dlg.querySelector('#f-iconurl').value.trim() };
      var src = iconSrc(probe);
      var pc = plateColour(plate);
      dlg.querySelector('#f-prev').innerHTML = src
        ? (pc ? '<span class="plate" style="background:' + esc(pc) + '"><img src="' +
                esc(src) + '" alt=""></span>'
              : '<img src="' + esc(src) + '" alt="">')
        : '<span class="letters" style="background:' + colourFor(probe.name) + '">' +
          initials(probe.name) + '</span>';
      syncPlate();
    }
    ['f-name', 'f-icon', 'f-iconurl'].forEach(function (id) {
      dlg.querySelector('#' + id).addEventListener('input', paint);
    });
    dlg.querySelector('#f-href').addEventListener('blur', function () {
      this.value = normaliseHref(this.value);
    });
    dlg.querySelector('#f-plate').addEventListener('click', function (ev) {
      var b = ev.target.closest('button');
      if (!b) return;
      plate = b.dataset.v;
      paint();
    });
    dlg.querySelector('#f-platec').addEventListener('input', function () {
      plate = this.value;
      paint();
    });

    dlg.querySelector('#f-upload').addEventListener('change', function () {
      var file = this.files[0]; if (!file) return;
      api('POST', '/api/asset?kind=icon&name=' + encodeURIComponent(file.name), file, true)
        .then(function (j) {
          BOOT.iconFiles[j.slug] = j.file;
          dlg.querySelector('#f-icon').value = j.slug;
          dlg.querySelector('#f-iconurl').value = '';
          paint(); toast('Uploaded ' + j.file + ' \u2014 press Done, then Save', 5000);
        })
        .catch(function (e) { toast(e.message, 5000); });
    });
    paint();

    dlg.onclose = function () {
      if (dlg.returnValue === 'cancel' || dlg.returnValue === '') return;
      if (dlg.returnValue === 'delete') { cfg.groups[gi].services.splice(si, 1); render(); return; }
      var out = {
        name: dlg.querySelector('#f-name').value.trim(),
        href: normaliseHref(dlg.querySelector('#f-href').value),
        icon: dlg.querySelector('#f-icon').value.trim()
      };
      var u = dlg.querySelector('#f-iconurl').value.trim();
      if (u) out.icon_url = u;
      var note = dlg.querySelector('#f-note').value.trim();
      if (note) out.note = note;
      if (plate) out.plate = plate;
      if (svc.pos != null) out.pos = svc.pos;
      if (!out.name) { toast('A service needs a name'); return; }
      var tgi = +dlg.querySelector('#f-group').value;
      if (isNew) cfg.groups[tgi].services.push(out);
      else if (tgi === gi) cfg.groups[gi].services[si] = out;
      else { cfg.groups[gi].services.splice(si, 1); cfg.groups[tgi].services.push(out); }
      render();
    };
    dlg.showModal();
  }

  function pageSettings() {
    var s = cfg.settings;
    dlg.className = '';
    var seg = function (key, vals) {
      return '<div class="seg">' + vals.map(function (v) {
        return '<button type="button" data-k="' + key + '" data-v="' + v[0] + '" aria-pressed="' +
          (s[key] === v[0]) + '">' + v[1] + '</button>';
      }).join('') + '</div>';
    };
    dlg.innerHTML = '<form method="dialog">' +
      '<h3>Page settings</h3>' +
      field('Title', 'p-title', s.title || '', 'Blank for no heading.') +
      field('Background', 'p-bg', s.background || '',
            'An image URL, or a filename already uploaded. Blank for none. A big photo is a ' +
            'big page \u2014 this one loads on a bad link too.') +
      '<div class="field"><label for="p-upload">Upload background</label>' +
        '<input type="file" id="p-upload" accept="image/*"></div>' +
      '<div class="ctl"><span>Default layout</span>' + seg('layout', [['flat', 'One grid'], ['grouped', 'Grouped']]) + '</div>' +
      '<div class="ctl"><span>Default sort</span>' + seg('sort', [['az', 'A\\u2013Z'], ['curated', 'Curated']]) + '</div>' +
      '<div class="ctl"><span>Default size</span><input type="range" id="p-sz" min="24" max="260" step="2" value="' +
        (typeof s.size === 'number' ? s.size : (LEGACY[s.size] || 88)) + '"></div>' +
      '<div class="ctl"><span>Default gap X</span><input type="range" id="p-gx" min="0" max="300" step="2" value="' + (s.gapX || 0) + '"></div>' +
      '<div class="ctl"><span>Default gap Y</span><input type="range" id="p-gy" min="0" max="300" step="2" value="' + (s.gapY || 0) + '"></div>' +
      '<div class="ctl"><span>Default margin X</span><input type="range" id="p-px" min="0" max="800" step="4" value="' + (s.padX || 0) + '"></div>' +
      '<div class="ctl"><span>Default margin Y</span><input type="range" id="p-py" min="0" max="800" step="4" value="' + (s.padY || 0) + '"></div>' +
      '<div class="ctl"><span>Default tint</span>' + seg('bgTint', [['dark', 'Dark'], ['light', 'Light']]) + '</div>' +
      '<div class="ctl"><span>Default backdrop</span><input type="range" id="p-bd" min="0" max="100" step="1" value="' +
        (s.bgDim == null ? 72 : s.bgDim) + '"></div>' +
      '<span class="hint">Defaults apply to a browser that has not set its own. ' +
      'Yours are remembered locally and are not changed by this.</span>' +
      '<div class="actions"><button class="btn" value="cancel">Cancel</button>' +
      '<button class="btn primary" value="ok">Done</button></div></form>';

    dlg.querySelectorAll('.seg button').forEach(function (b) {
      b.addEventListener('click', function () {
        s[b.dataset.k] = b.dataset.v;
        dlg.querySelectorAll('.seg button[data-k="' + b.dataset.k + '"]').forEach(function (o) {
          o.setAttribute('aria-pressed', o.dataset.v === b.dataset.v);
        });
      });
    });
    dlg.querySelector('#p-upload').addEventListener('change', function () {
      var file = this.files[0]; if (!file) return;
      api('POST', '/api/asset?kind=bg&name=' + encodeURIComponent(file.name), file, true)
        .then(function (j) {
          dlg.querySelector('#p-bg').value = j.file;
          toast('Uploaded ' + j.file + ' (' + (j.bytes / 1048576).toFixed(1) +
                ' MB) \u2014 press Done, then Save', 6000);
        })
        .catch(function (e) { toast(e.message, 6000); });
    });
    dlg.onclose = function () {
      if (dlg.returnValue !== 'ok') return;
      s.title = dlg.querySelector('#p-title').value.trim();
      s.background = dlg.querySelector('#p-bg').value.trim();
      s.gapX = +dlg.querySelector('#p-gx').value;
      s.size = +dlg.querySelector('#p-sz').value;
      s.gapY = +dlg.querySelector('#p-gy').value;
      s.padX = +dlg.querySelector('#p-px').value;
      s.padY = +dlg.querySelector('#p-py').value;
      s.bgDim = +dlg.querySelector('#p-bd').value;
      toast('Applied on save');
    };
    dlg.showModal();
  }

  // ── mode switching ────────────────────────────────────────────────────
  function enter() {
    cfg = clone(BOOT.config);
    cfg.settings = Object.assign({ title: '', background: '', layout: 'flat', sort: 'az',
                                   size: 88, gapX: 8, gapY: 8, padX: 32, padY: 28,
                                   bgDim: 72, bgTint: 'dark' },
                                 cfg.settings || {});
    cfg.groups = cfg.groups || [];
    // Open the editor on whatever you are currently looking at.
    editLayout = state.layout === 'grouped' ? 'grouped' : 'flat';
    document.querySelectorAll('#ed-layout button').forEach(function (o) {
      o.setAttribute('aria-pressed', o.dataset.v === editLayout);
    });
    document.body.classList.add('editing');
    openPanel(false);
    render();
  }
  function leave() { document.body.classList.remove('editing'); cfg = null; applyView(); }

  document.getElementById('ed-layout').addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    editLayout = b.dataset.v;
    this.querySelectorAll('button').forEach(function (o) {
      o.setAttribute('aria-pressed', o.dataset.v === editLayout);
    });
    render();
  });

  document.getElementById('edit-open').addEventListener('click', enter);
  document.getElementById('ed-cancel').addEventListener('click', function () {
    if (confirm('Discard changes?')) leave();
  });
  document.getElementById('ed-add-group').addEventListener('click', function () {
    var n = prompt('Group name:', ''); if (!n) return;
    cfg.groups.push({ name: n.trim(), services: [] }); render();
  });
  document.getElementById('ed-add-service').addEventListener('click', function () {
    if (!cfg.groups.length) { toast('Add a group first'); return; }
    pickApp(0);
  });
  document.getElementById('ed-settings').addEventListener('click', pageSettings);
  document.getElementById('ed-save').addEventListener('click', function () {
    var btn = this; btn.disabled = true;
    // Empty groups are dropped on the way out rather than persisted: a group
    // with nothing in it renders as a heading over blank space.
    var out = clone(cfg);
    out.groups = out.groups.filter(function (g) { return (g.services || []).length; });
    api('PUT', '/api/config', out)
      .then(function () { toast('Saved — reloading'); setTimeout(function () { location.reload(); }, 600); })
      .catch(function (e) { toast(e.message, 6000); btn.disabled = false; });
  });
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
