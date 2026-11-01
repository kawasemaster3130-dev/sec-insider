#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
買いの行（purchases.parquet）→ 人 × 会社 × 届け出日 → 会社 × 届け出日（イベント）の表を作る。事前登録 PREREG_v1.md（v1.1）の 1 章・2 章の定義どおり。
特徴は全部「届け出日 D の終値まで」の情報で作る。発行株数は「使える日（提出日）が D 以前」の最新の値。クラスターは届け出日で数える。

使い方: python3 research/build_events.py --raw research/raw --out research/raw/events.parquet --outdir research/out
出す物: events.parquet（1 行 = 1 イベント）、event_daily.npz（カレンダータイム用の日次リターン）、research/out/{coverage.csv, exclusions.csv, build_meta.json}
"""
import argparse, json, os, pickle, re, sys, warnings
from collections import Counter
import numpy as np
import pandas as pd

BUILDER = "build_events-2.1"
HORIZONS = [21, 63, 126, 252]
H_MAIN = 126
ROLE_ORDER = ["CEO", "Chair", "President", "CFO", "Officer", "Director", "TenPct", "Other"]
RE_CEO = re.compile(r"chief executive|\bceo\b|principal executive|\bpeo\b", re.I)
RE_CFO = re.compile(r"chief financial|\bcfo\b|principal financial|\bpfo\b", re.I)
RE_CHAIR = re.compile(r"chairman|chairwoman|chairperson|\bchair\b|\bcob\b", re.I)
RE_PRES = re.compile(r"\bpresident\b", re.I)
RE_VP = re.compile(r"vice[\s-]*president|\bvp\b|\bevp\b|\bsvp\b", re.I)
ENTRY_WAIT_DAYS = 3        # 翌営業日に株の値が無ければ、ものさし（SPY）の営業日で 3 日まで待つ
STALE_DAYS = 10            # H 日後の値が、その日より 10 日以上前の値しか無ければ「無い」と見る
PRICE_TOL = 1.15           # Form 4 の単価 ÷ 株価 が、同じ銘柄・同じ年の中央値の ±15% なら OK
MCAP_SHARE_MAX = 0.05      # 1 イベントの金額が時価総額の 5% 超なら外す
ROW_VALUE_MAX = 1e9        # 1 行 10 億ドル超は外す
ADV_MULT_MAX = 50.0        # 金額が売買代金の 50 倍超は外す


def role_of(rel, title):
    rel = (rel or "").lower(); title = title or ""
    is_off = "officer" in rel; is_dir = "director" in rel; is_ten = "tenpercent" in rel.replace(" ", "") or "10%" in rel
    if is_off or RE_CEO.search(title) or RE_CFO.search(title):
        if RE_CEO.search(title):
            return "CEO"
        if RE_CHAIR.search(title):
            return "Chair"
        if RE_PRES.search(title) and not RE_VP.search(title):
            return "President"
        if RE_CFO.search(title):
            return "CFO"
        return "Officer"
    if RE_CHAIR.search(title):
        return "Chair"
    if is_dir:
        return "Director"
    if is_ten:
        return "TenPct"
    return "Other"


def norm_symbol(s):
    s = (s or "").strip().upper()
    if not s or s in ("N/A", "NONE", "NA", "NULL", "-", "NAN"):
        return ""
    s = s.split(",")[0].split(" ")[0].split("/")[0]
    s = s.replace(".", "-")
    return s if re.fullmatch(r"[A-Z0-9-]{1,10}", s) else ""


def date_int(ts):
    return int(ts.year) * 10000 + int(ts.month) * 100 + int(ts.day)


def ints_to_ts(arr):
    return pd.to_datetime(pd.Series(np.asarray(arr, dtype=np.int64)).astype(str), format="%Y%m%d", errors="coerce")


def first_nonempty(s):
    for v in s:
        if isinstance(v, str) and v:
            return v
    return ""


def plausible_factor(m):
    """単価 ÷ 株価 の中央値が、分割の比として説明できるか（1 の近く、または 1.7 倍以上・0.59 以下で 600 倍の中）"""
    if not np.isfinite(m) or m <= 0:
        return False
    return (1 / PRICE_TOL <= m <= PRICE_TOL) or (1.7 <= m <= 600) or (1 / 600 <= m <= 0.59)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="research/raw")
    ap.add_argument("--out", default="research/raw/events.parquet")
    ap.add_argument("--outdir", default="research/out")
    a = ap.parse_args()
    R = a.raw
    os.makedirs(a.outdir, exist_ok=True)
    P = pd.read_parquet(os.path.join(R, "purchases.parquet"))
    O = pd.read_parquet(os.path.join(R, "owners.parquet"))
    S = pd.read_parquet(os.path.join(R, "sales.parquet")) if os.path.exists(os.path.join(R, "sales.parquet")) else pd.DataFrame(columns=["issuer_cik", "filing_date"])
    A = pd.read_parquet(os.path.join(R, "amendments.parquet")) if os.path.exists(os.path.join(R, "amendments.parquet")) else pd.DataFrame(columns=["issuer_cik", "owner_cik", "trans_date"])
    with open(os.path.join(R, "prices.pkl"), "rb") as f:
        pk = pickle.load(f)
    prices = pk["prices"] if isinstance(pk, dict) and "prices" in pk else pk
    market = pk.get("market", {}) if isinstance(pk, dict) and "prices" in pk else {}
    shares = pd.read_parquet(os.path.join(R, "shares.parquet")) if os.path.exists(os.path.join(R, "shares.parquet")) else pd.DataFrame(columns=["cik", "period_end", "shares", "avail"])
    ct = pd.read_parquet(os.path.join(R, "cik_ticker.parquet")) if os.path.exists(os.path.join(R, "cik_ticker.parquet")) else pd.DataFrame(columns=["cik", "ticker"])
    meta = {"builder": BUILDER, "rows_in": int(len(P)), "owners_in": int(len(O)), "tickers_in_prices": int(len(prices)), "shares_rows": int(len(shares)), "cik_ticker_rows": int(len(ct))}
    print(f"買いの行 {len(P):,} / 届け出人 {len(O):,} / 株価の銘柄 {len(prices):,} / 発行株数 {len(shares):,} / 対応表 {len(ct):,}", flush=True)
    excl = Counter()

    # ---- 届け出人と役職。1 つの届け出に複数の人（連名）がいれば 1 人と数える: 一番上の役職の人を代表にする
    O = O.copy()
    O["role"] = [role_of(r, t) for r, t in zip(O["RPTOWNER_RELATIONSHIP"].astype(str), O["RPTOWNER_TITLE"].astype(str))]
    O["role_rank"] = O["role"].map({r: i for i, r in enumerate(ROLE_ORDER)})
    O = O.sort_values(["ACCESSION_NUMBER", "role_rank"])
    O1 = O.groupby("ACCESSION_NUMBER").agg(owner_cik=("owner_cik", "first"), role=("role", "first"), n_owners=("owner_cik", "nunique"),
                                            has_tenpct=("role", lambda s: bool((s == "TenPct").any()))).reset_index()
    P = P.merge(O1, on="ACCESSION_NUMBER", how="left")
    P["owner_cik"] = pd.to_numeric(P["owner_cik"], errors="coerce").fillna(-1).astype("int64")
    P["role"] = P["role"].fillna("Other")
    P["has_tenpct"] = P["has_tenpct"].fillna(False).astype(bool)
    P["issuer_cik"] = pd.to_numeric(P["issuer_cik"], errors="coerce").fillna(-1).astype("int64")
    P = P[P["issuer_cik"] > 0].copy()
    P["filing_date"] = pd.to_datetime(P["filing_date"]).dt.normalize(); P["trans_date"] = pd.to_datetime(P["trans_date"]).dt.normalize()
    n0 = len(P)
    P = P[(P["trans_date"] <= P["filing_date"] + pd.Timedelta(days=3)) & (P["trans_date"] >= P["filing_date"] - pd.Timedelta(days=730))]
    excl["行: 取引日が届け出日より未来・2 年以上前"] += n0 - len(P)
    # 二重: 同じ（会社・届け出人・取引日・株数・単価）は 1 件
    n0 = len(P)
    P = P.drop_duplicates(["issuer_cik", "owner_cik", "trans_date", "shares", "price"], keep="first")
    excl["行: 二重（会社・人・取引日・株数・単価が同じ）"] += n0 - len(P)
    P["value"] = P["shares"] * P["price"]
    P["symbol"] = P["symbol"].astype(str).map(norm_symbol)
    for c in ["offmarket", "plan", "ads", "spac"]:
        if c not in P.columns:
            P[c] = False
        P[c] = P[c].fillna(False).astype(bool)
    P["late"] = P["TRANS_TIMELINESS"].astype(str).str.strip().str.upper().eq("L") if "TRANS_TIMELINESS" in P.columns else False
    # 決まった買いの可能性（会社の制度）: 同じ会社・同じ取引日・同じ単価で 3 人以上
    key = P["issuer_cik"].astype(str) + "|" + P["trans_date"].dt.strftime("%Y%m%d") + "|" + P["price"].round(4).astype(str)
    cnt = P.assign(k=key).groupby("k")["owner_cik"].nunique()
    P["plan_like"] = key.map(cnt).fillna(0).astype(int) >= 3

    # ---- 人 × 会社 × 届け出日
    P["role_rank"] = P["role"].map({r: i for i, r in enumerate(ROLE_ORDER)})
    g = P.groupby(["issuer_cik", "owner_cik", "filing_date"], sort=True)
    PF = g.agg(shares=("shares", "sum"), value=("value", "sum"), owned_after=("owned_after", "max"), ob_known=("owned_after", lambda s: bool(s.notna().all())),
               role_rank=("role_rank", "min"), last_trans=("trans_date", "max"), first_trans=("trans_date", "min"), all_direct=("direct", "all"),
               symbol=("symbol", first_nonempty), issuer_name=("ISSUERNAME", "first"), has_tenpct=("has_tenpct", "max"), late=("late", "any"),
               offmarket=("offmarket", "any"), plan=("plan", "any"), ads=("ads", "any"), spac=("spac", "any"), plan_like=("plan_like", "any"), max_row_value=("value", "max")).reset_index()
    PF["price_f4"] = PF["value"] / PF["shares"]
    PF["owned_before"] = (PF["owned_after"] - PF["shares"]).clip(lower=0)
    PF["role"] = PF["role_rank"].map({i: r for i, r in enumerate(ROLE_ORDER)})
    PF["filing_delay"] = (PF["filing_date"] - PF["last_trans"]).dt.days.clip(lower=0)
    meta["person_filings"] = int(len(PF))
    print(f"人 × 会社 × 届け出日: {len(PF):,}", flush=True)

    # ---- その人の履歴（先読みなし: 届け出日より前の自分の買いだけを見る）
    PF = PF.sort_values(["owner_cik", "issuer_cik", "filing_date"]).reset_index(drop=True)
    first_time = np.ones(len(PF), dtype=bool); routine = np.full(len(PF), "unknown", dtype=object); persist = np.zeros(len(PF), dtype=bool)
    fd_all = PF["filing_date"].values; lt_all = PF["last_trans"].values; ft_all = PF["first_trans"].values
    for _, idx in PF.groupby(["owner_cik", "issuer_cik"], sort=False).indices.items():
        idx = np.asarray(idx)
        fd = fd_all[idx]; lt = lt_all[idx]; ft = ft_all[idx]
        lt_i = pd.DatetimeIndex(lt)
        for k in range(len(idx)):
            d = fd[k]
            if k > 0 and (d - fd[:k].max()) <= np.timedelta64(365, "D"):
                first_time[idx[k]] = False
            # 日課（買いだけで判定。本家 Cohen・Malloy・Pomorski は売りも含む）: 過去 3 年とも同じ月に買っている
            if k > 0 and (lt_i[k] - lt_i[:k].min()) >= pd.Timedelta(days=365 * 3 - 30):
                m = lt_i[k].month; y0 = lt_i[k].year; prev = lt_i[:k]
                hit = all(((prev.year == y0 - j) & (prev.month == m)).any() for j in (1, 2, 3))
                routine[idx[k]] = "routine" if hit else "opportunistic"
            # 連続性: この届け出を含む直近 14 日（届け出日）に、取引日が 3 日以上
            lo = d - np.timedelta64(13, "D")
            days = set()
            for kk in range(k + 1):
                if fd[kk] >= lo:
                    for x in (lt[kk], ft[kk]):
                        if x >= lo:
                            days.add(pd.Timestamp(x).date())
            persist[idx[k]] = len(days) >= 3
    PF["first_time"] = first_time; PF["routine"] = routine; PF["persist"] = persist

    # ---- 外す人 × 届け出（理由ごとに 1 回だけ数える。順番: SPAC → 預託証券 → 市場外の疑い → 報酬・制度 → 決まった買いの可能性）
    reason = np.full(len(PF), "", dtype=object)
    for col, nm in [("spac", "SPAC"), ("ads", "預託証券（ADS/ADR）"), ("offmarket", "市場の外の買いの疑い（脚注の言葉）"), ("plan", "報酬や会社の制度（脚注の言葉）"), ("plan_like", "決まった買いの可能性（同じ日・同じ単価で 3 人以上）")]:
        m = (reason == "") & PF[col].values.astype(bool)
        reason[m] = nm
    PF["excl_reason"] = reason
    for nm, k in Counter(reason).items():
        if nm:
            excl[f"人×届け出: {nm}"] += int(k)

    # ---- 銘柄の対応と単価の確かめ（CIK → 今の銘柄、無ければ届け出の銘柄。単価 ÷ 株価 が同じ銘柄・同じ年の中央値の ±15% なら OK）
    cik2t = {}
    if len(ct):
        for c, t in zip(ct["cik"].astype(int), ct["ticker"].astype(str)):
            t = norm_symbol(t)
            if t and c not in cik2t:
                cik2t[c] = t
    def adj_close_at(tk, dint):
        rec = prices.get(tk)
        if rec is None:
            return np.nan
        k = int(np.searchsorted(rec["d"], dint, side="right")) - 1
        if k < 0:
            return np.nan
        if (pd.Timestamp(str(int(dint))) - pd.Timestamp(str(int(rec["d"][k])))).days > STALE_DAYS:
            return np.nan
        return float(rec["c"][k])
    PF["cand1"] = PF["issuer_cik"].map(cik2t).fillna("")
    PF["cand2"] = PF["symbol"].where(PF["symbol"] != PF["cand1"], "")
    lt_int = np.array([date_int(x) for x in pd.DatetimeIndex(PF["last_trans"])], dtype=np.int64)
    PF["lt_year"] = pd.DatetimeIndex(PF["last_trans"]).year
    for cnum in (1, 2):
        col = f"cand{cnum}"
        ratios = np.full(len(PF), np.nan)
        for i, (tk, d, pf4) in enumerate(zip(PF[col].values, lt_int, PF["price_f4"].values)):
            if tk and tk in prices:
                c = adj_close_at(tk, int(d))
                if c and c > 0:
                    ratios[i] = pf4 / c
        PF[f"ratio{cnum}"] = ratios
        med = PF[PF[f"ratio{cnum}"].notna()].groupby([col, "lt_year"])[f"ratio{cnum}"].median()
        PF[f"med{cnum}"] = [med.get((tk, y), np.nan) if tk else np.nan for tk, y in zip(PF[col].values, PF["lt_year"].values)]
    tickers, factors = [], []
    for c1, c2, r1, r2, m1, m2 in zip(PF["cand1"].values, PF["cand2"].values, PF["ratio1"].values, PF["ratio2"].values, PF["med1"].values, PF["med2"].values):
        pick, fac = "", np.nan
        for tk, r, m in ((c1, r1, m1), (c2, r2, m2)):
            if not tk or tk not in prices or np.isnan(r) or np.isnan(m):
                continue
            if plausible_factor(m) and (1 / PRICE_TOL) <= r / m <= PRICE_TOL:
                pick, fac = tk, m; break
        tickers.append(pick); factors.append(fac)
    PF["ticker"] = tickers; PF["split_factor"] = factors
    has_ratio = PF["ratio1"].notna().values | PF["ratio2"].notna().values
    m = (PF["excl_reason"] == "").values & (PF["ticker"] == "").values & has_ratio
    PF.loc[m, "excl_reason"] = "単価と株価が合わない（銘柄の引き違い・桁誤り・市場外の疑い）"
    excl["人×届け出: 単価と株価が合わない（銘柄の引き違い・桁誤り・市場外の疑い）"] += int(m.sum())
    meta["pf_no_price_data"] = int(((PF["excl_reason"] == "").values & (PF["ticker"] == "").values & ~has_ratio).sum())
    PF_clean = PF[PF["excl_reason"] == ""].copy()
    print(f"外した人×届け出: {dict(excl)}。残り {len(PF_clean):,}（株価が無い物 {meta['pf_no_price_data']:,} を含む）", flush=True)

    # ---- 会社 × 届け出日（イベント）
    PF_clean = PF_clean.sort_values(["issuer_cik", "filing_date", "value"], ascending=[True, True, False]).reset_index(drop=True)
    g = PF_clean.groupby(["issuer_cik", "filing_date"], sort=True)
    def routine_of(s):
        if (s == "opportunistic").any():
            return "opportunistic"
        if (s == "routine").any():
            return "routine"
        return "unknown"
    def mode_ticker(s):
        s = s[s != ""]
        return s.mode().iloc[0] if len(s) else ""
    E = g.agg(value=("value", "sum"), shares=("shares", "sum"), n_persons=("owner_cik", "nunique"), role_rank=("role_rank", "min"), has_tenpct=("has_tenpct", "max"),
              all_direct=("all_direct", "all"), owned_before_top=("owned_before", "first"), ob_known_top=("ob_known", "first"), shares_top=("shares", "first"),
              filing_delay=("filing_delay", "min"), last_trans=("last_trans", "max"), all_first_time=("first_time", "all"), any_routine=("routine", routine_of),
              any_persist=("persist", "any"), late_any=("late", "any"), max_row_value=("max_row_value", "max"),
              ticker=("ticker", mode_ticker), symbol=("symbol", first_nonempty), issuer_name=("issuer_name", "first"), split_factor=("split_factor", "median")).reset_index()
    E["price_f4"] = E["value"] / E["shares"]
    E["role"] = E["role_rank"].map({i: r for i, r in enumerate(ROLE_ORDER)})
    hi_ = np.where(E["owned_before_top"] > 0, E["shares_top"] / E["owned_before_top"].replace(0, np.nan), np.inf)
    E["holding_increase"] = np.where(E["ob_known_top"].astype(bool), hi_, np.nan)
    E["cluster14"] = 0; E["cluster30"] = 0; E["cluster14_insiders_only"] = 0; E["cluster30_insiders_only"] = 0
    ins_rank = ROLE_ORDER.index("Director")
    pf_by = {c: gg for c, gg in PF_clean.groupby("issuer_cik", sort=False)}
    for cik, idx in E.groupby("issuer_cik", sort=False).indices.items():
        sub = pf_by[cik]
        fd = sub["filing_date"].values; oc = sub["owner_cik"].values; rr = sub["role_rank"].values
        ed = E["filing_date"].values[idx]
        c14 = np.zeros(len(idx), dtype=int); c30 = np.zeros(len(idx), dtype=int); c14i = np.zeros(len(idx), dtype=int); c30i = np.zeros(len(idx), dtype=int)
        for k, d in enumerate(ed):
            m14 = (fd <= d) & (fd >= d - np.timedelta64(13, "D")); m30 = (fd <= d) & (fd >= d - np.timedelta64(29, "D"))
            c14[k] = len(set(oc[m14])); c30[k] = len(set(oc[m30]))
            c14i[k] = len(set(oc[m14 & (rr <= ins_rank)])); c30i[k] = len(set(oc[m30 & (rr <= ins_rank)]))
        E.loc[idx, "cluster14"] = c14; E.loc[idx, "cluster30"] = c30; E.loc[idx, "cluster14_insiders_only"] = c14i; E.loc[idx, "cluster30_insiders_only"] = c30i
    # 軸 18: 同じ会社で直近 30 日（届け出日。D−30 < 日 ≤ D）に売り（S）の届け出があったか
    E["sells30"] = False
    if len(S):
        S = S.copy(); S["issuer_cik"] = pd.to_numeric(S["issuer_cik"], errors="coerce").fillna(-1).astype("int64"); S["filing_date"] = pd.to_datetime(S["filing_date"])
        s_by = {c: np.sort(gg["filing_date"].values.astype("datetime64[D]")) for c, gg in S.groupby("issuer_cik")}
        flags = np.zeros(len(E), dtype=bool)
        for i, (cik, d) in enumerate(zip(E["issuer_cik"].values, E["filing_date"].values.astype("datetime64[D]"))):
            arr = s_by.get(cik)
            if arr is None:
                continue
            lo = d - np.timedelta64(30, "D")
            j = np.searchsorted(arr, lo, side="right")
            flags[i] = j < len(arr) and arr[j] <= d
        E["sells30"] = flags
    # 4/A で後から直された元の届け出の数（会社・人・取引日が一致）
    if len(A):
        A2 = A.copy(); A2["issuer_cik"] = pd.to_numeric(A2["issuer_cik"], errors="coerce"); A2["owner_cik"] = pd.to_numeric(A2["owner_cik"], errors="coerce"); A2["trans_date"] = pd.to_datetime(A2["trans_date"])
        akeys = set(zip(A2["issuer_cik"].fillna(-1).astype(int), A2["owner_cik"].fillna(-1).astype(int), A2["trans_date"].dt.strftime("%Y%m%d")))
        pf_keys = list(zip(PF_clean["issuer_cik"], PF_clean["owner_cik"], PF_clean["last_trans"].dt.strftime("%Y%m%d")))
        n_amended = sum(1 for k in pf_keys if k in akeys)
        meta["amended_share_of_person_filings"] = n_amended / max(len(pf_keys), 1); meta["amended_person_filings"] = int(n_amended)
    print(f"イベント（会社 × 届け出日）: {len(E):,}", flush=True)

    # ---- 市場全体の買いの多さ（直近 21 日に買いがあった会社数 ÷ 過去 1 年の中央値。D の前日までの値）
    daily = E.groupby("filing_date")["issuer_cik"].nunique().sort_index()
    cal = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(cal, fill_value=0)
    c21 = daily.rolling(21, min_periods=1).sum()
    med = c21.shift(1).rolling(365, min_periods=180).median()
    breadth = (c21.shift(1) / med).rename("breadth")
    E = E.merge(breadth.reset_index().rename(columns={"index": "filing_date"}), on="filing_date", how="left")

    # ---- 発行株数（使える日 avail が D 以前の最新。期末が D の 400 日以上前なら使わない）→ 時価総額 = 発行株数 × Form 4 の単価
    sh = shares.copy()
    if len(sh):
        sh["cik"] = pd.to_numeric(sh["cik"], errors="coerce").fillna(-1).astype(int)
        if "avail" not in sh.columns:
            sh["avail"] = pd.to_datetime(sh["period_end"]) + pd.Timedelta(days=45)
        sh["avail_i"] = pd.to_datetime(sh["avail"]).map(date_int).astype(int); sh["pe_i"] = pd.to_datetime(sh["period_end"]).map(date_int).astype(int)
        sh = sh.sort_values(["cik", "avail_i"])
    sh_by = {c: (gg["avail_i"].to_numpy(), gg["pe_i"].to_numpy(), gg["shares"].to_numpy(dtype=float)) for c, gg in sh.groupby("cik")} if len(sh) else {}
    def shares_at(cik, dint):
        rec = sh_by.get(int(cik))
        if rec is None:
            return np.nan
        av, pe, s = rec
        k = int(np.searchsorted(av, dint, side="right")) - 1
        if k < 0:
            return np.nan
        if (pd.Timestamp(str(int(dint))) - pd.Timestamp(str(int(pe[k])))).days > 400:
            return np.nan
        return float(s[k])
    fdi_all = np.array([date_int(x) for x in pd.DatetimeIndex(E["filing_date"])], dtype=np.int64)
    E["shares_out"] = [shares_at(c, d) for c, d in zip(E["issuer_cik"].values, fdi_all)]
    E["mcap"] = E["shares_out"] * E["price_f4"]

    # ---- 株価: ものさし（SPY の営業日）、基準（売買代金の 5 分位の等金額平均）、イベントのリターン
    spy = prices.get("SPY")
    if spy is None:
        print("SPY が無い", file=sys.stderr); sys.exit(3)
    grid = spy["d"].astype(np.int64); G = len(grid)
    gi = {int(d): i for i, d in enumerate(grid)}
    spy_c = spy["c"].astype(np.float64)
    def aligned_returns(rec):
        pos = np.array([gi.get(int(d), -1) for d in rec["d"]])
        ok = pos >= 0
        c = rec["c"].astype(np.float64)[ok]; pos = pos[ok]
        r = np.full(G, np.nan, dtype=np.float32)
        if len(c) > 1:
            r[pos[1:]] = (c[1:] / c[:-1] - 1.0).astype(np.float32)
        return r
    # 基準の母集団: 株のフォルダ（stocks）にある銘柄。フォルダ情報が無ければ全部（ETF の名が付くフォルダは外す）
    folders = set(market.values())
    if any("stocks" in f for f in folders):
        universe = [t for t in prices if "stocks" in market.get(t, "") and t != "SPY"]
    else:
        universe = [t for t in prices if "etf" not in market.get(t, "") and t != "SPY"]
    event_tickers = set(E["ticker"].values) - {""}
    need_ar = set(universe) | event_tickers
    print(f"基準の母集団: {len(universe):,} 銘柄、日次リターンを並べる銘柄: {len(need_ar):,}", flush=True)
    AR = {t: aligned_returns(prices[t]) for t in need_ar if t in prices}
    def aligned_prices(rec):
        """ものさしの日ごとの終値。株が休んだ日は前の値で埋める（最初の日より前・最後の日より後は NaN）"""
        pos = np.array([gi.get(int(d), -1) for d in rec["d"]])
        ok = pos >= 0
        c = rec["c"].astype(np.float64)[ok]; pos = pos[ok]
        out = np.full(G, np.nan, dtype=np.float32)
        if len(c) == 0:
            return out
        out[pos] = c
        lo, hi = int(pos[0]), int(pos[-1])
        seg = out[lo:hi + 1]
        idx = np.where(~np.isnan(seg), np.arange(len(seg)), 0)
        np.maximum.accumulate(idx, out=idx)
        out[lo:hi + 1] = seg[idx]
        return out
    CP = {t: aligned_prices(prices[t]) for t in universe if t in prices}
    years = sorted(set(int(d // 10000) for d in grid))
    def adv_at(rec, gidx_end):
        """ものさしの位置 gidx_end までの 63 営業日の平均売買代金（株価 × 出来高）"""
        dend = int(grid[gidx_end]); dstart = int(grid[max(0, gidx_end - 62)])
        k1 = int(np.searchsorted(rec["d"], dend, side="right")); k0 = int(np.searchsorted(rec["d"], dstart, side="left"))
        if k1 - k0 < 20:
            return np.nan
        c = rec["c"][k0:k1].astype(np.float64); v = rec["v"][k0:k1].astype(np.float64)
        return float(np.nanmean(c * v))
    cuts_by_year = {}; bench_daily = np.zeros((6, G), dtype=np.float64)
    # 買い持ちの基準: 階級 q の銘柄を t に買って t+H に売ったときの平均リターン（Barber・Lyon 1997 の買い持ちの対照）。日次の等金額指数を複利にすると、ばらつきの大きい株の買い持ちより低く出る（分散の半分の分）ので、対照も買い持ちにする
    bh = {q: {h: np.full(G, np.nan) for h in HORIZONS} for q in range(6)}
    bench_clipped = {q: {h: [] for h in HORIZONS} for q in range(6)}
    for y in years:
        ymask = (grid >= y * 10000 + 101) & (grid <= y * 10000 + 1231)
        prev_idx = np.where(grid < y * 10000 + 101)[0]
        members = {q: [] for q in range(6)}
        if len(prev_idx) >= 63:
            gidx = int(prev_idx[-1])
            advs = {}
            for t in universe:
                v = adv_at(prices[t], gidx)
                if v and not np.isnan(v) and v > 0:
                    advs[t] = v
            if len(advs) >= 100:
                vals = np.array(list(advs.values())); cuts = np.quantile(vals, [0.2, 0.4, 0.6, 0.8]); cuts_by_year[y] = cuts
                for t, v in advs.items():
                    members[int(np.searchsorted(cuts, v))].append(t)
        members[5] = universe
        tpos = np.where(ymask)[0]
        for q in range(6):
            mem = members[q] if len(members[q]) >= 50 else universe
            mem = [t for t in mem if t in AR]
            if not mem:
                continue
            M = np.vstack([AR[t][ymask] for t in mem])
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                ew = np.nanmean(M.astype(np.float64), axis=0)
            bench_daily[q, ymask] = np.nan_to_num(ew, nan=0.0)
            Mp = np.vstack([CP[t] for t in mem if t in CP]).astype(np.float64)
            for h in HORIZONS:
                tt = tpos[tpos + h < G]
                if len(tt) == 0:
                    continue
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    start = Mp[:, tt]
                    ratio = Mp[:, tt + h] / start
                    # 10 セント未満の株は数えない（値が粗く、×100 の動きが平均を壊す）。1 銘柄のリターンは +300% で頭を切る（winsorize）
                    ratio = np.where(start < 0.10, np.nan, ratio)
                    clipped = np.nanmean((ratio > 4.0).astype(np.float64)) if np.isfinite(ratio).any() else 0.0
                    bench_clipped[q][h].append(float(clipped))
                    ratio = np.minimum(ratio, 4.0)
                    bh[q][h][tt] = np.nanmean(ratio, axis=0) - 1.0
    del CP
    bench = np.cumprod(1.0 + bench_daily, axis=1)
    spy_daily = np.concatenate([[0.0], spy_c[1:] / spy_c[:-1] - 1.0])
    meta["benchmark_years_with_quintiles"] = sorted(cuts_by_year.keys())
    meta["benchmark_share_clipped_at_300pct"] = {f"q{q}_H{h}": (float(np.mean(v)) if v else None) for q in range(6) for h, v in bench_clipped[q].items()}

    # ---- イベントごと: 入る日（D の次のものさしの営業日。株に値が無ければ 3 日まで待つ）、D までの特徴、H 日後のリターン
    n = len(E)
    cols = {k: np.full(n, np.nan) for k in ["pm1", "p0", "prev63", "dd252", "adv63", "ret_last", "move_d1", "p0b", "bench_move", f"ret2_{H_MAIN}", f"bench2_{H_MAIN}"]
            + [f"ret_{h}" for h in HORIZONS] + [f"spy_{h}" for h in HORIZONS] + [f"bench_{h}" for h in HORIZONS]}
    g0_arr = np.full(n, -1, dtype=np.int64); t0_arr = np.zeros(n, dtype=np.int64); last_arr = np.zeros(n, dtype=np.int64); q_arr = np.full(n, 5, dtype=np.int64)
    daily_mat = np.full((n, H_MAIN), np.nan, dtype=np.float32)
    last_grid = int(grid[-1])
    def price_at_or_before(rec, dint, i_min):
        k = int(np.searchsorted(rec["d"], dint, side="right")) - 1
        if k < i_min or k < 0:
            return np.nan
        if (pd.Timestamp(str(int(dint))) - pd.Timestamp(str(int(rec["d"][k])))).days > STALE_DAYS:
            return np.nan
        return float(rec["c"][k])
    for i, (tk, fdi) in enumerate(zip(E["ticker"].values, fdi_all)):
        if not tk or tk not in prices:
            continue
        rec = prices[tk]; d = rec["d"]; c = rec["c"].astype(np.float64); v = rec["v"].astype(np.float64)
        last_arr[i] = int(d[-1])
        gD = int(np.searchsorted(grid, fdi, side="right"))  # D の次のものさしの営業日
        if gD >= G:
            continue
        kD = int(np.searchsorted(d, fdi, side="right")) - 1  # D 以前の最後の値
        if kD < 0:
            continue
        pm1 = c[kD]
        g0 = -1; i0 = -1
        for w in range(ENTRY_WAIT_DAYS):
            if gD + w >= G:
                break
            k = int(np.searchsorted(d, int(grid[gD + w])))
            if k < len(d) and int(d[k]) == int(grid[gD + w]):
                g0 = gD + w; i0 = k; break
        if g0 < 0:
            continue
        p0 = c[i0]
        g0_arr[i] = g0; t0_arr[i] = int(grid[g0])
        cols["pm1"][i] = pm1; cols["p0"][i] = p0; cols["move_d1"][i] = p0 / pm1 - 1.0
        cols["prev63"][i] = pm1 / c[kD - 63] - 1.0 if kD - 63 >= 0 else np.nan
        hi = c[max(0, kD - 251):kD + 1].max()
        cols["dd252"][i] = pm1 / hi - 1.0 if hi > 0 else np.nan
        w0 = max(0, kD - 62)
        cols["adv63"][i] = float(np.nanmean(c[w0:kD + 1] * v[w0:kD + 1])) if kD - w0 >= 19 else np.nan
        y0 = int(grid[g0] // 10000); cuts = cuts_by_year.get(y0)
        q = int(np.searchsorted(cuts, cols["adv63"][i])) if (cuts is not None and not np.isnan(cols["adv63"][i])) else 5
        q_arr[i] = q
        cols["bench_move"][i] = bench[q][g0] / bench[q][g0 - 1] - 1.0 if g0 >= 1 else np.nan
        cols["ret_last"][i] = c[-1] / p0 - 1.0
        for h in HORIZONS:
            gh = g0 + h
            if gh >= G:
                continue
            ph = price_at_or_before(rec, int(grid[gh]), i0 + 1)
            if not np.isnan(ph):
                cols[f"ret_{h}"][i] = ph / p0 - 1.0
            cols[f"spy_{h}"][i] = spy_c[gh] / spy_c[g0] - 1.0
            cols[f"bench_{h}"][i] = bh[q][h][g0]
        # 参考: D+2 の終値で入る（g0+1）
        if g0 + 1 < G:
            k1 = int(np.searchsorted(d, int(grid[g0 + 1])))
            if k1 < len(d) and int(d[k1]) == int(grid[g0 + 1]):
                p0b = c[k1]; cols["p0b"][i] = p0b
                gh = g0 + 1 + H_MAIN
                if gh < G:
                    ph = price_at_or_before(rec, int(grid[gh]), k1 + 1)
                    if not np.isnan(ph):
                        cols[f"ret2_{H_MAIN}"][i] = ph / p0b - 1.0
                    cols[f"bench2_{H_MAIN}"][i] = bh[q][H_MAIN][g0 + 1]
        ar = AR.get(tk)
        if ar is not None:
            s0 = g0 + 1; s1 = min(G, g0 + 1 + H_MAIN)
            if s1 > s0:
                daily_mat[i, : s1 - s0] = ar[s0:s1]
        if (i + 1) % 50000 == 0:
            print(f"  イベント {i+1:,}/{n:,}", flush=True)
    for k, vals in cols.items():
        E[k] = vals
    E["g0"] = g0_arr; E["t0_date"] = t0_arr; E["last_date_in_data"] = last_arr; E["adv_q"] = q_arr
    last_ts = pd.Timestamp(str(last_grid))
    ld = ints_to_ts(np.where(E["last_date_in_data"].values > 0, E["last_date_in_data"].values, 19000101))
    E["data_ended"] = (E["t0_date"].values > 0) & ((last_ts - ld).dt.days > STALE_DAYS).fillna(False).values
    E["year"] = E["filing_date"].dt.year
    for h in HORIZONS:
        E[f"pending_{h}"] = (E["g0"] >= 0) & (E["g0"] + h >= G)

    # ---- 金額の確かめ（イベントの外し。理由ごとに数える）
    reason = np.full(n, "", dtype=object)
    m = (reason == "") & E["mcap"].notna().values & (E["value"].values > MCAP_SHARE_MAX * E["mcap"].values)
    reason[m] = "金額が時価総額の 5% 超"
    m = (reason == "") & (E["max_row_value"].values > ROW_VALUE_MAX); reason[m] = "1 行 10 億ドル超"
    m = (reason == "") & E["adv63"].notna().values & (E["value"].values > ADV_MULT_MAX * E["adv63"].values); reason[m] = "金額が売買代金の 50 倍超"
    E["excl_reason"] = reason
    for nm, k in Counter(reason).items():
        if nm:
            excl[f"イベント: {nm}"] += int(k)
    noprice = (E["excl_reason"] == "").values & (E["t0_date"].values == 0)
    E["noprice_reason"] = ""
    E.loc[noprice & (E["ticker"] == "").values, "noprice_reason"] = "銘柄が株価データに無い"
    E.loc[noprice & (E["ticker"] != "").values, "noprice_reason"] = "入る日の前後に値が無い"

    # ---- 保存
    E.to_parquet(a.out, index=False)
    np.savez(os.path.join(os.path.dirname(a.out), "event_daily.npz"), daily=daily_mat, grid=grid, bench_daily=bench_daily.astype(np.float32), spy_daily=spy_daily.astype(np.float32))
    K = E[E["excl_reason"] == ""]
    cov = K.groupby("year").agg(events=("issuer_cik", "size"), with_price=("t0_date", lambda s: int((s > 0).sum())),
                                with_126=("ret_126", lambda s: int(s.notna().sum())), with_252=("ret_252", lambda s: int(s.notna().sum())),
                                with_mcap=("mcap", lambda s: int(s.notna().sum())), data_ended=("data_ended", "sum"), pending_126=("pending_126", "sum")).reset_index()
    cov["with_price_pct"] = (cov["with_price"] / cov["events"] * 100).round(1); cov["with_126_pct"] = (cov["with_126"] / cov["events"] * 100).round(1)
    cov.to_csv(os.path.join(a.outdir, "coverage.csv"), index=False)
    ex = pd.DataFrame([{"理由": k, "数": v} for k, v in sorted(excl.items())])
    ex.to_csv(os.path.join(a.outdir, "exclusions.csv"), index=False)
    meta.update({"events": int(len(E)), "events_kept": int(len(K)), "events_with_price": int((K["t0_date"] > 0).sum()), "events_with_ret126": int(K["ret_126"].notna().sum()),
                 "exclusions": dict(excl), "grid_first": int(grid[0]), "grid_last": int(grid[-1]), "universe": len(universe), "noprice_reasons": dict(Counter(E.loc[noprice, "noprice_reason"]))})
    json.dump(meta, open(os.path.join(a.outdir, "build_meta.json"), "w"), ensure_ascii=False, indent=1, default=str)
    print(cov.to_string(index=False), flush=True)
    print(f"終了: イベント {len(E):,}（残した物 {meta['events_kept']:,}、株価あり {meta['events_with_price']:,}、126 日後まである {meta['events_with_ret126']:,}）", flush=True)


if __name__ == "__main__":
    main()
