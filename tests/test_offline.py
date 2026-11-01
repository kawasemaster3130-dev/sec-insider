# fetch_edgar.py の流れを、ネット無しで通す試験（偽の通信係を差し込む）
import sys, os, json, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_edgar as fe

xml = open(os.path.join(os.path.dirname(__file__), 'rvi_form4.xml'), encoding='utf-8').read()
sub = ("<SEC-DOCUMENT>0000950103-26-016190.txt : 20261026\n<DOCUMENT>\n<TYPE>4\n<SEQUENCE>1\n<FILENAME>dp255534_4-whitney.xml\n<TEXT>\n<XML>\n" + xml + "\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>")
master = ("Description: Master Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n--------------------\n"
          "2108587|Whitney Meredith|4|20261026|edgar/data/2108587/0000950103-26-016190.txt\n"
          "2085091|Robinhood Ventures Fund I|4|20261026|edgar/data/2085091/0000950103-26-016190.txt\n"
          "320193|Apple Inc.|10-K|20261026|edgar/data/320193/0000320193-26-000104.txt\n"
          "999|Broken Filer|4|20261026|edgar/data/999/0000000999-26-000001.txt\n")

class FakeFetcher(fe.Fetcher):
    def __init__(self): super().__init__(rps=1000); self.calls=[]
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261026.idx'): return master.encode('latin-1')
        if url.endswith('master.20261027.idx'): return None  # 休日/未公開
        if '0000950103-26-016190.txt' in url: return sub.encode('utf-8')
        if '0000000999-26-000001.txt' in url: return b"<SEC-DOCUMENT>no xml here</SEC-DOCUMENT>"
        return None

out = os.path.join(os.path.dirname(__file__), '_out_days'); shutil.rmtree(out, ignore_errors=True)
fe.UA = "test test@example.com"
f = FakeFetcher()
from datetime import date
idx = fe.master_index(f, date(2026,10,26))
assert len(idx) == 2, idx  # 重複(同じ届け出が2行)は1つに、10-K は除く、壊れた物は残る
sys.argv = ['fetch_edgar.py','--from','2026-10-26','--to','2026-10-27','--out',out]
# main() は Fetcher を自分で作るので、差し替える
fe.Fetcher = lambda rps=8.0, max_fail=30: f
fe.main()
d = json.load(open(os.path.join(out,'2026-10-26.json'), encoding='utf-8'))
# 読めない物が 1 件まだ残っている（次の回に取り直す）ので、この日は「不完全」（ブラウザ版と同じ決まり）
assert d['filings_seen']==2 and d['filings_parsed']==1 and len(d['rows'])==1 and d['errors']==['0000000999-26-000001'] and d['complete'] is False and d['tries']=={'0000000999-26-000001':1}, d
assert d['collector'] == 'fetch_edgar-2.8' and d['index_n'] == 2 and len(d['index_sig']) == 16 and d['rows'][0]['ipoInFiling'] is False and d['rows'][0]['docNote'] == 'test' and d['rows'][0]['issuerExchange'] == '', d['rows'][0]
d2 = json.load(open(os.path.join(out,'2026-10-27.json'), encoding='utf-8'))
assert d2['filings_seen']==0 and d2['complete'] is True
# 2回目・3回目: 既に取った届け出は取りに行かない。読めない物は3回目であきらめて記録する
n0=len(f.calls); fe.main(); fe.main()
assert not any('0000950103-26-016190.txt' in u for u in f.calls[n0:]), '取り直してしまった'
assert sum('0000000999-26-000001.txt' in u for u in f.calls[n0:])==2, '読めない物を取り直していない'
d = json.load(open(os.path.join(out,'2026-10-26.json'), encoding='utf-8'))
assert d['gave_up']==['0000000999-26-000001'] and d['errors']==[] and '0000000999-26-000001' in d['accessions'], d
assert d['complete'] is True, '3 回であきらめた 1 件だけなら完全（1% か最低 1 件まで）'
# 上場（IPO）の言葉が、買いの行ではなく同じ届け出の別の脚注にあるとき → ipoInFiling
ipo_xml = xml.replace('</ownershipDocument>', '<footnotes><footnote id="F9">Each share of Series A Preferred Stock automatically converted into Common Stock upon the closing of the Issuer\'s initial public offering.</footnote></footnotes></ownershipDocument>') if '<footnotes>' not in xml else xml.replace('</footnotes>', '<footnote id="F9">Each share of Series A Preferred Stock automatically converted upon the closing of the Issuer\'s initial public offering.</footnote></footnotes>')
rows = fe.parse_form4(ipo_xml)
assert rows and all(r['_ipo'] for r in rows), rows
long_note = 'x' * 5000
cap_rows = fe.parse_form4(xml.replace('<remarks>test</remarks>', '<remarks>' + long_note + '</remarks>'))
assert cap_rows and len(cap_rows[0]['_doc_note']) == fe.DOCNOTE_MAX, len(cap_rows[0]['_doc_note'])
n1=len(f.calls); fe.main()
assert not any('0000000999-26-000001.txt' in u for u in f.calls[n1:]), 'あきらめた物を取りに行った'
# 索引にあるのに SEC に無い届け出（404）→ not_found に数え、取り直さない。1 件だけなら「完全」
master2 = master.replace("20261026", "20261020") + "777|Gone Filer|4|20261020|edgar/data/777/0000000777-26-000001.txt\n"
class Fake2(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261020.idx'): return master2.encode('latin-1')
        if url.endswith('master.20261021.idx'): return fe.FAILED   # 通信の失敗（休日と決めつけない）
        if '0000000777-26-000001.txt' in url: return None
        return FakeFetcher.get(self, url, retries)
f2 = Fake2(); fe.Fetcher = lambda rps=8.0, max_fail=30: f2
sys.argv = ['fetch_edgar.py','--from','2026-10-20','--to','2026-10-21','--out',out]
fe.main()
d3 = json.load(open(os.path.join(out,'2026-10-20.json'), encoding='utf-8'))
assert d3['not_found'] == ['0000000777-26-000001'] and d3['errors'] == ['0000000999-26-000001'] and d3['complete'] is False, d3
d4 = json.load(open(os.path.join(out,'2026-10-21.json'), encoding='utf-8'))
assert d4['complete'] is False and 'INDEX' in d4['errors'] and not d4.get('index_missing'), d4
# 前に取れた日（10/26）の索引が取れなくても、前の日ファイルを壊さない
before = open(os.path.join(out,'2026-10-26.json'), encoding='utf-8').read()
class Fake3(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        return fe.FAILED if url.endswith('master.20261026.idx') else None
f3 = Fake3(); fe.Fetcher = lambda rps=8.0, max_fail=30: f3
sys.argv = ['fetch_edgar.py','--from','2026-10-26','--to','2026-10-26','--out',out]
fe.main()
assert open(os.path.join(out,'2026-10-26.json'), encoding='utf-8').read() == before, '索引の失敗で前の日ファイルが変わった'
# 前に索引があった日を「索引なし」（None）で上書きしない
class Fake4(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url); return None
f4 = Fake4(); fe.Fetcher = lambda rps=8.0, max_fail=30: f4
fe.main()
assert open(os.path.join(out,'2026-10-26.json'), encoding='utf-8').read() == before, '索引なしで前の日ファイルを上書きした'
# 前の回に「索引なし」と書いた日（index_missing）に、あとで索引が出た → 中身を取り、印を消す（消さないと build.py がその日を「索引なし」のまま扱う）
d27 = json.load(open(os.path.join(out,'2026-10-27.json'), encoding='utf-8'))
assert d27.get('index_missing') is True, d27
class Fake5(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261027.idx'): return master.replace("20261026", "20261027").encode('latin-1')
        return FakeFetcher.get(self, url, retries)
f5 = Fake5(); fe.Fetcher = lambda rps=8.0, max_fail=30: f5
sys.argv = ['fetch_edgar.py','--from','2026-10-27','--to','2026-10-27','--out',out]
fe.main()
d27 = json.load(open(os.path.join(out,'2026-10-27.json'), encoding='utf-8'))
assert 'index_missing' not in d27 and d27['filings_seen'] == 2 and len(d27['rows']) == 1, d27
# ブラウザ版（collector-2.1。accessions の一覧が無い）の日ファイルが置いてあっても止まらず、その日を最初から取り直す（行が二重にならない）
browser_day = {"date": "2026-10-26", "filings_seen": 2, "filings_parsed": 2, "rows": [{"dummy": 1}], "complete": True, "errors": [], "collector": "collector-2.1", "accessions_count": 2, "fetched_at": "2026-10-27T00:00:00Z"}
json.dump(browser_day, open(os.path.join(out,'2026-10-26.json'), 'w', encoding='utf-8'))
fb = FakeFetcher(); fe.Fetcher = lambda rps=8.0, max_fail=30: fb
sys.argv = ['fetch_edgar.py','--from','2026-10-26','--to','2026-10-26','--out',out]
fe.main()
d = json.load(open(os.path.join(out,'2026-10-26.json'), encoding='utf-8'))
assert d['collector'] == 'fetch_edgar-2.8' and len(d['rows']) == 1 and 'dummy' not in d['rows'][0] and d['accessions'] == ['0000950103-26-016190'] and d['errors'] == ['0000000999-26-000001'], d
# ブラウザ版の「索引なし」の印だけのファイル → 印は引き継ぎ、索引が出れば取る
json.dump({"date": "2026-10-27", "filings_seen": 0, "index_missing": True, "complete": True, "collector": "collector-2.1", "fetched_at": "2026-10-28T00:00:00Z"}, open(os.path.join(out,'2026-10-27.json'), 'w', encoding='utf-8'))
f5b = Fake5(); fe.Fetcher = lambda rps=8.0, max_fail=30: f5b
sys.argv = ['fetch_edgar.py','--from','2026-10-27','--to','2026-10-27','--out',out]
fe.main()
d27 = json.load(open(os.path.join(out,'2026-10-27.json'), encoding='utf-8'))
assert 'index_missing' not in d27 and d27['filings_seen'] == 2 and len(d27['rows']) == 1 and d27['accessions'] == ['0000950103-26-016190'], d27
# 索引の通信失敗（INDEX）のあとで索引が出た → INDEX の印が消え、complete が True に戻る（消えないと build.py が毎回 NG）
class Fake6(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261022.idx'): return fe.FAILED
        return FakeFetcher.get(self, url, retries)
f6 = Fake6(); fe.Fetcher = lambda rps=8.0, max_fail=30: f6
sys.argv = ['fetch_edgar.py','--from','2026-10-22','--to','2026-10-22','--out',out]
fe.main()
assert 'INDEX' in json.load(open(os.path.join(out,'2026-10-22.json'), encoding='utf-8'))['errors']
class Fake7(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261022.idx'): return master.replace("20261026", "20261022").encode('latin-1')
        return FakeFetcher.get(self, url, retries)
f7 = Fake7(); fe.Fetcher = lambda rps=8.0, max_fail=30: f7
fe.main(); fe.main(); fe.main()   # 読めない 1 件は 3 回であきらめる
d22 = json.load(open(os.path.join(out,'2026-10-22.json'), encoding='utf-8'))
assert 'INDEX' not in d22['errors'] and d22['complete'] is True and d22['filings_seen'] == 2 and len(d22['rows']) == 1, d22
# 索引の通信失敗（INDEX）のあとで「索引なし」（休日）と分かった → INDEX の印を消して索引なしにする
class Fake6b(FakeFetcher):
    def get(self, url, retries=4):
        self.calls.append(url)
        if url.endswith('master.20261023.idx'): return fe.FAILED
        return FakeFetcher.get(self, url, retries)
f6b = Fake6b(); fe.Fetcher = lambda rps=8.0, max_fail=30: f6b
sys.argv = ['fetch_edgar.py','--from','2026-10-23','--to','2026-10-23','--out',out]
fe.main()
assert 'INDEX' in json.load(open(os.path.join(out,'2026-10-23.json'), encoding='utf-8'))['errors']
f4c = Fake4(); fe.Fetcher = lambda rps=8.0, max_fail=30: f4c
fe.main()
d23 = json.load(open(os.path.join(out,'2026-10-23.json'), encoding='utf-8'))
assert d23['errors'] == [] and d23.get('index_missing') is True and d23['complete'] is True, d23
# ブラウザ版の中身ありファイルがある日に、索引が空（None）で返っても上書きしない
json.dump(browser_day, open(os.path.join(out,'2026-10-26.json'), 'w', encoding='utf-8'))
before_b = open(os.path.join(out,'2026-10-26.json'), encoding='utf-8').read()
f4b = Fake4(); fe.Fetcher = lambda rps=8.0, max_fail=30: f4b
sys.argv = ['fetch_edgar.py','--from','2026-10-26','--to','2026-10-26','--out',out]
fe.main()
assert open(os.path.join(out,'2026-10-26.json'), encoding='utf-8').read() == before_b, 'ブラウザ版の日ファイルを索引なしで上書きした'
# 403 の中身が gzip で来ても「無いファイル」（AccessDenied）と分かる（GitHub Actions の実測。休日の索引を INDEX にしないため）
import gzip as _gz, io as _io, urllib.error as _ue
_xml = b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'
def _err(body, enc):
    h = {"Content-Encoding": enc} if enc else {}
    from email.message import Message
    m = Message()
    for k, v in h.items(): m[k] = v
    return _ue.HTTPError("https://www.sec.gov/x", 403, "Forbidden", m, _io.BytesIO(body))
assert fe.is_access_denied(_err(_xml, None)) is True
assert fe.is_access_denied(_err(_gz.compress(_xml), "gzip")) is True
assert fe.is_access_denied(_err(_gz.compress(_xml), None)) is True   # 見出しが無くても gzip の印（1f 8b）で分かる
assert fe.is_access_denied(_err(b"<html>Request Rate Threshold Exceeded</html>", None)) is False
assert fe.is_access_denied(_err(b"", None)) is False
print('offline test OK; calls:', len(f.calls))
