#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ブラウザ版の取り込みが出した 1 日分（gzip+base64）を、検算してから data/days/日付.json に置く。
使い方: python3 receive_day.py --date 2026-10-23 --sha <sha256> --b64file in/2026-10-23.b64 [--out data/days] [--force]
  分けて写したときは先に cat in/2026-10-23.b64.* > in/2026-10-23.b64 でつなぐ（.000 .001 … の順）
  1 つの部分だけを確かめる: python3 receive_day.py --piece in/2026-10-23.b64.001 --piece-sha <part() の h>
  ブラウザに写したコードが元と同じかの答え: python3 receive_day.py --js-hash collect_edgar.js（または check_index.js。読み込みの返事の src= と比べる）
  索引の確かめ（check_index.js の答えを JSON のファイルにして渡す）: python3 receive_day.py --confirm-file check.json --out days
    1 日だけなら: python3 receive_day.py --confirm 2026-10-23 --status 200 --n 312 --sig <16 字> [--missing] --out days
出る行（1 行目が答え。日ファイルを書いたときは最後に必ず "SAVED <ファイル>"）:
  OK      保存した（ACCEPT・MERGE のあとにも出る）
  ACCEPT  前の回も今回も同じ届け出だけが取れなかった（1 件か 1% まで）ので、その日を「完全」として保存した
  MERGE   前は完全で、今回は取れなかった・SEC に無い・読めなかった届け出がある。そのうち前に読めていた物は前の行を使って保存した（索引が同じならたいてい完全になる）
  KEEP    前の分を残した（今回は途中で止まった・索引が取れなかった・索引でない中身だった など）
  CONFIRM 索引が前に取ったときと同じ（または、索引がまだ無いままの日で、確かめた時刻を書いた）
  DIFF    索引が変わった・手元の日が確かめに向かない → その日はまるごと取り直す
  SKIP    索引を確かめられなかった → 次の回にもう一度
  NG      壊れている（base64・gzip・sha256・日付のどれかが合わない。保存しない。出口コード 2）"""
import argparse, base64, gzip, hashlib, json, os, re, sys
from datetime import date, datetime, timedelta, timezone

ap = argparse.ArgumentParser()
ap.add_argument("--js-hash", default=None, help="collect_edgar.js / check_index.js の関数 yk…() の文字の FNV-1a（ブラウザの読み込みの返事の src= と比べる）")
ap.add_argument("--piece", default=None, help="写した 1 つの部分のファイル（--piece-sha と組で、その部分だけを確かめる）")
ap.add_argument("--piece-sha", default=None, help="ブラウザの part() が返した h（その部分の sha256 の先頭 16 字）")
ap.add_argument("--confirm-file", default=None, help="check_index.js の答え（日ごとの {status, n, sig} / {status, missing}）の JSON のファイル")
ap.add_argument("--confirm", default=None, help="日付。1 日分の確かめ（--status --n --sig --missing）")
ap.add_argument("--status", type=int, default=None)
ap.add_argument("--n", type=int, default=None)
ap.add_argument("--sig", default=None)
ap.add_argument("--missing", action="store_true", help="SEC に索引が無かった（404・AccessDenied）")
ap.add_argument("--asof", default=None, help="試験用: 今の時刻（UTC。例 2026-10-28T12:50:00Z）")
ap.add_argument("--date")
ap.add_argument("--sha")
ap.add_argument("--b64file")
ap.add_argument("--out", default="data/days")
ap.add_argument("--force", action="store_true", help="前の日ファイルより悪くても上書きする")
a = ap.parse_args()


LOG = None


def say(msg):
    """出力の行を表に出し、<--out の上のフォルダ>/recv.log にも足す（run_status.py が読む）"""
    print(msg)
    if LOG:
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass


def now_iso():
    if a.asof:
        return a.asof
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def settle_line(d):
    """その日の索引が出そろったとみなす時刻（翌日 12:00 UTC）。plan_days.py と同じ"""
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat() + "T12:00"


def write_day(fp, obj):
    with open(fp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    say(f"SAVED {fp}")


def load(fp):
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return None


if a.js_hash:
    src = open(a.js_hash, encoding="utf-8").read()
    m = re.search(r"function yk\w*\s*\(", src)
    i, j = m.start(), src.rstrip().rindex("})();")
    text = src[i:j + 1]
    u16 = text.encode("utf-16-le")   # JavaScript の charCodeAt と同じ 16 ビットの単位で数える
    h = 0x811C9DC5
    for k in range(0, len(u16), 2):
        h ^= u16[k] | (u16[k + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    print(f"{h:08x}"); sys.exit(0)
if a.piece:
    # 1 つの部分を確かめる（ファイルの中身そのもの。前後の空白や改行は外して比べる）
    t = open(a.piece, encoding="ascii").read().strip()
    h = hashlib.sha256(t.encode("ascii")).hexdigest()[:16]
    if h == (a.piece_sha or "").strip().lower()[:16]:
        print(f"OK {os.path.basename(a.piece)}: {len(t)} 字"); sys.exit(0)
    print(f"NG {os.path.basename(a.piece)}: 写し間違い（{len(t)} 字・{h} != {a.piece_sha}）。この部分だけ写し直す"); sys.exit(2)


if a.confirm_file or a.confirm or a.date:
    LOG = os.path.join(os.path.dirname(os.path.abspath(a.out)), "recv.log")


def confirm_one(d, r):
    """1 日分の確かめ。r = check_index.js の答えの 1 日分（{status, n, sig} か {status, missing}）"""
    date.fromisoformat(d)
    fp = os.path.join(a.out, f"{d}.json")
    old = load(fp) if os.path.exists(fp) else None
    status = r.get("status")
    if status == 200:
        sig = str(r.get("sig") or "").strip().lower()
        n = r.get("n")
        why = None
        if not old:
            why = "手元に日ファイルが無い"
        elif old.get("index_missing"):
            why = "前は索引が無かった日に、索引が出た"
        elif not old.get("complete"):
            why = "手元の日が不完全"
        elif old.get("fetch_failed") or old.get("gave_up") or old.get("not_found") or old.get("errors"):
            why = "手元の日に、取れなかった・読めなかった・SEC に無かった届け出がある"
        elif not old.get("index_sig"):
            why = "手元の日に索引の印が無い（前の版で取った日）"
        elif old.get("index_sig") != sig or old.get("index_n") != n:
            why = f"索引が変わった（前 {old.get('index_n')} 件 → 今 {n} 件）"
        if why:
            say(f"DIFF {d}: {why}。この日はまるごと取り直す")
            return
        old["confirmed_at"] = now_iso()
        say(f"CONFIRM {d}: 索引が前に取ったときと同じ（{n} 件）。取り直さずに確かめ済みにした"
              + ("" if old["confirmed_at"] >= settle_line(d) else "（ただし翌日 12:00 UTC より前なので、次の回にもう一度確かめる）"))
        write_day(fp, old)
        return
    if r.get("missing") and status in (403, 404):
        if old and old.get("index_missing"):
            old["confirmed_at"] = now_iso()
            say(f"CONFIRM {d}: 索引はまだ無い（SEC の休みの日か、まだ出していない日）。確かめた時刻を書いた")
            write_day(fp, old)
            return
        if not old:
            say(f"DIFF {d}: 手元に日ファイルが無い。この日はまるごと取り直す")
            return
        say(f"SKIP {d}: 前は索引があったのに、今は見つからない。この回はそのままにして、次の回にもう一度確かめる")
        return
    say(f"SKIP {d}: SEC の索引を確かめられなかった（status {status}{'・' + str(r.get('error')) if r.get('error') else ''}）。この回はそのままにして、次の回にもう一度確かめる")


if a.confirm_file:
    try:
        res = json.load(open(a.confirm_file, encoding="utf-8"))
        if isinstance(res, str):
            res = json.loads(res)   # JavaScript の JSON の文字をそのまま書いた物
    except Exception as e:
        say(f"NG 確かめの答えが読めない（{type(e).__name__}: {e}）"); sys.exit(2)
    if not isinstance(res, dict):
        say("NG 確かめの答えの形が違う（日付ごとの辞書ではない）"); sys.exit(2)
    for d in sorted(res, reverse=True):
        confirm_one(d, res[d] if isinstance(res[d], dict) else {})
    sys.exit(0)
if a.confirm:
    r = {"status": a.status, "n": a.n, "sig": a.sig, "missing": a.missing}
    confirm_one(a.confirm, r)
    sys.exit(0)
if not (a.date and a.sha and a.b64file):
    ap.error("--date --sha --b64file が要る（部分だけを確かめるときは --piece と --piece-sha、索引の確かめは --confirm-file）")

b64 = "".join(open(a.b64file, encoding="ascii").read().split())
try:
    raw = gzip.decompress(base64.b64decode(b64, validate=True))
except Exception as e:
    say(f"NG {a.date}: 解けない（{type(e).__name__}: {e}）"); sys.exit(2)
sha = hashlib.sha256(raw).hexdigest()
if sha != a.sha.strip().lower():
    say(f"NG {a.date}: sha256 が合わない {sha[:12]} != {a.sha[:12]}"); sys.exit(2)
try:
    day = json.loads(raw.decode("utf-8"))
except Exception as e:
    say(f"NG {a.date}: 中身の JSON が読めない（{type(e).__name__}）"); sys.exit(2)
if not isinstance(day, dict) or day.get("date") != a.date:
    say(f"NG {a.date}: 中身の日付が違う（{day.get('date') if isinstance(day, dict) else '?'}）。別の日の部分を写した可能性。保存しない"); sys.exit(2)
os.makedirs(a.out, exist_ok=True)
fp_old = os.path.join(a.out, f"{a.date}.json")


def limit_of(dy):
    return max(1, int((dy.get("filings_seen") or 0) * 0.01))


if os.path.exists(fp_old) and not a.force:
    old = load(fp_old)
    # 索引が出る前に取っただけの空の日（翌日 12:00 UTC より前に「索引なし」だった日）は、何でも置き換えてよい
    placeholder = bool(old) and bool(old.get("index_missing")) and str(old.get("fetched_at", "")) < settle_line(a.date)
    # 同じ届け出が 2 回続けて取れないだけなら（1 件か 1% まで）、その日は「取れるだけ取れた」として完全にする（何週間も止まらないため）
    ff_new, ff_old = set(day.get("fetch_failed") or []), set((old or {}).get("fetch_failed") or [])
    bad = len(ff_new) + len(day.get("gave_up") or []) + len(day.get("not_found") or [])
    if (old and not old.get("complete") and not day.get("complete") and ff_new and ff_new <= ff_old
            and not day.get("errors") and bad <= limit_of(day)):
        day["complete"] = True
        day["accepted_failures"] = sorted(ff_new)
        raw = json.dumps(day, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        say(f"ACCEPT {a.date}: 同じ届け出 {len(ff_new)} 件が 2 回続けて取れなかったので、取れるだけ取れた日として完全にした")
    has_bad = bool(day.get("fetch_failed") or day.get("not_found") or day.get("gave_up"))
    if old and not old.get("index_missing") and old.get("filings_seen", 0) > 0 and day.get("index_missing"):
        say(f"KEEP {a.date}: 前の日を残す（今回は索引が取れなかった）"); sys.exit(0)
    elif old and old.get("complete") and not placeholder and not day.get("complete") and day.get("errors"):
        # 今回は途中で止まった・索引を読めなかった → 前の完全な分を残す（次の回にもう一度）
        say(f"KEEP {a.date}: 前の完全な日を残す（今回は不完全: {','.join(day.get('errors'))}）"); sys.exit(0)
    elif old and old.get("complete") and old.get("filings_seen", 0) > 0 and not day.get("index_missing") and not day.get("filings_seen"):
        say(f"KEEP {a.date}: 前の日を残す（今回は索引に届け出が 0 件。索引でない中身を読んだ疑い）"); sys.exit(0)
    elif old and old.get("complete") and not placeholder and not day.get("complete") and not has_bad:
        # 数が合わない（索引の届け出が全部どれかに数えられていない）→ 前の完全な分を残す（次の回にもう一度）
        say(f"KEEP {a.date}: 前の完全な日を残す（今回は不完全: 数が合わない）"); sys.exit(0)
    elif old and old.get("complete") and not placeholder and has_bad and not day.get("errors"):
        # 前は完全・今回は取れなかった・SEC に無い・読めなかった届け出がある → 前の分を残すのでなく、今回の分に「前に読めていた届け出の行」を足して置く
        # （今回新しく取れた届け出を捨てないため。朝に読めた届け出が夜だけ SEC に無いと言われても、その行を消さないため。
        #   索引が同じなら、前に読めていた届け出は前の行が正しい。索引から消えた届け出は今回の一覧に無いので足さない）
        was_complete = bool(day.get("complete"))   # 今回が完全なら、前の行を足しても完全のまま
        same = bool(old.get("index_sig")) and old.get("index_sig") == day.get("index_sig")
        old_rows = {}
        for r in old.get("rows") or []:
            old_rows.setdefault(r.get("accession"), []).append(r)
        old_ff, old_gu, old_nf = set(old.get("fetch_failed") or []), set(old.get("gave_up") or []), set(old.get("not_found") or [])
        old_bad = old_ff | old_gu | old_nf
        nf, gu = set(day.get("not_found") or []), set(day.get("gave_up") or [])
        known, carried, still = [], [], []
        for acc in sorted(ff_new):
            if acc in old_nf:
                nf.add(acc)                 # 前の回も SEC に無かった
            elif acc in old_gu:
                gu.add(acc)                 # 前の回も読めなかった
            elif acc in old_rows or (same and acc not in old_ff):
                known.append(acc)           # 前の回に取れていた（買いの行が無い届け出も、索引が同じなら取れていた）
            elif acc in old_ff:
                carried.append(acc)         # 前の回に「取れるだけ取れた」として受け入れた物
                still.append(acc)
            else:
                still.append(acc)
        back = [acc for acc in sorted(nf | gu) if acc not in known and (acc in old_rows or (same and acc not in old_bad))]
        nf -= set(back)                     # 今回は SEC に無い・読めなかったが、前の回は読めていた
        gu -= set(back)
        for acc in known + back:
            day.setdefault("rows", []).extend(old_rows.get(acc, []))
        day["filings_parsed"] = (day.get("filings_parsed") or 0) + len(known) + len(back)
        day["fetch_failed"] = sorted(still)
        day["not_found"], day["gave_up"] = sorted(nf), sorted(gu)
        if carried:
            day["accepted_failures"] = sorted(carried)
        day["merged_from_previous"] = sorted(known + back)
        bad = len(day["fetch_failed"]) + len(day["gave_up"]) + len(day["not_found"])
        counted = (day.get("filings_parsed") or 0) + bad   # 索引の届け出が全部どれかに数えられているか（数が合わなければ完全にしない）
        day["complete"] = was_complete or (set(day["fetch_failed"]) <= set(carried) and bad <= limit_of(day) and counted >= (day.get("filings_seen") or 0))
        raw = json.dumps(day, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if known or back or ff_new:
            say(f"MERGE {a.date}: 今回取れなかった {len(ff_new)} 件のうち {len(known)} 件と、今回 SEC に無い・読めなかった物のうち {len(back)} 件は、"
                f"前に読めていた分を使った（索引 {'同じ' if same else '変わった・前の版'}・完全 {day['complete']}）")
day["saved_at"] = now_iso()   # 作業場所の時計（ボスのパソコンの時計のずれに左右されない）。CONFIRM では書かない
raw = json.dumps(day, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
with open(fp_old, "wb") as f:
    f.write(raw)
say(f"OK {a.date}: sha {sha[:12]} / 届け出 {day.get('filings_seen')} / 読めた {day.get('filings_parsed')} / 買いの行 {len(day.get('rows', []))} / 完全 {day.get('complete')} / 索引なし {bool(day.get('index_missing'))}")
say(f"SAVED {fp_old}")
