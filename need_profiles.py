#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一覧に載った会社のうち、会社情報（FMP）がまだ無い銘柄を出す（毎日の更新で FMP に聞く銘柄）。
使い方: python3 need_profiles.py --data out/data.json --profiles market/profiles.json [--max 25]
出力: JSON の配列（例 ["ABC","XYZ"]）を 1 行で。まだ無い銘柄、30 日より前に取った銘柄、上場日（ipo_date）をまだ持っていない銘柄の順。
  FMP で見つからなかった銘柄（not_found）は聞き直さない"""
import argparse, json, os

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="out/data.json")
ap.add_argument("--profiles", default="market/profiles.json")
ap.add_argument("--max", type=int, default=25)
a = ap.parse_args()
d = json.load(open(a.data, encoding="utf-8"))
p = json.load(open(a.profiles, encoding="utf-8")) if os.path.exists(a.profiles) else {}
from datetime import datetime, timedelta, timezone
limit = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
fresh = set()
for k, v in p.items():
    if isinstance(v, dict) and (v.get("not_found") or str(v.get("fetched", "")) >= limit):
        fresh.add(k.upper())
        if v.get("symbol"):
            fresh.add(str(v["symbol"]).upper())
# 上場日（ipo_date）をまだ持っていない会社情報（2026-10-28 より前に取った物）も、最後に聞き直す（上場のときの買いの見分けに使う）
noipo = set()
for k, v in p.items():
    if isinstance(v, dict) and not v.get("not_found") and "ipo_date" not in v:
        noipo.add(k.upper())
        if v.get("symbol"):
            noipo.add(str(v["symbol"]).upper())
need, stale, late = [], [], []
for c in d.get("companies", []):
    s = c["symbol"].upper()
    if s in need or s in stale or s in late:
        continue
    if s in fresh:
        if s in noipo:
            late.append(s)
        continue
    known = any(k.upper() == s or str(v.get("symbol", "")).upper() == s for k, v in p.items() if isinstance(v, dict))
    (stale if known else need).append(s)
# まだ無い銘柄を先に、30 日より古い銘柄（株価の検査のため聞き直す）を次に、上場日の無い銘柄を最後に
print(json.dumps((need + stale + late)[: a.max]))
