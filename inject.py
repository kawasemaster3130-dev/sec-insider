#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.template.html の __YK_DATA_JSON__ に out/data.json を埋め込んで out/app.html を作る。
使い方: python3 inject.py --template app.template.html --data out/data.json --out out/app.html [--standalone]"""
import argparse, json, hashlib

ap = argparse.ArgumentParser()
ap.add_argument("--template", default="app.template.html")
ap.add_argument("--data", default="out/data.json")
ap.add_argument("--out", default="out/app.html")
ap.add_argument("--standalone", action="store_true", help="GitHub Pages などにそのまま置く 1 枚の HTML にする（doctype・viewport・文字コードを足す）。Claude の公開ページに出すときは付けない")
ap.add_argument("--noindex", action="store_true", help="--standalone のとき、検索エンジンに載せない印（meta robots noindex）を足す。URL を知っている人だけが見るページ用")
a = ap.parse_args()

with open(a.template, "r", encoding="utf-8") as f:
    tpl = f.read()
with open(a.data, "r", encoding="utf-8") as f:
    data = json.load(f)
# script タグの中に入れるので「<」をすべて \u003c に（</script や <!-- で壊れないように。JSON として正しいまま）。U+2028/2029 も避ける
js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
js = js.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
assert json.loads(js) == data, "埋め込むデータが元と一致しない"
assert tpl.count("__YK_DATA_JSON__") == 1, "置き場の印が1つではない"
html = tpl.replace("__YK_DATA_JSON__", js)
if a.standalone:
    html = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            + ('<meta name="robots" content="noindex,nofollow">' if a.noindex else '')
            + '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>[hidden]{display:none!important}img{max-width:100%}body{margin:0}</style></head><body>'
            + html + '</body></html>')
# 書いた物から埋め込みを取り出して読めるか確かめる（読めなければ出口 2）
import re, sys
m = re.search(r'<script id="yk-data" type="application/json">(.*?)</script>', html, re.S)
try:
    ok = m is not None and json.loads(m.group(1)) == data
except Exception:
    ok = False
if not ok:
    print("NG: 埋め込んだデータが読めない", file=sys.stderr)
    sys.exit(2)
with open(a.out, "w", encoding="utf-8") as f:
    f.write(html)
sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
print(f"{a.out}: {len(html):,} bytes, 会社 {len(data.get('companies', []))}, sha256 {sha[:12]}")
