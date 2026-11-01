#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""毎日の更新で、ブラウザに渡す日付を決める（v6）。
  決まった日 = 完全に取れていて、その日の翌日 12:00（UTC。米国東部の朝 7〜8 時）より後に取ったか確かめた日。
    索引が無い日は、米国の祝日（SEC の休み）ならすぐ決まった日。祝日でない平日は、3 日後の 12:00 UTC を過ぎても無いままなら決まった日
    （それまでは毎回、索引が出たかを確かめる。SEC が遅れて出すことがあるため）
  まだ決まっていない日のうち:
    check（確かめるだけの日）= 前に完全に取れていて、取れなかった届け出が 1 件も無く、索引の印（index_sig）がある日と、索引が無かった日。
      ブラウザで索引を 1 回だけ読み（check_index.js）、印が同じなら取り直さずに決まった日にする（receive_day.py --confirm-file）。
      索引が出ていれば DIFF になり、その日をまるごと取る
    days（まるごと取る日）= それ以外（まだ無い日・不完全な日・前の版で取った日）。新しい日が先。--max で切る（残りは次の回）
  「今日」（米国東部）は取らない（索引は夜にならないと出ない）
  --prev に前回の data.json を渡すと、ページを作り直す要があるか（前のページが今日＝日本時間の物でない、
    または前のページを作ったあとに取った日ファイルがある）も出す
使い方: python3 plan_days.py --days days --window 31 --max 8 [--prev prev_data.json] [--asof 2026-10-28T06:47:00Z]
出力: 1 行の JSON {"days": [日付…], "check": [日付…], "missing": [日付…], "left": 次の回に回した日数, "rebuild": true/false}
  missing = check のうち、前は索引が無かった日（祝日でない平日）。確かめで CONFIRM（索引はまだ無い）か DIFF にならなかったら
    （呼び出しの失敗・SKIP・写し間違い）、その回にまるごと取る日に足す（取らないまま「更新済み」にしないため）
  days も check も空で rebuild が false → 何もしなくてよい（今日の分はもう更新済み）
  days も check も空で rebuild が true → 取り込みは要らないが、集計とページの出し直しは要る"""
import argparse, json, os, sys
from datetime import date, datetime, timedelta, timezone


def us_holidays(year):
    """米国の連邦の祝日（振替を含む）。SEC（EDGAR）が休む日。build.py と同じ"""
    def nth(month, weekday, n):
        d = date(year, month, 1)
        return d + timedelta(days=(weekday - d.weekday()) % 7) + timedelta(weeks=n - 1)

    def last(month, weekday):
        d = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    def obs(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d

    hs = {obs(date(year, 1, 1)), nth(1, 0, 3), nth(2, 0, 3), last(5, 0), obs(date(year, 6, 19)), obs(date(year, 7, 4)),
          nth(9, 0, 1), nth(10, 0, 2), obs(date(year, 11, 11)), nth(11, 3, 4), obs(date(year, 12, 25))}
    if date(year + 1, 1, 1).weekday() == 5:
        hs.add(date(year, 12, 31))   # 次の年の元日が土曜なら、その前の金曜（12/31）が休み
    return hs


def is_holiday(d):
    return d in us_holidays(d.year)


def settled(d, day):
    """決まった日か（receive_day.py の settle_line・build.py の検査と同じ考え方）"""
    if not day or not day.get("complete"):
        return False
    t = max(str(day.get("fetched_at", "")), str(day.get("confirmed_at", "")))
    if day.get("index_missing"):
        return is_holiday(d) or t >= (d + timedelta(days=3)).isoformat() + "T12:00"
    return t >= (d + timedelta(days=1)).isoformat() + "T12:00"


def checkable(day):
    """取り直さずに、索引の確かめだけでよい日（receive_day.py --confirm-file が CONFIRM か DIFF にできる日）"""
    if not day or not day.get("complete"):
        return False
    if day.get("index_missing"):
        return True
    return bool(day.get("index_sig")) and not (day.get("fetch_failed") or day.get("gave_up") or day.get("not_found") or day.get("errors"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="days")
    ap.add_argument("--window", type=int, default=31)
    ap.add_argument("--max", type=int, default=0, help="1 回にまるごと取る日数の上限（0 は上限なし）")
    ap.add_argument("--prev", default=None)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    now = datetime.now(timezone.utc) if not a.asof else datetime.fromisoformat(a.asof.replace("Z", "+00:00"))
    today_et = (now - timedelta(hours=5)).date()   # 米国東部の日付（夏も冬も安全側）
    today_jst = (now + timedelta(hours=9)).date()

    def day_file(d):
        fp = os.path.join(a.days, d.isoformat() + ".json")
        if not os.path.exists(fp):
            return None
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return None

    want, check = [], []
    d = today_et - timedelta(days=1)
    while d >= today_et - timedelta(days=a.window):
        if d.weekday() < 5:
            day = day_file(d)
            if not settled(d, day):
                (check if checkable(day) else want).append(d.isoformat())
        d -= timedelta(days=1)
    left = 0
    if a.max and len(want) > a.max:
        left = len(want) - a.max
        print(f"まるごと取る日が {len(want)} 日あるので、新しい {a.max} 日だけにした（残り {left} 日は次の回）", file=sys.stderr)
        want = want[: a.max]

    rebuild = True
    if a.prev and os.path.exists(a.prev):
        try:
            prev = json.load(open(a.prev, encoding="utf-8"))
            rebuild = not (str(prev.get("generated_at_jst", ""))[:10] == today_jst.isoformat() and not prev.get("partial"))
            gen = str(prev.get("generated_at") or "")
            if not rebuild and gen:
                # 前のページを作ったあとに置いた日ファイルがある（検査 NG などで、日ファイルだけ保存した回のあと）→ 作り直す。
                # 置いた時刻 saved_at（receive_day.py が作業場所の時計で書く）と、ページを作った時刻 generated_at（作業場所の時計）を比べる。
                # saved_at が無い前の版の日ファイルは fetched_at（ボスのパソコンの時計）で比べる
                d = today_et - timedelta(days=1)
                while d >= today_et - timedelta(days=a.window) and not rebuild:
                    dy = day_file(d) or {}
                    if str(dy.get("saved_at") or dy.get("fetched_at") or "") > gen:
                        rebuild = True
                    d -= timedelta(days=1)
        except Exception:
            rebuild = True
    missing = [x for x in check if (day_file(date.fromisoformat(x)) or {}).get("index_missing")]
    print(json.dumps({"days": want, "check": check, "missing": missing, "left": left, "rebuild": rebuild}))
