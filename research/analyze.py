#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
events.parquet（build_events.py の出力）を、事前登録 research/PREREG_v1.md（v1.1）の決まりどおりに総当たりで評価する。
人も AI も数字を手で書かない。この道具が全部の表と summary.md を書く。

使い方: python3 research/analyze.py --events research/raw/events.parquet --out research/out
決まりの要点（PREREG v1.1 の 3 章）:
  学習 2006-01〜2017-06 だけで合格と順位を決めて凍結 → 検定 2018-01〜2023-12 は○×の答え合わせ → 金庫 2024-01〜2026-09 は開けない
  t は届け出月の系列に Newey-West（隣 L か月、L = H ÷ 21）。合格線 = 費用後プラス・t ≥ 3・残り全部との差の t ≥ 3・n ≥ 300・学習の 3 期間全部プラス
出す物（research/out/）: summary.md, top10.csv, passed.csv, axis1_H*.csv, axis2_H*.csv, control.csv, calendar_time.csv, robustness.csv, predictions.csv,
  axis_effects.csv, survivorship.csv, events_sample.csv, summary.json
"""
import argparse, json, math, os, sys
import numpy as np
import pandas as pd

ANALYZER = "analyze-2.1"
HORIZONS = [21, 63, 126, 252]
H_MAIN, H_ALT = 126, 63
TRAIN_START, TRAIN_END = pd.Timestamp("2006-01-01"), pd.Timestamp("2017-06-30")
TEST_START, TEST_END = pd.Timestamp("2018-01-01"), pd.Timestamp("2023-12-31")
VAULT_START, VAULT_END = pd.Timestamp("2024-01-01"), pd.Timestamp("2026-09-30")
SUBPERIODS = [("2006〜2009", pd.Timestamp("2006-01-01"), pd.Timestamp("2009-12-31")), ("2010〜2013", pd.Timestamp("2010-01-01"), pd.Timestamp("2013-12-31")), ("2014〜2017-06", pd.Timestamp("2014-01-01"), pd.Timestamp("2017-06-30"))]
ERAS = ["2006〜2012", "2013〜2019", "2020〜2026"]
ADV_MAIN, ADV_SMALL = 1_000_000.0, 100_000.0
COST_BY_ADV = [(1e6, 0.025), (1e7, 0.012), (1e8, 0.005), (np.inf, 0.003)]
PASS_T, PASS_TDIFF, PASS_N, SUB_MIN_N = 3.0, 3.0, 300, 30
TEST_T = 2.0
C_SHARE_MAX = 0.15
P_T3, P_T2 = 0.0027, 0.046
ROLE_JA = {"CEO": "CEO", "CFO": "CFO", "Chair": "会長", "President": "社長", "Officer": "その他の役員", "Director": "取締役だけ", "TenPct": "大株主だけ", "Other": "その他"}
ROLE_ORDER_JA = ["CEO", "CFO", "会長", "社長", "その他の役員", "取締役だけ", "大株主だけ", "その他"]
AXES_FOR_RANK = ["金額", "役職", "クラスター人数", "クラスターの中身", "時価総額", "金額÷時価総額(bp)", "持ち株の増え方", "直前63日", "高値からの下落", "株価の水準",
                 "届け出の遅れ", "保有の種類", "初めてか", "日課か", "連続性", "市場全体の買い", "直近の売り"]


# ----------------------------------------------------------------------------- 段（事前登録 2 章）
def cut(x, edges, labels, unknown="不明"):
    x = np.asarray(x, dtype=np.float64)
    idx = np.searchsorted(np.asarray(edges, dtype=np.float64), x, side="right")
    cats = list(labels) + ([unknown] if unknown else [])
    lab = np.array(cats, dtype=object)
    idx = np.where(np.isnan(x), len(labels) if unknown else 0, idx)
    return pd.Categorical(lab[idx], categories=cats)


def make_bins(D, cluster_col="cluster14"):
    B = {}
    B["金額"] = cut(D["value"], [1e4, 5e4, 1e5, 2.5e5, 1e6, 5e6], ["1万未満", "1〜5万", "5〜10万", "10〜25万", "25〜100万", "100〜500万", "500万超"], unknown=None)
    B["役職"] = pd.Categorical(D["role"].map(ROLE_JA).fillna("その他"), categories=ROLE_ORDER_JA)
    B["クラスター人数"] = cut(D[cluster_col], [2, 3, 4], ["1人", "2人", "3人", "4人以上"], unknown=None)
    comp = D[cluster_col].values > D[cluster_col + "_insiders_only"].values
    B["クラスターの中身"] = pd.Categorical(np.where(comp, "大株主を含む", "役員・取締役だけ"), categories=["役員・取締役だけ", "大株主を含む"])
    B["時価総額"] = cut(D["mcap"], [3e8, 2e9, 1e10], ["3億未満", "3億〜20億", "20億〜100億", "100億超"])
    B["金額÷時価総額(bp)"] = cut(D["value"].values / D["mcap"].values * 1e4, [1, 10, 100], ["1未満", "1〜10", "10〜100", "100超"])
    hi = D["holding_increase"].values.astype(np.float64)
    hcat = np.asarray(cut(np.where(np.isinf(hi), np.nan, hi), [0.05, 0.25, 1.0], ["5%未満", "5〜25%", "25〜100%", "100%超"])).astype(object)
    B["持ち株の増え方"] = pd.Categorical(np.where(np.isinf(hi), "新規", hcat), categories=["新規", "5%未満", "5〜25%", "25〜100%", "100%超", "不明"])
    B["直前63日"] = cut(D["prev63"], [-0.3, -0.1, 0.1, 0.3], ["−30%未満", "−30〜−10%", "−10〜+10%", "+10〜+30%", "+30%超"])
    B["高値からの下落"] = cut(-D["dd252"].values, [0.2, 0.5], ["20%未満", "20〜50%", "50%超"])
    B["株価の水準"] = cut(D["price_f4"], [1, 5, 20], ["1ドル未満", "1〜5", "5〜20", "20超"])
    B["届け出の遅れ"] = cut(D["filing_delay"], [3, 11], ["0〜2日", "3〜10日", "10日超"], unknown=None)
    B["保有の種類"] = pd.Categorical(np.where(D["all_direct"].values, "直接だけ", "間接を含む"), categories=["直接だけ", "間接を含む"])
    B["初めてか"] = pd.Categorical(np.where(D["all_first_time"].values, "初めて", "繰り返しを含む"), categories=["初めて", "繰り返しを含む"])
    B["日課か"] = pd.Categorical(D["any_routine"].map({"routine": "日課", "opportunistic": "日課でない", "unknown": "判定不能"}).fillna("判定不能"), categories=["日課", "日課でない", "判定不能"])
    B["連続性"] = pd.Categorical(np.where(D["any_persist"].values, "あり", "なし"), categories=["あり", "なし"])
    B["市場全体の買い"] = cut(D["breadth"], [0.7, 1.3], ["0.7未満", "0.7〜1.3", "1.3超"])
    B["直近の売り"] = pd.Categorical(np.where(D["sells30"].values, "あり", "なし"), categories=["なし", "あり"])
    y = D["year"].values
    B["時期"] = pd.Categorical(np.where(y <= 2012, ERAS[0], np.where(y <= 2019, ERAS[1], ERAS[2])), categories=ERAS)
    return B


def cost_of_adv(adv):
    adv = np.asarray(adv, dtype=np.float64)
    out = np.full(len(adv), 0.025)
    for lim, c in reversed(COST_BY_ADV):
        out = np.where(adv < lim, c, out)
    return np.where(np.isnan(adv), 0.025, out)


def prepare(E, H, cluster_col="cluster14", cost_mult=1.0, entry="d1", trunc_assume=None, dedup=False):
    """主要の母集団に、段とリターンと費用を付ける。ret が無い行（B・C・R）も残す（数え上げに使う）"""
    D = E[(E["excl_reason"] == "") & (E["filing_date"] >= TRAIN_START) & (E["filing_date"] <= TEST_END)].copy()
    adv = D["adv63"].values.astype(np.float64)
    D["tier"] = np.where(np.isnan(adv), "不明", np.where(adv >= ADV_MAIN, "主要", np.where(adv >= ADV_SMALL, "小口だけ", "買えない")))
    D["cost"] = cost_of_adv(adv) * cost_mult
    if entry == "d2" and H == H_MAIN:
        D["ret"] = D[f"ret2_{H}"]; D["bench"] = D[f"bench2_{H}"]
    else:
        D["ret"] = D[f"ret_{H}"]; D["bench"] = D[f"bench_{H}"]
    D["spy"] = D[f"spy_{H}"]
    has_t0 = D["t0_date"].values > 0
    D["pending"] = D[f"pending_{H}"].astype(bool).values
    D["trunc"] = has_t0 & D["ret"].isna().values & D["data_ended"].astype(bool).values & ~D["pending"].values
    D["noprice"] = ~has_t0
    if trunc_assume is not None:
        m = D["trunc"].values
        D.loc[m, "ret"] = (1.0 + D.loc[m, "ret_last"].values) * (1.0 + trunc_assume) - 1.0
    D["has_ret"] = D["ret"].notna().values & D["bench"].notna().values
    D["ex"] = D["ret"] - D["bench"]; D["ex_spy"] = D["ret"] - D["spy"]; D["net"] = D["ex"] - D["cost"]
    D["win"] = (D["ex"] > 0).astype(float)
    D["ym"] = D["filing_date"].dt.strftime("%Y%m")
    D["yq"] = D["filing_date"].dt.year.astype(str) + "Q" + D["filing_date"].dt.quarter.astype(str)
    D["period"] = np.where(D["filing_date"] <= TRAIN_END, "train", np.where(D["filing_date"] >= TEST_START, "test", "gap"))
    sp = np.full(len(D), "", dtype=object)
    for nm, s0, s1 in SUBPERIODS:
        sp[(D["filing_date"] >= s0).values & (D["filing_date"] <= s1).values] = nm
    D["sub"] = sp
    if dedup:
        D = D.sort_values(["issuer_cik", "filing_date"]).drop_duplicates(["issuer_cik", "yq"], keep="first")
    for k, v in make_bins(D, cluster_col).items():
        D[k] = v
    return D


# ----------------------------------------------------------------------------- 統計（Newey-West の t と差の t）
def month_index(period):
    s, e = (TRAIN_START, TRAIN_END) if period == "train" else (TEST_START, TEST_END)
    return pd.period_range(s, e, freq="M").strftime("%Y%m").tolist()


def nw_var(S, L):
    """S: [cells × months] の系列。隣 L か月までの相関を入れた分散（Bartlett の重み）"""
    V = np.sum(S * S, axis=1)
    T = S.shape[1]
    for l in range(1, min(L, T - 1) + 1):
        w = 1.0 - l / (L + 1.0)
        V = V + 2.0 * w * np.sum(S[:, l:] * S[:, :-l], axis=1)
    return V


def cell_stats(df, keys, L, months):
    """段ごとの n・平均（費用後）・中央値・勝率・下位10%・−50%以下の割合・上位5%抜きの平均・上位5%の月抜きの平均・t（NW）・差の t（NW）。df = has_ret & tier=主要 の行"""
    cols = ["n", "mean", "median", "win", "p10", "share_m50", "trim_mean", "ex_top_months_mean", "t", "diff", "t_diff", "mean_ex", "cost_mean", "months", "n_dedup"]
    if len(df) == 0:
        return pd.DataFrame(columns=keys + cols)
    df = df.copy()
    df["_cell"] = pd.MultiIndex.from_frame(df[keys].astype(str)).to_flat_index() if len(keys) > 1 else df[keys[0]].astype(str)
    grp = df.groupby("_cell", observed=True, sort=False)
    base = grp.agg(n=("net", "size"), mean=("net", "mean"), median=("net", "median"), win=("win", "mean"), mean_ex=("ex", "mean"), cost_mean=("cost", "mean"),
                   p10=("net", lambda s: float(np.quantile(s, 0.10))), share_m50=("ex", lambda s: float((s <= -0.5).mean())),
                   trim_mean=("net", lambda s: float(s[s <= s.quantile(0.95)].mean()) if len(s) >= 20 else float(s.mean())))
    # 会社 1 件に間引いた数（会社 × 四半期）
    nd = df.drop_duplicates(["_cell", "issuer_cik", "yq"]).groupby("_cell", observed=True).size()
    base["n_dedup"] = nd.reindex(base.index).fillna(0).astype(int).values
    # 月の系列
    gm = df.groupby(["_cell", "ym"], observed=True)["net"].agg(["sum", "count"])
    cells = base.index.tolist(); mi = {m: j for j, m in enumerate(months)}; ci = {c: i for i, c in enumerate(cells)}
    C = np.zeros((len(cells), len(months))); N = np.zeros((len(cells), len(months)))
    for (c, m), row in gm.iterrows():
        j = mi.get(m)
        if j is not None:
            C[ci[c], j] = row["sum"]; N[ci[c], j] = row["count"]
    Tm = C.sum(axis=0); Nm = N.sum(axis=0)
    n = N.sum(axis=1); mean = C.sum(axis=1) / n
    S = C - N * mean[:, None]
    V = nw_var(S, L)
    se = np.sqrt(np.maximum(V, 0)) / n
    base["t"] = np.where(se > 0, mean / se, np.nan)
    base["months"] = (N > 0).sum(axis=1)
    # 残り全部との差
    n_rest = Nm.sum() - n; sum_rest = Tm.sum() - C.sum(axis=1)
    m_rest = np.where(n_rest > 0, sum_rest / np.where(n_rest > 0, n_rest, 1), np.nan)
    U = (S / n[:, None]) - (((Tm[None, :] - C) - (Nm[None, :] - N) * m_rest[:, None]) / np.where(n_rest > 0, n_rest, 1)[:, None])
    VU = nw_var(U, L)
    base["diff"] = mean - m_rest
    base["t_diff"] = np.where((VU > 0) & (n_rest > 0), base["diff"] / np.sqrt(np.maximum(VU, 1e-300)), np.nan)
    # 上位 5% の月を抜いた平均
    ex_top = np.full(len(cells), np.nan)
    for i in range(len(cells)):
        active = np.where(N[i] > 0)[0]
        if len(active) < 10:
            ex_top[i] = mean[i]; continue
        k = int(math.ceil(0.05 * len(active)))
        order = active[np.argsort(C[i, active])[::-1]]
        drop = order[:k]
        ex_top[i] = (C[i].sum() - C[i, drop].sum()) / max(n[i] - N[i, drop].sum(), 1)
    base["ex_top_months_mean"] = ex_top
    out = base.reset_index()
    if len(keys) > 1:
        for j, k in enumerate(keys):
            out[k] = [c[j] for c in out["_cell"]]
    else:
        out[keys[0]] = out["_cell"]
    return out[keys + cols]


def cell_means(df, keys):
    if len(df) == 0:
        return pd.DataFrame(columns=keys + ["n", "mean"])
    g = df.groupby(keys, observed=True)["net"].agg(["size", "mean"]).reset_index().rename(columns={"size": "n"})
    for k in keys:
        g[k] = g[k].astype(str)
    return g


def full_grid(D, keys):
    cats = [list(D[k].cat.categories) for k in keys]
    return pd.MultiIndex.from_product(cats, names=keys).to_frame(index=False).astype(str)


def table_for(D, keys, H, variants):
    """1 つの段の組について、学習（合格の判定に使う列）・検定（答え合わせの列）・不安定の確かめの列・A/B/C を横に並べた表"""
    L = max(1, H // 21)
    out = full_grid(D, keys)
    M = D[D["tier"] == "主要"]
    R = M[M["has_ret"]]
    for pname in ["train", "test"]:
        st = cell_stats(R[R["period"] == pname], keys, L, month_index(pname))
        st = st.rename(columns={c: f"{c}_{pname}" for c in st.columns if c not in keys})
        out = out.merge(st, on=keys, how="left")
    for nm, s0, s1 in SUBPERIODS:
        st = cell_means(R[R["sub"] == nm], keys).rename(columns={"n": f"n_{nm}", "mean": f"mean_{nm}"})
        out = out.merge(st, on=keys, how="left")
    # A（主要の枠で結果あり）・B（主要の枠で途中で株価が終わる）・R（主要の枠でまだ結果が出ていない）・C（最初から株価が引けない。枠は分からないので全部のイベントの中で数える）・N（その段の全部のイベント）。学習・検定
    for pname in ["train", "test"]:
        Mp = M[M["period"] == pname]
        cnt = Mp.groupby(keys, observed=True).agg(A=("has_ret", "sum"), B=("trunc", "sum"), Rp=("pending", "sum")).reset_index()
        for k in keys:
            cnt[k] = cnt[k].astype(str)
        cnt = cnt.rename(columns={"A": f"A_{pname}", "B": f"B_{pname}", "Rp": f"R_{pname}"})
        out = out.merge(cnt, on=keys, how="left")
        Dp = D[D["period"] == pname]
        cc = Dp.groupby(keys, observed=True).agg(C=("noprice", "sum"), N=("noprice", "size")).reset_index()
        for k in keys:
            cc[k] = cc[k].astype(str)
        cc = cc.rename(columns={"C": f"C_{pname}", "N": f"N_{pname}"})
        out = out.merge(cc, on=keys, how="left")
    for vn, Dv in variants.items():
        Mv = Dv[(Dv["tier"] == "主要") & Dv["has_ret"]]
        for pname in ["train", "test"]:
            st = cell_means(Mv[Mv["period"] == pname], keys).rename(columns={"n": f"n_{pname}_{vn}", "mean": f"mean_{pname}_{vn}"})
            out = out.merge(st, on=keys, how="left")
    for c in out.columns:
        if c.startswith(("n_", "A_", "B_", "C_", "R_", "N_", "months_", "n_dedup_")):
            out[c] = out[c].fillna(0).astype(int)
    out["c_share_train"] = out["C_train"] / out["N_train"].replace(0, np.nan)
    out["c_share_test"] = out["C_test"] / out["N_test"].replace(0, np.nan)
    out["subs_positive"] = sum(((out[f"mean_{nm}"] > 0) & (out[f"n_{nm}"] >= SUB_MIN_N)).astype(int) for nm, _, _ in SUBPERIODS)
    out["eligible"] = out["n_train"] >= PASS_N
    out["passed"] = (out["eligible"] & (out["mean_train"] > 0) & (out["t_train"] >= PASS_T) & (out["t_diff_train"] >= PASS_TDIFF) & (out["subs_positive"] == len(SUBPERIODS)))
    out["c_ok"] = out["c_share_train"].fillna(0) <= C_SHARE_MAX
    # 検定の○×（合格の条件ではない）
    out["test_same_sign"] = out["mean_ex_test"] > 0
    out["test_net_positive"] = out["mean_test"] > 0
    out["test_t_ok"] = out["t_test"] >= TEST_T
    # 不安定: どの確かめでも、学習か検定の費用後の平均が 0 以下になったら印
    flips = []
    for vn in variants:
        f = (out[f"mean_train_{vn}"] <= 0) | (out[f"mean_test_{vn}"] <= 0)
        flips.append(f.fillna(False))
    out["unstable"] = np.logical_or.reduce([f.values for f in flips]) if flips else False
    out["unstable_detail"] = ["、".join(vn for vn, f in zip(variants, flips) if f.iloc[i]) for i in range(len(out))]
    out["axes"] = " × ".join(keys)
    out["cell"] = [" × ".join(f"{k}={v}" for k, v in zip(keys, row)) for row in out[keys].values]
    out["events_per_year_train"] = out["n_train"] / ((TRAIN_END - TRAIN_START).days / 365.25)
    out["events_per_year_test"] = out["n_test"] / ((TEST_END - TEST_START).days / 365.25)
    return out


def all_tables(D, H, variants, two_axis=True):
    a1 = pd.concat([table_for(D, [k], H, variants) for k in AXES_FOR_RANK + ["時期"]], ignore_index=True); a1["kind"] = "1軸"
    if not two_axis:
        return a1, None
    parts = []
    for i in range(len(AXES_FOR_RANK)):
        for j in range(i + 1, len(AXES_FOR_RANK)):
            parts.append(table_for(D, [AXES_FOR_RANK[i], AXES_FOR_RANK[j]], H, variants))
    a2 = pd.concat(parts, ignore_index=True); a2["kind"] = "2軸"
    return a1, a2


# ----------------------------------------------------------------------------- 対照とカレンダータイム
def control_rows(E, H, D_main=None, D_d2=None):
    rows = []
    D = D_main if D_main is not None else prepare(E, H)
    L = max(1, H // 21)
    def one(sub, label, pname, tier):
        if len(sub) == 0:
            return
        st = cell_stats(sub.assign(_k="x"), ["_k"], L, month_index(pname) if pname in ("train", "test") else month_index("train") + month_index("test")).iloc[0]
        rows.append({"H": H, "対象": label, "枠": tier, "期間": pname, "n": int(len(sub)), "生の平均": sub["ret"].mean(), "SPY比の平均": sub["ex_spy"].mean(), "同じ階級比の平均": sub["ex"].mean(),
                     "費用後の平均": st["mean"], "費用後の中央値": st["median"], "勝率": st["win"], "下位10%": st["p10"], "−50%以下の割合": st["share_m50"], "上位5%抜きの平均": st["trim_mean"],
                     "上位5%の月抜きの平均": st["ex_top_months_mean"], "t": st["t"], "SPY買い持ちの平均": sub["spy"].mean(), "同じ階級の基準の平均": sub["bench"].mean(), "1年あたりの件数": len(sub) / max((sub["filing_date"].max() - sub["filing_date"].min()).days / 365.25, 0.5)})
    for tier in ["主要", "小口だけ", "買えない", "不明"]:
        R = D[D["has_ret"] & (D["tier"] == tier)]
        for pname in ["train", "test"]:
            one(R[R["period"] == pname], "全部の買い（絞らない）", pname, tier)
    # 今の画面と同じ絞り（1 人・1 日 10 万ドル以上、遅れた届け出を除く）に近い物: 金額 10 万ドル以上・遅れ 10 日以内
    R = D[D["has_ret"] & (D["tier"] == "主要") & (D["value"] >= 1e5) & (D["filing_delay"] <= 10)]
    for pname in ["train", "test"]:
        one(R[R["period"] == pname], "画面と同じ絞り（10 万ドル以上・遅れ 10 日以内）", pname, "主要")
    if D_d2 is not None and H == H_MAIN:
        R = D_d2[D_d2["has_ret"] & (D_d2["tier"] == "主要")]
        for pname in ["train", "test"]:
            one(R[R["period"] == pname], "参考: D+2 の終値で入る", pname, "主要")
        R = D[(D["tier"] == "主要") & D["move_d1"].notna() & D["bench_move"].notna()]
        for pname in ["train", "test"]:
            sub = R[R["period"] == pname]
            if len(sub):
                rows.append({"H": 1, "対象": "参考: D の終値 → D+1 の終値（公表の日の動き。費用なし）", "枠": "主要", "期間": pname, "n": int(len(sub)), "生の平均": sub["move_d1"].mean(), "同じ階級比の平均": (sub["move_d1"] - sub["bench_move"]).mean(), "勝率": float(((sub["move_d1"] - sub["bench_move"]) > 0).mean())})
    return pd.DataFrame(rows)


def calendar_time(D, members_mask, daily, bench_daily, grid, period, label):
    """毎日、保有中の全イベントを等金額で持つ。超過（同じ階級比）と生の日次リターンの年率・t・資金の使われ方"""
    idx = np.where(members_mask & (D["g0"].values >= 0) & (D["tier"].values == "主要"))[0]
    G = len(grid); Hd = daily.shape[1]
    s_ex = np.zeros(G); s_raw = np.zeros(G); cnt = np.zeros(G)
    ev_pos = D.index.values  # D の行 → daily の行番号は D の元の index（E の index）
    for start in range(0, len(idx), 20000):
        ii = idx[start:start + 20000]
        rows = ev_pos[ii]
        g0 = D["g0"].values[ii].astype(np.int64); q = D["adv_q"].values[ii].astype(np.int64)
        pos = g0[:, None] + 1 + np.arange(Hd)[None, :]
        ok = pos < G
        pos_c = np.where(ok, pos, G - 1)
        r = daily[rows].astype(np.float64)
        b = bench_daily[q[:, None].repeat(Hd, axis=1), pos_c].astype(np.float64)
        valid = ok & ~np.isnan(r)
        np.add.at(s_ex, pos_c[valid], (r - b)[valid]); np.add.at(s_raw, pos_c[valid], r[valid]); np.add.at(cnt, pos_c[valid], 1)
    s, e = (TRAIN_START, TRAIN_END) if period == "train" else (TEST_START, TEST_END)
    gdates = pd.to_datetime(pd.Series(grid.astype(np.int64)).astype(str), format="%Y%m%d")
    # 保有は期間の終わりの先に伸びるので、ものさしの日は期間の始めから終わり + H 日まで
    inwin = ((gdates >= s) & (gdates <= e + pd.Timedelta(days=200))).values
    have = inwin & (cnt > 0)
    if have.sum() < 60:
        return {"対象": label, "期間": period, "日数": int(have.sum())}
    pex = s_ex[have] / cnt[have]; praw = s_raw[have] / cnt[have]
    mean_ex = pex.mean(); t_ex = mean_ex / (np.sqrt(max(nw_var(pex[None, :] - mean_ex, 10)[0], 1e-300)) / len(pex))
    return {"対象": label, "期間": period, "日数": int(have.sum()), "資金が使われている日の割合": float(have.sum() / max(inwin.sum(), 1)), "平均の保有件数": float(cnt[have].mean()),
            "超過の年率": mean_ex * 252, "超過の年率のばらつき": pex.std() * np.sqrt(252), "超過の t": t_ex, "生の年率": praw.mean() * 252, "生の年率のばらつき": praw.std() * np.sqrt(252)}


def fmt_pct(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f"{x*100:+.{nd}f}%"


def fmt_num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f"{x:.{nd}f}"


def mark(b):
    return "○" if b is True or (isinstance(b, (bool, np.bool_)) and bool(b)) else "×"


# ----------------------------------------------------------------------------- 本体
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="research/raw/events.parquet")
    ap.add_argument("--out", default="research/out")
    ap.add_argument("--no-two-axis", action="store_true")
    ap.add_argument("--no-calendar", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    E = pd.read_parquet(a.events)
    E["filing_date"] = pd.to_datetime(E["filing_date"])
    raw_dir = os.path.dirname(a.events)
    metas = {}
    for nm in ["purchases_meta.json", "prices_meta.json"]:
        p = os.path.join(raw_dir, nm)
        if os.path.exists(p):
            try:
                metas[nm] = json.load(open(p))
            except Exception:
                pass
    for nm in ["build_meta.json"]:
        p = os.path.join(a.out, nm)
        if os.path.exists(p):
            try:
                metas[nm] = json.load(open(p))
            except Exception:
                pass
    n_vault = int(((E["excl_reason"] == "") & (E["filing_date"] >= VAULT_START) & (E["filing_date"] <= VAULT_END)).sum())
    print(f"イベント {len(E):,}（残した物 {(E['excl_reason'] == '').sum():,}、金庫 2024-01〜2026-09 {n_vault:,} は開けない）", flush=True)

    # ---- 主要（H=126、窓 14 日、費用 ×1.0、D+1 の終値）と、不安定の確かめの 5 通り
    D = prepare(E, H_MAIN)
    variants = {"費用×0.5": prepare(E, H_MAIN, cost_mult=0.5), "費用×1.5": prepare(E, H_MAIN, cost_mult=1.5), "窓30日": prepare(E, H_MAIN, cluster_col="cluster30"),
                "H=63": prepare(E, H_ALT), "途切れ−55%": prepare(E, H_MAIN, trunc_assume=-0.55), "会社1件に間引き": prepare(E, H_MAIN, dedup=True)}
    a1, a2 = all_tables(D, H_MAIN, variants, two_axis=not a.no_two_axis)
    a1.to_csv(os.path.join(a.out, f"axis1_H{H_MAIN}.csv"), index=False)
    if a2 is not None:
        a2.to_csv(os.path.join(a.out, f"axis2_H{H_MAIN}.csv"), index=False)
    for H in HORIZONS:
        if H == H_MAIN:
            continue
        t1, _ = all_tables(prepare(E, H), H, {}, two_axis=False)
        t1.to_csv(os.path.join(a.out, f"axis1_H{H}.csv"), index=False)
    ALL = pd.concat([a1, a2], ignore_index=True) if a2 is not None else a1.copy()
    ALL = ALL[ALL["axes"] != "時期"].copy()
    # 途切れの 4 通り（−30%・−55%・−100%・+30%）の学習と検定の平均（B の行を最後の値 × 仮定で埋めて平均を出し直す）
    for x, nm in [(-0.30, "m30"), (-0.55, "m55"), (-1.0, "m100"), (0.30, "p30")]:
        Dx = prepare(E, H_MAIN, trunc_assume=x)
        Mx = Dx[(Dx["tier"] == "主要") & Dx["has_ret"]]
        rows = []
        for axes in ALL["axes"].unique():
            keys = axes.split(" × ")
            cm_tr = cell_means(Mx[Mx["period"] == "train"], keys).rename(columns={"mean": f"mean_train_trunc_{nm}"}).drop(columns=["n"])
            cm_te = cell_means(Mx[Mx["period"] == "test"], keys).rename(columns={"mean": f"mean_test_trunc_{nm}"}).drop(columns=["n"])
            cmm = cm_tr.merge(cm_te, on=keys, how="outer")
            cmm["axes"] = axes; cmm["cell"] = [" × ".join(f"{k}={v}" for k, v in zip(keys, row)) for row in cmm[keys].values]
            rows.append(cmm[["axes", "cell", f"mean_train_trunc_{nm}", f"mean_test_trunc_{nm}"]])
        if rows:
            ALL = ALL.merge(pd.concat(rows, ignore_index=True), on=["axes", "cell"], how="left")

    # ---- 合格と順位（学習だけで決めて凍結）。C が 15% 超の段は参考に落とす
    passed = ALL[ALL["passed"] & ALL["c_ok"]].copy()
    p1 = passed[passed["kind"] == "1軸"].sort_values("mean_train", ascending=False)
    p2 = passed[passed["kind"] == "2軸"].sort_values("mean_train", ascending=False)
    top = pd.concat([p1, p2], ignore_index=True); top["区分"] = "合格"
    if len(top) < 10:
        ref = ALL[ALL["eligible"] & ~(ALL["passed"] & ALL["c_ok"])].sort_values("t_diff_train", ascending=False).head(10 - len(top)).copy()
        ref["区分"] = np.where(ref["passed"] & ~ref["c_ok"], "参考（合格だが株価が引けない割合 15% 超）", "参考（合格ではない。学習の差の t の順）")
        top = pd.concat([top, ref], ignore_index=True)
    top = top.head(10).copy(); top.insert(0, "順位", range(1, len(top) + 1))
    top["検定○の数"] = top["test_same_sign"].astype(int) + top["test_net_positive"].astype(int) + top["test_t_ok"].astype(int)
    keep_cols = ["順位", "区分", "kind", "axes", "cell", "n_train", "mean_train", "median_train", "win_train", "p10_train", "share_m50_train", "trim_mean_train", "ex_top_months_mean_train", "t_train", "diff_train", "t_diff_train",
                 "subs_positive", "events_per_year_train", "n_dedup_train", "A_train", "B_train", "C_train", "N_train", "c_share_train",
                 "n_test", "mean_test", "median_test", "win_test", "p10_test", "share_m50_test", "trim_mean_test", "ex_top_months_mean_test", "t_test", "t_diff_test", "events_per_year_test", "n_dedup_test", "A_test", "B_test", "C_test", "N_test", "c_share_test",
                 "test_same_sign", "test_net_positive", "test_t_ok", "検定○の数", "unstable", "unstable_detail"] + [c for c in ALL.columns if c.startswith("mean_train_") or c.startswith("mean_test_")]
    top[[c for c in keep_cols if c in top.columns]].to_csv(os.path.join(a.out, "top10.csv"), index=False)
    passed[[c for c in keep_cols if c in passed.columns and c not in ("順位", "区分", "検定○の数")]].to_csv(os.path.join(a.out, "passed.csv"), index=False)
    ALL[["kind", "axes", "cell", "eligible", "passed", "c_ok", "unstable", "unstable_detail", "n_train", "mean_train", "t_train", "t_diff_train", "subs_positive", "n_test", "mean_test", "t_test"]
        + [c for c in ALL.columns if c.startswith("mean_train_") or c.startswith("mean_test_")]].to_csv(os.path.join(a.out, "robustness.csv"), index=False)

    # ---- 偶然の数
    n1 = int(a1[(a1["axes"] != "時期") & a1["eligible"]].shape[0]); n2 = int(a2[a2["eligible"]].shape[0]) if a2 is not None else 0
    fp = {"1軸の試した段（n≥300）": n1, "2軸の試した段（n≥300）": n2, "1軸 t>3 の偶然の見込み": n1 * P_T3, "2軸 t>3 の偶然の見込み": n2 * P_T3, "1軸 t>2 の偶然の見込み": n1 * P_T2, "2軸 t>2 の偶然の見込み": n2 * P_T2,
          "何も無くても一番良い段に見える t（√(2 ln N)）": math.sqrt(2 * math.log(max(n1 + n2, 2))),
          "1軸 学習で差の t>3": int(((a1["axes"] != "時期") & a1["eligible"] & (a1["t_diff_train"] > 3)).sum()), "2軸 学習で差の t>3": int((a2["eligible"] & (a2["t_diff_train"] > 3)).sum()) if a2 is not None else 0,
          "1軸 合格": int(len(p1)), "2軸 合格": int(len(p2))}

    # ---- 対照
    D_d2 = prepare(E, H_MAIN, entry="d2")
    ctrl = pd.concat([control_rows(E, H, D if H == H_MAIN else None, D_d2 if H == H_MAIN else None) for H in HORIZONS], ignore_index=True)
    ctrl.to_csv(os.path.join(a.out, "control.csv"), index=False)
    def ctrl_val(period, col, tier="主要", H=H_MAIN, label="全部の買い（絞らない）"):
        if len(ctrl) == 0 or any(c not in ctrl.columns for c in ("H", "期間", "枠", "対象", col)):
            return np.nan
        sub = ctrl[(ctrl["H"] == H) & (ctrl["期間"] == period) & (ctrl["枠"] == tier) & (ctrl["対象"] == label)]
        v = sub[col].iloc[0] if len(sub) else np.nan
        return float(v) if v is not None and not pd.isna(v) else np.nan

    # ---- 1 軸ごとに、効く軸と効かない軸（学習の差の t と、学習→検定の順位の一致）
    eff_rows = []
    for ax, g in a1.groupby("axes", sort=False):
        if ax == "時期":
            continue
        gg = g[g["eligible"]]
        rho = gg["mean_train"].corr(gg["mean_test"], method="spearman") if len(gg) >= 3 and gg["n_test"].min() >= 100 else np.nan
        best = gg.sort_values("t_diff_train", ascending=False).iloc[0] if len(gg) else None
        maxt = gg["t_diff_train"].abs().max() if len(gg) else np.nan
        if best is not None and best["t_diff_train"] >= 3 and (np.isnan(rho) or rho >= 0.5):
            verdict = "効く（学習で差の t ≥ 3。検定でも順位が合う）" if not np.isnan(rho) else "効く（学習で差の t ≥ 3。段が少なく順位の一致は見られない）"
        elif not np.isnan(maxt) and maxt < 2:
            verdict = "効かない（学習でどの段も差の t < 2）"
        else:
            verdict = "はっきりしない"
        eff_rows.append({"軸": ax, "段の数（n≥300）": int(len(gg)), "差の t の最大": maxt, "学習→検定の順位の一致（スピアマン）": rho, "判定": verdict,
                         "学習で一番差が出た段": best["cell"] if best is not None else "", "その段の学習の平均": best["mean_train"] if best is not None else np.nan, "その段の検定の平均": best["mean_test"] if best is not None else np.nan})
    eff = pd.DataFrame(eff_rows); eff.to_csv(os.path.join(a.out, "axis_effects.csv"), index=False)

    # ---- 上場廃止の抜け（年ごと・売買代金の階級ごと・10 選の段ごと）
    M = D[D["tier"] == "主要"]
    surv = []
    for name, grp in [("年", D.groupby("year")), ("売買代金の階級（不明 = 株価なし）", D.groupby("adv_q")), ("時価総額", D.groupby("時価総額", observed=True)), ("枠", D.groupby("tier"))]:
        for k, g in grp:
            tot = len(g)
            surv.append({"区切り": name, "値": str(k), "イベント": tot, "A 結果あり": int(g["has_ret"].sum()), "B 途中で終わる": int(g["trunc"].sum()), "C 引けない": int(g["noprice"].sum()), "R まだ": int(g["pending"].sum()),
                         "A の割合": g["has_ret"].mean() if tot else np.nan, "B の割合": g["trunc"].mean() if tot else np.nan, "C の割合": g["noprice"].mean() if tot else np.nan})
    surv = pd.DataFrame(surv); surv.to_csv(os.path.join(a.out, "survivorship.csv"), index=False)
    cov_path = os.path.join(a.out, "coverage.csv")
    cov = pd.read_csv(cov_path) if os.path.exists(cov_path) else None

    # ---- カレンダータイム（1 軸の全部の段・10 選・全部の買い・SPY）
    cal_rows = []
    if not a.no_calendar and os.path.exists(os.path.join(raw_dir, "event_daily.npz")):
        z = np.load(os.path.join(raw_dir, "event_daily.npz"))
        daily, grid, bench_daily, spy_daily = z["daily"], z["grid"], z["bench_daily"], z["spy_daily"]
        if daily.shape[0] == len(E):
            for pname in ["train", "test"]:
                pm = (D["period"].values == pname)
                cal_rows.append(calendar_time(D, pm, daily, bench_daily, grid, pname, "全部の買い（絞らない）"))
                for _, r in top.iterrows():
                    keys = r["axes"].split(" × "); vals = [kv.split("=", 1)[1] for kv in r["cell"].split(" × ")]
                    mask = pm.copy()
                    for k, v in zip(keys, vals):
                        mask &= (D[k].astype(str).values == v)
                    cal_rows.append(calendar_time(D, mask, daily, bench_daily, grid, pname, f"順位 {r['順位']}: {r['cell']}"))
                for ax in AXES_FOR_RANK:
                    for v in D[ax].cat.categories:
                        mask = pm & (D[ax].astype(str).values == v)
                        if mask.sum() >= 300:
                            cal_rows.append(calendar_time(D, mask, daily, bench_daily, grid, pname, f"{ax}={v}"))
                # SPY 買い持ち（同じ期間）
                s, e = (TRAIN_START, TRAIN_END) if pname == "train" else (TEST_START, TEST_END)
                gdates = pd.to_datetime(pd.Series(grid.astype(np.int64)).astype(str), format="%Y%m%d")
                w = ((gdates >= s) & (gdates <= e)).values
                cal_rows.append({"対象": "SPY 買い持ち", "期間": pname, "日数": int(w.sum()), "生の年率": float(spy_daily[w].mean() * 252), "生の年率のばらつき": float(spy_daily[w].std() * np.sqrt(252))})
        else:
            print(f"[warn] event_daily の行数 {daily.shape[0]} がイベント数 {len(E)} と違うのでカレンダータイムは出さない", flush=True)
    cal = pd.DataFrame(cal_rows); cal.to_csv(os.path.join(a.out, "calendar_time.csv"), index=False)

    # ---- 予想の答え合わせ（PREREG v1.1 の 4 章。1〜11）
    def cm(axes, cell, period):
        r = ALL[(ALL["axes"] == axes) & (ALL["cell"] == cell)]
        return float(r[f"mean_{period}"].iloc[0]) if len(r) and not pd.isna(r[f"mean_{period}"].iloc[0]) else np.nan
    def cn(axes, cell, period):
        r = ALL[(ALL["axes"] == axes) & (ALL["cell"] == cell)]
        return int(r[f"n_{period}"].iloc[0]) if len(r) else 0
    def wavg(pairs):
        num = sum(m * n for m, n in pairs if not np.isnan(m)); den = sum(n for m, n in pairs if not np.isnan(m))
        return num / den if den else np.nan
    preds = []
    tr, te = ctrl_val("train", "費用後の平均"), ctrl_val("test", "費用後の平均")
    Dtr = D[(D["period"] == "train") & ~D["pending"]]
    A_share_train = float(Dtr["has_ret"].sum() / max(len(Dtr), 1)) if len(Dtr) else 0.0
    ok1 = None if A_share_train < 0.85 else (bool(0.01 <= tr <= 0.03 and 0.0 <= te <= 0.02) if not (np.isnan(tr) or np.isnan(te)) else None)
    preds.append({"番号": 1, "予想": "全部の買いの費用後 126 日の超過: 学習 +1〜+3%、検定 0〜+2%（A の割合が 85% 未満なら答え合わせしない）", "実際": f"学習 {fmt_pct(tr)}、検定 {fmt_pct(te)}、学習の A の割合 {A_share_train*100:.1f}%", "当たり": ok1, "別枠": False})
    c1 = cm("クラスター人数", "クラスター人数=1人", "test"); c3p = wavg([(cm("クラスター人数", f"クラスター人数={v}", "test"), cn("クラスター人数", f"クラスター人数={v}", "test")) for v in ["3人", "4人以上"]])
    preds.append({"番号": 2, "予想": "クラスター 3 人以上 − 1 人の差: +2% 以上（検定）", "実際": f"3人以上 {fmt_pct(c3p)}、1人 {fmt_pct(c1)}、差 {fmt_pct(c3p - c1)}", "当たり": bool(c3p - c1 >= 0.02) if not np.isnan(c3p - c1) else None, "別枠": False})
    small = wavg([(cm("時価総額", f"時価総額={v}", "test"), cn("時価総額", f"時価総額={v}", "test")) for v in ["3億未満", "3億〜20億"]]); big = cm("時価総額", "時価総額=100億超", "test")
    preds.append({"番号": 3, "予想": "時価総額 20 億未満 ≥ 100 億超 + 2%。100 億超は費用後 −0.5〜+0.5%", "実際": f"20億未満 {fmt_pct(small)}、100億超 {fmt_pct(big)}", "当たり": bool(small >= big + 0.02 and -0.005 <= big <= 0.005) if not (np.isnan(small) or np.isnan(big)) else None, "別枠": False})
    ro, op = cm("日課か", "日課か=日課", "test"), cm("日課か", "日課か=日課でない", "test")
    preds.append({"番号": 4, "予想": "日課でない − 日課の差: +2% 以上。日課は費用後 −1〜+1%", "実際": f"日課 {fmt_pct(ro)}、日課でない {fmt_pct(op)}、差 {fmt_pct(op - ro)}", "当たり": bool(op - ro >= 0.02 and -0.01 <= ro <= 0.01) if not (np.isnan(ro) or np.isnan(op)) else None, "別枠": False})
    r5 = ALL[(ALL["axes"] == "役職 × 高値からの下落") & ALL["cell"].str.contains("役職=CEO|役職=CFO") & ALL["cell"].str.contains("50%超")]
    if len(r5) and r5["n_test"].sum() > 0:
        m5 = wavg(list(zip(r5["mean_test"], r5["n_test"]))); w5 = wavg(list(zip(r5["win_test"], r5["n_test"]))); p5 = wavg(list(zip(r5["p10_test"], r5["n_test"])))
        preds.append({"番号": 5, "予想": "下落 50% 超 × CEO・CFO: 全部の買いより +3% 以上、勝率 55% 未満、下位 10% は −40% より悪い", "実際": f"平均 {fmt_pct(m5)}（全部 {fmt_pct(te)}）、勝率 {fmt_pct(w5,0)}、下位10% {fmt_pct(p5)}",
                      "当たり": bool(m5 >= te + 0.03 and w5 < 0.55 and p5 < -0.40) if not np.isnan(m5) else None, "別枠": False})
    tp = top[top["区分"] == "合格"]
    if len(tp) and tp["n_test"].sum() > 0:
        ratio = (tp["mean_test"] * tp["n_test"]).sum() / max(tp["n_test"].sum(), 1) / max((tp["mean_train"] * tp["n_train"]).sum() / max(tp["n_train"].sum(), 1), 1e-9)
        flips = int((tp["mean_test"] <= 0).sum())
        preds.append({"番号": 6, "予想": "10 選の検定の平均は学習の 5〜7 割。検定で符号が逆になるのは 3 本以下", "実際": f"検定÷学習 = {fmt_num(ratio)}、符号が逆 {flips} 本 / {len(tp)}", "当たり": bool(0.5 <= ratio <= 0.7 and flips <= 3), "別枠": False})
    else:
        preds.append({"番号": 6, "予想": "10 選の検定の平均は学習の 5〜7 割。検定で符号が逆になるのは 3 本以下", "実際": "合格が 0 本なので判定できず", "当たり": None, "別枠": False})
    if cov is not None and len(cov):
        early = cov[(cov["year"] >= 2006) & (cov["year"] <= 2012)]; late = cov[cov["year"] >= 2020]
        pe = early["with_price"].sum() / max(early["events"].sum(), 1) * 100; pl = late["with_price"].sum() / max(late["events"].sum(), 1) * 100
        preds.append({"番号": 7, "予想": "2006〜2012 の A の割合は 2020〜2026 より 10 ポイント以上低い（データの予想）", "実際": f"2006〜2012 {pe:.1f}%、2020〜 {pl:.1f}%", "当たり": bool(pl - pe >= 10), "別枠": True})
    preds.append({"番号": 8, "予想": "2 軸の合格は偶然（t>3）の 3 倍以下", "実際": f"合格 {fp['2軸 合格']} 本、偶然の見込み {fp['2軸 t>3 の偶然の見込み']:.1f} 本", "当たり": bool(fp["2軸 合格"] <= 3 * fp["2軸 t>3 の偶然の見込み"]) if n2 else None, "別枠": False})
    ub = ctrl_val("test", "生の平均", tier="買えない"); mb = ctrl_val("test", "生の平均", tier="主要")
    preds.append({"番号": 9, "予想": "「買えない」枠（売買代金 10 万ドル未満）の生の平均は主要より大きく見える（データの予想）", "実際": f"買えない {fmt_pct(ub)}、主要 {fmt_pct(mb)}", "当たり": bool(ub > mb) if not (np.isnan(ub) or np.isnan(mb)) else None, "別枠": True})
    d2 = ctrl_val("test", "費用後の平均", label="参考: D+2 の終値で入る")
    preds.append({"番号": 10, "予想": "D+2 の終値で入ると全部の買いの平均は 0.3〜1.0 ポイント減る（半分以上減れば「公表の日に終わる動き」）", "実際": f"D+1 {fmt_pct(te)}、D+2 {fmt_pct(d2)}、差 {fmt_pct(te - d2)}", "当たり": bool(0.003 <= te - d2 <= 0.010) if not (np.isnan(te) or np.isnan(d2)) else None, "別枠": False})
    r252 = ctrl_val("test", "費用後の平均", H=252); r126 = te
    preds.append({"番号": 11, "予想": "保有 252 日の超過は 126 日より大きい（反転しない）", "実際": f"126 日 {fmt_pct(r126)}、252 日 {fmt_pct(r252)}", "当たり": bool(r252 > r126) if not (np.isnan(r252) or np.isnan(r126)) else None, "別枠": False})
    P = pd.DataFrame(preds); P.to_csv(os.path.join(a.out, "predictions.csv"), index=False)
    misses = int(((P["当たり"] == False) & (~P["別枠"])).sum()); judged = int((P["当たり"].notna() & (~P["別枠"])).sum())

    # ---- 見本と外した数
    smp_cols = [c for c in ["issuer_name", "ticker", "filing_date", "value", "n_persons", "role", "cluster14", "mcap", "adv63", "prev63", "dd252", "price_f4", "ret_126", "bench_126", "spy_126"] if c in E.columns]
    K = E[(E["excl_reason"] == "") & (E["t0_date"] > 0)]
    K.sample(n=min(1000, len(K)), random_state=7)[smp_cols].to_csv(os.path.join(a.out, "events_sample.csv"), index=False)
    ex_path = os.path.join(a.out, "exclusions.csv")
    excl = pd.read_csv(ex_path) if os.path.exists(ex_path) else None
    checks_path = os.path.join(a.out, "data_checks.md")
    checks = open(checks_path, encoding="utf-8").read() if os.path.exists(checks_path) else ""

    # ---- summary.md
    L = []
    L.append(f"# 役員の買い（Form 4・P）総当たり検証 — 結果（{ANALYZER}、作成 {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M')} UTC）\n")
    L.append("数字は全部この道具（research/analyze.py）が書いた。人も AI も手で直していない。決まりは research/PREREG_v1.md（v1.1。測る前に凍結）。\n")
    L.append("言葉: bp = 1 万分の 1。5 分位 = 大きさ順に 5 等分。t 値 = 平均が偶然でない度合い（3.0 以上で合格）。差の t = その段と残り全部の差が偶然でない度合い。感応度 = 仮定を変えたときの数字の動き。A = 窓の終わりまで株価がある、B = 途中で株価が終わる（上場廃止・買収の疑い）、C = 最初から株価が引けない。\n")
    n_tr = int(ctrl_val("train", "n")) if not np.isnan(ctrl_val("train", "n")) else 0; n_te = int(ctrl_val("test", "n")) if not np.isnan(ctrl_val("test", "n")) else 0
    L.append("## Status\n")
    L.append(f"- 母集団: イベント {len(E):,}（外した物を除いて {(E['excl_reason']=='').sum():,}）。主要の枠（売買代金 100 万ドル以上）で結果がある物: 学習 {n_tr:,}・検定 {n_te:,}。金庫 2024-01〜2026-09 の {n_vault:,} は開けていない")
    L.append(f"- 合格した段（学習だけで判定）: 1 軸 {fp['1軸 合格']} 本、2 軸 {fp['2軸 合格']} 本。試した段 1 軸 {n1}・2 軸 {n2}。偶然で t>3 を超える見込み 1 軸 {fp['1軸 t>3 の偶然の見込み']:.1f} 本・2 軸 {fp['2軸 t>3 の偶然の見込み']:.1f} 本。何も無くても一番良い段は t ≈ {fp['何も無くても一番良い段に見える t（√(2 ln N)）']:.1f} に見える")
    verdict = "見つかっていない（合格の本数が偶然の見込みと同じ程度）" if (fp["1軸 合格"] + fp["2軸 合格"]) <= (fp["1軸 t>3 の偶然の見込み"] + fp["2軸 t>3 の偶然の見込み"]) * 1.5 else "偶然では説明しにくい段がある（検定の○×を見る）"
    L.append(f"- 判定: {verdict}")
    L.append(f"- 全部の買い（絞らない・主要）の費用後 126 日の超過: 学習 {fmt_pct(tr)}、検定 {fmt_pct(te)}")
    L.append(f"- 予想の答え合わせ: 別枠を除く {judged} 個のうち外れ {misses} 個（4 個以上なら相場観を見直す）\n")
    L.append("## 1. 順位 10 選（学習の費用後 126 日の超過リターンの大きい順。1 軸の合格 → 2 軸の合格 → 参考。検定は答え合わせの○×）\n")
    L.append("| 順位 | 区分 | 段 | 学習 n | 学習 平均 | 学習 中央値 | 学習 勝率 | 学習 下位10% | 学習 t | 差の t | 3 期間 | 年の件数 | 検定 n | 検定 平均 | 検定 中央値 | 検定 勝率 | 検定 t | 同符号 | 費用後＋ | t≥2 | 不安定 | 株価が引けない割合（学習） | A/B/C（検定） | B を −55% |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in top.iterrows():
        L.append(f"| {r['順位']} | {r['区分']} | {r['cell']} | {r['n_train']} | {fmt_pct(r['mean_train'])} | {fmt_pct(r['median_train'])} | {fmt_pct(r['win_train'],0)} | {fmt_pct(r['p10_train'])} | {fmt_num(r['t_train'])} | {fmt_num(r['t_diff_train'])} | {r['subs_positive']}/3 | {fmt_num(r['events_per_year_train'],0)} | "
                 f"{r['n_test']} | {fmt_pct(r['mean_test'])} | {fmt_pct(r['median_test'])} | {fmt_pct(r['win_test'],0)} | {fmt_num(r['t_test'])} | {mark(r['test_same_sign'])} | {mark(r['test_net_positive'])} | {mark(r['test_t_ok'])} | {('不安定: ' + r['unstable_detail']) if r['unstable'] else '安定'} | {fmt_pct(r['c_share_train'],0)} | {r['A_test']}/{r['B_test']}/{r['C_test']} | {fmt_pct(r.get('mean_test_trunc_m55', np.nan))} |")
    L.append("")
    L.append("読み方: 「平均」は、同じ売買代金の階級の平均を引き、往復の費用（売買代金で 0.3〜2.5%）も引いたあとの 126 営業日のリターン（イベント等重み）。「差の t」は残り全部との差。「3 期間」は学習を 3 つに切って全部プラスだった数。「不安定」は費用 ±50%・クラスターの窓 14→30 日・H 126→63 日・途切れ −55%・会社 1 件に間引き のどれかで学習か検定の平均が 0 以下になった印。")
    L.append("年率は出さない（信号が無い間の資金の空きを無視するため）。生徒が受け取る年率は 2.1 のカレンダータイムの表を見る。\n")
    L.append("## 2. 対照（これが今の画面の中身の期待値。絞らない買いと、買い持ち）\n")
    L.append("| 対象 | 枠 | 期間 | H | n | 生の平均 | SPY 比 | 同じ階級比 | 費用後 | 中央値 | 勝率 | 下位10% | −50%以下 | 上位5%抜き | 上位5%の月抜き | t | SPY 買い持ち | 年の件数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in ctrl.iterrows():
        L.append(f"| {r['対象']} | {r['枠']} | {r['期間']} | {r['H']} | {r['n']} | {fmt_pct(r.get('生の平均', np.nan))} | {fmt_pct(r.get('SPY比の平均', np.nan))} | {fmt_pct(r.get('同じ階級比の平均', np.nan))} | {fmt_pct(r.get('費用後の平均', np.nan))} | {fmt_pct(r.get('費用後の中央値', np.nan))} | {fmt_pct(r.get('勝率', np.nan),0)} | {fmt_pct(r.get('下位10%', np.nan))} | {fmt_pct(r.get('−50%以下の割合', np.nan),0)} | {fmt_pct(r.get('上位5%抜きの平均', np.nan))} | {fmt_pct(r.get('上位5%の月抜きの平均', np.nan))} | {fmt_num(r.get('t', np.nan))} | {fmt_pct(r.get('SPY買い持ちの平均', np.nan))} | {fmt_num(r.get('1年あたりの件数', np.nan),0)} |")
    L.append("")
    if len(cal):
        L.append("### 2.1 カレンダータイム（毎日、保有中の全イベントを等金額で持つ。10 選と全部の買いと SPY）\n")
        L.append("| 対象 | 期間 | 日数 | 資金が使われている日 | 平均の保有件数 | 超過の年率 | 超過の t | 生の年率 | ばらつき（年率） |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for _, r in cal.iterrows():
            if str(r["対象"]).startswith(("順位", "全部", "SPY")):
                L.append(f"| {r['対象']} | {r['期間']} | {r['日数']} | {fmt_pct(r.get('資金が使われている日の割合', np.nan),0)} | {fmt_num(r.get('平均の保有件数', np.nan),0)} | {fmt_pct(r.get('超過の年率', np.nan))} | {fmt_num(r.get('超過の t', np.nan))} | {fmt_pct(r.get('生の年率', np.nan))} | {fmt_pct(r.get('生の年率のばらつき', np.nan))} |")
        L.append("")
    L.append("## 3. 1 軸ごとに、効く軸と効かない軸\n")
    L.append("| 軸 | 段の数 | 差の t の最大 | 学習→検定の順位の一致 | 判定 | 学習で一番差が出た段 | その段の学習 | その段の検定 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for _, r in eff.iterrows():
        L.append(f"| {r['軸']} | {r['段の数（n≥300）']} | {fmt_num(r['差の t の最大'])} | {fmt_num(r['学習→検定の順位の一致（スピアマン）'])} | {r['判定']} | {r['学習で一番差が出た段']} | {fmt_pct(r['その段の学習の平均'])} | {fmt_pct(r['その段の検定の平均'])} |")
    L.append("")
    L.append("### 3.1 1 軸の全部の段（H=126、費用後。学習と検定）\n")
    L.append("| 段 | 学習 n | 学習 平均 | 学習 t | 差の t | 3 期間 | 検定 n | 検定 平均 | 検定 勝率 | 検定 t | 合格 | A/B/C（学習） |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in a1.iterrows():
        if r["n_train"] + r["n_test"] == 0:
            continue
        L.append(f"| {r['cell']} | {r['n_train']} | {fmt_pct(r['mean_train'])} | {fmt_num(r['t_train'])} | {fmt_num(r['t_diff_train'])} | {r['subs_positive']}/3 | {r['n_test']} | {fmt_pct(r['mean_test'])} | {fmt_pct(r['win_test'],0)} | {fmt_num(r['t_test'])} | {'合格' if (r['passed'] and r['c_ok']) else ('C超' if r['passed'] else '')} | {r['A_train']}/{r['B_train']}/{r['C_train']} |")
    L.append("")
    L.append("## 4. 偶然の数（試した段の数と、偶然で合格する見込み）\n")
    for k, v in fp.items():
        L.append(f"- {k}: {v if isinstance(v, int) else f'{v:.1f}'}")
    L.append("- 2 軸の合格が 0 本でも失敗ではない。126 日の超過のばらつきは 1 件で 30〜50% あり、n 300 の段が t>3 を通るには平均 10% 級が要る。\n")
    L.append("## 5. 上場廃止の抜け（A/B/C の割合）\n")
    L.append("| 区切り | 値 | イベント | A 結果あり | B 途中で終わる | C 引けない | R まだ | A | B | C |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in surv.iterrows():
        L.append(f"| {r['区切り']} | {r['値']} | {r['イベント']} | {r['A 結果あり']} | {r['B 途中で終わる']} | {r['C 引けない']} | {r['R まだ']} | {fmt_pct(r['A の割合'],0)} | {fmt_pct(r['B の割合'],0)} | {fmt_pct(r['C の割合'],0)} |")
    L.append("")
    L.append("B を最後の値から −30%／−55%／−100%／+30% と仮定したときの平均は top10.csv・robustness.csv の列 mean_*_trunc_* にある。下落・低位・小型の段は −55% の数字（10 選の表の最後の列）を見出しにする。C が 15% を超える段は 10 選に入れていない。\n")
    L.append("## 6. 予想の答え合わせ（測る前に書いた 11 個。7・9 はデータの予想で別枠）\n")
    L.append("| 番号 | 予想 | 実際 | 当たり |")
    L.append("|---|---|---|---|")
    for _, r in P.iterrows():
        L.append(f"| {r['番号']} | {r['予想']} | {r['実際']} | {'○' if r['当たり'] is True else ('×' if r['当たり'] is False else '判定できず')} |")
    L.append(f"\n別枠を除く {judged} 個のうち外れ {misses} 個。{'4 個以上外れたので相場観を見直す' if misses >= 4 else '4 個未満'}。\n")
    L.append("## 7. 外した物の数（理由ごと）\n")
    if excl is not None:
        for _, r in excl.iterrows():
            L.append(f"- {r['理由']}: {int(r['数']):,}")
        L.append("")
    if "build_meta.json" in metas:
        bm = metas["build_meta.json"]
        L.append(f"- 4/A で後から直された元の届け出の割合: {bm.get('amended_share_of_person_filings', float('nan'))*100:.2f}%（3% 超なら置き換え版も出す）")
        L.append(f"- 株価が引けない理由: {bm.get('noprice_reasons', {})}\n")
    L.append("## 8. データの確かめ（測る前。道具が書いた）\n")
    L.append(checks if checks else "（data_checks.md が無い）")
    L.append("\n## 9. 言えないこと・弱点\n")
    L.append("- 株価は無料のデータ（stooq）。上場廃止になった会社が抜けている分、成績を良く見ている可能性がある（5 章の A/B/C と、B の仮定を見る）。買収で消えた株も B に入るので、−55% の仮定は厳しめ。")
    L.append("- 配当は株価に入っていないと見る（8 章の確かめ）。配当の多い大型株では超過リターンを少し低く見ている。")
    L.append("- 時価総額は SEC の XBRL の発行株数（提出日が D 以前）× Form 4 の単価。XBRL は 2009〜2011 に始まったので、それより前は「不明」が多い。株式が 2 種類以上ある会社は片方だけの可能性。")
    L.append("- 費用は売買代金の段で決めた目安（往復 0.3〜2.5%）。実際の値幅・滑りは銘柄と時期で違う。")
    L.append("- 入る日は届け出日 D の次の営業日（SPY が開いている日）の終値。D+2 と D→D+1 の行を 2 章に参考として出した。")
    L.append("- 日課の判定は買いだけで行った（本家 Cohen・Malloy・Pomorski は売りも含む）。")
    L.append("- 対照（同じ階級の平均）は売買代金の 5 分位。時価総額の階級ではない（2006〜2008 の時価総額が無いため。分割で直された株価の影響も受けない）。")
    L.append("- 単価が空で脚注にだけ幅がある行は落とした（7 章の数）。大きい買いほどこの書き方が多い。")
    L.append("- これは過去の平均。1 回の売買の結果はばらつく。中央値・勝率・下位 10% を横に見る。")
    if metas:
        L.append(f"- データの素性: {json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ('missing_sample', 'exclusions', 'noprice_reasons')} for k, v in metas.items()}, ensure_ascii=False, default=str)}")
    open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump({"analyzer": ANALYZER, "events": int(len(E)), "kept": int((E["excl_reason"] == "").sum()), "vault": n_vault, "fp": fp, "passed_1": int(len(p1)), "passed_2": int(len(p2)),
               "control_train_net126": tr, "control_test_net126": te, "pred_misses": misses, "pred_judged": judged}, open(os.path.join(a.out, "summary.json"), "w"), ensure_ascii=False, indent=1, default=float)
    print(f"終了: 合格 1 軸 {len(p1)} / 2 軸 {len(p2)}、対照 学習 {fmt_pct(tr)} 検定 {fmt_pct(te)}、予想の外れ {misses}/{judged} → {a.out}/summary.md", flush=True)


if __name__ == "__main__":
    main()
