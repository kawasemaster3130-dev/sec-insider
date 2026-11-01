#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開ページ（artifact）に一緒に置くファイルの対応表（Artifact の files に渡す JSON）を出す。
使い方: python3 tools/publish_map.py [--root /home/claude/yakuin-kai] [--remove days/2026-09-01.json ...]
  --remove に書いた公開パスは null（公開ページから消す）にする"""
import argparse, glob, json, os

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap.add_argument("--remove", nargs="*", default=[])
a = ap.parse_args()
R = a.root
TXT = "text/plain"
m = {
    "data.json": f"{R}/out/data.json",
    "checks.json": f"{R}/out/checks.json",
    "market/profiles.json": f"{R}/data/market/profiles.json",
    "market/usdjpy.json": f"{R}/data/market/usdjpy.json",
    "market/overrides.json": f"{R}/data/market/overrides.json",
    "market/sec_exchange.json": f"{R}/data/market/sec_exchange.json",
    "build.py": {"from": f"{R}/build.py", "contentType": TXT},
    "inject.py": {"from": f"{R}/inject.py", "contentType": TXT},
    "need_profiles.py": {"from": f"{R}/need_profiles.py", "contentType": TXT},
    "add_profile.py": {"from": f"{R}/add_profile.py", "contentType": TXT},
    "receive_day.py": {"from": f"{R}/browser/receive_day.py", "contentType": TXT},
    "plan_days.py": {"from": f"{R}/browser/plan_days.py", "contentType": TXT},
    "collect_edgar.js": {"from": f"{R}/browser/collect_edgar.js", "contentType": TXT},
    "check_index.js": {"from": f"{R}/browser/check_index.js", "contentType": TXT},
    "run_status.py": {"from": f"{R}/browser/run_status.py", "contentType": TXT},
    "app.template.html": {"from": f"{R}/app.template.html", "contentType": TXT},
}
for fp in sorted(glob.glob(f"{R}/data/days/*.json")):
    m["days/" + os.path.basename(fp)] = fp
for p in a.remove:
    m[p] = None
for k, v in m.items():
    src = v if isinstance(v, str) else (v or {}).get("from")
    if src and not os.path.exists(src):
        raise SystemExit(f"無いファイル: {src}")
print(json.dumps(m, ensure_ascii=False))
