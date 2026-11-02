#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
買う前の確かめ: 「今の株価データ（Yahoo）で株価が引けなかったイベント」を、候補の株価データがどれだけ埋められるかを、
各社が無料で公開している銘柄の一覧だけで数える（買ったデータは使わない）。出すのは数だけ（一覧そのものは出さない）。

候補と一覧:
  FirstRate Data … Complete Stocks Bundle のページに載っている銘柄の一覧（"TICKER (名前) Start Date:YYYY-MM-DD"。上場廃止は -DELISTED）
  Tiingo          … supported_tickers.zip（ticker, exchange, assetType, priceCurrency, startDate, endDate）
  参考: Alpha Vantage の上場廃止の一覧（LISTING_STATUS state=delisted。取引所に上場していた会社か＝店頭ではないかの目安）

使い方: python3 research/coverage_check.py --events research/raw/events.parquet --out research/out/coverage_vendors.csv
"""
import argparse, io, json, os, re, sys, zipfile, urllib.request
import numpy as np
import pandas as pd

UA = "Mozilla/5.0 (sec-insider research coverage check; contact via GitHub kawasemaster3130-dev/sec-insider)"
FR_URLS = ["https://firstratedata.com/b/22/stock-complete-historical-intraday"]
TIINGO_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
AV_URL = "https://www.alphavantage.co/query?function=LISTING_STATUS&state=delisted&apikey=demo"


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def norm(s):
    s = str(s or "").strip().upper()
    if not s or s in ("N/A", "NONE", "NA", "NULL", "-", "NAN"):
        return ""
    return s.replace(".", "-").replace("/", "-").replace(" ", "")


def firstrate():
    """{記号: 最初の日}。上場廃止は '記号-DELISTED' のまま入れる"""
    out, info = {}, {}
    for u in FR_URLS:
        try:
            html = get(u).decode("utf-8", errors="replace")
        except Exception as e:
            info[u] = f"取れなかった: {e}"; continue
        text = re.sub(r"<[^>]+>", "\n", html)
        text = text.replace("&amp;", "&").replace("&#39;", "'")
        rows = re.findall(r"([A-Z0-9][A-Z0-9.\-]{0,20})\s*\(([^)]{0,200})\)\s*Start Date:\s*(\d{4}-\d{2}-\d{2})", text)
        for t, name, sd in rows:
            out[norm(t)] = sd
        info[u] = {"html_bytes": len(html), "entries": len(rows), "raw_DELISTED_mentions": html.upper().count("DELISTED")}
    return out, info


def tiingo():
    try:
        raw = get(TIINGO_URL)
    except Exception as e:
        return None, f"取れなかった: {e}"
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    df = pd.read_csv(z.open(name), dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    df["t"] = df["ticker"].map(norm)
    df["sd"] = pd.to_datetime(df.get("startdate", ""), errors="coerce")
    df["ed"] = pd.to_datetime(df.get("enddate", ""), errors="coerce")
    info = {"rows": int(len(df)), "assetType": df.get("assettype", pd.Series(dtype=str)).value_counts().head(8).to_dict(),
            "exchange_top": df.get("exchange", pd.Series(dtype=str)).value_counts().head(12).to_dict(),
            "rows_with_dates": int(df["sd"].notna().sum()), "ended_before_2025": int((df["ed"] < "2025-01-01").sum())}
    return df, info


def alphavantage():
    try:
        raw = get(AV_URL).decode("utf-8", errors="replace")
    except Exception as e:
        return None, f"取れなかった: {e}"
    if not raw.lower().startswith("symbol"):
        return None, f"CSV でない: {raw[:200]}"
    df = pd.read_csv(io.StringIO(raw), dtype=str, keep_default_na=False)
    df["t"] = df["symbol"].map(norm)
    df["sd"] = pd.to_datetime(df["ipoDate"], errors="coerce"); df["ed"] = pd.to_datetime(df["delistingDate"], errors="coerce")
    info = {"rows": int(len(df)), "assetType": df["assetType"].value_counts().to_dict(), "exchange": df["exchange"].value_counts().head(8).to_dict(),
            "delisted_2006_2023": int(((df["ed"] >= "2006-01-01") & (df["ed"] <= "2023-12-31")).sum())}
    return df, info


def window_match(ev_t, ev_d, table, pad_days=5):
    """表（t, sd, ed）に、記号が同じで期間に届け出日が入る行があるか"""
    if table is None or len(table) == 0:
        return np.zeros(len(ev_t), dtype=bool)
    by = {}
    for t, sd, ed in zip(table["t"].values, table["sd"].values, table["ed"].values):
        if t:
            by.setdefault(t, []).append((sd, ed))
    out = np.zeros(len(ev_t), dtype=bool)
    pad = np.timedelta64(pad_days, "D")
    for i, (t, d) in enumerate(zip(ev_t, ev_d)):
        for cand in (t, t + "Q") if t else ():
            for sd, ed in by.get(cand, []):
                ok_s = pd.isna(sd) or sd <= d + pad
                ok_e = pd.isna(ed) or ed >= d - pad
                if ok_s and ok_e:
                    out[i] = True; break
            if out[i]:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="research/raw/events.parquet")
    ap.add_argument("--out", default="research/out/coverage_vendors.csv")
    a = ap.parse_args()
    E = pd.read_parquet(a.events, columns=["issuer_cik", "filing_date", "symbol", "ticker", "t0_date", "excl_reason", "year"])
    E["filing_date"] = pd.to_datetime(E["filing_date"])
    E = E[(E["excl_reason"] == "") & (E["filing_date"] >= "2006-01-01") & (E["filing_date"] <= "2023-12-31")].copy()
    E["sym"] = E["symbol"].map(norm)
    E["has_price"] = E["t0_date"] > 0
    print(f"イベント {len(E):,}（株価あり {int(E['has_price'].sum()):,}・無し {int((~E['has_price']).sum()):,}・無しのうち記号が空 {int(((~E['has_price']) & (E['sym'] == '')).sum()):,}）", flush=True)

    fr, fr_info = firstrate()
    print("FirstRate の一覧:", json.dumps(fr_info, ensure_ascii=False), f"記号 {len(fr):,}（-DELISTED {sum(1 for k in fr if k.endswith('-DELISTED')):,}）", flush=True)
    ti, ti_info = tiingo()
    print("Tiingo の一覧:", json.dumps(ti_info, ensure_ascii=False, default=str), flush=True)
    av, av_info = alphavantage()
    print("Alpha Vantage の上場廃止の一覧:", json.dumps(av_info, ensure_ascii=False, default=str), flush=True)

    # FirstRate: 記号が同じ（今の銘柄で最初の日が届け出日より前）か、記号-DELISTED がある
    fr_start = {k: pd.Timestamp(v) for k, v in fr.items()}
    def fr_match(t, d):
        if not t:
            return False
        sd = fr_start.get(t)
        if sd is not None and sd <= d + pd.Timedelta(days=5):
            return True
        return (t + "-DELISTED") in fr_start
    E["fr"] = [fr_match(t, d) for t, d in zip(E["sym"].values, E["filing_date"].values.astype("datetime64[ns]"))]
    if ti is not None:
        ti_stocks = ti[ti.get("assettype", "").str.lower().isin(["stock", ""])] if "assettype" in ti.columns else ti
        E["tiingo"] = window_match(E["sym"].values, E["filing_date"].values.astype("datetime64[ns]"), ti_stocks)
    else:
        E["tiingo"] = False
    E["av_delisted"] = window_match(E["sym"].values, E["filing_date"].values.astype("datetime64[ns]"), av) if av is not None else False
    E["fr_or_tiingo"] = E["fr"] | E["tiingo"]

    rows = []
    for y, g in E.groupby("year"):
        for grp, gg in [("株価なし（Yahoo）", g[~g["has_price"]]), ("株価あり（Yahoo）", g[g["has_price"]])]:
            n = len(gg)
            rows.append({"年": int(y), "区分": grp, "イベント": n, "FirstRate": gg["fr"].mean() if n else np.nan, "Tiingo": gg["tiingo"].mean() if n else np.nan,
                         "どちらか": gg["fr_or_tiingo"].mean() if n else np.nan, "AV の上場廃止一覧にある（取引所の会社の目安）": gg["av_delisted"].mean() if n else np.nan})
    R = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    R.to_csv(a.out, index=False)
    pd.set_option("display.width", 250)
    print(R.to_string(index=False, float_format=lambda x: f"{x*100:5.1f}%"), flush=True)
    # 全体（学習 2006〜2017-06 と 検定 2018〜2023）
    for nm, m in [("学習", E["filing_date"] <= "2017-06-30"), ("検定", E["filing_date"] >= "2018-01-01")]:
        g = E[m]
        miss = g[~g["has_price"]]; have = g[g["has_price"]]
        cur = have["has_price"].sum()
        def after(col):
            return (cur + miss[col].sum()) / max(len(g), 1)
        print(f"{nm}: イベント {len(g):,}。今の株価あり {cur/len(g)*100:.1f}% → FirstRate を足すと {after('fr')*100:.1f}%、Tiingo を足すと {after('tiingo')*100:.1f}%、どちらかなら {after('fr_or_tiingo')*100:.1f}%。"
              f"株価なしのうち AV の上場廃止一覧にある {miss['av_delisted'].mean()*100:.1f}%。株価ありの物が FirstRate に {have['fr'].mean()*100:.1f}%・Tiingo に {have['tiingo'].mean()*100:.1f}% ある（確かめ）", flush=True)
    json.dump({"firstrate": fr_info, "tiingo": ti_info, "alphavantage": av_info}, open(a.out.replace(".csv", "_meta.json"), "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
