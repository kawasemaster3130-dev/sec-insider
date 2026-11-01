# browser/run_status.py（回の種類と報告の 2 行目）の決まりを確かめる（ネット無し）
import json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
T = os.path.join(HERE, "_out_status")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
TOOL = os.path.join(ROOT, "browser", "run_status.py")


def run(plan, log, build=None, inject=None, blog="", before="", prev=None, data=None, asof="2026-10-30T06:47:00Z"):
    pf, lf, bf, inc = (os.path.join(T, n) for n in ("plan.json", "recv.log", "build.log", "incomplete_before.txt"))
    open(pf, "w").write("まるごと取る日が 9 日あるので…\n" + json.dumps(plan) + "\n")
    open(lf, "w").write("\n".join(log) + "\n")
    open(bf, "w").write(blog)
    open(inc, "w").write(before)
    args = [sys.executable, TOOL, "--plan", pf, "--log", lf, "--asof", asof]
    if build is not None:
        args += ["--build-exit", str(build), "--build-log", bf, "--incomplete-before", inc]
    if inject is not None:
        args += ["--inject-exit", str(inject)]
    if prev is not None:
        pp, dp = os.path.join(T, "prev_data.json"), os.path.join(T, "data.json")
        json.dump({"daily": prev}, open(pp, "w")); json.dump({"daily": data}, open(dp, "w"))
        args += ["--prev-data", pp, "--data", dp]
    p = subprocess.run(args, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


P = lambda days=(), check=(), left=0, rebuild=False, missing=(): {"days": list(days), "check": list(check), "missing": list(missing), "left": left, "rebuild": rebuild}
OK = lambda d: [f"OK {d}: sha abc / 届け出 300 / 読めた 300 / 買いの行 50 / 完全 True / 索引なし False", f"SAVED /x/yk/days/{d}.json"]
CONF = lambda d: [f"CONFIRM {d}: 索引が前に取ったときと同じ（300 件）。", f"SAVED /x/yk/days/{d}.json"]

# ふつうの朝: 昨日をまるごと取った → 集計 → 更新した。Drive の控えを作る
r = run(P(["2026-10-27"]), OK("2026-10-27"))
assert r["kind"] == "new" and r["line2"] is None and r["new_days"] == ["2026-10-27"], r
r = run(P(["2026-10-27"]), OK("2026-10-27"), build=0, inject=0)
assert r["line2"] == "更新した" and r["drive_log"] is True and r["failed"] == [], r
# ふつうの夜: 確かめだけ → 集計しない → 前のページと日ファイルだけ。Drive の控えは作らない
r = run(P(check=["2026-10-27"]), CONF("2026-10-27"))
assert r["kind"] == "confirm" and r["saved"] == ["2026-10-27"], r
r = run(P(check=["2026-10-27"]), CONF("2026-10-27"), inject=0)
assert r["line2"] == "今日の分はもう更新済み（前に取った日を SEC で確かめた）" and r["drive_log"] is False, r
# 何もしない回
r = run(P(), [])
assert r["kind"] == "nothing" and r["line2"] == "今日の分はもう更新済み" and r["drive_log"] is False, r
# 確かめの呼び出しが失敗（行が無い）・SKIP だけ → 失敗ではない（次の回にもう一度）
r = run(P(check=["2026-10-27"]), [])
assert r["kind"] == "nothing" and r["line2"] == "今日の分はもう更新済み" and r["skipped"] == ["2026-10-27"], r
r = run(P(check=["2026-10-27"]), ["SKIP 2026-10-27: SEC の索引を確かめられなかった（status 403）。"])
assert r["line2"] == "今日の分はもう更新済み" and r["skipped"] == ["2026-10-27"], r
# 取り込みの道具を写せなかったが、確かめた日は保存した → 更新できず（確かめだけ保存した）
r = run(P(["2026-10-28"], check=["2026-10-27"]), CONF("2026-10-27") + ["COPYFAIL collector"], inject=0)
assert r["kind"] == "confirm" and r["line2"] == "更新できず: 取り込みの道具を正しく写せなかった（前に取った日の確かめだけ保存した）" and r["failed"] == ["2026-10-28"] and r["drive_log"], r
r = run(P(["2026-10-28"]), ["COPYFAIL collector"])
assert r["kind"] == "nothing" and r["line2"] == "更新できず: 取り込みの道具を正しく写せなかった" and r["drive_log"], r
# 1 日あきらめた（待っても届かない）が、ほかの日は取れて検査も通った → 更新した（ただし…）
r = run(P(["2026-10-27", "2026-10-26"]), OK("2026-10-27") + ["GIVEUP 2026-10-26 待っても届かなかった"], build=0, inject=0)
assert r["line2"] == "更新した（ただし 10/26 の分を取れなかった（次の回にもう一度））" and r["failed"] == ["2026-10-26"], r
# 取り込みが止まった（abort）・NG が 3 回続いた日（結果の行が無い）も失敗
r = run(P(["2026-10-27", "2026-10-26"]), OK("2026-10-27") + ["NG 2026-10-26: sha256 が合わない", "ABORT SEC に断られた回数が多い"], build=0, inject=0)
assert r["line2"].startswith("更新した（ただし SEC からの取り込みが途中で止まった") and "10/26 の分を取れなかった" in r["line2"], r
# 確かめで DIFF → まるごと取ったが KEEP（索引が変わったのに保存できない）→ 失敗
r = run(P(check=["2026-10-27"]), ["DIFF 2026-10-27: 索引が変わった（前 300 件 → 今 302 件）。", "KEEP 2026-10-27: 前の完全な日を残す（今回は不完全: NOT_REACHED_12）"])
assert r["kind"] == "nothing" and r["line2"] == "更新できず: 10/27 の分を取り直せなかった（前に取った分のまま。次の回にもう一度）" and r["failed"] == ["2026-10-27"], r
# DIFF → まるごと取って MERGE → OK → 更新した
r = run(P(check=["2026-10-27"]), ["DIFF 2026-10-27: 索引が変わった", "MERGE 2026-10-27: …", ] + OK("2026-10-27"), build=0, inject=0)
assert r["line2"] == "更新した" and r["new_days"] == ["2026-10-27"], r
# 35 分で切った（残り 2 日は次の回）→ 失敗ではない
r = run(P(["2026-10-27", "2026-10-26", "2026-10-23"]), OK("2026-10-27") + ["CUT 2"], build=0, inject=0)
assert r["line2"] == "更新した（ただし 残り 2 日は次の回）" and r["failed"] == [] and r["left"] == 2, r
# 上限で次の回に回した日があり、検査が「欠け」で NG → 取り込みの途中
r = run(P(["2026-10-27"], left=5), OK("2026-10-27"), build=2, inject=0, blog="  NG  期間の平日の分が全部そろっている（2日前まで）  欠け 2026-10-01,2026-10-02\n")
assert r["line2"] == "更新できず: 取り込みの途中（残り 5 日は次の回）" and r["drive_log"], r
# 2 回続けて不完全 → SEC 側の問題の可能性（欠けより先）
r = run(P(["2026-10-27", "2026-10-14"], left=1), OK("2026-10-27") + OK("2026-10-14"), build=2, inject=0,
        blog="  NG  期間の平日の分が全部そろっている（2日前まで）  欠け 2026-10-01 不完全 2026-10-14\n", before="不完全だった日: ['2026-10-14']\n")
assert r["line2"] == "更新できず: 10/14 の分が 2 回続けて取り切れない（SEC 側の問題の可能性）", r
# ページを作り直すだけ（取る日なし・rebuild）→ 更新した（新しい届け出の日は無し…）
r = run(P(rebuild=True), [], build=0, inject=0)
assert r["kind"] == "new" and r["line2"] == "更新した（新しい届け出の日は無し。日付と円の計算だけ新しくした）", r
# 集計が止まった（出口 1）
r = run(P(["2026-10-27"]), OK("2026-10-27"), build=1, inject=None)
assert r["line2"] == "更新できず: 集計かページづくりが止まった（控えにエラーを写した）", r
# 確かめだけの回でページを作れなかった
r = run(P(check=["2026-10-27"]), CONF("2026-10-27"), inject=1)
assert r["line2"].startswith("更新できず: ページを作れなかった"), r
# ---- 監査 5 回目の場面 ----
NOIDX = lambda d: [f"OK {d}: sha abc / 届け出 0 / 読めた 0 / 買いの行 0 / 完全 True / 索引なし True", f"SAVED /x/yk/days/{d}.json"]
# 1-1 前は索引が無かった日の確かめが失敗し（答えなし）、その日を取っていない → 失敗（「更新済み」にしない）
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), [])
assert r["kind"] == "nothing" and r["line2"] == "更新できず: 10/29 の分を取れなかった（次の回にもう一度）" and r["failed"] == ["2026-10-29"] and r["skipped"] == [], r
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), ["SKIP 2026-10-29: SEC の索引を確かめられなかった（status 0）。"])
assert r["line2"].startswith("更新できず: 10/29 の分を取れなかった") and r["drive_log"], r
# 確かめが SKIP でも、指示書どおりまるごと取りに移して取れた → 更新した（「確かめられなかった」に出さない）
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), ["SKIP 2026-10-29: SEC の索引を確かめられなかった（status 0）。"] + OK("2026-10-29"), build=0, inject=0)
assert r["line2"] == "更新した" and r["new_days"] == ["2026-10-29"] and r["skipped"] == [] and r["failed"] == [], r
# 移して取ったが、まだ索引が無い → 確かめだけの回。2 行目にその日を書く
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), NOIDX("2026-10-29"))
assert r["kind"] == "confirm" and r["still_missing"] == ["2026-10-29"], r
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), NOIDX("2026-10-29"), inject=0)
assert r["line2"] == "今日の分はもう更新済み（10/29 は SEC がまだ索引を出していない。休みか遅れで、次の回にもう一度確かめる）", r
# 2-3 確かめで「索引はまだ無い」→ 同じ 2 行目
r = run(P(check=["2026-10-29"], missing=["2026-10-29"]), ["CONFIRM 2026-10-29: 索引はまだ無い（SEC の休みの日か、まだ出していない日）。確かめた時刻を書いた", "SAVED /x/yk/days/2026-10-29.json"], inject=0)
assert r["kind"] == "confirm" and r["line2"].startswith("今日の分はもう更新済み（10/29 は SEC がまだ索引を出していない"), r
# 米国の祝日（11/11）の「索引なし」は書かない
r = run(P(["2026-11-11"]), NOIDX("2026-11-11"), inject=0)
assert r["still_missing"] == [] and r["line2"] == "今日の分はもう更新済み（前に取った日を SEC で確かめた）", r
# 2-2 前の回（検査 NG）に保存した日が、この回で初めてページに出た → そのことを書く
prev = [{"date": "2026-12-09", "have": True, "no_index": True}, {"date": "2026-12-10", "have": False, "no_index": True}]
data = [{"date": "2026-12-09", "have": True, "no_index": True}, {"date": "2026-12-10", "have": True, "no_index": False}, {"date": "2026-12-11", "have": True, "no_index": False}]
r = run(P(rebuild=True), [], build=0, inject=0, prev=prev, data=data)
assert r["line2"] == "更新した（前の回に取った 12/10・12/11 の分をページに出した）" and r["shown"] == ["2026-12-11", "2026-12-10"], r
# 2-6 印の行（ABORT）が無くても、取る日が KEEP（途中で止まった）なら失敗
r = run(P(["2026-10-27"]), ["KEEP 2026-10-27: 前の完全な日を残す（今回は不完全: NOT_REACHED_120）"])
assert r["kind"] == "nothing" and r["line2"] == "更新できず: 10/27 の分を取り直せなかった（前に取った分のまま。次の回にもう一度）", r
# 2-7 部分の確かめの行（OK 日付.b64.000:）は、日の結果に数えない
r = run(P(["2026-10-27"]), ["OK 2026-10-27.b64.000: 6000 字", "OK 2026-10-27.b64.001: 2100 字", "NG 2026-10-27: sha256 が合わない"])
assert r["new_days"] == [] and r["failed"] == ["2026-10-27"] and r["kind"] == "nothing", r
# 2-8 確かめの道具を写せず、確かめる日をまるごと取りに移して取れた → 「確かめられなかった」に出さない
r = run(P(check=["2026-10-26"]), OK("2026-10-26"), build=0, inject=0)
assert r["skipped"] == [] and r["new_days"] == ["2026-10-26"] and r["line2"] == "更新した", r
# 監査 6 回目: 前の回の日と新しい日が一緒に出た回・かっこを 1 つにまとめる・3 日たって休みと決まった日
r = run(P(["2026-12-14"]), OK("2026-12-14"), build=0, inject=0, prev=prev, data=data + [{"date": "2026-12-14", "have": True, "no_index": False}], asof="2026-12-15T06:47:00Z")
assert r["line2"] == "更新した（前の回に取った 12/10・12/11 の分も出した）", r
r = run(P(check=["2026-11-10"], missing=["2026-11-10"], rebuild=True), ["CONFIRM 2026-11-10: 索引はまだ無い（SEC の休みの日か、まだ出していない日）。確かめた時刻を書いた", "SAVED /x/yk/days/2026-11-10.json"], build=0, inject=0, asof="2026-11-11T06:47:00Z")
assert r["line2"] == "更新した（新しい届け出の日は無し。日付と円の計算だけ新しくした。ただし 11/10 は SEC がまだ索引を出していない。休みか遅れで、次の回にもう一度確かめる）", r
r = run(P(check=["2026-12-09"], missing=["2026-12-09"]), ["CONFIRM 2026-12-09: 索引はまだ無い（SEC の休みの日か、まだ出していない日）。確かめた時刻を書いた", "SAVED /x/yk/days/2026-12-09.json"], inject=0, asof="2026-12-12T12:50:00Z")
assert r["closed"] == ["2026-12-09"] and r["line2"] == "今日の分はもう更新済み（12/9 は SEC が索引を出さなかったので、休みとして扱う）", r
print("run status test OK")
