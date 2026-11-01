#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""毎日の更新の 1 回が「どの回か」と、報告の 2 行目を決める（モデルが文から選ばずに済むように）。
使い方:
  集計の前: python3 run_status.py --plan plan.json --log recv.log
  集計の後: python3 run_status.py --plan plan.json --log recv.log --build-exit 0 --build-log build.log --inject-exit 0
              [--incomplete-before incomplete_before.txt] [--prev-data prev_data.json --data out/data.json]
  確かめだけの回の後: python3 run_status.py --plan plan.json --log recv.log --inject-exit 0
  plan.json = plan_days.py の出力（最後の { で始まる行を読む）。
  recv.log = receive_day.py が自分で書く行（OK・MERGE・KEEP・ACCEPT・CONFIRM・DIFF・SKIP・NG・SAVED。手で tee しない）と、
    指示書が echo で足す次の印の行:
    COPYFAIL collector      … collect_edgar.js を正しく写せなかった（写し直しも合わなかった）
    ABORT <理由>            … 取り込みが止まった（abort か error）
    GIVEUP <日付> <理由>    … その日をあきらめた（待っても届かない・NG が 3 回続いた など）
    CUT <残りの日数>        … 35 分を過ぎたので、残りの日をやめた
出力: 1 行の JSON
  kind: "new" = 集計してページを新しくする / "confirm" = 集計せず、前のページと保存する日だけ出し直す / "nothing" = 出し直さない
  line2: 報告の 2 行目（集計の前の new と confirm では null。集計・ページづくりの後にもう一度呼ぶと決まる）
  drive_log: Drive に控えを作るか / new_days・shown・saved・confirmed・still_missing・diff・skipped・failed・left・notes
決まり:
  - 取る日 = 計画の days ＋ 確かめで DIFF になった日 ＋ 確かめから取り込みに移した日（写し間違い・呼び出しの失敗のとき。
    前に索引が無かった日（計画の missing）は、CONFIRM か DIFF にならなければ必ず移す）。取る日が OK にならなければ失敗
  - KEEP（前の分を残した）になった取る日も失敗（取り直せなかった。前に取った分のまま）
  - 前に完全に取れた日の確かめが SKIP か答えなしなら、失敗ではない（前の分がページにある。次の回にもう一度）
  - 索引がまだ無い平日（祝日でない）は、2 行目にその日を書く"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from plan_days import is_holiday   # 米国の祝日の暦（同じフォルダの plan_days.py）
except Exception:   # 無くても動く（祝日の見分けだけしない）
    def is_holiday(d):
        return False
from datetime import date, datetime, timedelta, timezone

ap = argparse.ArgumentParser()
ap.add_argument("--plan", required=True)
ap.add_argument("--log", required=True)
ap.add_argument("--build-exit", type=int, default=None)
ap.add_argument("--build-log", default=None)
ap.add_argument("--inject-exit", type=int, default=None)
ap.add_argument("--incomplete-before", default=None)
ap.add_argument("--prev-data", default=None, help="前のページの data.json（新しくページに出た日を数える）")
ap.add_argument("--data", default=None, help="この回に作った data.json")
ap.add_argument("--asof", default=None, help="試験用: 今の時刻（UTC。例 2026-12-12T12:50:00Z）")
a = ap.parse_args()

DATE = r"(\d{4}-\d{2}-\d{2})"


def md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}"


def mds(ds):
    return "・".join(md(d) for d in sorted(ds))


plan = {}
try:
    rows = [ln for ln in open(a.plan, encoding="utf-8").read().splitlines() if ln.strip().startswith("{")]
    plan = json.loads(rows[-1]) if rows else {}
except Exception:
    plan = {}
plan_days, plan_check = list(plan.get("days") or []), list(plan.get("check") or [])
plan_missing = set(plan.get("missing") or [])
rebuild, left = bool(plan.get("rebuild")), int(plan.get("left") or 0)
try:
    lines = [ln.strip() for ln in open(a.log, encoding="utf-8").read().splitlines() if ln.strip()]
except FileNotFoundError:
    lines = []

got = {k: set() for k in ("OK", "KEEP", "MERGE", "ACCEPT", "CONFIRM", "DIFF", "SKIP", "NG")}
ok_noidx, confirm_noidx = set(), set()
saved, giveup = set(), set()
copyfail, aborts, cut, notes = False, [], 0, []
for ln in lines:
    # 日付のすぐ後ろが「:」の行だけ（部分の確かめの行「OK 2026-10-27.b64.000: …」を日の結果と読まない）
    m = re.match(r"(OK|KEEP|MERGE|ACCEPT|CONFIRM|DIFF|SKIP|NG) " + DATE + r":", ln)
    if m:
        got[m.group(1)].add(m.group(2))
        if m.group(1) == "OK" and re.search(r"索引なし True", ln):
            ok_noidx.add(m.group(2))
        if m.group(1) == "CONFIRM" and "索引はまだ無い" in ln:
            confirm_noidx.add(m.group(2))
        continue
    m = re.match(r"SAVED \S*?" + DATE + r"\.json\s*$", ln)
    if m:
        saved.add(m.group(1))
        continue
    if ln.startswith("COPYFAIL collector"):
        copyfail = True
    elif ln.startswith("ABORT"):
        aborts.append(ln[5:].strip() or "取り込みが止まった")
    elif re.match(r"GIVEUP " + DATE, ln):
        m = re.match(r"GIVEUP " + DATE + r"\s*(.*)", ln)
        giveup.add(m.group(1))
        notes.append(f"{m.group(1)} をあきらめた（{m.group(2) or '理由なし'}）")
    elif re.match(r"CUT\b", ln):
        m = re.match(r"CUT\s+(\d+)", ln)
        cut = int(m.group(1)) if m else 0

ok, keep, diff, confirm = got["OK"], got["KEEP"], got["DIFF"], got["CONFIRM"]
checkset = set(plan_check)
attempted = ok | keep
# 確かめから取り込みに移した日: 結果が出た確かめの日で CONFIRM も DIFF も無い日と、前に索引が無かった日で CONFIRM も DIFF も無い日
moved = {d for d in checkset if d not in confirm and d not in diff and (d in attempted or d in plan_missing)}
full = sorted(set(plan_days) | diff | moved, reverse=True)       # まるごと取る日（新しい日が先。この順に取り込む）
unfinished = [d for d in full if d not in ok and d not in keep]
cut_days = unfinished[-cut:] if cut else []                      # 35 分で切った残り（古い日の方）は「次の回」
failed_get = set(unfinished) - set(cut_days)
if copyfail:
    failed_get |= set(full) - ok - keep
failed_get |= giveup - ok
failed_keep = (set(full) & keep) - ok                            # 取り直せなかった（前に取った分のまま）
failed = sorted(failed_get | failed_keep, reverse=True)
skipped = sorted(checkset - confirm - diff - moved, reverse=True)   # 前に完全に取れた日で、確かめられなかった日（次の回にもう一度）
new_days = sorted(ok - ok_noidx, reverse=True)                   # 届け出のある日を取れた
still_missing = sorted(d for d in (confirm_noidx | ok_noidx) if not is_holiday(date.fromisoformat(d)))
# 3 日後の 12:00 UTC を過ぎても索引が無いままの日は、この回で「SEC の休み」として決まる（もう確かめない）。plan_days.py の settled と同じ線
now = datetime.fromisoformat(a.asof.replace("Z", "+00:00")) if a.asof else datetime.now(timezone.utc)
closed = [d for d in still_missing if now.strftime("%Y-%m-%dT%H:%M") >= (date.fromisoformat(d) + timedelta(days=3)).isoformat() + "T12:00"]
waiting = [d for d in still_missing if d not in closed]
saved_l = sorted(saved, reverse=True)
rest = left + len(cut_days)

# 新しくページに出た日（前の回に保存したが、検査 NG などでページに出ていなかった日も数える）
shown = []
if a.prev_data and a.data:
    def have(fp):
        try:
            dd = json.load(open(fp, encoding="utf-8"))
            return {x["date"] for x in dd.get("daily") or [] if x.get("have") and not x.get("no_index")}
        except Exception:
            return None
    hp, hn = have(a.prev_data), have(a.data)
    if hp is not None and hn is not None:
        shown = sorted(hn - hp, reverse=True)

reasons = []
if copyfail:
    reasons.append("取り込みの道具を正しく写せなかった")
if aborts:
    reasons.append("SEC からの取り込みが途中で止まった（" + aborts[0][:40] + "）")
if failed_get and not copyfail:
    reasons.append(mds(failed_get) + " の分を取れなかった（次の回にもう一度）")
if failed_keep:
    reasons.append(mds(failed_keep) + " の分を取り直せなかった（前に取った分のまま。次の回にもう一度）")
wait_note = "。".join(x for x in (
    (mds(waiting) + " は SEC がまだ索引を出していない。休みか遅れで、次の回にもう一度確かめる") if waiting else "",
    (mds(closed) + " は SEC が索引を出さなかったので、休みとして扱う") if closed else "") if x)

kind = "new" if (new_days or rebuild) else ("confirm" if saved_l else "nothing")
line2 = None
built = a.build_exit is not None or a.inject_exit is not None
if kind == "nothing":
    line2 = ("更新できず: " + "、".join(reasons)) if reasons else "今日の分はもう更新済み" + (f"（{wait_note}）" if wait_note else "")
elif kind == "confirm" and built:
    if a.inject_exit not in (None, 0):
        line2 = "更新できず: ページを作れなかった（前に取った日の確かめも出し直していない）"
    elif reasons:
        line2 = "更新できず: " + "、".join(reasons) + "（前に取った日の確かめだけ保存した）"
    elif wait_note:
        line2 = f"今日の分はもう更新済み（{wait_note}）"
    else:
        line2 = "今日の分はもう更新済み（前に取った日を SEC で確かめた）"
elif kind == "new" and built:
    if a.build_exit == 0 and a.inject_exit in (None, 0):
        # かっこは 1 つにまとめる（例「更新した（新しい届け出の日は無し。…。ただし 11/10 は …）」）
        before = [d for d in shown if d not in new_days]
        items, warn = [], list(reasons)
        if new_days:
            base = "更新した"
            if before:
                items.append(f"前の回に取った {mds(before)} の分も出した")
        elif before:
            base = "更新した"
            items.append(f"前の回に取った {mds(before)} の分をページに出した")
        elif reasons:
            base, warn = "更新できず: " + "、".join(reasons), []
            items.append("ページは前のデータで作り直した")
        else:
            base = "更新した"
            items.append("新しい届け出の日は無し。日付と円の計算だけ新しくした")
        warn += ([f"残り {rest} 日は次の回"] if rest else []) + ([wait_note] if wait_note else [])
        if warn:
            items.append("ただし " + "。".join(warn))
        line2 = base + (f"（{'。'.join(items)}）" if items else "")
    elif a.build_exit == 2:
        blog = open(a.build_log, encoding="utf-8").read() if a.build_log else ""
        ngl = [ln.strip() for ln in blog.splitlines() if ln.strip().startswith("NG")]
        before = set(re.findall(DATE, open(a.incomplete_before, encoding="utf-8").read())) if a.incomplete_before else set()
        inc = set()
        for ln in ngl:
            if "不完全" in ln:
                inc |= set(re.findall(DATE, ln.split("不完全", 1)[1]))
        twice = sorted(inc & before)
        if copyfail or aborts:
            line2 = "更新できず: " + "、".join(reasons)
        elif twice:
            line2 = f"更新できず: {mds(twice)} の分が 2 回続けて取り切れない（SEC 側の問題の可能性）"
        elif any("欠け" in ln for ln in ngl) and (rest or failed):
            line2 = f"更新できず: 取り込みの途中（残り {max(rest, len(failed))} 日は次の回）"
        elif failed:
            line2 = "更新できず: " + "、".join(reasons)
        else:
            line2 = "更新できず: 自己検査が NG（" + (ngl[0][2:].strip()[:60] if ngl else "理由は控えに") + "）"
    else:
        line2 = "更新できず: 集計かページづくりが止まった（控えにエラーを写した）"
drive_log = kind == "new" or bool(reasons) or (line2 is not None and line2.startswith("更新できず"))

print(json.dumps({"kind": kind, "line2": line2, "drive_log": drive_log, "new_days": new_days, "shown": shown, "saved": saved_l,
                  "confirmed": sorted(confirm, reverse=True), "still_missing": still_missing, "closed": closed, "diff": sorted(diff, reverse=True),
                  "skipped": skipped, "failed": failed, "left": rest, "notes": notes}, ensure_ascii=False))
