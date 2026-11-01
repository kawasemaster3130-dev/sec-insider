#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SECインサイダー  EDGAR 取り込みスクリプト（標準ライブラリだけ・鍵なし）

SEC EDGAR から、指定した日付の範囲の Form 4（と 4/A）の届け出を取り、
「買い（取引コード P）」の取引だけを抜き出して data/days/YYYY-MM-DD.json に保存する。
日ごとの結果は保存されるので、同じ日をもう一度実行しても、取り直すのは新しく増えた届け出だけ。

使い方:
  EDGAR_UA="会社名 連絡先メール" python3 fetch_edgar.py --from 2026-09-27 --to 2026-10-27 [--out data/days] [--rps 8]
  EDGAR_UA は SEC の決まり（連絡先の分かる User-Agent）。無いと動かない。

出口コード: 0=正常 / 1=引数や環境の誤り / 3=ネットワークの失敗が多すぎて途中で止めた（保存済みの分は残る）
"""
import argparse, hashlib, json, math, os, re, sys, time, random
import urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date, timezone

BASE = "https://www.sec.gov"
UA = os.environ.get("EDGAR_UA", "").strip()
COLLECTOR = "fetch_edgar-2.7"   # 2.7: 索引なし・INDEX の印の消し忘れと、ブラウザ版の日ファイルの読み方を直した（2026-11-01）
FAILED = False   # get() の返り: bytes=取れた / None=SEC に無い（404・AccessDenied） / FAILED=何度やっても取れなかった
NOTE_MAX, DOCNOTE_MAX = 4000, 1000   # ブラウザ版 collect_edgar.js v2.3 と同じ
EXMAP = None   # SEC の銘柄と取引所の一覧（1 回だけ読む）。{"by_cik": {...}, "by_ticker": {...}}
# 届け出のどこかの脚注・備考に上場（IPO）の言葉があれば ipoInFiling を付ける（ブラウザ版の IPO_RE と同じ）。
# 「市場の外の買いの可能性」の判定そのものは build.py がする
IPO_RE = re.compile(r"initial public offering|\bIPO\b", re.I)


class Fetcher:
    def __init__(self, rps=8.0, max_fail=30):
        self.min_gap = 1.0 / max(0.5, rps)
        self.last = 0.0
        self.fail = 0
        self.max_fail = max_fail
        self.n = 0

    def get(self, url, retries=4):
        for attempt in range(retries):
            gap = self.min_gap - (time.monotonic() - self.last)
            if gap > 0:
                time.sleep(gap)
            self.last = time.monotonic()
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    if r.headers.get("Content-Encoding", "") == "gzip":
                        import gzip
                        data = gzip.decompress(data)
                    self.n += 1
                    return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 403:
                    # SEC の保管庫は「無いファイル」を 403 AccessDenied（XML）で返す（休日・未公開の日の索引も）。速すぎて断られたときは HTML
                    try:
                        body = e.read()
                    except Exception:
                        body = b""
                    if b"<Code>AccessDenied</Code>" in body:
                        return None
                wait = (2 ** attempt) * 2 + random.random()
                print(f"[warn] HTTP {e.code} {url} → {wait:.0f}秒待つ", file=sys.stderr)
                time.sleep(wait)
            except Exception as e:  # タイムアウト・切断など
                wait = (2 ** attempt) * 2 + random.random()
                print(f"[warn] {type(e).__name__}: {e} {url} → {wait:.0f}秒待つ", file=sys.stderr)
                time.sleep(wait)
        self.fail += 1
        if self.fail > self.max_fail:
            raise RuntimeError("ネットワークの失敗が多すぎる")
        return FAILED


def quarter(d: date) -> str:
    return f"QTR{(d.month - 1) // 3 + 1}"


INDEX_HEAD_RE = re.compile(r"CIK\|Company Name\|Form Type\|Date Filed\|File ?name", re.I)


def master_index(f: Fetcher, d: date):
    """その日の master index（CIK|会社名|様式|日付|ファイル）を返す。無い日（休日・まだ出ていない日）は空、取れなかったときは None"""
    url = f"{BASE}/Archives/edgar/daily-index/{d.year}/{quarter(d)}/master.{d.strftime('%Y%m%d')}.idx"
    raw = f.get(url)
    if raw is FAILED:
        return None
    if raw is None:
        return []
    text = raw.decode("latin-1", errors="replace")
    if not INDEX_HEAD_RE.search(text):
        return None   # 200 でも索引の見出しが無ければ（保守の画面など）索引として読まない（ブラウザ版と同じ）
    rows = []
    # 行は \n だけで分ける（splitlines は会社名の中の 0x85・0x0c などでも行を割ってしまう。ブラウザ版の split(/\r?\n/) と同じにする）
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form, filed, fn = parts
        filed = filed.strip()
        if form.strip() in ("4", "4/A") and fn.strip().endswith(".txt") and re.fullmatch(r"\d{8}", filed):
            rows.append({"cik": cik.strip(), "name": name.strip(), "form": form.strip(),
                         "filed": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}", "file": fn.strip()})
    # 同じ届け出は、出した人の分と会社の分の2行で載るので、1つにする
    seen, uniq = set(), []
    for r in rows:
        acc = os.path.basename(r["file"]).replace(".txt", "")
        if acc in seen:
            continue
        seen.add(acc)
        uniq.append(r)
    return uniq


def index_sig(idx):
    """索引の印（ブラウザ版の index_sig・check_index.js と同じ）: 届け出番号を並べ替えて改行でつないだ文字の sha256 の先頭 16 字"""
    accs = sorted({os.path.basename(m["file"])[:-4] if m["file"].endswith(".txt") else os.path.basename(m["file"]) for m in idx})
    return hashlib.sha256("\n".join(accs).encode("utf-8")).hexdigest()[:16]


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def all_text(el):
    return clean("".join(el.itertext())) if el is not None else ""


def text_of(el, path):
    """ブラウザ版の textOf と同じ: 子の value があればその文字、無ければその要素の中の文字全部"""
    x = el.find(path)
    if x is None:
        return ""
    v = x.find("value")
    if v is not None:
        return all_text(v)
    return all_text(x)


def cap(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def note_ids(el):
    return [f.get("id") for f in el.iter("footnoteId") if f.get("id")] if el is not None else []


def parse_form4(xml_text: str):
    """ownershipDocument の XML から、買い（P）の取引だけを行にして返す。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for el in root.iter():  # 名前空間が付いていても読めるように外す（ブラウザ版の localName と同じ）
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    if root.tag != "ownershipDocument":
        return None
    def ft(el, tag):
        return all_text(el.find(tag)) if el is not None else ""
    doc_type = ft(root, "documentType")
    issuer = root.find("issuer")
    sym = ft(issuer, "issuerTradingSymbol").upper()
    issuer_name = ft(issuer, "issuerName")
    issuer_cik = ft(issuer, "issuerCik")
    owners = []
    for ro in root.findall("reportingOwner"):
        rid = ro.find("reportingOwnerId")
        rel = ro.find("reportingOwnerRelationship")
        name = ft(rid, "rptOwnerName")
        cik = ft(rid, "rptOwnerCik")
        parts = []
        if rel is not None:
            def flag(tag):
                return ft(rel, tag).lower() in ("1", "true")
            title = ft(rel, "officerTitle")
            other = ft(rel, "otherText")
            if flag("isOfficer"):
                parts.append("officer: " + title if title else "officer")
            if flag("isDirector"):
                parts.append("director")
            if flag("isTenPercentOwner"):
                parts.append("10 percent owner")
            if flag("isOther"):
                parts.append("other: " + other if other else "other")
        owners.append({"name": name, "cik": cik, "type": ", ".join(parts)})
    plan = ft(root, "aff10b5One").lower() in ("1", "true")
    fn = {f.get("id"): all_text(f) for f in root.iter("footnote") if f.get("id")}
    remarks = all_text(root.find("remarks"))
    doc_note = cap(remarks, DOCNOTE_MAX)
    ipo_in_filing = bool(IPO_RE.search(" ".join(fn.values()) + " " + remarks))
    rows = []
    table = root.find("nonDerivativeTable")
    if table is None:
        return rows
    line = -1
    for tr in table.findall("nonDerivativeTransaction"):
        line += 1
        code = text_of(tr, "transactionCoding/transactionCode")
        if code != "P":
            continue
        ids = list(dict.fromkeys(note_ids(tr)))
        notes = cap(" | ".join(fn[i] for i in ids if fn.get(i)), NOTE_MAX)
        nature = text_of(tr, "ownershipNature/natureOfOwnership")
        if not nature:
            nid = note_ids(tr.find("ownershipNature/natureOfOwnership"))
            nature = cap(" | ".join(fn[i] for i in nid if fn.get(i)), 200)
        ad = text_of(tr, "transactionAmounts/transactionAcquiredDisposedCode") or "A"
        shares = text_of(tr, "transactionAmounts/transactionShares")
        price = text_of(tr, "transactionAmounts/transactionPricePerShare")
        owned = text_of(tr, "postTransactionAmounts/sharesOwnedFollowingTransaction")
        rows.append({
            "transactionCode": code,
            "acquisitionOrDisposition": ad,
            "transactionDate": text_of(tr, "transactionDate"),
            "securityName": text_of(tr, "securityTitle"),
            "securitiesTransacted": shares,
            "price": price,
            "securitiesOwned": owned,
            "directOrIndirect": text_of(tr, "ownershipNature/directOrIndirectOwnership"),
            "natureOfOwnership": nature,
            "_line": line, "_notes": notes, "_doc_note": doc_note, "_ipo": ipo_in_filing,
            "_doc_type": doc_type, "_symbol": sym, "_issuer": issuer_name, "_issuer_cik": issuer_cik,
            "_owners": owners, "_plan": plan,
        })
    return rows


def extract_xml(submission_text: str):
    """完全提出ファイル（.txt）の中から ownershipDocument の XML を取り出す。"""
    m = re.search(r"<XML>\s*(<\?xml[^>]*>)?\s*(<ownershipDocument(?:\s[^>]*)?>.*?</ownershipDocument>)", submission_text, re.S)
    if not m:
        return None
    return m.group(2)


def num(s):
    try:
        x = float(str(s).replace(",", "").strip())
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def load_exchanges(f):
    """SEC の /files/company_tickers_exchange.json を 1 回だけ読み、会社の番号（CIK）と銘柄コードから取引所を引けるようにする（ブラウザ版と同じ）"""
    global EXMAP
    if EXMAP is not None:
        return
    by_cik, by_ticker = {}, {}
    raw = f.get(f"{BASE}/files/company_tickers_exchange.json")
    if raw:
        try:
            j = json.loads(raw.decode("utf-8"))
            fi = j.get("fields") or []
            ic, it, ie = fi.index("cik"), fi.index("ticker"), fi.index("exchange")
            for row in j.get("data") or []:
                ex = row[ie] or ""
                cik = str(row[ic] or "").lstrip("0")
                if cik and ex and cik not in by_cik:
                    by_cik[cik] = ex
                t = str(row[it] or "").upper()
                if t and ex and t not in by_ticker:
                    by_ticker[t] = ex
        except Exception:
            pass   # 形が変わっていたら使わない
    EXMAP = {"by_cik": by_cik, "by_ticker": by_ticker}


def exchange_of(cik, sym):
    if not EXMAP:
        return ""
    c = str(cik or "").lstrip("0")
    s = str(sym or "").upper()
    return EXMAP["by_cik"].get(c) or EXMAP["by_ticker"].get(s) or EXMAP["by_ticker"].get(s.replace(".", "-")) or ""


def to_rows(parsed, meta):
    """build.py が読む FMP と同じかたちの行にする（1取引=1行、届け出人は最初の人を代表にし、連名は joint に残す）。"""
    acc = os.path.basename(meta["file"]).replace(".txt", "")
    acc_nodash = acc.replace("-", "")
    folder_cik = meta["file"].split("/")[2] if meta["file"].count("/") >= 3 else meta["cik"]
    url = f"{BASE}/Archives/edgar/data/{folder_cik}/{acc_nodash}/{acc}-index.htm"
    out = []
    for t in parsed:
        owners = t["_owners"] or [{"name": meta["name"], "cik": meta["cik"], "type": ""}]
        first = owners[0]
        out.append({
            "symbol": t["_symbol"],
            "issuerName": t["_issuer"],
            "filingDate": meta["filed"],
            "transactionDate": t["transactionDate"] or meta["filed"],
            "reportingCik": first["cik"],
            "companyCik": t["_issuer_cik"] or meta["cik"],
            "transactionType": "P-Purchase",
            "securitiesOwned": num(t["securitiesOwned"]),
            "reportingName": first["name"],
            "typeOfOwner": first["type"],
            "acquisitionOrDisposition": t["acquisitionOrDisposition"],
            "directOrIndirect": t["directOrIndirect"],
            "natureOfOwnership": t["natureOfOwnership"],
            "formType": t["_doc_type"] or meta["form"],
            "securitiesTransacted": num(t["securitiesTransacted"]),
            "price": num(t["price"]),
            "securityName": t["securityName"],
            "url": url,
            "accession": acc,
            "line": t["_line"],
            "plan10b5": t["_plan"],
            "notes": t["_notes"],
            "docNote": t["_doc_note"],
            "ipoInFiling": t["_ipo"],
            "issuerExchange": exchange_of(t["_issuer_cik"] or meta["cik"], t["_symbol"]),
            "jointNames": [o["name"] for o in owners[1:]],
            "jointTypes": [o["type"] for o in owners[1:]],
        })
    return out


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", required=True)
    ap.add_argument("--out", default="data/days")
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--max-filings", type=int, default=0, help="試しに少しだけ取るとき")
    a = ap.parse_args()
    if not UA or "@" not in UA:
        print("EDGAR_UA（会社名と連絡先メール）が要る。例: EDGAR_UA='ChartMaster yakuin-kai contact@example.com'", file=sys.stderr)
        sys.exit(1)
    d0 = datetime.strptime(a.dfrom, "%Y-%m-%d").date()
    d1 = datetime.strptime(a.dto, "%Y-%m-%d").date()
    os.makedirs(a.out, exist_ok=True)
    f = Fetcher(rps=a.rps)
    load_exchanges(f)
    total_new = 0
    for d in daterange(d0, d1):
        if d.weekday() >= 5:
            continue
        fp = os.path.join(a.out, f"{d.isoformat()}.json")
        fresh = {"date": d.isoformat(), "filings_seen": 0, "filings_parsed": 0, "accessions": [], "rows": [], "complete": False, "errors": [], "collector": COLLECTOR}
        day = dict(fresh)
        old = None   # 前の回の日ファイル（無ければ None）
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as fh:
                old = json.load(fh)
            if isinstance(old, dict) and isinstance(old.get("accessions"), list) and isinstance(old.get("rows"), list) and isinstance(old.get("errors"), list):
                day = old   # この道具が書いた日ファイル。続きから取る
            elif isinstance(old, dict) and not old.get("index_missing"):
                # ブラウザ版（collector）の日ファイルは accessions の一覧が無く、続きを取れないので、その日は最初から取り直す（行は二重にしない）
                print(f"{d}: 前の日ファイルは別の道具の書き方なので、最初から取り直す", file=sys.stderr)
            # ブラウザ版の「索引なし」の印だけのファイルは、中身が無いので fresh から始める（索引が出れば取る。無ければ下で書き直す）
        idx = master_index(f, d)
        if idx is None:
            # 索引が取れなかった（通信の失敗）。休日と決めつけない。前の日ファイルがあればそのまま残し、無ければ「不完全」の印だけ置く（次の回に取り直す）
            if not os.path.exists(fp):
                day["complete"] = False
                day["errors"] = sorted(set(day.get("errors", []) + ["INDEX"]))
                day["fetched_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                with open(fp, "w", encoding="utf-8") as fh:
                    json.dump(day, fh, ensure_ascii=False)
            print(f"{d}: 索引が取れなかった（次の回に取り直す）", file=sys.stderr)
            continue
        if not idx:
            had_filings = day.get("filings_seen", 0) > 0 or (isinstance(old, dict) and old.get("filings_seen", 0) > 0)
            if had_filings and not day.get("index_missing"):
                # 前に索引があった日（この道具の物でも、ブラウザ版の物でも）を「索引なし」で上書きしない（receive_day.py と同じ）
                print(f"{d}: 索引が見つからないが、前の日ファイルを残す", file=sys.stderr)
                continue
            day["errors"] = [e for e in day.get("errors", []) if e != "INDEX"]   # 前の回の「索引が取れなかった」の印は消す
            day["filings_seen"] = 0
            day["index_missing"] = True
            day["complete"] = True
            day["collector"] = COLLECTOR
            day["fetched_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(day, fh, ensure_ascii=False)
            print(f"{d}: Form 4 の届け出なし（休日か未公開）")
            continue
        # 索引が出た（前の回に「索引なし」と書いた日でも）。印を消さないと build.py がその日を「索引なし」のまま扱う
        day.pop("index_missing", None)
        day.pop("confirmed_at", None)
        if "INDEX" in day["errors"]:
            day["errors"].remove("INDEX")   # 前の回に索引が取れなかった印。索引が取れたので消す（残すと complete が永久に False になり、build.py が毎回 NG）
        done = set(day.get("accessions", []))
        todo = [m for m in idx if os.path.basename(m["file"]).replace(".txt", "") not in done]
        if a.max_filings:
            todo = todo[: a.max_filings]
        day["filings_seen"] = len(idx)
        day["index_n"] = len(idx)
        day["index_sig"] = index_sig(idx)
        n_new = 0
        for i, m in enumerate(todo):
            acc = os.path.basename(m["file"]).replace(".txt", "")
            raw = f.get(f"{BASE}/Archives/{m['file']}")
            if raw is None:
                # 索引にあるのに SEC に無い届け出。取り直しても出てこないので、読み済みにして not_found に数える
                day.setdefault("not_found", []).append(acc)
                day["accessions"].append(acc)
                if acc in day["errors"]:
                    day["errors"].remove(acc)
                continue
            text = raw.decode("utf-8", errors="replace") if raw is not FAILED else ""
            xml_text = extract_xml(text) if text else None
            parsed = parse_form4(xml_text) if xml_text else None
            if parsed is None:
                # 取れない・読めない物は読み済みにしない（次の回にまた取りに行く）。3 回失敗したらあきらめて記録する
                tries = day.setdefault("tries", {})
                tries[acc] = tries.get(acc, 0) + 1
                if tries[acc] >= 3:
                    day.setdefault("gave_up", []).append(acc)
                    day["accessions"].append(acc)
                    if acc in day["errors"]:
                        day["errors"].remove(acc)
                else:
                    if acc not in day["errors"]:
                        day["errors"].append(acc)
                continue
            day["accessions"].append(acc)
            day["filings_parsed"] += 1
            if acc in day["errors"]:
                day["errors"].remove(acc)
            day.get("tries", {}).pop(acc, None)
            rows = to_rows(parsed, m)
            day["rows"].extend(rows)
            n_new += len(rows)
            if (i + 1) % 100 == 0:
                with open(fp, "w", encoding="utf-8") as fh:
                    json.dump(day, fh, ensure_ascii=False)
                print(f"{d}: {i+1}/{len(todo)} 件 … 買いの行 {len(day['rows'])}")
        day["accessions_count"] = len(day.get("accessions", []))
        day["errors"] = sorted(set(day["errors"]))
        day["gave_up"] = sorted(set(day.get("gave_up", [])))
        day["not_found"] = sorted(set(day.get("not_found", [])))
        day.setdefault("fetch_failed", [])
        bad = len(day["errors"]) + len(day["gave_up"]) + len(day["not_found"])
        # 完全＝まだ取れていない届け出が 0 件で、読めなかった届け出が 1%（最低 1 件）まで（ブラウザ版と同じ）
        day["collector"] = COLLECTOR
        day["complete"] = (not a.max_filings) and not day["errors"] and (len(day["gave_up"]) + len(day["not_found"])) <= max(1, int(len(idx) * 0.01))
        day["fetched_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(day, fh, ensure_ascii=False)
        total_new += n_new
        print(f"{d}: 届け出 {len(idx)} 件（新しく取った {len(todo)}）/ 買いの行 {len(day['rows'])}（今回 +{n_new}）/ 読めない {len(day['errors'])}（あきらめた {len(day['gave_up'])}）")
        if len(todo) >= 20 and bad > len(todo) * 0.05:
            print(f"中断: {d} は失敗が 5% を超えた（{bad}/{len(todo)}）。SEC の形式が変わったか、通信の問題。保存済みの分は残る", file=sys.stderr)
            sys.exit(3)
    print(f"終了: 取った通信 {f.n} 回 / 今回増えた買いの行 {total_new}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"中断: {e}", file=sys.stderr)
        sys.exit(3)
