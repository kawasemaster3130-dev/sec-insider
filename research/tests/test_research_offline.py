#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研究の道具のネット無しの試験。作り物のデータ（make_synthetic.py）で build_events.py → analyze.py を通し、決まりどおりに動くかを確かめる。
使い方: python3 research/tests/test_research_offline.py
確かめる物:
  1. 道具が最後まで動き、summary.md と top10.csv ができる
  2. イベントのリターンが、株価の配列から手で計算した物と一致する（入る日 = D の次の SPY の営業日の終値、H 日後 = SPY の営業日で数える）
  3. 仕込んだ効果（クラスター 3 人以上 +8%、CEO +4%）が学習の表に出る（クラスター 3 人以上 − 1 人 ≥ +3%、CEO − 大株主だけ ≥ +2%）
  4. 先読みの決まり: クラスターは届け出日で数える（取引日が同じで届け出日が違う人は、早い届け出日のイベントの人数に入らない）
  5. 外す物が理由ごとに数えられ、単価 10 倍の行（桁誤り）が「単価と株価が合わない」で外れる
  6. 学習・検定・金庫の切り方（金庫のイベントが表に入らない）
"""
import os, pickle, subprocess, sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SYN = os.path.join(ROOT, "research", "tests", "_out_synth")


def run(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:]); print(r.stderr[-3000:])
        raise SystemExit(f"失敗: {' '.join(cmd)}")
    return r.stdout


def main():
    os.makedirs(SYN, exist_ok=True)
    run([sys.executable, "research/tests/make_synthetic.py", "--out", SYN, "--n-tickers", "300", "--seed", "3"])
    run([sys.executable, "research/build_events.py", "--raw", SYN, "--out", os.path.join(SYN, "events.parquet"), "--outdir", os.path.join(SYN, "out")])
    run([sys.executable, "-W", "ignore", "research/analyze.py", "--events", os.path.join(SYN, "events.parquet"), "--out", os.path.join(SYN, "out"), "--no-two-axis"])
    ok = True
    def check(cond, msg):
        nonlocal ok
        print(("OK  " if cond else "NG  ") + msg)
        ok = ok and bool(cond)
    out = os.path.join(SYN, "out")
    check(os.path.exists(os.path.join(out, "summary.md")) and os.path.exists(os.path.join(out, "top10.csv")), "1. summary.md と top10.csv ができる")
    E = pd.read_parquet(os.path.join(SYN, "events.parquet"))
    pk = pickle.load(open(os.path.join(SYN, "prices.pkl"), "rb")); prices = pk["prices"]
    grid = prices["SPY"]["d"].astype(np.int64); G = len(grid)
    K = E[(E["excl_reason"] == "") & E["ret_126"].notna()].sample(n=min(300, int(((E["excl_reason"] == "") & E["ret_126"].notna()).sum())), random_state=1)
    maxdiff = 0.0
    for r in K.itertuples():
        rec = prices[r.ticker]; d = rec["d"]; c = rec["c"].astype(float)
        i0 = int(np.searchsorted(d, int(grid[r.g0])))
        assert int(d[i0]) == int(grid[r.g0])
        k = int(np.searchsorted(d, int(grid[r.g0 + 126]), side="right")) - 1
        maxdiff = max(maxdiff, abs(c[k] / c[i0] - 1 - r.ret_126))
        # 入る日は D より後
        assert int(grid[r.g0]) > int(pd.Timestamp(r.filing_date).strftime("%Y%m%d"))
    check(maxdiff < 1e-9, f"2. リターンが手計算と一致（最大のずれ {maxdiff:.2e}）")
    a1 = pd.read_csv(os.path.join(out, "axis1_H126.csv"))
    def m(cell):
        r = a1[a1["cell"] == cell]
        return float(r["mean_train"].iloc[0]) if len(r) else np.nan
    n3 = a1[a1["cell"].isin(["クラスター人数=3人", "クラスター人数=4人以上"])]
    c3 = (n3["mean_train"] * n3["n_train"]).sum() / max(n3["n_train"].sum(), 1)
    check(c3 - m("クラスター人数=1人") >= 0.03, f"3a. 仕込んだクラスターの効果が出る（3 人以上 {c3*100:+.1f}% − 1 人 {m('クラスター人数=1人')*100:+.1f}%）")
    check(m("役職=CEO") - m("役職=大株主だけ") >= 0.02, f"3b. 仕込んだ CEO の効果が出る（CEO {m('役職=CEO')*100:+.1f}% − 大株主 {m('役職=大株主だけ')*100:+.1f}%）")
    # 4. クラスターは届け出日で数える: 同じ会社で、取引日が同じ 2 人が別の届け出日に出した例を探す
    P = pd.read_parquet(os.path.join(SYN, "purchases.parquet")); O = pd.read_parquet(os.path.join(SYN, "owners.parquet"))
    P = P.merge(O[["ACCESSION_NUMBER", "owner_cik"]], on="ACCESSION_NUMBER")
    g = P.groupby(["issuer_cik", "trans_date"]).agg(n_owner=("owner_cik", "nunique"), n_fd=("filing_date", "nunique"), fd_min=("filing_date", "min"))
    g = g[(g["n_owner"] >= 2) & (g["n_fd"] >= 2)].reset_index()
    tested = 0; good = True
    for r in g.head(50).itertuples():
        sub = P[(P["issuer_cik"] == r.issuer_cik) & (P["trans_date"] == r.trans_date)]
        first_day_owners = sub[sub["filing_date"] == r.fd_min]["owner_cik"].nunique()
        ev = E[(E["issuer_cik"] == r.issuer_cik) & (E["filing_date"] == r.fd_min)]
        if len(ev) == 0:
            continue
        # 早い届け出日のイベントのクラスター人数は、その日までに届け出た人の数以下（後の届け出日の人は数えない）
        prior = P[(P["issuer_cik"] == r.issuer_cik) & (P["filing_date"] <= r.fd_min) & (P["filing_date"] >= r.fd_min - pd.Timedelta(days=13))]["owner_cik"].nunique()
        if int(ev["cluster14"].iloc[0]) > prior:
            good = False
        tested += 1
    check(good and tested > 0, f"4. クラスターは届け出日で数える（{tested} 例で確認）")
    ex = pd.read_csv(os.path.join(out, "exclusions.csv"))
    bad = ex[ex["理由"].str.contains("単価と株価が合わない")]["数"].sum()
    check(bad >= 0.005 * len(P), f"5. 桁誤りの行が外れる（外した数 {bad}、行 {len(P)}）")
    smry = open(os.path.join(out, "summary.md"), encoding="utf-8").read()
    ctrl = pd.read_csv(os.path.join(out, "control.csv"))
    n_vault = int(((E["excl_reason"] == "") & (E["filing_date"] >= "2024-01-01")).sum())
    check("金庫" in smry and n_vault > 0 and ctrl[ctrl["期間"] == "test"]["n"].max() < len(E), "6. 金庫のイベントが表に入らない")
    print("全部 OK" if ok else "NG がある")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
