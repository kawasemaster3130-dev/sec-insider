#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SECインサイダー  相場の付け足し（ドル円・現在値）。鍵なし・標準ライブラリだけ。どれも取れなくても止めない。
  ドル円: frankfurter.dev（欧州中央銀行の参照レート。平日1日1回更新。おおよその円換算に使うだけ）
  現在値: stooq.com の軽い気配 API（終値ベース。取れない銘柄は空のまま。会社名は EDGAR の issuerName を使う）
使い方: python3 fetch_market.py --days data/days --out data/market [--window 30]
"""
import argparse, csv, glob, io, json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (yakuin-kai watch; contact via repo)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_fx(out_dir):
    try:
        j = json.loads(get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=JPY"))
        rate = float(j["rates"]["JPY"])
        row = [{"symbol": "USDJPY", "name": "USD/JPY (ECB reference, frankfurter.dev)", "price": rate, "date": j.get("date", "")}]
        with open(os.path.join(out_dir, "usdjpy.json"), "w", encoding="utf-8") as f:
            json.dump(row, f)
        print(f"ドル円 {rate} ({j.get('date')})")
    except Exception as e:
        print(f"[warn] ドル円が取れない: {e}（前回のファイルがあればそれを使う）", file=sys.stderr)


def symbols_from_days(days_dir, window):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).date().isoformat()
    syms = set()
    for fp in glob.glob(os.path.join(days_dir, "*.json")):
        name = os.path.basename(fp)[:10]
        if name < cutoff:
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for r in d.get("rows", []):
            s = str(r.get("symbol") or "").strip().upper()
            # 一覧に載る見込みのある物だけ（1万ドル以上）
            try:
                if s and float(r.get("securitiesTransacted") or 0) * float(r.get("price") or 0) >= 10000:
                    syms.add(s)
            except Exception:
                pass
    return sorted(syms)


def fetch_prices(syms, out_dir):
    rows = []
    for i in range(0, len(syms), 40):
        batch = syms[i:i + 40]
        q = "+".join(s.lower().replace(".", "-") + ".us" for s in batch)
        try:
            txt = get(f"https://stooq.com/q/l/?s={q}&f=sd2t2ohlcv&h&e=csv")
            for rec in csv.DictReader(io.StringIO(txt)):
                sym = (rec.get("Symbol") or "").upper().replace(".US", "").replace("-", ".")
                try:
                    close = float(rec.get("Close") or "")
                except ValueError:
                    continue
                if sym and close > 0:
                    rows.append({"symbol": sym, "name": "", "price": close, "date": rec.get("Date", "")})
        except Exception as e:
            print(f"[warn] 現在値の取得に失敗（{batch[0]}〜）: {e}", file=sys.stderr)
        time.sleep(1.0)
    with open(os.path.join(out_dir, "quotes", "stooq.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f)
    print(f"現在値: {len(rows)}/{len(syms)} 銘柄")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="data/days")
    ap.add_argument("--out", default="data/market")
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--no-prices", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "quotes"), exist_ok=True)
    fetch_fx(a.out)
    if not a.no_prices:
        fetch_prices(symbols_from_days(a.days, a.window), a.out)


if __name__ == "__main__":
    main()
