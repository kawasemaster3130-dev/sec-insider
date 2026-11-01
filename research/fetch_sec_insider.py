#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEC「Insider Transactions Data Sets」（Form 3・4・5 の四半期 TSV）から、市場での買い（P）を抜き出して 1 つの表にする。
pandas だけ。鍵なし。EDGAR_UA（連絡先の分かる User-Agent）が要る。

使い方: EDGAR_UA="組織名 メール" python3 research/fetch_sec_insider.py --out research/raw --from 2006 --to 2026 [--keep-zips]
出す物（research/raw/）:
  purchases.parquet  1 行 = 1 取引行。Form 4 のみ（4/A・3・5 と、Form 4 の中の Form 5 扱いの行は除く）。P・取得・株数>0・単価>0。
                     脚注と備考の文（fn_text）、市場の外の買いの疑い（offmarket）、報酬や制度（plan）、預託証券（ads）、SPAC（spac）の印を付ける
  owners.parquet     届け出人の役職（ACCESSION_NUMBER × RPTOWNERCIK）
  sales.parquet      Form 4 の売り（S）の届け出（会社 × 届け出日。軸 18 に使う）
  amendments.parquet 4/A に載った買い（会社・届け出人・取引日。元の届け出が後で直された数を数える）
  purchases_meta.json 落とした行の数（単価なし・普通株でない・など）と期間
印の言葉の決まりは、アプリの build.py（OFFMARKET_RE・PLAN_RE・PLAN_NOT_RE・SPAC_RE・ADS_TITLE_RE）をそのまま使う。
"""
import argparse, io, json, os, re, sys, time, zipfile
import urllib.request, urllib.error
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from build import OFFMARKET_RE, PLAN_RE, PLAN_NOT_RE, SPAC_RE, ADS_TITLE_RE
except Exception as e:  # build.py が無い場所で動かすときの保険（同じ言葉を持つ）
    print(f"[warn] build.py の印の決まりが読めない（{e}）。簡易版を使う", file=sys.stderr)
    OFFMARKET_RE = re.compile(r"private placement|securities purchase agreement|subscription agreement|registered direct|\bPIPE\b|public offering|underwritten offering|direct offering|rights offering|at-the-market|in the offering|initial public offering|\bIPO\b|directed share|offering price|underwriter|concurrent placement|directly from the (?:company|issuer)|purchased from the (?:company|issuer)|issued by the (?:company|issuer)|irrevocable trust|exchange agreement|in exchange for|business combination|merger agreement|rebalancing transaction|between (?:the )?funds|not (?:bought|sold|purchased) (?:in|on) the open market|contribution agreement|privately[- ]negotiated|private (?:transactions?|sales?|purchases?)|\bin[- ]kind\b|pro rata distribution|in settlement of|other than (?:in|on|through) the open market|outside (?:of )?the open market", re.I)
    PLAN_RE = re.compile(r"cashless exercise|exercise of (?:vested |the )?(?:stock )?options?|compensation plan|deferred compensation|\b409A\b|employee (?:stock|share) purchase|\bESPP\b|401\(k\)|dividend reinvestment|\bDRIP\b|in lieu of|director(?:'s|s')? (?:fees|compensation)|\bretainer\b|shares were issued pursuant to|promissory note", re.I)
    PLAN_NOT_RE = re.compile(r"10b5-?1", re.I)
    SPAC_RE = re.compile(r"acquisition corp|acquisition co\b|acquisition company|blank check|\bspac\b", re.I)
    ADS_TITLE_RE = re.compile(r"american depositary|\bADSs?\b|\bADRs?\b|depositary", re.I)

UA = os.environ.get("EDGAR_UA", "").strip()
INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
PATTERN = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip"
COMMON_RE = re.compile(r"common|ordinary|class [a-c]\b|shares? of beneficial|\bstock\b|\bshares?\b", re.I)
EXCLUDE_RE = re.compile(r"preferred|warrant|\bunits?\b|\brights?\b|\bnotes?\b|debenture|option|convertible|restricted stock unit|\brsu\b|phantom|deferred|lp interest|partnership|trust unit|\bpsu\b|\bdsu\b", re.I)
FN_ID_RE = re.compile(r"F\d+", re.I)


def log(msg, fh=None):
    print(msg, flush=True)
    if fh:
        fh.write(msg + "\n"); fh.flush()


def get(url, retries=4, timeout=300):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return None


def find_zip_links():
    """索引のページから *_form345.zip のリンクを拾う。拾えなければ決まった形の URL を使う"""
    links = {}
    html = get(INDEX_URL)
    if html:
        for m in re.finditer(r'href="([^"]*?(\d{4})q([1-4])_form345\.zip)"', html.decode("utf-8", errors="replace"), re.I):
            u = m.group(1)
            if u.startswith("/"):
                u = "https://www.sec.gov" + u
            links[(int(m.group(2)), int(m.group(3)))] = u
    return links


def read_tsv(z, name_part):
    """zip の中の TSV を大文字小文字を問わず探して読む（文字コードは latin-1 で壊さない）"""
    for n in z.namelist():
        if os.path.basename(n).upper() == name_part.upper() + ".TSV":
            with z.open(n) as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            try:
                df = pd.read_csv(io.StringIO(text), sep="\t", dtype=str, keep_default_na=False, na_values=[], quoting=3, on_bad_lines="skip", engine="c", low_memory=False)
            except Exception:
                df = pd.read_csv(io.StringIO(text), sep="\t", dtype=str, keep_default_na=False, na_values=[], quoting=3, on_bad_lines="skip", engine="python")
            df.columns = [c.strip().upper() for c in df.columns]
            return df
    return None


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")


def parse_date(s):
    # 形は "15-MAR-2024" か "2024-03-15" か "03/15/2024"。月の文字は大文字でも小文字でも読む
    s = s.astype(str).str.strip()
    t = s.str.replace(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", lambda m: f"{m.group(1)}-{m.group(2).title()}-{m.group(3)}", regex=True)
    d = pd.to_datetime(t, format="%d-%b-%Y", errors="coerce")
    miss = d.isna() & s.ne("")
    if miss.any():
        d2 = pd.to_datetime(s[miss], errors="coerce", format="mixed")
        d = d.copy(); d[miss] = d2
    return d


def footnote_text(nd, fn_df):
    """各行の *_FN 列にある脚注番号（F1, F2 …）を FOOTNOTES の文に置き換えてつなぐ"""
    if fn_df is None or len(fn_df) == 0 or "FOOTNOTE_ID" not in fn_df.columns or "FOOTNOTE_TXT" not in fn_df.columns:
        return pd.Series([""] * len(nd), index=nd.index)
    k = fn_df["ACCESSION_NUMBER"].astype(str).str.strip() + "|" + fn_df["FOOTNOTE_ID"].astype(str).str.strip().str.upper()
    txt = dict(zip(k, fn_df["FOOTNOTE_TXT"].astype(str)))
    fn_cols = [c for c in nd.columns if c.endswith("_FN")]
    if not fn_cols:
        return pd.Series([""] * len(nd), index=nd.index)
    ids = nd[fn_cols].astype(str).agg(" ".join, axis=1)
    acc = nd["ACCESSION_NUMBER"].astype(str).str.strip()
    out = []
    for a_, s in zip(acc, ids):
        parts, seen = [], set()
        for m in FN_ID_RE.findall(s):
            kk = a_ + "|" + m.upper()
            if kk in seen:
                continue
            seen.add(kk)
            t = txt.get(kk)
            if t:
                parts.append(t)
        out.append(" | ".join(parts))
    return pd.Series(out, index=nd.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/raw")
    ap.add_argument("--from", dest="y0", type=int, default=2006)
    ap.add_argument("--to", dest="y1", type=int, default=2026)
    ap.add_argument("--keep-zips", action="store_true")
    a = ap.parse_args()
    if not UA or "@" not in UA:
        print("EDGAR_UA（組織名と連絡先メール）が要る", file=sys.stderr); sys.exit(1)
    os.makedirs(a.out, exist_ok=True)
    zdir = os.path.join(a.out, "zips"); os.makedirs(zdir, exist_ok=True)
    fh = open(os.path.join(a.out, "fetch_log.txt"), "a", encoding="utf-8")
    links = find_zip_links()
    log(f"索引のページから見つけたファイル: {len(links)} 個", fh)
    purchases, owners, sales, amends = [], [], [], []
    drops = {"単価なし・0": 0, "株数なし・0": 0, "単価10万ドル超": 0, "普通株でない証券名": 0, "Form 4 の中の Form 5 扱いの行": 0, "10b5-1 の印": 0, "日付が読めない": 0}
    counts = {"Form4 届け出": 0, "4/A 届け出": 0, "買いの行（Form 4）": 0, "買いの行（4/A）": 0, "売りの届け出（Form 4）": 0}
    quarters = [(y, q) for y in range(a.y0, a.y1 + 1) for q in (1, 2, 3, 4)]
    got_quarters = []
    headers_logged = False
    date_samples = set()
    for (y, q) in quarters:
        url = links.get((y, q)) or PATTERN.format(y=y, q=q)
        zpath = os.path.join(zdir, f"{y}q{q}_form345.zip")
        if not os.path.exists(zpath):
            data = get(url)
            if data is None:
                log(f"{y}Q{q}: 無い（まだ公開されていないか、URL が違う）{url}", fh)
                continue
            with open(zpath, "wb") as f:
                f.write(data)
            time.sleep(0.15)
        try:
            z = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            log(f"{y}Q{q}: zip が壊れている", fh); os.remove(zpath); continue
        sub = read_tsv(z, "SUBMISSION"); own = read_tsv(z, "REPORTINGOWNER"); nd = read_tsv(z, "NONDERIV_TRANS"); fn = read_tsv(z, "FOOTNOTES")
        if sub is None or own is None or nd is None:
            log(f"{y}Q{q}: 表が見つからない {z.namelist()[:10]}", fh); continue
        if not headers_logged:
            log("SUBMISSION の列: " + ",".join(sub.columns), fh)
            log("REPORTINGOWNER の列: " + ",".join(own.columns), fh)
            log("NONDERIV_TRANS の列: " + ",".join(nd.columns), fh)
            log("FOOTNOTES の列: " + (",".join(fn.columns) if fn is not None else "（無い）"), fh)
            headers_logged = True
        for v in sub["FILING_DATE"].astype(str).head(3).tolist():
            date_samples.add(v)
        doc = sub["DOCUMENT_TYPE"].str.strip().str.upper() if "DOCUMENT_TYPE" in sub.columns else pd.Series(["4"] * len(sub), index=sub.index)
        sub4 = sub[doc == "4"].copy(); sub4a = sub[doc == "4/A"].copy()
        counts["Form4 届け出"] += len(sub4); counts["4/A 届け出"] += len(sub4a)
        # 10b5-1 の印（2023 年以降だけ列がある）
        if "AFF10B5ONE" in sub4.columns:
            m = sub4["AFF10B5ONE"].astype(str).str.strip().str.lower().isin(["1", "true", "y", "yes"])
            drops["10b5-1 の印"] += int(m.sum()); sub4 = sub4[~m]
        keep_cols = [c for c in ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL", "REMARKS"] if c in sub.columns]
        sub4 = sub4[keep_cols].copy(); sub4a = sub4a[keep_cols].copy()
        need = ["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "TRANS_DATE", "SECURITY_TITLE", "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP"]
        for c in need:
            if c not in nd.columns:
                nd[c] = ""
        code = nd["TRANS_CODE"].str.strip().str.upper()
        # 売り（S）の届け出（Form 4 だけ。会社 × 届け出日）
        s_rows = nd[code == "S"][["ACCESSION_NUMBER"]].drop_duplicates().merge(sub4[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK"]], on="ACCESSION_NUMBER", how="inner")
        if len(s_rows):
            s_rows = s_rows.assign(filing_date=parse_date(s_rows["FILING_DATE"]), issuer_cik=to_num(s_rows["ISSUERCIK"]).astype("Int64"))
            sales.append(s_rows[["ACCESSION_NUMBER", "issuer_cik", "filing_date"]])
            counts["売りの届け出（Form 4）"] += int(len(s_rows))
        # 買いの行
        p = nd[code == "P"].copy()
        p = p[p["TRANS_ACQUIRED_DISP_CD"].str.strip().str.upper().isin(["A", ""])]
        if "TRANS_FORM_TYPE" in p.columns:
            m5 = p["TRANS_FORM_TYPE"].astype(str).str.strip() == "5"
            drops["Form 4 の中の Form 5 扱いの行"] += int(m5.sum()); p = p[~m5]
        p["shares"] = to_num(p["TRANS_SHARES"]); p["price"] = to_num(p["TRANS_PRICEPERSHARE"]); p["owned_after"] = to_num(p["SHRS_OWND_FOLWNG_TRANS"])
        if len(p):
            drops["株数なし・0"] += int((~(p["shares"] > 0)).sum())
        p = p[p["shares"] > 0]
        if len(p):
            drops["単価なし・0"] += int((~(p["price"] > 0)).sum())
        p = p[p["price"] > 0]
        drops["単価10万ドル超"] += int((p["price"] >= 100000).sum()); p = p[p["price"] < 100000]
        title = p["SECURITY_TITLE"].astype(str)
        okt = title.str.contains(COMMON_RE) & ~title.str.contains(EXCLUDE_RE)
        drops["普通株でない証券名"] += int((~okt).sum()); p = p[okt]
        # 4/A に載った買い（元の届け出が直された数を数えるため。本体には入れない）
        pa = p.merge(sub4a[["ACCESSION_NUMBER", "ISSUERCIK"]], on="ACCESSION_NUMBER", how="inner")
        if len(pa):
            oa = own[own["ACCESSION_NUMBER"].isin(set(pa["ACCESSION_NUMBER"]))][["ACCESSION_NUMBER", "RPTOWNERCIK"]].drop_duplicates("ACCESSION_NUMBER")
            pa = pa.merge(oa, on="ACCESSION_NUMBER", how="left")
            pa = pa.assign(issuer_cik=to_num(pa["ISSUERCIK"]).astype("Int64"), owner_cik=to_num(pa["RPTOWNERCIK"]).astype("Int64"), trans_date=parse_date(pa["TRANS_DATE"]))
            amends.append(pa[["ACCESSION_NUMBER", "issuer_cik", "owner_cik", "trans_date", "shares", "price"]])
            counts["買いの行（4/A）"] += len(pa)
        p = p.merge(sub4, on="ACCESSION_NUMBER", how="inner")
        p["fn_text"] = footnote_text(p, fn)
        p["remarks"] = p["REMARKS"].astype(str) if "REMARKS" in p.columns else ""
        allt = p["fn_text"].astype(str) + " | " + p["remarks"].astype(str)
        p["offmarket"] = allt.str.contains(OFFMARKET_RE)
        p["plan"] = allt.str.contains(PLAN_RE) & ~allt.str.contains(PLAN_NOT_RE)
        p["ads"] = p["SECURITY_TITLE"].astype(str).str.contains(ADS_TITLE_RE)
        p["spac"] = p["ISSUERNAME"].astype(str).str.contains(SPAC_RE)
        p["trans_date"] = parse_date(p["TRANS_DATE"]); p["filing_date"] = parse_date(p["FILING_DATE"])
        bad = p["filing_date"].isna() | p["trans_date"].isna()
        drops["日付が読めない"] += int(bad.sum()); p = p[~bad]
        p["issuer_cik"] = to_num(p["ISSUERCIK"]).astype("Int64")
        p["symbol"] = p["ISSUERTRADINGSYMBOL"].astype(str).str.strip().str.upper()
        p["direct"] = p["DIRECT_INDIRECT_OWNERSHIP"].astype(str).str.strip().str.upper().str.startswith("D")
        p["quarter"] = f"{y}Q{q}"
        for c in ["TRANS_TIMELINESS", "NATURE_OF_OWNERSHIP"]:
            if c not in p.columns:
                p[c] = ""
        purchases.append(p[["ACCESSION_NUMBER", "issuer_cik", "ISSUERNAME", "symbol", "filing_date", "trans_date", "shares", "price", "owned_after", "direct", "SECURITY_TITLE", "quarter",
                            "TRANS_TIMELINESS", "NATURE_OF_OWNERSHIP", "fn_text", "remarks", "offmarket", "plan", "ads", "spac"]])
        counts["買いの行（Form 4）"] += len(p)
        got_quarters.append(f"{y}Q{q}")
        own_q = own[own["ACCESSION_NUMBER"].isin(set(p["ACCESSION_NUMBER"]))].copy()
        for c in ["RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]:
            if c not in own_q.columns:
                own_q[c] = ""
        own_q = own_q[["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]].copy()
        own_q["owner_cik"] = to_num(own_q["RPTOWNERCIK"]).astype("Int64")
        owners.append(own_q)
        log(f"{y}Q{q}: Form 4 {len(sub4)} 件・4/A {len(sub4a)} 件、買いの行 {len(p)}（市場外の疑い {int(p['offmarket'].sum())}・制度 {int(p['plan'].sum())}・ADS {int(p['ads'].sum())}・SPAC {int(p['spac'].sum())}）、届け出人 {len(own_q)}", fh)
        z.close()
        if not a.keep_zips:
            os.remove(zpath)
    if not purchases:
        log("買いの行が 1 つも取れなかった", fh); sys.exit(3)
    P = pd.concat(purchases, ignore_index=True); O = pd.concat(owners, ignore_index=True)
    P.to_parquet(os.path.join(a.out, "purchases.parquet"), index=False)
    O.to_parquet(os.path.join(a.out, "owners.parquet"), index=False)
    S = pd.concat(sales, ignore_index=True) if sales else pd.DataFrame(columns=["ACCESSION_NUMBER", "issuer_cik", "filing_date"])
    S.to_parquet(os.path.join(a.out, "sales.parquet"), index=False)
    A = pd.concat(amends, ignore_index=True) if amends else pd.DataFrame(columns=["ACCESSION_NUMBER", "issuer_cik", "owner_cik", "trans_date", "shares", "price"])
    A.to_parquet(os.path.join(a.out, "amendments.parquet"), index=False)
    log(f"終了: 買いの行 {len(P):,} / 会社 {P['issuer_cik'].nunique():,} / 期間 {P['filing_date'].min().date()}〜{P['filing_date'].max().date()} / 落とした行 {drops} / 数 {counts}", fh)
    json.dump({"rows": int(len(P)), "owners": int(len(O)), "sales_filings": int(len(S)), "amend_rows": int(len(A)), "first": str(P['filing_date'].min().date()), "last": str(P['filing_date'].max().date()),
               "drops": drops, "counts": counts, "date_samples": sorted(date_samples)[:6], "quarters_got": got_quarters,
               "quarters_missing": [f"{y}Q{q}" for (y, q) in quarters if f"{y}Q{q}" not in got_quarters]},
              open(os.path.join(a.out, "purchases_meta.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
