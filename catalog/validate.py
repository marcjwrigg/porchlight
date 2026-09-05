#!/usr/bin/env python3
"""Check every catalogue icon actually exists upstream.

    python3 catalog/validate.py

Run this before opening a PR that touches catalog/apps.yaml. A wrong slug is
invisible in review and only shows up as a lettered tile for whoever picks that
app, which is a bad way to find out.

Checks, against homarr-labs/dashboard-icons:
  * every `icon` resolves as .svg, or as .png when `ext: png` is set
  * `ext: png` is present exactly when there is no .svg
  * no duplicate app names, no empty categories
"""
import json
import sys
import urllib.request

import yaml

TREE = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons@main/tree.json"
CATALOG = "catalog/apps.yaml"


def main():
    with urllib.request.urlopen(TREE, timeout=30) as r:
        tree = json.load(r)
    svg = {n[:-4] for n in tree["svg"]}
    png = {n[:-4] for n in tree["png"]}

    cats = (yaml.safe_load(open(CATALOG)) or {}).get("categories") or []
    problems, names, total = [], {}, 0

    for cat in cats:
        apps = cat.get("apps") or []
        if not apps:
            problems.append(f"category {cat.get('name')!r} is empty")
        for app in apps:
            total += 1
            name, slug, ext = app.get("name"), app.get("icon"), app.get("ext")
            where = f"{cat.get('name')} / {name}"
            if name in names:
                problems.append(f"{where}: duplicate name, also in {names[name]}")
            names[name] = cat.get("name")
            if not slug:
                problems.append(f"{where}: no icon slug")
                continue
            if ext == "png":
                if slug not in png:
                    problems.append(f"{where}: {slug}.png does not exist upstream")
                elif slug in svg:
                    problems.append(f"{where}: {slug} has an .svg — drop `ext: png`")
            elif slug not in svg:
                hint = " (it exists as .png — add `ext: png`)" if slug in png else ""
                problems.append(f"{where}: {slug}.svg does not exist upstream{hint}")

    print(f"{total} apps in {len(cats)} categories")
    for p in problems:
        print(f"  !! {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
