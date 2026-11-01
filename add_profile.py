#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FMP の会社情報（company / profile-symbol）から必要な項目だけを market/profiles.json に足す。
使い方（毎日の更新はこちら。会社名に $ や ` があってもシェルで動かないよう、FMP の返りを JSON のファイルにして渡す）:
  python3 add_profile.py --file market/profiles.json --json market/fmp/HEI.json [--asked "HEI, HEI.A"]
    JSON は FMP の返りそのまま（配列でも 1 社の辞書でもよい）か、そこから次のキーだけを抜いた辞書:
    symbol, price, marketCap, companyName, exchange, exchangeFullName, isEtf, isFund, isAdr, country, currency, ipoDate
    空の配列・{"status":"not_found"}・symbol の無い物は「見つからない」として記録する（次の回に聞き直さない）
手で足すとき:
  python3 add_profile.py --file market/profiles.json --symbol HEI --price 220.5 --name "HEICO Corporation" \\
          --exchange NYSE --exchange-full "New York Stock Exchange" --market-cap 30000000000 --is-etf false --is-fund false \\
          --is-adr false --country US --currency USD [--ipo-date 1993-01-01]
  見つからなかった銘柄: python3 add_profile.py --file market/profiles.json --symbol XYZ --not-found
"""
import argparse, json, math, os, re, sys
from datetime import datetime, timezone


def b(s):
    return str(s).strip().lower() in ("1", "true", "yes")


def num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def txt(v):
    return "" if v is None else str(v).strip()


ap = argparse.ArgumentParser()
ap.add_argument("--file", default="market/profiles.json")
ap.add_argument("--json", default=None, help="FMP の返り（またはそこから抜いた辞書）の JSON ファイル")
ap.add_argument("--symbol", default=None)
ap.add_argument("--asked", default="", help="一覧での表記（例 'HEI, HEI.A'）。省略時は symbol")
ap.add_argument("--price", type=float, default=0.0)
ap.add_argument("--name", default="")
ap.add_argument("--exchange", default="")
ap.add_argument("--exchange-full", default="")
ap.add_argument("--market-cap", type=float, default=0.0)
ap.add_argument("--is-etf", default="false")
ap.add_argument("--is-fund", default="false")
ap.add_argument("--is-adr", default="false")
ap.add_argument("--country", default="")
ap.add_argument("--currency", default="")
ap.add_argument("--ipo-date", default="")
ap.add_argument("--not-found", action="store_true")
a = ap.parse_args()

now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
rec, sym, not_found = None, (a.symbol or "").strip().upper(), a.not_found
if a.json:
    raw_text = open(a.json, encoding="utf-8").read()
    raw = json.loads(raw_text)
    # FMP の上限や誤りの返事（{"Error Message": "Limit Reach …"} など）は「見つからない」と記録しない（次の回に聞き直す）
    if (isinstance(raw, dict) and (raw.get("Error Message") or raw.get("error"))) or re.search(r"limit reach|upgrade your plan|invalid api key", raw_text, re.I):
        print(f"NG {(a.asked or a.symbol or '').strip()}: FMP の上限か誤りの返事なので記録しない（この回は会社情報をここでやめる）"); sys.exit(2)
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        raw = raw["data"]
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict) or raw.get("status") == "not_found" or not txt(raw.get("symbol")):
        not_found = True
        if not sym:
            # 毎日の更新は番号のファイル（1.json）で渡すので、銘柄は --asked の文字を使う（ファイル名は最後の手段）
            asked0 = a.asked.split(",")[0].strip().upper() if a.asked.strip() else ""
            sym = asked0 or os.path.splitext(os.path.basename(a.json))[0].upper()
    else:
        sym = sym or txt(raw.get("symbol")).upper()
        rec = {
            "symbol": txt(raw.get("symbol")).upper(), "name": txt(raw.get("companyName")), "exchange": txt(raw.get("exchange")),
            "exchange_full": txt(raw.get("exchangeFullName")), "price": num(raw.get("price")), "market_cap": num(raw.get("marketCap")),
            "is_etf": b(raw.get("isEtf")), "is_fund": b(raw.get("isFund")), "is_adr": b(raw.get("isAdr")),
            "country": txt(raw.get("country")), "currency": txt(raw.get("currency")), "ipo_date": txt(raw.get("ipoDate")), "fetched": now,
        }
elif not not_found:
    rec = {
        "symbol": sym, "name": a.name, "exchange": a.exchange, "exchange_full": a.exchange_full,
        "price": a.price, "market_cap": a.market_cap, "is_etf": b(a.is_etf), "is_fund": b(a.is_fund), "is_adr": b(a.is_adr),
        "country": a.country, "currency": a.currency, "ipo_date": a.ipo_date, "fetched": now,
    }
if not sym:
    print("NG: 銘柄が分からない（--symbol か、symbol の入った --json が要る）", file=sys.stderr)
    sys.exit(2)

data = {}
if os.path.exists(a.file):
    with open(a.file, encoding="utf-8") as f:
        data = json.load(f)
key = (a.asked or sym).strip().upper()
data[key] = {"symbol": sym, "not_found": True, "fetched": now} if not_found else rec
os.makedirs(os.path.dirname(a.file) or ".", exist_ok=True)
with open(a.file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
print(f"OK {key}: {data[key]}")
