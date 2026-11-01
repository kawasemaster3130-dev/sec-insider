#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
米国株の日足を stooq の一括ファイル（d_us_txt.zip。無料・鍵なし）から取り、研究で使う形（銘柄ごとの配列）にして保存する。
一括ファイルが取れないときは、必要な銘柄だけ yfinance で取る（--need に purchases.parquet を渡す）。

使い方: python3 research/fetch_prices.py --out research/raw --need research/raw/purchases.parquet [--zip research/raw/d_us_txt.zip]
出す物: research/raw/prices.pkl  = {"prices": {"TICKER": {"d": int32 の日付(YYYYMMDD), "o","c","v": float32}}, "market": {"TICKER": "zip の中のフォルダ名"}}
        research/raw/prices_meta.json = 銘柄数・期間・取り方・フォルダの数
        research/out/data_checks.md   = 測る前の確かめ（上場廃止 5 社があるか、分割・配当・出来高の直し方）
注意: stooq の値段は分割で直されている（配当は入っていないと見る）。上場廃止の会社は入っていないことがある（研究の報告に書く）。
"""
import argparse, io, json, os, pickle, re, sys, time, zipfile
import urllib.request
import numpy as np

BULK_URLS = ["https://static.stooq.com/db/h/d_us_txt.zip", "https://stooq.com/db/h/d_us_txt.zip"]
UA = "Mozilla/5.0 (sec-insider research; contact via GitHub kawasemaster3130-dev/sec-insider)"
DELISTED_CHECK = ["LEH", "WM", "SHLD", "BBBY", "SIVB", "WAMUQ", "SHLDQ", "BBBYQ", "SIVBQ", "LEHMQ"]


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
    """<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>"""
    txt = raw.decode("utf-8", errors="replace")
    lines = txt.splitlines()
    if len(lines) < 2:
        return None
    head = [h.strip("<>").upper() for h in lines[0].split(",")]
    try:
        iD, iO, iC, iV = head.index("DATE"), head.index("OPEN"), head.index("CLOSE"), head.index("VOL")
    except ValueError:
        return None
    d, o, c, v = [], [], [], []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) <= max(iD, iO, iC, iV):
            continue
        try:
            dd = int(p[iD]); cc = float(p[iC]); oo = float(p[iO]) if p[iO] else cc; vv = float(p[iV]) if p[iV] else 0.0
        except ValueError:
            continue
        if cc <= 0:
            continue
        d.append(dd); o.append(oo); c.append(cc); v.append(vv)
    if not d:
        return None
    d = np.asarray(d, dtype=np.int32); order = np.argsort(d, kind="stable")
    d = d[order]; keep = np.concatenate([[True], d[1:] != d[:-1]])  # 同じ日の重複は最初だけ
    return {"d": d[keep], "o": np.asarray(o, dtype=np.float32)[order][keep], "c": np.asarray(c, dtype=np.float32)[order][keep], "v": np.asarray(v, dtype=np.float32)[order][keep]}


def need_symbols(path):
    import pandas as pd
    P = pd.read_parquet(path, columns=["symbol"])
    syms = set()
    for s in P["symbol"].dropna().unique():
        s = str(s).strip().upper()
        if not s or s in ("N/A", "NONE", "NA", "NULL", "-"):
            continue
        s = s.split(",")[0].split(" ")[0].split("/")[0].replace(".", "-")
        if re.fullmatch(r"[A-Z0-9-]{1,10}", s):
            syms.add(s)
    syms.add("SPY")
    return sorted(syms)


def yf_fetch(symbols, batch=40, pause=1.0):
    """yfinance でまとめて取る（分割で直した終値 Close、出来高 Volume）"""
    import yfinance as yf
    import pandas as pd
    out = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            df = yf.download(chunk, period="max", interval="1d", auto_adjust=False, progress=False, threads=True, group_by="ticker")
        except Exception as e:
            print(f"[warn] yfinance {chunk[0]}〜: {e}", flush=True); time.sleep(pause * 5); continue
        for tk in chunk:
            try:
                sub = df[tk] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) < 20:
                    continue
                d = np.asarray([int(x.strftime("%Y%m%d")) for x in sub.index], dtype=np.int32)
                out[tk] = {"d": d, "o": sub["Open"].to_numpy(dtype=np.float32).ravel(), "c": sub["Close"].to_numpy(dtype=np.float32).ravel(), "v": sub["Volume"].to_numpy(dtype=np.float32).ravel()}
            except Exception:
                pass
        if (i // batch) % 10 == 0:
            print(f"  yfinance {i + len(chunk):,}/{len(symbols):,} … 取れた {len(out):,}", flush=True)
        time.sleep(pause)
    return out


def nearest(rec, dint):
    k = int(np.searchsorted(rec["d"], dint, side="right")) - 1
    return k if k >= 0 else None


def write_checks(prices, market, out_md, source):
    L = [f"# 株価データの確かめ（{source}。道具が書いた）\n"]
    L.append("## 上場廃止になった会社が入っているか（LEH・WM・SHLD・BBBY・SIVB と、廃止後の Q 付きの銘柄）")
    for t in DELISTED_CHECK:
        if t in prices:
            L.append(f"- {t}: ある（{prices[t]['d'][0]}〜{prices[t]['d'][-1]}、{len(prices[t]['d'])} 日）")
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
        L.append(f"- フォルダ {folder}: {n:,} 銘柄")
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    open(out_md, "w", encoding="utf-8").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/raw")
    ap.add_argument("--zip", default=None, help="既に落とした d_us_txt.zip の場所")
    ap.add_argument("--need", default=None, help="必要な銘柄の一覧（purchases.parquet）。一括が取れないときの yfinance の対象にもなる")
    ap.add_argument("--yf-missing", type=int, default=0, help="一括で取れても、無い銘柄を yfinance で最大 N 銘柄だけ補う（0 なら補わない）")
    ap.add_argument("--checks", default="research/out/data_checks.md")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    zpath = a.zip or os.path.join(a.out, "d_us_txt.zip")
    meta = {"source": "", "yf_filled": 0}
    prices, market = {}, {}
    bulk_ok = os.path.exists(zpath) and os.path.getsize(zpath) > 1_000_000
    if not bulk_ok:
        for u in BULK_URLS:
            try:
                n = download(u, zpath); print(f"取った: {u} {n:,} bytes", flush=True)
                if n > 1_000_000:
                    bulk_ok = True; meta["url"] = u; break
            except Exception as e:
                print(f"[warn] {u}: {e}", flush=True)
    if bulk_ok:
        try:
            with zipfile.ZipFile(zpath) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                print(f"zip の中のファイル: {len(names):,}", flush=True)
                for i, n in enumerate(names):
                    base = os.path.basename(n).lower()
                    m = re.match(r"^(.+?)\.us\.txt$", base)
                    if not m:
                        continue
                    tk = m.group(1).upper().replace(".", "-")
                    try:
                        with z.open(n) as fh:
                            rec = parse_stooq_txt(fh.read())
                    except Exception:
                        rec = None
                    if rec is not None and len(rec["d"]) >= 20:
                        prices[tk] = rec
                        market[tk] = os.path.dirname(n).lower().replace("data/daily/us/", "")
                    if (i + 1) % 2000 == 0:
                        print(f"  {i+1:,}/{len(names):,} … 銘柄 {len(prices):,}", flush=True)
            meta["source"] = "stooq d_us_txt.zip"
            print(f"stooq から読めた銘柄: {len(prices):,}", flush=True)
        except zipfile.BadZipFile:
            print("[warn] zip が壊れている（ログイン画面の HTML などの可能性）", flush=True); bulk_ok = False
            try:
                os.remove(zpath)
            except OSError:
                pass
    need = need_symbols(a.need) if (a.need and os.path.exists(a.need)) else []
    missing = [s for s in need if s not in prices]
    if need:
        meta["need"] = len(need); meta["need_found"] = len(need) - len(missing)
        print(f"必要な銘柄 {len(need):,} のうち無い物 {len(missing):,}", flush=True)
    if not bulk_ok and need:
        print("一括ファイルが取れないので yfinance で必要な銘柄だけ取る", flush=True)
        got = yf_fetch(missing)
        prices.update(got); market.update({t: "yfinance" for t in got})
        meta["source"] = "yfinance（stooq の一括が取れなかった）"; meta["yf_filled"] = len(got)
    elif a.yf_missing > 0 and missing:
        got = yf_fetch(missing[: a.yf_missing])
        prices.update(got); market.update({t: "yfinance" for t in got}); meta["yf_filled"] = len(got)
    if "SPY" not in prices:
        print("SPY が無い。市場のものさしが作れない", file=sys.stderr)
        if not prices:
            sys.exit(3)
    with open(os.path.join(a.out, "prices.pkl"), "wb") as f:
        pickle.dump({"prices": prices, "market": market}, f, protocol=pickle.HIGHEST_PROTOCOL)
    alld = np.concatenate([p["d"] for p in prices.values()]) if prices else np.array([0])
    missing2 = [s for s in need if s not in prices]
    meta.update({"tickers": len(prices), "first": int(alld.min()), "last": int(alld.max()), "missing": len(missing2), "missing_sample": missing2[:50]})
    json.dump(meta, open(os.path.join(a.out, "prices_meta.json"), "w"), ensure_ascii=False, indent=1)
    write_checks(prices, market, a.checks, meta["source"])
    print("終了:", json.dumps({k: v for k, v in meta.items() if k != "missing_sample"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
