# 毎日の更新の道具（browser/plan_days.py・browser/receive_day.py・add_profile.py）の決まりを確かめる（ネット無し）
import base64, gzip, hashlib, json, os, shutil, subprocess, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
T = os.path.join(HERE, "_out_daily")
shutil.rmtree(T, ignore_errors=True)
DAYS = os.path.join(T, "days")
os.makedirs(DAYS)
RECV = os.path.join(ROOT, "browser", "receive_day.py")


def put(d, **kw):
    # 既定: 翌々日の 06:50Z に取った完全な日（＝決まった日）
    day = {"date": d, "filings_seen": 300, "filings_parsed": 300, "rows": [], "complete": True, "fetched_at": (date.fromisoformat(d) + timedelta(days=2)).isoformat() + "T06:50:00Z"}
    day.update(kw)
    json.dump(day, open(os.path.join(DAYS, d + ".json"), "w", encoding="utf-8"))


def plan(asof, *extra):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "browser", "plan_days.py"), "--days", DAYS, "--window", "31", "--asof", asof, *extra], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr


def saved(d):
    return json.load(open(os.path.join(DAYS, d + ".json"), encoding="utf-8"))


# ---- plan_days.py ----
# 9/28〜10/26 の平日は全部「決まった日」。10/12 は SEC の休み（翌日 12:00 UTC より後に取って索引なし）
d = date(2026, 9, 28)
while d <= date(2026, 10, 26):
    if d.weekday() < 5:
        put(d.isoformat())
    d += timedelta(days=1)
put("2026-10-12", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-27T14:45:00Z")
put("2026-10-27", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-27T14:45:00Z")   # 索引が出る前に取っただけ
put("2026-10-14", complete=False)                                                                            # 不完全
put("2026-10-23", fetched_at="2026-10-24T06:50:00Z")                                                         # 翌日 12:00 より前に 1 回見ただけ（前の版。印なし）
put("2026-10-22", fetched_at="2026-10-23T06:50:00Z", index_sig="aaaaaaaaaaaaaaaa", index_n=300)              # 朝に 1 回見ただけ（印あり）→ 確かめるだけ
put("2026-10-21", fetched_at="2026-10-22T06:50:00Z", index_sig="bbbbbbbbbbbbbbbb", index_n=300, not_found=["X"])  # 印はあるが SEC に無い届け出がある → まるごと
put("2026-10-20", fetched_at="2026-10-21T06:50:00Z", index_sig="cccccccccccccccc", index_n=300, confirmed_at="2026-10-21T12:50:00Z")  # 夜に確かめ済み

# 10/28（水）13:47 バンコク = 06:47Z: 今日（10/28）は取らない
got, _ = plan("2026-10-28T06:47:00Z")
assert got["days"] == ["2026-10-23", "2026-10-21", "2026-10-14"] and got["check"] == ["2026-10-27", "2026-10-22"] and got["left"] == 0, got
assert got["missing"] == ["2026-10-27"], got          # 確かめる日のうち、前は索引が無かった日（確かめられなければ、その回にまるごと取る）
# 上限は「まるごと取る日」だけに効く（確かめるだけの日は軽いので切らない）
got, err = plan("2026-10-28T06:47:00Z", "--max", "1")
assert got["days"] == ["2026-10-23"] and got["check"] == ["2026-10-27", "2026-10-22"] and got["left"] == 2 and "残り 2 日" in err, (got, err)
# 前のページが今日（日本時間）の物でも、まだ決まっていない日があれば取る
prev = os.path.join(T, "prev.json")
json.dump({"generated_at_jst": "2026-10-28 15:50", "partial": False}, open(prev, "w"))
got, _ = plan("2026-10-28T12:47:00Z", "--prev", prev)
assert got["days"] == ["2026-10-23", "2026-10-21", "2026-10-14"] and got["check"] == ["2026-10-27", "2026-10-22"] and got["rebuild"] is False, got
# 全部決まった日になったあとの回 → days も check も空。前のページが今日なら rebuild false（何もしない）、昨日の物なら rebuild true
put("2026-10-27", fetched_at="2026-10-28T12:55:00Z")
put("2026-10-23")
put("2026-10-22", index_sig="aaaaaaaaaaaaaaaa", index_n=300, fetched_at="2026-10-23T06:50:00Z", confirmed_at="2026-10-23T12:51:00Z")
put("2026-10-21")
put("2026-10-14")
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got == {"days": [], "check": [], "missing": [], "left": 0, "rebuild": False}, got
# 前のページを作ったあとに取った日ファイルがある（検査 NG で日ファイルだけ保存した回のあと）→ 作り直す
json.dump({"generated_at_jst": "2026-10-28 15:50", "generated_at": "2026-10-28T06:50:00Z", "partial": False}, open(prev, "w"))
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got["rebuild"] is True and got["days"] == [] and got["check"] == [], got
json.dump({"generated_at_jst": "2026-10-28 21:56", "generated_at": "2026-10-28T12:56:00Z", "partial": False}, open(prev, "w"))
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got["rebuild"] is False, got
# 比べるのは作業場所の時計で書いた saved_at（ボスのパソコンの時計が進んでいて fetched_at がページより後でも、作り直さない）
put("2026-10-27", fetched_at="2026-10-28T13:20:00Z", saved_at="2026-10-28T12:55:30Z")
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got["rebuild"] is False, got
put("2026-10-27", fetched_at="2026-10-28T12:40:00Z", saved_at="2026-10-28T12:58:00Z")   # ページを作ったあとに置いた
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got["rebuild"] is True, got
put("2026-10-27", fetched_at="2026-10-28T12:55:00Z")
json.dump({"generated_at_jst": "2026-10-27 23:50", "partial": False}, open(prev, "w"))
got, _ = plan("2026-10-28T13:00:00Z", "--prev", prev)
assert got == {"days": [], "check": [], "missing": [], "left": 0, "rebuild": True}, got
put("2026-10-26", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-27T12:50:00Z")      # 祝日でない月曜に索引が無い
got, _ = plan("2026-10-29T06:47:00Z", "--prev", prev)
assert "2026-10-26" in got["check"] and "2026-10-26" in got["missing"], got
put("2026-10-26", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-27T12:50:00Z", confirmed_at="2026-10-29T12:50:00Z")
got, _ = plan("2026-10-30T06:47:00Z", "--prev", prev)
assert "2026-10-26" not in got["check"] + got["days"], got                     # 3 日後の 12:00 UTC を過ぎても無い → SEC が出さなかった日として決まった日
put("2026-10-26")
sys.path.insert(0, os.path.join(ROOT, "browser"))
import plan_days as PD
sys.path.insert(0, ROOT)
import build as BD
for y in (2026, 2027, 2028):
    assert PD.us_holidays(y) == BD.us_holidays(y), y
assert PD.is_holiday(date(2026, 10, 12)) and PD.is_holiday(date(2026, 11, 11)) and PD.is_holiday(date(2026, 11, 26)) and PD.is_holiday(date(2027, 7, 5)) and PD.is_holiday(date(2027, 12, 31))
assert not PD.is_holiday(date(2026, 10, 26)) and not PD.is_holiday(date(2026, 11, 27))
assert PD.settled(date(2026, 11, 11), {"complete": True, "index_missing": True, "fetched_at": "2026-11-12T06:50:00Z"})        # 祝日はすぐ
assert not PD.settled(date(2026, 11, 10), {"complete": True, "index_missing": True, "fetched_at": "2026-11-11T12:50:00Z"})   # 祝日でない
# 日ファイルが 1 つも無ければ、窓の平日を新しい順に（上限 8）。今日は入れない
shutil.rmtree(DAYS); os.makedirs(DAYS)
got, err = plan("2026-10-28T06:47:00Z", "--max", "8")
assert got["days"][0] == "2026-10-27" and len(got["days"]) == 8 and "2026-10-28" not in got["days"] and got["left"] == 14 and got["check"] == [], got


# ---- receive_day.py（取り込んだ日を置く）----
def pack(day):
    raw = json.dumps(day).encode("utf-8")
    fp = os.path.join(T, day["date"] + ".b64")
    open(fp, "w").write(base64.b64encode(gzip.compress(raw)).decode())
    return fp, hashlib.sha256(raw).hexdigest()


def recv(day):
    fp, sha = pack(day)
    p = subprocess.run([sys.executable, RECV, "--date", day["date"], "--sha", sha, "--b64file", fp, "--out", DAYS], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def confirm(d, status, n, sig, asof="2026-10-28T12:50:00Z"):
    args = [sys.executable, RECV, "--confirm", d, "--out", DAYS, "--asof", asof]
    if status is not None:
        args += ["--status", str(status)]
    if n is not None:
        args += ["--n", str(n)]
    if sig is not None:
        args += ["--sig", sig]
    p = subprocess.run(args, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


# 空の日（索引が出る前）は、不完全でも中身のある日で置き換える。書いた日ファイルは SAVED の行で分かる
put("2026-10-27", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-27T14:45:00Z")
code, out = recv({"date": "2026-10-27", "filings_seen": 300, "filings_parsed": 299, "rows": [{"x": 1}], "complete": False, "fetch_failed": ["A1"], "errors": [], "fetched_at": "2026-10-28T06:50:00Z"})
assert code == 0 and out.startswith("OK") and saved("2026-10-27")["complete"] is False and out.splitlines()[-1] == "SAVED " + os.path.join(DAYS, "2026-10-27.json"), out
# 同じ届け出（A1）が次の回も取れない → 取れるだけ取れた日として「完全」にする（ACCEPT のあとに OK と SAVED も出る）
code, out = recv({"date": "2026-10-27", "filings_seen": 300, "filings_parsed": 299, "rows": [{"x": 1}], "complete": False, "fetch_failed": ["A1"], "errors": [], "fetched_at": "2026-10-28T12:50:00Z"})
lines = out.splitlines()
assert code == 0 and lines[0].startswith("ACCEPT") and lines[1].startswith("OK") and lines[2].startswith("SAVED") and saved("2026-10-27")["complete"] is True and saved("2026-10-27")["accepted_failures"] == ["A1"], out
# ちがう届け出が取れないときは完全にしない
put("2026-10-22", complete=False, fetch_failed=["B1"])
code, out = recv({"date": "2026-10-22", "filings_seen": 300, "filings_parsed": 299, "rows": [], "complete": False, "fetch_failed": ["B2"], "errors": [], "fetched_at": "2026-10-28T12:50:00Z"})
assert code == 0 and "ACCEPT" not in out and saved("2026-10-22")["complete"] is False, out
# 完全な日は、不完全な日で上書きしない（印が無い前の版の日で、翌日 12:00 より前 → そのまま。SAVED は出ない）
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z")
code, out = recv({"date": "2026-10-26", "filings_seen": 300, "filings_parsed": 10, "rows": [], "complete": False, "fetched_at": "2026-10-27T08:50:00Z"})
assert code == 0 and out.startswith("KEEP") and "SAVED" not in out and "confirmed_at" not in saved("2026-10-26"), out
# (a) 朝は 007 が SEC に無かった（1% までなので完全）。夜は 007 が出たが 009 だけ取れない。索引は同じ
#     → 前の分を残すのでなく、今回の分（007 入り）に、前に取れていた 009 の行を足して置く。完全になる
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300, filings_parsed=299,
    rows=[{"accession": "A009", "line": 0, "x": 9}, {"accession": "A010", "line": 0, "x": 10}], not_found=["A007"])
code, out = recv({"date": "2026-10-26", "filings_seen": 300, "filings_parsed": 299, "rows": [{"accession": "A007", "line": 0, "x": 7}, {"accession": "A010", "line": 0, "x": 10}],
                  "complete": False, "fetch_failed": ["A009"], "gave_up": [], "not_found": [], "errors": [], "index_sig": "1111111111111111", "index_n": 300, "fetched_at": "2026-10-27T12:52:00Z"})
lines = out.splitlines()
sv = saved("2026-10-26")
assert code == 0 and lines[0].startswith("MERGE") and lines[1].startswith("OK") and lines[2].startswith("SAVED"), out
assert sv["complete"] is True and sv["fetch_failed"] == [] and sv["merged_from_previous"] == ["A009"] and sorted(r["accession"] for r in sv["rows"]) == ["A007", "A009", "A010"] and sv["filings_parsed"] == 300, sv
# (b) 索引が変わり（300 → 302）、増えた A031 が取れない（A032 は取れた）→ A032 を捨てずに不完全のまま置く（次の回の ACCEPT にまかせる）
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300, rows=[{"accession": "A010", "line": 0}])
code, out = recv({"date": "2026-10-26", "filings_seen": 302, "filings_parsed": 301, "rows": [{"accession": "A010", "line": 0}, {"accession": "A032", "line": 0}],
                  "complete": False, "fetch_failed": ["A031"], "errors": [], "index_sig": "3333333333333333", "index_n": 302, "fetched_at": "2026-10-27T12:52:00Z"})
sv = saved("2026-10-26")
assert code == 0 and out.startswith("MERGE") and sv["complete"] is False and sv["fetch_failed"] == ["A031"] and any(r["accession"] == "A032" for r in sv["rows"]), (out, sv)
code, out = recv({"date": "2026-10-26", "filings_seen": 302, "filings_parsed": 301, "rows": [{"accession": "A010", "line": 0}, {"accession": "A032", "line": 0}],
                  "complete": False, "fetch_failed": ["A031"], "errors": [], "index_sig": "3333333333333333", "index_n": 302, "fetched_at": "2026-10-28T06:52:00Z"})
assert code == 0 and out.startswith("ACCEPT") and saved("2026-10-26")["complete"] is True, out
# (c) 朝に読めた A011 が、夜は SEC に無い（not_found。1% までなので今回は完全）と言われた → A011 の行を消さず、前の分を使う
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300, filings_parsed=300,
    rows=[{"accession": "A011", "line": 0, "x": 11}, {"accession": "A010", "line": 0, "x": 10}])
code, out = recv({"date": "2026-10-26", "filings_seen": 300, "filings_parsed": 299, "rows": [{"accession": "A010", "line": 0, "x": 10}],
                  "complete": True, "fetch_failed": [], "gave_up": [], "not_found": ["A011"], "errors": [], "index_sig": "1111111111111111", "index_n": 300, "fetched_at": "2026-10-27T12:52:00Z"})
lines = out.splitlines()
sv = saved("2026-10-26")
assert code == 0 and lines[0].startswith("MERGE") and lines[1].startswith("OK") and lines[2].startswith("SAVED"), out
assert sv["complete"] is True and sv["not_found"] == [] and sv["merged_from_previous"] == ["A011"] and sorted(r["accession"] for r in sv["rows"]) == ["A010", "A011"] and sv["filings_parsed"] == 300, sv
# (d) 前も読めなかった届け出（前の gave_up）は、今回も読めなければそのまま（前の行は無い）
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300, filings_parsed=299, gave_up=["A012"], rows=[{"accession": "A010", "line": 0}])
code, out = recv({"date": "2026-10-26", "filings_seen": 300, "filings_parsed": 299, "rows": [{"accession": "A010", "line": 0}],
                  "complete": True, "fetch_failed": [], "gave_up": ["A012"], "not_found": [], "errors": [], "index_sig": "1111111111111111", "index_n": 300, "fetched_at": "2026-10-27T12:52:00Z"})
sv = saved("2026-10-26")
assert code == 0 and out.startswith("OK") and sv["gave_up"] == ["A012"] and sv["complete"] is True, (out, sv)
# 置いた日ファイルには、作業場所の時計で saved_at を書く
assert saved("2026-10-26").get("saved_at", "").endswith("Z"), saved("2026-10-26")
# 索引を読めなかった（errors に INDEX・届け出 0 件）→ 理由は「不完全: INDEX」（索引でない中身の疑い、とは書かない）
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300)
code, out = recv({"date": "2026-10-26", "filings_seen": 0, "filings_parsed": 0, "rows": [], "complete": False, "errors": ["INDEX"], "fetched_at": "2026-10-27T12:52:00Z"})
assert code == 0 and out.startswith("KEEP") and "INDEX" in out and "索引でない" not in out, out
# 途中で止まった（errors）今回の分では、前の完全な分を残す
put("2026-10-26", fetched_at="2026-10-27T06:50:00Z", index_sig="1111111111111111", index_n=300)
code, out = recv({"date": "2026-10-26", "filings_seen": 300, "filings_parsed": 120, "rows": [], "complete": False, "fetch_failed": [], "errors": ["NOT_REACHED_180"], "index_sig": "1111111111111111", "index_n": 300, "fetched_at": "2026-10-27T12:52:00Z"})
assert code == 0 and out.startswith("KEEP") and "SAVED" not in out and saved("2026-10-26")["filings_parsed"] == 300, out
# 索引でない中身（届け出 0 件・完全）で、届け出のあった完全な日を上書きしない
code, out = recv({"date": "2026-10-26", "filings_seen": 0, "filings_parsed": 0, "rows": [], "complete": True, "errors": [], "fetched_at": "2026-10-27T12:52:00Z"})
assert code == 0 and out.startswith("KEEP") and "SAVED" not in out and saved("2026-10-26")["filings_seen"] == 300, out
# 中身の日付が違う（別の日の部分を写した）→ NG（保存しない）
fp, sha = pack({"date": "2026-10-23", "rows": []})
p = subprocess.run([sys.executable, RECV, "--date", "2026-10-22", "--sha", sha, "--b64file", fp, "--out", DAYS], capture_output=True, text=True)
assert p.returncode == 2 and p.stdout.startswith("NG 2026-10-22") and "日付が違う" in p.stdout, (p.returncode, p.stdout, p.stderr)
# 索引があった日は「索引なし」で上書きしない
code, out = recv({"date": "2026-10-26", "filings_seen": 0, "filings_parsed": 0, "rows": [], "complete": True, "index_missing": True, "fetched_at": "2026-10-28T06:50:00Z"})
assert code == 0 and out.startswith("KEEP") and "SAVED" not in out, out
# 壊れた物は保存しない
fp, sha = pack({"date": "2026-10-23", "rows": []})
p = subprocess.run([sys.executable, RECV, "--date", "2026-10-23", "--sha", "0" * 64, "--b64file", fp, "--out", DAYS], capture_output=True, text=True)
assert p.returncode == 2 and p.stdout.startswith("NG") and "SAVED" not in p.stdout, p.stdout
# 1 つの部分だけを確かめる（写し間違いを、つなぐ前に見つける）
pc = os.path.join(T, "piece.000")
open(pc, "w").write("QUJDREVGRw==\n")
good = hashlib.sha256(b"QUJDREVGRw==").hexdigest()[:16]
p = subprocess.run([sys.executable, RECV, "--piece", pc, "--piece-sha", good], capture_output=True, text=True)
assert p.returncode == 0 and p.stdout.startswith("OK"), p.stdout
open(pc, "w").write("QUJDREVGR==")   # 1 字落ちた
p = subprocess.run([sys.executable, RECV, "--piece", pc, "--piece-sha", good], capture_output=True, text=True)
assert p.returncode == 2 and p.stdout.startswith("NG"), p.stdout

# ---- receive_day.py --confirm（索引の確かめ）----
put("2026-10-21", fetched_at="2026-10-22T06:50:00Z", index_sig="abcdef0123456789", index_n=312)
before = open(os.path.join(DAYS, "2026-10-21.json"), encoding="utf-8").read()
# 確かめられなかった（SEC に断られた・つながらない・--status を書き忘れた）→ SKIP。日ファイルはそのまま
assert confirm("2026-10-21", 403, None, None).startswith("SKIP")
assert confirm("2026-10-21", None, 312, "abcdef0123456789").startswith("SKIP")
assert open(os.path.join(DAYS, "2026-10-21.json"), encoding="utf-8").read() == before
# 索引が変わった（件数か印が違う。写し間違いも同じ扱い）→ DIFF（まるごと取り直す）。日ファイルはそのまま
assert confirm("2026-10-21", 200, 313, "abcdef0123456789").startswith("DIFF")
assert confirm("2026-10-21", 200, 312, "abcdef012345678a").startswith("DIFF")
assert confirm("2026-10-21", 200, 312, None).startswith("DIFF")
assert open(os.path.join(DAYS, "2026-10-21.json"), encoding="utf-8").read() == before
# 同じ → CONFIRM。confirmed_at を書き、SAVED を出す。plan では決まった日になる
out = confirm("2026-10-21", 200, 312, "ABCDEF0123456789")
assert out.startswith("CONFIRM") and out.splitlines()[-1] == "SAVED " + os.path.join(DAYS, "2026-10-21.json") and saved("2026-10-21")["confirmed_at"] == "2026-10-28T12:50:00Z", out
assert saved("2026-10-21")["fetched_at"] == "2026-10-22T06:50:00Z" and saved("2026-10-21")["index_sig"] == "abcdef0123456789"
# 確かめに向かない日 → DIFF
assert confirm("2026-10-20", 200, 312, "abcdef0123456789").startswith("DIFF")                       # 手元に無い
put("2026-10-19", index_sig="abcdef0123456789", index_n=312, not_found=["Z1"])
assert "SEC に無かった" in confirm("2026-10-19", 200, 312, "abcdef0123456789")                      # 取れなかった届け出がある
put("2026-10-16", complete=False, index_sig="abcdef0123456789", index_n=312)
assert confirm("2026-10-16", 200, 312, "abcdef0123456789").startswith("DIFF")                       # 不完全
put("2026-10-15")
assert "前の版" in confirm("2026-10-15", 200, 300, "abcdef0123456789")                              # 印が無い
# 翌日 12:00 UTC より前に確かめても、その日は決まらない（次の回にもう一度）
put("2026-10-27", fetched_at="2026-10-28T06:50:00Z", index_sig="9999999999999999", index_n=10)
out = confirm("2026-10-27", 200, 10, "9999999999999999", asof="2026-10-28T08:00:00Z")
assert out.startswith("CONFIRM") and "次の回にもう一度" in out, out
got, _ = plan("2026-10-28T08:01:00Z")
assert "2026-10-27" in got["check"] and "2026-10-21" not in got["check"] + got["days"], got

# --confirm-file: check_index.js の答えをそのまま（JSON の文字）書いたファイル。索引が無いままの日は時刻を書く、出た日は DIFF
put("2026-10-21", fetched_at="2026-10-22T06:50:00Z", index_sig="abcdef0123456789", index_n=312)
put("2026-10-20", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-21T06:50:00Z")
put("2026-10-19", filings_seen=0, filings_parsed=0, index_missing=True, fetched_at="2026-10-20T06:50:00Z")
cf = os.path.join(T, "check.json")
json.dump(json.dumps({"2026-10-21": {"status": 200, "n": 312, "sig": "abcdef0123456789"}, "2026-10-20": {"status": 403, "missing": True},
                      "2026-10-19": {"status": 200, "n": 5, "sig": "0000000000000000"}, "2026-10-16": {"status": 403, "missing": False}, "2026-10-15": {"status": -1, "error": "not an index"}}), open(cf, "w"))
p = subprocess.run([sys.executable, RECV, "--confirm-file", cf, "--out", DAYS, "--asof", "2026-10-28T12:50:00Z"], capture_output=True, text=True)
out = p.stdout
assert p.returncode == 0, p.stderr
assert "CONFIRM 2026-10-21" in out and "CONFIRM 2026-10-20" in out and "索引はまだ無い" in out and "DIFF 2026-10-19" in out and "SKIP 2026-10-16" in out and "SKIP 2026-10-15" in out, out
assert saved("2026-10-20")["confirmed_at"] == "2026-10-28T12:50:00Z" and saved("2026-10-20")["index_missing"] is True and "confirmed_at" not in saved("2026-10-19"), out
assert out.count("SAVED") == 2, out

# ---- add_profile.py（FMP の答えを足す）----
PROF = os.path.join(T, "profiles.json")
fmp = os.path.join(T, "1.json")
json.dump([], open(fmp, "w"))   # 見つからない
p = subprocess.run([sys.executable, os.path.join(ROOT, "add_profile.py"), "--file", PROF, "--json", fmp, "--asked", "SWZ"], capture_output=True, text=True)
assert p.returncode == 0, p.stderr
prof = json.load(open(PROF, encoding="utf-8"))
assert prof["SWZ"]["not_found"] is True and prof["SWZ"]["symbol"] == "SWZ", prof
json.dump([{"symbol": "BRK-B", "companyName": "Berkshire $(rm -rf /) `x`", "price": 480.1, "marketCap": 1e12, "exchange": "NYSE", "exchangeFullName": "New York Stock Exchange",
            "isEtf": False, "isFund": False, "isAdr": False, "country": "US", "currency": "USD", "ipoDate": "1996-05-09"}], open(fmp, "w"))
p = subprocess.run([sys.executable, os.path.join(ROOT, "add_profile.py"), "--file", PROF, "--json", fmp, "--asked", "BRK.B"], capture_output=True, text=True)
assert p.returncode == 0, p.stderr
prof = json.load(open(PROF, encoding="utf-8"))
assert prof["BRK.B"]["symbol"] == "BRK-B" and prof["BRK.B"]["name"].startswith("Berkshire $(rm") and prof["BRK.B"]["ipo_date"] == "1996-05-09" and "SWZ" in prof, prof
# FMP の上限の返事は「見つからない」と記録しない（次の回に聞き直す）
json.dump({"Error Message": "Limit Reach . Please upgrade your plan or visit our documentation for more details at https://site.financialmodelingprep.com/"}, open(fmp, "w"))
p = subprocess.run([sys.executable, os.path.join(ROOT, "add_profile.py"), "--file", PROF, "--json", fmp, "--asked", "ZZZ"], capture_output=True, text=True)
assert p.returncode == 2 and p.stdout.startswith("NG") and "ZZZ" not in json.load(open(PROF, encoding="utf-8")), (p.returncode, p.stdout)
print("daily tools test OK")
