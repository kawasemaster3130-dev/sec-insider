#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
米国株の日足を取る。3 つの使い方（GitHub Actions では 2 → 3 → 4 の順に別々の機械で動かす）。
  1. 一括: stooq の d_us_txt.zip（2026-11 時点ではログインが要り 401 で取れない。残してある）
  2. 銘柄の一覧を作る: --list-symbols  … 届け出の会社の今の銘柄（CIK → company_tickers.json）＋ 今の銘柄が無い会社の届け出の銘柄 ＋ SEC の全銘柄（基準の母集団）＋ SPY
  3. 分けて取る: --shard k/n  … 一覧の k 番目の分だけを、1 銘柄ずつゆっくり yfinance で取る（Yahoo は 1 つの IP から約 2,000 件で止めるので、機械を n 台に分ける）。
     Yahoo に無い銘柄は stooq の 1 銘柄ずつの CSV（https://stooq.com/q/d/l/?s=xxx.us&i=d）でもう一度引く（上場廃止の会社が残っていることがある）
  4. 合わせる: --merge dir … 分けて取った物を 1 つの prices.pkl にして、測る前の確かめ（data_checks.md）を書く

出す物: prices.pkl = {"prices": {"TICKER": {"d": int32 の日付(YYYYMMDD), "o","c","v": float32}}, "market": {"TICKER": 出どころ}}
        prices_meta.json、research/out/data_checks.md
注意: Yahoo・stooq の終値は分割で直されている（配当は入っていない）。Yahoo には今ある銘柄しか無いので、上場廃止の会社は抜けやすい（研究の報告に A/B/C で書く）。
"""
import argparse, glob, io, json, os, pickle, re, sys, time, warnings, zipfile
import urllib.request, urllib.error
import numpy as np

warnings.filterwarnings("ignore")
BULK_URLS = ["https://static.stooq.com/db/h/d_us_txt.zip", "https://stooq.com/db/h/d_us_txt.zip"]
UA = "Mozilla/5.0 (sec-insider research; contact via GitHub kawasemaster3130-dev/sec-insider)"
DELISTED_CHECK = ["LEH", "WM", "SHLD", "BBBY", "SIVB", "WAMUQ", "SHLDQ", "BBBYQ", "SIVBQ", "LEHMQ"]
STOOQ_LIMIT_WORDS = ("exceeded", "limit", "przekroczon")


def norm_symbol(s):
    s = str(s).strip().upper()
    if not s or s in ("N/A", "NONE", "NA", "NULL", "-", "NAN"):
        return ""
    s = s.split(",")[0].split(" ")[0].split("/")[0].replace(".", "-")
    return s if re.fullmatch(r"[A-Z0-9-]{1,10}", s) else ""


def download(url, path, timeout=900):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        n = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk); n += len(chunk)
    return n


def parse_stooq_txt(raw):
    """stooq の CSV: Date,Open,High,Low,Close,Volume（一括は <TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,…）"""
    txt = raw.decode("utf-8", errors="replace")
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    head = [h.strip("<>").strip().upper() for h in lines[0].split(",")]
    def idx(*names):
        for nm in names:
            if nm in head:
                return head.index(nm)
        return None
    iD, iO, iC, iV = idx("DATE"), idx("OPEN"), idx("CLOSE"), idx("VOL", "VOLUME")
    if iD is None or iC is None:
        return None
    d, o, c, v = [], [], [], []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) <= max(iD, iC):
            continue
        try:
            ds = p[iD].strip().replace("-", "")
            dd = int(ds); cc = float(p[iC])
            oo = float(p[iO]) if (iO is not None and len(p) > iO and p[iO]) else cc
            vv = float(p[iV]) if (iV is not None and len(p) > iV and p[iV]) else 0.0
        except ValueError:
            continue
        if cc <= 0 or dd < 19000101:
            continue
        d.append(dd); o.append(oo); c.append(cc); v.append(vv)
    if len(d) < 20:
        return None
    d = np.asarray(d, dtype=np.int32); order = np.argsort(d, kind="stable")
    d = d[order]; keep = np.concatenate([[True], d[1:] != d[:-1]])
    return {"d": d[keep], "o": np.asarray(o, dtype=np.float32)[order][keep], "c": np.asarray(c, dtype=np.float32)[order][keep], "v": np.asarray(v, dtype=np.float32)[order][keep]}


# ----------------------------------------------------------------------------- 2. 銘柄の一覧
def list_symbols(purchases_path, tickers_path, out_path):
    import pandas as pd
    P = pd.read_parquet(purchases_path, columns=["issuer_cik", "symbol"])
    P["issuer_cik"] = pd.to_numeric(P["issuer_cik"], errors="coerce")
    cik2t = {}
    if tickers_path and os.path.exists(tickers_path):
        T = pd.read_parquet(tickers_path, columns=["cik", "ticker"])
        for c, t in zip(T["cik"].astype(int), T["ticker"].astype(str)):
            t = norm_symbol(t)
            if t and c not in cik2t:
                cik2t[c] = t
    current, old = set(), set()
    for cik, g in P.groupby("issuer_cik"):
        if pd.isna(cik):
            continue
        t = cik2t.get(int(cik))
        if t:
            current.add(t)
        else:
            old |= {norm_symbol(x) for x in g["symbol"].dropna().unique()}
    old.discard("")
    universe = set(cik2t.values())
    syms = sorted((current | universe | old | {"SPY"}) - {""})
    # 先に優先の高い物（SPY・今の銘柄）、後に古い銘柄。分けたときに偏らないよう、並びは名前順
    with open(out_path, "w") as f:
        f.write("\n".join(syms) + "\n")
    meta = {"current_issuers": len(current), "old_symbols": len(old), "sec_universe": len(universe), "total": len(syms)}
    json.dump(meta, open(out_path + ".json", "w"), indent=1)
    print(f"銘柄の一覧: {meta}", flush=True)
    return syms


# ----------------------------------------------------------------------------- 3. 分けて取る
class RateLimited(Exception):
    pass


def yf_one(sym):
    """Yahoo から 1 銘柄。終値 Close は分割で直されている（配当は入っていない）。Yahoo が止めたら RateLimited を投げる
    （yf.download は止められても例外を出さずに空を返すので、例外を出す Ticker.history(raise_errors=True) を使う）"""
    import yfinance as yf
    try:
        df = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False, actions=False, raise_errors=True)
    except Exception as e:
        msg = str(e)
        if "Too Many Requests" in msg or "ate limit" in msg or "429" in msg:
            raise RateLimited(msg)
        return None
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return None
    try:
        df = df.dropna(subset=["Close"]); df = df[df["Close"] > 0]
        if len(df) < 20:
            return None
        idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
        d = np.asarray([int(x.strftime("%Y%m%d")) for x in idx], dtype=np.int32)
        order = np.argsort(d, kind="stable"); d = d[order]; keep = np.concatenate([[True], d[1:] != d[:-1]])
        o = df["Open"].to_numpy(dtype=np.float32).ravel() if "Open" in df.columns else df["Close"].to_numpy(dtype=np.float32).ravel()
        v = df["Volume"].to_numpy(dtype=np.float32).ravel() if "Volume" in df.columns else np.zeros(len(df), dtype=np.float32)
        return {"d": d[keep], "o": o[order][keep], "c": df["Close"].to_numpy(dtype=np.float32).ravel()[order][keep], "v": v[order][keep]}
    except Exception:
        return None


def stooq_one(sym):
    """stooq の 1 銘柄 CSV。('ok', rec) / ('none', None) / ('limit', None)"""
    url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception:
        return "none", None
    low = raw[:400].decode("utf-8", errors="replace").lower()
    if any(w in low for w in STOOQ_LIMIT_WORDS):
        return "limit", None
    rec = parse_stooq_txt(raw)
    return ("ok", rec) if rec is not None else ("none", None)


def fetch_shard(symbols, k, n, out_pkl, yf_pause=0.6, stooq_pause=1.2, stooq_cap=600, budget_min=130.0):
    """分担の分を取る。100 銘柄ごとに途中保存し、時間の予算（分）を超えたら残りはあきらめて保存する（機械の時間切れで全部を失わないため）"""
    mine = [s for i, s in enumerate(symbols) if i % n == k]
    if k == 0 and "SPY" not in mine:
        mine = ["SPY"] + mine
    print(f"分担 {k}/{n}: {len(mine):,} 銘柄（時間の予算 {budget_min:.0f} 分）", flush=True)
    os.makedirs(os.path.dirname(out_pkl) or ".", exist_ok=True)
    prices, market = {}, {}
    def save(note=""):
        with open(out_pkl, "wb") as f:
            pickle.dump({"prices": prices, "market": market, "shard": [k, n], "mine": len(mine), "note": note}, f, protocol=pickle.HIGHEST_PROTOCOL)
    t0 = time.time(); done = 0; limits = 0; stopped = ""
    for sym in mine:
        if (time.time() - t0) / 60 > budget_min * 0.75:
            stopped = f"時間の予算の 75% を使ったので Yahoo はここまで（{done}/{len(mine)}）"; print(stopped, flush=True); break
        rec = None
        for attempt in range(4):
            try:
                rec = yf_one(sym); break
            except RateLimited:
                limits += 1
                wait = 45 * (attempt + 1)
                print(f"  Yahoo が止めた（{sym}、{limits} 回目）。{wait} 秒待つ", flush=True)
                time.sleep(wait)
        else:
            stopped = f"Yahoo が止め続けるので Yahoo はここまで（{done}/{len(mine)}）"; print(stopped, flush=True); break
        if rec is not None:
            prices[sym] = rec; market[sym] = "yfinance stocks"
        done += 1
        if done % 100 == 0:
            save("途中"); print(f"  yfinance {done:,}/{len(mine):,} … 取れた {len(prices):,}（{(time.time()-t0)/60:.1f} 分、止められた回数 {limits}）", flush=True)
        time.sleep(yf_pause)
    save("Yahoo まで")
    missing = [s for s in mine if s not in prices]
    print(f"Yahoo で取れた {len(prices):,} / {len(mine):,}（試した {done}）。無い {len(missing):,} を stooq の 1 銘柄 CSV で引く（上限 {stooq_cap}）", flush=True)
    got_s = 0; tried = 0
    for sym in missing[:stooq_cap]:
        if (time.time() - t0) / 60 > budget_min:
            print(f"  時間の予算を使ったので stooq はここまで（{tried} 件）", flush=True); break
        st, rec = stooq_one(sym)
        tried += 1
        if st == "limit":
            print(f"  stooq が止めた（{tried} 件目）。ここまで", flush=True); break
        if rec is not None:
            prices[sym] = rec; market[sym] = "stooq stocks (per-symbol)"; got_s += 1
        if tried % 100 == 0:
            save("stooq 途中")
        time.sleep(stooq_pause)
    print(f"stooq で補えた {got_s} / 試した {tried}", flush=True)
    save(stopped or "全部")
    print(f"終了: {out_pkl} に {len(prices):,} 銘柄（{(time.time()-t0)/60:.1f} 分）", flush=True)


# ----------------------------------------------------------------------------- 4. 合わせる・確かめ
def nearest(rec, dint):
    k = int(np.searchsorted(rec["d"], dint, side="right")) - 1
    return k if k >= 0 else None


def write_checks(prices, market, out_md, source):
    L = [f"# 株価データの確かめ（{source}。道具が書いた）\n"]
    L.append("## 上場廃止になった会社が入っているか（LEH・WM・SHLD・BBBY・SIVB と、廃止後の Q 付きの銘柄）")
    for t in DELISTED_CHECK:
        if t in prices:
            L.append(f"- {t}: ある（{prices[t]['d'][0]}〜{prices[t]['d'][-1]}、{len(prices[t]['d'])} 日。出どころ {market.get(t, '')}）")
        else:
            L.append(f"- {t}: 無い")
    L.append("\n## 分割の直し方（AAPL 2020-08-31 の 4 分割。直されていれば 8/28 と 8/31 の終値はほぼ同じ水準、出来高も直されていれば 8/28 の出来高は約 1.9 億）")
    if "AAPL" in prices:
        r = prices["AAPL"]
        for dd in (20200828, 20200831, 20140606, 20140609):
            k = nearest(r, dd)
            if k is not None and r["d"][k] == dd:
                L.append(f"- AAPL {dd}: 終値 {r['c'][k]:.2f}、出来高 {r['v'][k]:,.0f}")
            else:
                L.append(f"- AAPL {dd}: その日の値が無い")
        L.append("  読み方: 2020-08-28 の終値が 125 前後なら分割で直されている（直されていなければ 499）。出来高が 1.9 億前後なら出来高も直されている（直されていなければ 4,700 万）。")
        L.append("  配当: 2020-08-31 の終値が 129 前後なら配当は入っていない（配当まで直した系列なら 125 前後まで下がる）。")
    else:
        L.append("- AAPL が無い")
    L.append("\n## 配当の直し方（T・MO の権利落ち。配当が入っていなければ、権利落ち日に配当の分だけ下がって見える）")
    for t, dd, div in (("T", 20240109, 0.2775), ("MO", 20240314, 0.98)):
        if t in prices:
            r = prices[t]; k = nearest(r, dd)
            if k is not None and k >= 1 and r["d"][k] == dd:
                L.append(f"- {t} {dd}: 前日終値 {r['c'][k-1]:.2f} → 当日終値 {r['c'][k]:.2f}（配当 {div}）")
            else:
                L.append(f"- {t} {dd}: その日の値が無い")
        else:
            L.append(f"- {t}: 無い")
    L.append("\n## 全体")
    alld_last = np.array([p["d"][-1] for p in prices.values()]) if prices else np.array([0])
    L.append(f"- 銘柄数 {len(prices):,}、最後の日の最大 {int(alld_last.max())}、最後の日が最大より 10 日以上前の銘柄（上場廃止・銘柄変更の疑い） {int((alld_last < int(alld_last.max()) - 10).sum()):,}")
    from collections import Counter
    for folder, n in Counter(market.values()).most_common(30):
        L.append(f"- 出どころ {folder}: {n:,} 銘柄")
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)
    open(out_md, "w", encoding="utf-8").write("\n".join(L) + "\n")


def merge(shard_dir, out_dir, checks, symbols_file=None):
    prices, market = {}, {}
    files = sorted(glob.glob(os.path.join(shard_dir, "**", "prices_shard_*.pkl"), recursive=True))
    print(f"分担のファイル {len(files)} 個", flush=True)
    per = []
    for p in files:
        with open(p, "rb") as f:
            d = pickle.load(f)
        prices.update(d["prices"]); market.update(d["market"]); per.append((os.path.basename(p), len(d["prices"]), d.get("mine")))
    if "SPY" not in prices:
        print("SPY が無いので 1 回だけ取り直す", flush=True)
        try:
            rec = yf_one("SPY")
            if rec is not None:
                prices["SPY"] = rec; market["SPY"] = "yfinance etf"
        except RateLimited:
            pass
        if "SPY" not in prices:
            st, rec = stooq_one("SPY")
            if rec is not None:
                prices["SPY"] = rec; market["SPY"] = "stooq etf (per-symbol)"
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "prices.pkl"), "wb") as f:
        pickle.dump({"prices": prices, "market": market}, f, protocol=pickle.HIGHEST_PROTOCOL)
    need = [s.strip() for s in open(symbols_file)] if (symbols_file and os.path.exists(symbols_file)) else []
    missing = [s for s in need if s not in prices]
    from collections import Counter
    meta = {"source": "yfinance（1 銘柄ずつ・機械を分けて）＋ stooq の 1 銘柄 CSV（Yahoo に無い物）", "tickers": len(prices), "shards": per, "need": len(need), "missing": len(missing), "missing_sample": missing[:50],
            "by_source": dict(Counter(market.values())), "spy": "SPY" in prices}
    if prices:
        alld = np.concatenate([p["d"] for p in prices.values()]); meta.update({"first": int(alld.min()), "last": int(alld.max())})
    json.dump(meta, open(os.path.join(out_dir, "prices_meta.json"), "w"), ensure_ascii=False, indent=1)
    write_checks(prices, market, checks, meta["source"])
    print("終了:", json.dumps({k: v for k, v in meta.items() if k not in ("missing_sample", "shards")}, ensure_ascii=False), flush=True)
    if "SPY" not in prices:
        print("SPY が無い", file=sys.stderr); sys.exit(3)


# ----------------------------------------------------------------------------- 1. 一括（残してある）
def bulk(zpath, out_dir, checks):
    prices, market = {}, {}
    ok = os.path.exists(zpath) and os.path.getsize(zpath) > 1_000_000
    if not ok:
        for u in BULK_URLS:
            try:
                n = download(u, zpath); print(f"取った: {u} {n:,} bytes", flush=True)
                if n > 1_000_000:
                    ok = True; break
            except Exception as e:
                print(f"[warn] {u}: {e}", flush=True)
    if not ok:
        print("一括ファイルが取れなかった", file=sys.stderr); sys.exit(3)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".txt")]
        for n in names:
            m = re.match(r"^(.+?)\.us\.txt$", os.path.basename(n).lower())
            if not m:
                continue
            with z.open(n) as fh:
                rec = parse_stooq_txt(fh.read())
            if rec is not None:
                tk = m.group(1).upper().replace(".", "-"); prices[tk] = rec; market[tk] = os.path.dirname(n).lower().replace("data/daily/us/", "")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "prices.pkl"), "wb") as f:
        pickle.dump({"prices": prices, "market": market}, f, protocol=pickle.HIGHEST_PROTOCOL)
    json.dump({"source": "stooq d_us_txt.zip", "tickers": len(prices)}, open(os.path.join(out_dir, "prices_meta.json"), "w"))
    write_checks(prices, market, checks, "stooq d_us_txt.zip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/raw")
    ap.add_argument("--checks", default="research/out/data_checks.md")
    ap.add_argument("--list-symbols", action="store_true", help="銘柄の一覧を作る（--need と --need-tickers から）")
    ap.add_argument("--need", default="research/raw/purchases.parquet")
    ap.add_argument("--need-tickers", default="research/raw/cik_ticker.parquet")
    ap.add_argument("--symbols", default="research/raw/symbols.txt")
    ap.add_argument("--shard", default=None, help="k/n。一覧の k 番目の分だけ取る")
    ap.add_argument("--shard-out", default=None, help="分担の結果の置き場（既定 research/raw/shards/prices_shard_k.pkl）")
    ap.add_argument("--budget", type=float, default=130.0, help="分担 1 つの時間の予算（分）。超えたら残りはあきらめて保存する")
    ap.add_argument("--merge", default=None, help="分担の結果が入った場所。合わせて prices.pkl を作る")
    ap.add_argument("--bulk", action="store_true", help="stooq の一括を試す（今は取れない）")
    ap.add_argument("--zip", default=None)
    a = ap.parse_args()
    if a.list_symbols:
        list_symbols(a.need, a.need_tickers, a.symbols); return
    if a.shard:
        k, n = [int(x) for x in a.shard.split("/")]
        syms = [s.strip() for s in open(a.symbols) if s.strip()]
        out_pkl = a.shard_out or os.path.join(a.out, "shards", f"prices_shard_{k:02d}.pkl")
        fetch_shard(syms, k, n, out_pkl, budget_min=a.budget); return
    if a.merge:
        merge(a.merge, a.out, a.checks, a.symbols); return
    if a.bulk:
        bulk(a.zip or os.path.join(a.out, "d_us_txt.zip"), a.out, a.checks); return
    ap.print_help()


if __name__ == "__main__":
    main()
