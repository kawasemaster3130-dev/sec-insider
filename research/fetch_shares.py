#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
時価総額を作るための「発行株数」を SEC の XBRL frames API から四半期ごとに取る（dei:EntityCommonStockSharesOutstanding）。
あわせて会社番号（CIK）と銘柄コードの今の対応表（company_tickers.json）も取る。鍵なし。EDGAR_UA が要る。

使い方: EDGAR_UA="組織名 メール" python3 research/fetch_shares.py --out research/raw --from 2009 --to 2026
出す物: research/raw/shares.parquet（cik, period_end, shares, filed, avail）、research/raw/cik_ticker.parquet（cik, ticker）
      avail = その株数を使ってよい日（提出日 filed。無ければ期末の 45 日後）。届け出日 D 以前に avail がある最新の値だけ使う（先読みを防ぐ）
注意: XBRL は 2009〜2011 に段階的に始まったので、それより前の時価総額は「不明」になる。
"""
import argparse, json, os, sys, time
import urllib.request, urllib.error
import pandas as pd

UA = os.environ.get("EDGAR_UA", "").strip()
FRAMES = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"


def get(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/raw")
    ap.add_argument("--from", dest="y0", type=int, default=2009)
    ap.add_argument("--to", dest="y1", type=int, default=2026)
    a = ap.parse_args()
    if not UA or "@" not in UA:
        print("EDGAR_UA が要る", file=sys.stderr); sys.exit(1)
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for y in range(a.y0, a.y1 + 1):
        for q in (1, 2, 3, 4):
            data = get(FRAMES.format(y=y, q=q))
            time.sleep(0.12)
            if not data:
                print(f"{y}Q{q}: 無い", flush=True); continue
            try:
                j = json.loads(data)
            except json.JSONDecodeError:
                print(f"{y}Q{q}: JSON でない", flush=True); continue
            n = 0; n_filed = 0
            for r in j.get("data", []):
                try:
                    cik = int(r["cik"]); val = float(r["val"]); end = str(r.get("end", ""))
                except (KeyError, TypeError, ValueError):
                    continue
                filed = str(r.get("filed", "") or "")
                if filed:
                    n_filed += 1
                if val > 0 and end:
                    rows.append((cik, end, val, f"{y}Q{q}", filed, str(r.get("accn", "")))); n += 1
            print(f"{y}Q{q}: {n:,} 社（提出日 filed が入っている物 {n_filed:,}）", flush=True)
    if not rows:
        print("発行株数が 1 つも取れなかった", file=sys.stderr); sys.exit(3)
    S = pd.DataFrame(rows, columns=["cik", "period_end", "shares", "frame", "filed", "accn"])
    S["period_end"] = pd.to_datetime(S["period_end"], errors="coerce")
    S["filed"] = pd.to_datetime(S["filed"], errors="coerce")
    # 先読みを防ぐ「使える日」: 提出日 filed があればその日。無ければ期末 end の 45 日後（表紙の株数の日付から提出までの間を見込む）
    S["avail"] = S["filed"].where(S["filed"].notna(), S["period_end"] + pd.Timedelta(days=45))
    S = S.dropna(subset=["period_end"]).sort_values(["cik", "avail"]).drop_duplicates(["cik", "period_end"], keep="last")
    print(f"提出日 filed が入っている行: {int(S['filed'].notna().sum()):,} / {len(S):,}（無い行は end + 45 日を使える日にした）", flush=True)
    S.to_parquet(os.path.join(a.out, "shares.parquet"), index=False)
    data = get(TICKERS)
    T = pd.DataFrame(columns=["cik", "ticker", "title"])
    if data:
        j = json.loads(data)
        T = pd.DataFrame([(int(v["cik_str"]), str(v["ticker"]).upper().replace(".", "-"), v.get("title", "")) for v in j.values()], columns=["cik", "ticker", "title"])
    T.to_parquet(os.path.join(a.out, "cik_ticker.parquet"), index=False)
    print(f"終了: 発行株数 {len(S):,} 行 / 会社 {S['cik'].nunique():,} / 対応表 {len(T):,}", flush=True)


if __name__ == "__main__":
    main()
