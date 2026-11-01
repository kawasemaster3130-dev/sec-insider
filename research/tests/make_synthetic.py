#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
煙試験用の作り物のデータ（SEC の買い・株価・発行株数）を research/tests/_synth/ に作る。ネット無しで build_events.py と analyze.py を通すため。
仕込み: クラスター 3 人以上のイベントは 126 日で +8%、CEO の買いは +4% の上乗せ。ほかは上乗せ無し（市場と階級の動きだけ）。
使い方: python3 research/tests/make_synthetic.py --out research/tests/_synth [--n-tickers 400] [--seed 1]
"""
import argparse, os, pickle
import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/tests/_synth")
    ap.add_argument("--n-tickers", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    os.makedirs(a.out, exist_ok=True)
    days = pd.bdate_range("2005-01-03", "2026-10-30")
    # 祝日の代わりに年 9 日を抜く
    drop = set()
    for y in range(2005, 2027):
        yd = [d for d in days if d.year == y]
        for k in rng.choice(len(yd), size=min(9, len(yd)), replace=False):
            drop.add(yd[k])
    days = pd.DatetimeIndex([d for d in days if d not in drop])
    G = len(days); dints = np.array([d.year * 10000 + d.month * 100 + d.day for d in days], dtype=np.int32)
    mkt = rng.normal(0.0003, 0.011, G)
    prices, market = {}, {}
    spy = 100 * np.cumprod(1 + mkt)
    prices["SPY"] = {"d": dints, "o": spy.astype(np.float32), "c": spy.astype(np.float32), "v": np.full(G, 8e7, dtype=np.float32)}
    market["SPY"] = "nyse etfs"
    tickers = [f"T{i:04d}" for i in range(a.n_tickers)]
    info = {}
    event_drift = {t: np.zeros(G) for t in tickers}   # 仕込みの上乗せ（日次）
    for i, t in enumerate(tickers):
        cik = 1000 + i
        beta = rng.uniform(0.5, 1.5); vol = rng.uniform(0.012, 0.045); size = 10 ** rng.uniform(5.0, 8.5)  # 1 日の売買代金
        start = 0 if rng.random() < 0.7 else int(rng.integers(0, G // 2))
        end = G if rng.random() < 0.85 else int(rng.integers(start + 300, G))  # 15% は途中で消える
        info[t] = {"cik": cik, "start": start, "end": end, "size": size, "split_day": None, "split_factor": 1.0}
        if rng.random() < 0.15:
            sd = int(rng.integers(start + 500, max(start + 501, end - 100))) if end - start > 700 else None
            if sd:
                info[t]["split_day"] = sd; info[t]["split_factor"] = float(rng.choice([2, 3, 4, 10]))
        market[t] = "nasdaq stocks/1" if i % 2 == 0 else "nyse stocks/1"
    # 買いのイベントを先に決めて、その後の値動きに上乗せを仕込む
    rows, owners, sales, amends = [], [], [], []
    acc = 0
    for t in tickers:
        inf = info[t]; cik = inf["cik"]
        n_ev = int(rng.poisson(14))
        insiders = [(cik * 100 + k, k) for k in range(8)]
        for _ in range(n_ev):
            g0 = int(rng.integers(inf["start"] + 300, inf["end"] - 5)) if inf["end"] - inf["start"] > 310 else None
            if g0 is None:
                continue
            kind = rng.random()
            n_people = 1 if kind < 0.6 else (2 if kind < 0.8 else int(rng.integers(3, 6)))
            drift = 0.0
            who = rng.choice(len(insiders), size=n_people, replace=False)
            roles = []
            for w in who:
                k = insiders[w][1]
                roles.append("CEO" if k == 0 else ("CFO" if k == 1 else ("Director" if k < 6 else "TenPct")))
            if n_people >= 3:
                drift += 0.08 / 126
            if "CEO" in roles:
                drift += 0.04 / 126
            event_drift[t][g0 + 1:g0 + 127] += drift
            # 届け出日 = 取引日 + 0〜2 日（まれに遅れ）
            for w, role in zip(who, roles):
                ocik, k = insiders[w]
                lag = int(rng.choice([1, 2, 2, 3, 15], p=[0.3, 0.3, 0.2, 0.15, 0.05]))
                gf = min(g0 + lag, G - 1)
                acc += 1
                accn = f"0001-{acc:08d}"
                shares = float(rng.choice([500, 1000, 2000, 5000, 10000, 25000, 100000]))
                rows.append({"ACCESSION_NUMBER": accn, "issuer_cik": cik, "ISSUERNAME": f"Synthetic {t} Inc", "symbol": t if rng.random() < 0.9 else "N/A", "filing_date": days[gf], "trans_date": days[g0],
                             "shares": shares, "price": None, "owned_after": shares * float(rng.integers(1, 30)), "direct": bool(rng.random() < 0.8), "SECURITY_TITLE": "Common Stock", "quarter": f"{days[gf].year}Q{(days[gf].month-1)//3+1}",
                             "TRANS_TIMELINESS": "L" if lag > 10 else "", "NATURE_OF_OWNERSHIP": "", "fn_text": "", "remarks": "", "offmarket": bool(rng.random() < 0.03), "plan": bool(rng.random() < 0.02), "ads": False, "spac": False, "_g0": g0})
                owners.append({"ACCESSION_NUMBER": accn, "RPTOWNERCIK": str(ocik), "RPTOWNERNAME": f"Person {ocik}", "RPTOWNER_RELATIONSHIP": "Officer" if role in ("CEO", "CFO") else ("Director" if role == "Director" else "TenPercentOwner"),
                               "RPTOWNER_TITLE": {"CEO": "Chief Executive Officer", "CFO": "Chief Financial Officer", "Director": "", "TenPct": ""}[role], "owner_cik": ocik})
            if rng.random() < 0.3:
                gs = min(g0 + int(rng.integers(-20, 20)), G - 1)
                acc += 1
                sales.append({"ACCESSION_NUMBER": f"0001-{acc:08d}", "issuer_cik": cik, "filing_date": days[max(gs, 0)]})
    # 株価（上乗せ込み）。分割で直した系列（adjusted）を保存。Form 4 の単価は分割前の実際の値（adjusted × factor）
    shares_rows = []
    for t in tickers:
        inf = info[t]
        r = 0.0001 + rng.uniform(0.5, 1.5) * mkt + rng.normal(0, rng.uniform(0.012, 0.045), G) + event_drift[t]
        p = 10 ** rng.uniform(0.3, 2.0) * np.cumprod(1 + r)
        s, e = inf["start"], inf["end"]
        adj = p[s:e]
        vol = (inf["size"] / adj) * np.exp(rng.normal(0, 0.5, e - s))
        prices[t] = {"d": dints[s:e], "o": adj.astype(np.float32), "c": adj.astype(np.float32), "v": vol.astype(np.float32)}
        info[t]["adj"] = p
        so = 10 ** rng.uniform(6.5, 9.0)
        for y in range(2009, 2027):
            for q, (mm, dd) in enumerate([(3, 31), (6, 30), (9, 30), (12, 31)]):
                pe = pd.Timestamp(y, mm, dd)
                if pe < days[s] or pe > days[min(e, G - 1)]:
                    continue
                # 分割後は発行株数が factor 倍
                f = inf["split_factor"] if (inf["split_day"] is not None and pe >= days[inf["split_day"]]) else 1.0
                shares_rows.append({"cik": inf["cik"], "period_end": pe, "shares": so * f * (1 + 0.01 * (y - 2009)), "frame": f"CY{y}Q{q+1}I", "filed": pe + pd.Timedelta(days=40), "avail": pe + pd.Timedelta(days=40)})
    P = pd.DataFrame(rows)
    px = []
    for t, g0 in zip(P["symbol"].where(P["symbol"] != "N/A", None), P["_g0"]):
        pass
    # 単価: 取引日の実際の値 × (1 ± 1%)。銘柄が N/A の行でも会社は分かるので、会社の銘柄から出す
    t_of_cik = {info[t]["cik"]: t for t in tickers}
    prices_f4 = []
    for cik, g0 in zip(P["issuer_cik"], P["_g0"]):
        t = t_of_cik[cik]; inf = info[t]
        adj = inf["adj"][g0]
        f = inf["split_factor"] if (inf["split_day"] is not None and g0 < inf["split_day"]) else 1.0
        prices_f4.append(adj * f * (1 + rng.normal(0, 0.01)))
    P["price"] = prices_f4
    # 1% の行は桁誤り（単価 10 倍）、0.5% は別会社の銘柄コード
    bad = rng.random(len(P)) < 0.01
    P.loc[bad, "price"] = P.loc[bad, "price"] * 10
    wrong = rng.random(len(P)) < 0.005
    P.loc[wrong, "symbol"] = [tickers[int(rng.integers(0, len(tickers)))] for _ in range(int(wrong.sum()))]
    P = P.drop(columns=["_g0"])
    P.to_parquet(os.path.join(a.out, "purchases.parquet"), index=False)
    pd.DataFrame(owners).to_parquet(os.path.join(a.out, "owners.parquet"), index=False)
    pd.DataFrame(sales).to_parquet(os.path.join(a.out, "sales.parquet"), index=False)
    pd.DataFrame(columns=["ACCESSION_NUMBER", "issuer_cik", "owner_cik", "trans_date", "shares", "price"]).to_parquet(os.path.join(a.out, "amendments.parquet"), index=False)
    pd.DataFrame(shares_rows).to_parquet(os.path.join(a.out, "shares.parquet"), index=False)
    # 対応表: 8 割の会社だけ（残りは届け出の銘柄で引く）
    ct = pd.DataFrame([{"cik": info[t]["cik"], "ticker": t, "title": f"Synthetic {t} Inc"} for t in tickers if rng.random() < 0.8])
    ct.to_parquet(os.path.join(a.out, "cik_ticker.parquet"), index=False)
    with open(os.path.join(a.out, "prices.pkl"), "wb") as f:
        pickle.dump({"prices": prices, "market": market}, f, protocol=pickle.HIGHEST_PROTOCOL)
    import json
    json.dump({"rows": int(len(P)), "first": str(P["filing_date"].min().date()), "last": str(P["filing_date"].max().date()), "synthetic": True}, open(os.path.join(a.out, "purchases_meta.json"), "w"))
    json.dump({"source": "synthetic", "tickers": len(prices)}, open(os.path.join(a.out, "prices_meta.json"), "w"))
    print(f"作った: 買いの行 {len(P):,} / 銘柄 {len(prices):,} / 発行株数 {len(shares_rows):,} / 売り {len(sales):,} → {a.out}")


if __name__ == "__main__":
    main()
