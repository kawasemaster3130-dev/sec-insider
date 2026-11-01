# ブラウザ版（browser/collect_edgar.js）と Python 版（fetch_edgar.py）が、同じ届け出から同じ行を作るかを確かめる（ネット無し）
# Chromium（playwright）で偽の sec.gov を作り、collect_edgar.js を本当に動かして、出てきた日ファイルを Python 版の行と比べる
import asyncio, base64, gzip, hashlib, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_edgar as fe

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
xml = open(os.path.join(HERE, "rvi_form4.xml"), encoding="utf-8").read()

def submission(acc, x):
    return f"<SEC-DOCUMENT>{acc}.txt : 20261019\n<DOCUMENT>\n<TYPE>4\n<SEQUENCE>1\n<FILENAME>f.xml\n<TEXT>\n<XML>\n{x}\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>"

# 2 件目: 上場（IPO）の言葉が別の脚注にあり、長い備考（1,000 字を超える）と、買いの行の脚注（4,000 字を超える）がある
long_fn = "Shares held by the Fund. " + ("The Reporting Person disclaims beneficial ownership. " * 120)
ipo_xml = xml.replace("<remarks>test</remarks>", "<remarks>" + ("R" * 1500) + "</remarks>")
ipo_xml = ipo_xml.replace("</ownershipDocument>",
    "<footnotes><footnote id=\"F1\">" + long_fn + "</footnote>"
    "<footnote id=\"F2\">Each share of Series A Preferred Stock automatically converted upon the closing of the Issuer's initial public offering.</footnote></footnotes></ownershipDocument>")
# 買いの行に F1 を付ける（transactionPricePerShare の後ろに footnoteId）
ipo_xml = ipo_xml.replace("</transactionPricePerShare>", "<footnoteId id=\"F1\"/></transactionPricePerShare>", 1)
FILES = {
    "edgar/data/2108587/0000950103-26-016190.txt": submission("0000950103-26-016190", xml),
    "edgar/data/1219042/0001104659-26-116965.txt": submission("0001104659-26-116965", ipo_xml),
}
MASTER = ("Description: Master Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n--------------------\n"
          "2108587|Whitney Meredith|4|20261019|edgar/data/2108587/0000950103-26-016190.txt\n"
          "2085091|Robinhood Ventures Fund I|4|20261019|edgar/data/2108587/0000950103-26-016190.txt\n"
          "1219042|NELSEN ROBERT|4|20261019|edgar/data/1219042/0001104659-26-116965.txt\n"
          "777|Gone Filer|4|20261019|edgar/data/777/0000000777-26-000001.txt\n")   # 索引にあるのに SEC に無い届け出

# くせのある索引（CRLF・同じ届け出の 2 行・会社名の中の 0x85・4/A・.htm・8 けたでない日付・会社名の中の「|」・10-K）
QUIRK = (b"Description: Daily Index\r\n\r\nCIK|Company Name|Form Type|Date Filed|File Name\r\n-----\r\n"
         b"111|ALPHA INC|4|20261020|edgar/data/111/0000000111-26-000001.txt\r\n"
         b"222|ALPHA OWNER|4|20261020|edgar/data/111/0000000111-26-000001.txt\r\n"
         b"333|BETA \x85 CORP|4|20261020|edgar/data/333/0000000333-26-000002.txt\r\n"
         b"444|GAMMA|4/A|20261020|edgar/data/444/0000000444-26-000003.txt\r\n"
         b"555|DELTA|4|20261020|edgar/data/555/0000000555-26-000004-index.htm\r\n"
         b"666|EPSILON|4|2026-10-20|edgar/data/666/0000000666-26-000005.txt\r\n"
         b"777|ZETA|PIPE|4|20261020|edgar/data/777/0000000777-26-000006.txt\r\n"
         b"888|ETA|10-K|20261020|edgar/data/888/0000000888-26-000007.txt\r\n")
QUIRK_ACCS = ["0000000111-26-000001", "0000000333-26-000002", "0000000444-26-000003"]
NOT_INDEX = "<html><body>EDGAR is temporarily unavailable for maintenance.</body></html>"   # 200 で返る保守の画面

EXCHANGES = json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                        "data": [[2085091, "Robinhood Ventures Fund I", "RVI", "NYSE"], [999999, "Other Co", "OTHR", "OTC"], [888888, "No Exchange", "NOEX", None]]})


class FakeF(fe.Fetcher):
    def __init__(self): super().__init__(rps=1000)
    def get(self, url, retries=4):
        return EXCHANGES.encode("utf-8") if url.endswith("/files/company_tickers_exchange.json") else None


def python_rows():
    fe.EXMAP = None
    fe.load_exchanges(FakeF())
    out = []
    for line in MASTER.splitlines():
        p = line.split("|")
        if len(p) != 5 or not p[4].endswith(".txt") or p[4] not in FILES:
            continue
        meta = {"cik": p[0], "name": p[1], "form": p[2], "filed": "2026-10-19", "file": p[4]}
        acc = os.path.basename(p[4])[:-4]
        if any(r["accession"] == acc for r in out):
            continue
        parsed = fe.parse_form4(fe.extract_xml(FILES[p[4]]))
        out.extend(fe.to_rows(parsed, meta))
    return out

async def browser_day():
    from playwright.async_api import async_playwright
    js = open(os.path.join(ROOT, "browser", "collect_edgar.js"), encoding="utf-8").read()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        async def handle(route):
            u = route.request.url.split("www.sec.gov", 1)[1]
            if u == "/files/company_tickers_exchange.json":
                return await route.fulfill(status=200, body=EXCHANGES, content_type="application/json")
            if u.endswith("master.20261019.idx"):
                return await route.fulfill(status=200, body=MASTER, content_type="text/plain")
            if u.endswith("master.20261020.idx"):
                return await route.fulfill(status=200, body=QUIRK, content_type="text/plain")
            if u.endswith("master.20261021.idx"):
                return await route.fulfill(status=200, body=NOT_INDEX, content_type="text/html")
            for k, v in FILES.items():
                if u == "/Archives/" + k:
                    return await route.fulfill(status=200, body=v, content_type="text/plain")
            if u.startswith("/Archives/edgar/daily-index/") or u.endswith("0000000777-26-000001.txt"):
                return await route.fulfill(status=403, body="<Error><Code>AccessDenied</Code></Error>", content_type="application/xml")
            return await route.fulfill(status=200, body="<html></html>", content_type="text/html")
        await pg.route("https://www.sec.gov/**", handle)
        await pg.goto("https://www.sec.gov/Archives/edgar/daily-index/")
        loaded = await pg.evaluate(js)
        want_src = subprocess.run([sys.executable, os.path.join(ROOT, "browser", "receive_day.py"), "--js-hash", os.path.join(ROOT, "browser", "collect_edgar.js")], capture_output=True, text=True).stdout.strip()
        assert str(loaded).startswith("collector loaded collector-2.6 timer=") and str(loaded).endswith(" C=3 gap=130 part=6000 notes=4000/1000 selftest=ok src=" + want_src), (loaded, want_src)
        started = await pg.evaluate('window.__yk.start(["2026-10-19", "2026-10-12", "2026-10-20", "2026-10-21"])')
        assert str(started).startswith("started 4 days"), started
        for _ in range(100):
            st = await pg.evaluate("window.__yk.status()")
            if not st["running"]:
                break
            await pg.wait_for_timeout(100)
        assert st["error"] is None and not st["abort"], st
        days = {}
        for d in ("2026-10-19", "2026-10-12", "2026-10-20", "2026-10-21"):
            p0 = await pg.evaluate(f'window.__yk.part("{d}", 0)')
            parts = [p0["s"]]
            assert hashlib.sha256(p0["s"].encode()).hexdigest()[:16] == p0["h"], "部分の sha が合わない"
            for i in range(1, p0["n"]):
                pi = await pg.evaluate(f'window.__yk.part("{d}", {i})')
                assert hashlib.sha256(pi["s"].encode()).hexdigest()[:16] == pi["h"]
                parts.append(pi["s"])
            assert callable(getattr(pg, "evaluate"))
            assert await pg.evaluate("typeof window.__yk.sleep") == "function"
            raw = gzip.decompress(base64.b64decode("".join(parts)))
            assert hashlib.sha256(raw).hexdigest() == p0["sha"]
            days[d] = json.loads(raw)
        # 索引の確かめ（check_index.js）を同じページで動かし、取り込みの index_sig と比べる
        chk = open(os.path.join(ROOT, "browser", "check_index.js"), encoding="utf-8").read()
        want_chk = subprocess.run([sys.executable, os.path.join(ROOT, "browser", "receive_day.py"), "--js-hash", os.path.join(ROOT, "browser", "check_index.js")], capture_output=True, text=True).stdout.strip()
        assert await pg.evaluate(chk) == "check loaded check-2 src=" + want_chk
        # 1 字でも違う写しは src= が変わる（「4/A」→「4A」）
        bad = chk.replace('form === "4/A"', 'form === "4A"')
        assert bad != chk and await pg.evaluate(bad) != "check loaded check-2 src=" + want_chk
        await pg.evaluate(chk)
        days["_check"] = json.loads(await pg.evaluate('window.__ykCheck(["2026-10-19", "2026-10-12", "2026-10-20", "2026-10-21"])'))
        await b.close()
        return days

days = asyncio.run(browser_day())
d = days["2026-10-19"]
assert d["filings_seen"] == 3 and d["filings_parsed"] == 2 and d["complete"] is True and d["collector"] == "collector-2.6", {k: d[k] for k in ("filings_seen", "filings_parsed", "complete", "collector")}
assert all(r["issuerExchange"] == "NYSE" for r in d["rows"]), [r["issuerExchange"] for r in d["rows"]]
assert d["not_found"] == ["0000000777-26-000001"] and d["fetch_failed"] == [], (d["not_found"], d["fetch_failed"])
assert days["2026-10-12"]["index_missing"] is True and days["2026-10-12"]["complete"] is True
js_rows = sorted(d["rows"], key=lambda r: (r["accession"], r["line"]))
py_rows = sorted(python_rows(), key=lambda r: (r["accession"], r["line"]))
assert len(js_rows) == len(py_rows) == 2, (len(js_rows), len(py_rows))
for a, b in zip(js_rows, py_rows):
    for k in b:
        assert a.get(k) == b[k], (k, a.get(k), b[k])
    assert set(a) == set(b), set(a) ^ set(b)
# 索引の印: ブラウザの取り込み・check_index.js・Python 版（fetch_edgar.index_sig）が同じ答え
want_sig = hashlib.sha256("\n".join(sorted(["0000950103-26-016190", "0001104659-26-116965", "0000000777-26-000001"])).encode()).hexdigest()[:16]
class IdxF(fe.Fetcher):
    def __init__(self): super().__init__(rps=1000)
    def get(self, url, retries=4):
        return MASTER.encode("latin-1") if url.endswith("master.20261019.idx") else None
from datetime import date as _date
py_sig = fe.index_sig(fe.master_index(IdxF(), _date(2026, 10, 19)))
chk = days["_check"]
assert d["index_sig"] == want_sig == py_sig == chk["2026-10-19"]["sig"] and d["index_n"] == 3 == chk["2026-10-19"]["n"] and chk["2026-10-19"]["status"] == 200, (d.get("index_sig"), want_sig, py_sig, chk)
assert chk["2026-10-12"]["status"] == 403 and chk["2026-10-12"]["missing"] is True and "index_sig" not in days["2026-10-12"], (chk, days["2026-10-12"])
# くせのある索引: 3 つの道具が同じ 3 件・同じ印
class QF(fe.Fetcher):
    def __init__(self, body): super().__init__(rps=1000); self.body = body
    def get(self, url, retries=4): return self.body
q_py = fe.master_index(QF(QUIRK), _date(2026, 10, 20))
want_q = hashlib.sha256("\n".join(sorted(QUIRK_ACCS)).encode()).hexdigest()[:16]
assert sorted(os.path.basename(m["file"])[:-4] for m in q_py) == QUIRK_ACCS, [m["file"] for m in q_py]
assert days["2026-10-20"]["index_sig"] == fe.index_sig(q_py) == chk["2026-10-20"]["sig"] == want_q and days["2026-10-20"]["index_n"] == 3 == chk["2026-10-20"]["n"], (days["2026-10-20"].get("index_sig"), chk["2026-10-20"])
# 200 でも索引でない中身: 取り込みは INDEX の誤り（完全にしない）、確かめは status -1（SKIP）、Python 版は None（取れなかった）
assert days["2026-10-21"]["errors"] == ["INDEX"] and days["2026-10-21"]["complete"] is False and days["2026-10-21"]["filings_seen"] == 0, days["2026-10-21"]
assert chk["2026-10-21"]["status"] == -1 and fe.master_index(QF(NOT_INDEX.encode()), _date(2026, 10, 21)) is None, chk["2026-10-21"]
ipo_row = [r for r in js_rows if r["accession"] == "0001104659-26-116965"][0]
assert ipo_row["ipoInFiling"] is True and len(ipo_row["docNote"]) == 1000 and len(ipo_row["notes"]) == 4000, (ipo_row["ipoInFiling"], len(ipo_row["docNote"]), len(ipo_row["notes"]))
assert [r for r in js_rows if r["accession"] == "0000950103-26-016190"][0]["ipoInFiling"] is False
assert fe.exchange_of("0000999999", "X") == "OTC" and fe.exchange_of("1", "OTHR") == "OTC" and fe.exchange_of("888888", "NOEX") == "" and fe.exchange_of("5", "NONE") == ""
print("collector parity OK:", len(js_rows), "rows, fields", len(js_rows[0]))
