#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v1 の見出しの数字の直し（2026-11-02 の独立した道具の監査で見つかった誤りのうち、取り直し無しで直せる 2 つ）を、v1 の 6 回目のイベントの表に当てて数え直す。
  直し a: 対照だけに当てていた「+300% で頭を切る・始めの値 10 セント未満を数えない」を、イベントの側にも当てる（両側を同じ決まりに）
  直し b: 届け出の銘柄コードが、今は別の会社（別の CIK）の物なら、そのイベントを外す（使い回しの銘柄コードで別の会社の株価を測っていた）
出すのは数だけ。決まり（費用・枠・期間・t の取り方）は analyze.py のまま。

使い方: python3 research/diag_v1.py --events research/raw/events.parquet --cik-ticker research/raw/cik_ticker.parquet
"""
import argparse, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A  # noqa: E402

HS = [21, 63, 126, 252]


def norm(s):
    return str(s or "").strip().upper().replace(".", "-")


def apply_fix_a(E):
    E = E.copy()
    low = E["p0"].values < 0.10
    for h in HS:
        r = E[f"ret_{h}"].values.astype(float)
        E[f"ret_{h}"] = np.where(np.isnan(r), np.nan, np.minimum(1.0 + r, 4.0) - 1.0)
    r2 = E[f"ret2_{A.H_MAIN}"].values.astype(float)
    E[f"ret2_{A.H_MAIN}"] = np.where(np.isnan(r2), np.nan, np.minimum(1.0 + r2, 4.0) - 1.0)
    rl = E["ret_last"].values.astype(float)
    E["ret_last"] = np.where(np.isnan(rl), np.nan, np.minimum(1.0 + rl, 4.0) - 1.0)
    m = (E["excl_reason"] == "").values & (E["t0_date"].values > 0) & low
    E.loc[m, "excl_reason"] = "診断: 始めの値 10 セント未満"
    return E, int(m.sum())


def apply_fix_b(E, CT):
    E = E.copy()
    owned = {}
    for c, t in zip(pd.to_numeric(CT["cik"], errors="coerce").fillna(-1).astype(int), CT["ticker"].astype(str)):
        owned.setdefault(norm(t), set()).add(int(c))
    wrong = np.array([(t in owned) and (int(c) not in owned[t]) if t else False for t, c in zip(E["ticker"].map(norm).values, E["issuer_cik"].values)])
    m = (E["excl_reason"] == "").values & wrong
    E.loc[m, "excl_reason"] = "診断: 今は別の会社の銘柄コード"
    return E, int(m.sum())


def headline(E, label):
    rows = []
    for H in HS:
        D = A.prepare(E, H)
        L = max(1, H // 21)
        R = D[D["has_ret"] & (D["tier"] == "主要")]
        for pname in ["train", "test"]:
            sub = R[R["period"] == pname]
            if len(sub) == 0:
                continue
            st = A.cell_stats(sub.assign(_k="x"), ["_k"], L, A.month_index(pname)).iloc[0]
            rows.append({"版": label, "H": H, "期間": pname, "n": int(len(sub)), "費用後の平均": st["mean"], "中央値": st["median"], "勝率": st["win"],
                         "t": st["t"], "上位5%の月抜き": st["ex_top_months_mean"], "SPY比（費用前）": sub["ex_spy"].mean(), "同じ階級比（費用前）": sub["ex"].mean()})
    return rows


def cells(E, label, axes_cells):
    D = A.prepare(E, A.H_MAIN)
    R = D[D["has_ret"] & (D["tier"] == "主要")]
    out = []
    for ax, val in axes_cells:
        for pname in ["train", "test"]:
            sub = R[(R["period"] == pname) & (R[ax].astype(str) == val)]
            if len(sub) < 30:
                continue
            st = A.cell_stats(sub.assign(_k="x"), ["_k"], 6, A.month_index(pname)).iloc[0]
            out.append({"版": label, "段": f"{ax}={val}", "期間": pname, "n": int(len(sub)), "費用後の平均": st["mean"], "t": st["t"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="research/raw/events.parquet")
    ap.add_argument("--cik-ticker", default="research/raw/cik_ticker.parquet")
    a = ap.parse_args()
    E0 = pd.read_parquet(a.events)
    E0["filing_date"] = pd.to_datetime(E0["filing_date"])
    CT = pd.read_parquet(a.cik_ticker)
    Ea, n_low = apply_fix_a(E0)
    Eab, n_reuse = apply_fix_b(Ea, CT)
    Eb, _ = apply_fix_b(E0, CT)
    print(f"直し a で外したイベント（始めの値 10 セント未満）: {n_low:,}／直し b で外したイベント（今は別の会社の銘柄コード）: {n_reuse:,}", flush=True)
    # 主要の枠で、+300% を超えた分の平均（直し a がどれだけ平均を下げるかの目安）
    for nm, s, e in [("train", "2006-01-01", "2017-06-30"), ("test", "2018-01-01", "2023-12-31")]:
        M = E0[(E0["excl_reason"] == "") & (E0["adv63"] >= 1e6) & E0["filing_date"].between(s, e) & E0["ret_126"].notna() & E0["bench_126"].notna()]
        over = (M["ret_126"] - 3.0).clip(lower=0).mean()
        print(f"  {nm}: 主要の枠 n={len(M):,}、126 日で +300% を超えた分の平均 {over*100:.2f} ポイント、始めの値 10 セント未満 {(M['p0'] < 0.10).mean()*100:.2f}%", flush=True)
    rows = headline(E0, "6 回目のまま") + headline(Ea, "直し a") + headline(Eb, "直し b") + headline(Eab, "直し a＋b")
    T = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    fmt = {c: (lambda x: f"{x*100:+.2f}%") for c in ["費用後の平均", "中央値", "勝率", "上位5%の月抜き", "SPY比（費用前）", "同じ階級比（費用前）"]}
    print(T.to_string(index=False, formatters={**fmt, "t": lambda x: f"{x:.2f}"}), flush=True)
    picks = [("クラスター人数", "1人"), ("クラスター人数", "3人"), ("クラスター人数", "4人以上"), ("日課か", "日課"), ("日課か", "日課でない"),
             ("役職", "CEO"), ("役職", "CFO"), ("高値からの下落", "50%超"), ("株価の水準", "1〜5"), ("時価総額", "3億未満"), ("時価総額", "100億超")]
    C = pd.DataFrame(cells(E0, "6 回目のまま", picks) + cells(Eab, "直し a＋b", picks))
    print(C.to_string(index=False, formatters={"費用後の平均": lambda x: f"{x*100:+.2f}%", "t": lambda x: f"{x:.2f}"}), flush=True)
    os.makedirs("research/out", exist_ok=True)
    T.to_csv("research/out/diag_v1_headline.csv", index=False); C.to_csv("research/out/diag_v1_cells.csv", index=False)


if __name__ == "__main__":
    main()
