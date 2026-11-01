# build.py の決まりを、作った行で確かめる（ネット無し）
import json, os, shutil, subprocess, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "_out_build")
RAW = os.path.join(OUT, "days")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(RAW)

ASOF = "2026-10-27T05:00:00Z"
today = date(2026, 10, 27)

def row(sym, cik, name, typ, td, fd, shares, price, owned, form="4", sec="Common Stock", **kw):
    r = {"symbol": sym, "issuerName": sym + " Inc.", "filingDate": fd, "transactionDate": td, "reportingCik": cik,
         "companyCik": "1", "transactionType": "P-Purchase", "securitiesOwned": owned, "reportingName": name,
         "typeOfOwner": typ, "acquisitionOrDisposition": "A", "directOrIndirect": "D", "formType": form,
         "securitiesTransacted": shares, "price": price, "securityName": sec,
         "url": f"https://www.sec.gov/Archives/edgar/data/1/{cik}{td.replace('-', '')}/x-index.htm",
         "accession": f"{cik}-{td}", "plan10b5": False, "privateHint": False, "jointNames": [], "jointTypes": []}
    r.update(kw)
    return r

days = {}
def put(fd, r):
    days.setdefault(fd, []).append(r)

# A社: 3人の役員が 14日以内に買う（クラスター）。4人目はファンド（10% owner）→ 人数に数えない
put("2026-10-20", row("AAA", "c1", "Alpha Ann", "officer: Chief Executive Officer", "2026-10-17", "2026-10-20", 5000, 10.0, 105000))
put("2026-10-23", row("AAA", "c2", "Bravo Bob", "director", "2026-10-22", "2026-10-23", 2000, 10.5, 12000))
put("2026-10-27", row("AAA", "c3", "Charlie Cat", "officer: Executive Vice President", "2026-10-27", "2026-10-27", 1500, 11.0, 1500))  # 買った日が 10 日後（幅 14 の内側）
put("2026-10-27", row("AAA", "f1", "Fund LP", "10 percent owner", "2026-10-26", "2026-10-27", 100000, 10.0, 2000000))
# B社: 3人だが 3人目の買った日が 14 日ちょうど後 → 幅の外（クラスターにならない）
put("2026-10-02", row("BBB", "d1", "Dee", "officer: CFO", "2026-10-01", "2026-10-02", 20000, 10.0, 50000))
put("2026-10-06", row("BBB", "d2", "Eve", "director", "2026-10-03", "2026-10-06", 20000, 10.0, 50000))
put("2026-10-16", row("BBB", "d3", "Fay", "director", "2026-10-15", "2026-10-16", 20000, 10.0, 50000))
# C社: 訂正。元の 4 で 1万株、4/A で 6千株 → 4/A だけ残る（6千株）
put("2026-10-09", row("CCC", "e1", "Gus", "officer: President", "2026-10-08", "2026-10-09", 10000, 20.0, 10000))
put("2026-10-12", row("CCC", "e1", "Gus", "officer: President", "2026-10-08", "2026-10-12", 6000, 20.0, 6000, form="4/A"))
# D社: 関連会社の同じ買い（2つの CIK が同じ取引を届け出）→ 1 つに。遅れて届け出（買いから 20 日）
put("2026-10-21", row("DDD", "g1", "Holdco LLC", "10 percent owner", "2026-10-01", "2026-10-21", 30000, 5.0, 900000))
put("2026-10-21", row("DDD", "g2", "Hank", "director, 10 percent owner", "2026-10-01", "2026-10-21", 30000, 5.0, 900000, notes="Shares held by Holdco LLC, of which the reporting person is the managing member."))
# E社: 私募の疑い（脚注）と 10b5-1
put("2026-10-23", row("EEE", "h1", "Ivy", "officer: Chief Operating Officer", "2026-10-21", "2026-10-23", 50000, 4.0, 60000, privateHint=True))
put("2026-10-23", row("EEE", "h2", "Jon", "director", "2026-10-21", "2026-10-23", 50000, 4.0, 60000, plan10b5=True))
# F社: 同じ届け出の中の別の 2 取引（株数・単価が同じ）→ 2 件とも数える（line で区別）
put("2026-10-20", row("FFF", "k1", "Kay", "officer: Chief Financial Officer", "2026-10-19", "2026-10-20", 10000, 15.0, 20000, accession="acc-f1", line=0))
put("2026-10-20", row("FFF", "k1", "Kay", "officer: Chief Financial Officer", "2026-10-19", "2026-10-20", 10000, 15.0, 30000, accession="acc-f1", line=1))
# G社: 取締役 2 人が同じファンドの株を別々に届け出（間接保有・持ち主の書き方が同じ）→ 1 つにまとめる
put("2026-10-21", row("GGG", "m1", "Gordon Carl", "director", "2026-10-20", "2026-10-21", 50000, 8.0, 900000, directOrIndirect="I", natureOfOwnership="By OrbiMed Private Investments VIII, LP", accession="acc-g1", line=0))
put("2026-10-21", row("GGG", "m2", "Chimovits Peter", "director", "2026-10-20", "2026-10-21", 50000, 8.0, 900000, directOrIndirect="I", natureOfOwnership="By OrbiMed Private Investments VIII, LP", accession="acc-g2", line=0))
# H社: 名誉取締役（other: Director Emeritus）は役員に数えない
put("2026-10-22", row("HHH", "n1", "Malone John", "other: Director Emeritus", "2026-10-21", "2026-10-22", 10000, 30.0, 50000))
# I社: 注記に Brazilian reais → 外す。J社: 注記に ADS → 外す
put("2026-10-22", row("III", "o1", "Oscar", "director", "2026-10-21", "2026-10-22", 100000, 17.0, 900000, notes="Price in Brazilian reais (R$)."))
put("2026-10-22", row("JJJ", "p1", "Paula", "officer: Chief Executive Officer", "2026-10-21", "2026-10-22", 100000, 9.0, 900000, notes="Each ADS represents 60 ordinary shares. Price per ADS."))
# U社: 注記にカナダドルがあるが「米ドルに換えた」と書いてある → 外さない。V社: ADS 1 つ＝普通株 1 株 → 外さない。W社: 額面が CHF → 外さない
put("2026-10-22", row("UUH", "o2", "Una", "director", "2026-10-21", "2026-10-22", 10037, 12.91, 90000, notes="Converted from Canadian purchase price of C$18.3157 using an exchange rate of C$1.4188 = US$1.00."))
put("2026-10-22", row("VVA", "o3", "Vic", "director", "2026-10-21", "2026-10-22", 15037, 8.57, 90000, sec="Common shares", notes="The common shares may be represented by American Depositary Shares, each of which currently represents one common share."))
put("2026-10-22", row("WWC", "o4", "Wes", "director", "2026-10-21", "2026-10-22", 75037, 1.67, 900000, notes="Ordinary Shares, par value CHF 1/113.25, purchased in the open market."))
# K社: 上場時の割当（市場の外の買い）で 200 万ドル → 「大きな買い」に数えない。3 人でもクラスターにしない
for nm, ck in (("Quinn", "q1"), ("Rita", "q2"), ("Sam", "q3")):
    put("2026-10-23", row("KKK", ck, nm, "director", "2026-10-22", "2026-10-23", 200000, 10.0, 200000, notes="Shares purchased in the Issuer's initial public offering at the IPO price."))
# L社: SPAC（買収目的の会社）の CEO・取締役 3 人が上場時に 10 ドルで 300 万ドルずつ → 「大きな買い」・クラスター・CEO の絞り込みに数えない
for nm, ck, tp in (("Luke", "l1", "officer: Chief Executive Officer, director"), ("Lena", "l2", "director"), ("Lars", "l3", "director")):
    put("2026-10-15", row("LLL", ck, nm, tp, "2026-10-15", "2026-10-15", 300000, 10.0, 300000, issuerName="Lima Life Sciences Acquisition Corp. II"))
# M社: 上場（IPO）の言葉が同じ届け出の別の脚注にあり、単価 18.00 → 市場の外の買い（上場）。N社: 同じく IPO の言葉はあるが単価 18.37 → 印なし
put("2026-10-19", row("MMM", "s1", "Nelsen Rob", "director, 10 percent owner", "2026-10-19", "2026-10-19", 170000, 18.0, 13692507, ipoInFiling=True, notes="Shares held directly by the Fund.", directOrIndirect="I"))
put("2026-10-19", row("MMN", "s2", "Nora", "director", "2026-10-16", "2026-10-19", 100000, 18.37, 500000, ipoInFiling=True, notes=""))
# O社: 1回・1つの単価で合計がちょうど 100万ドル（362,319 株 × 2.76 = 1,000,000.44）→ 市場の外の買い（金額）
put("2026-10-06", row("OOO", "t1", "Lemon Mark", "officer: Chief Executive Officer, director", "2026-10-06", "2026-10-06", 362319, 2.76, 1076860, notes=""))
# P社: 関連会社と取締役が同じ買いを別々に届け出、私募の言葉は関連会社の届け出の注記にだけある → まとめた 1 件に印が残る
put("2026-10-20", row("PPP", "u1", "Pine Capital LLC", "10 percent owner", "2026-10-17", "2026-10-20", 50000, 20.37, 800000, notes="Purchased in a private placement pursuant to a securities purchase agreement.", accession="acc-p1", line=0))
put("2026-10-20", row("PPP", "u2", "Petra", "director", "2026-10-17", "2026-10-20", 50000, 20.37, 800000, notes="The reporting person is a managing member of Pine Capital, LLC.", accession="acc-p2", line=0))
# Q社: 会社情報の上場日（ipo_date 10/16）の 2 日後に 16.00 ドルで買った → 市場の外の買い（上場）。R社: 上場から 1 年後 → 印なし
put("2026-10-19", row("QQQ", "v1", "Quill", "director", "2026-10-18", "2026-10-19", 100037, 16.0, 400000))
put("2026-10-19", row("RRR", "w1", "Ruth", "director", "2026-10-18", "2026-10-19", 100037, 16.0, 400000))
# S社: SEC の一覧で店頭（OTC）。T社: SEC の一覧で Nasdaq（会社情報が無くても分かる）
put("2026-10-22", row("SSS", "x1", "Sue", "director", "2026-10-21", "2026-10-22", 50000, 3.07, 900000, issuerExchange="OTC"))
put("2026-10-22", row("TTT", "y1", "Tom", "director", "2026-10-21", "2026-10-22", 50000, 3.07, 900000, issuerExchange="Nasdaq"))
# ---- 報酬や会社の制度での取得（「制度」）----
# BOR: オプションを使った取得（cashless exercise）で 147 万ドル → 制度。大きな買いに数えない
put("2026-10-16", row("BOR", "b1", "Snow Harvey", "officer: Chief Operating Officer", "2026-10-14", "2026-10-16", 300000, 4.91, 900000, notes="The reported purchase was effected in connection with a cashless exercise of vested stock options."))
# HEJ: 3 人が同じ日・同じ単価（報酬の制度 409A）。4 人目は別の種類の株で、ほぼ同じ金額（±1%）→ 4 人とも「決まった買い」
for nm, ck, nt in (("Mend Vic", "h11", "Represents shares held for the reporting person by the HEJ Corporation Leadership Compensation Plan (409A Plan)."),
                   ("Henri Ado", "h12", "Represents shares held for the reporting person by the HEJ Corporation Leadership Compensation Plan (409A Plan)."), ("Schr Alan", "h13", "")):
    put("2026-10-13", row("HEJ", ck, nm, "director", "2026-10-08", "2026-10-13", 795, 220.3043, 30000, notes=nt))
put("2026-10-13", row("HEJ", "h14", "Neitz Julie", "director", "2026-10-08", "2026-10-13", 592, 294.9343, 1000, sec="Class A Common Stock"))
# DRP: 持ち株の説明の文（Includes … Dividend Reinvestment Plan）だけ・株数が整数 → 印なし。端数の株数なら制度
put("2026-10-21", row("DRP", "dr1", "Dora", "director", "2026-10-20", "2026-10-21", 5000, 21.37, 90000, notes="Includes 846 shares acquired through the Dividend Reinvestment Plan."))
put("2026-10-21", row("DRQ", "dr2", "Drew", "director", "2026-10-20", "2026-10-21", 4700.7506, 21.37, 90000, notes="Includes 846.3057 shares acquired through the Dividend Reinvestment Plan."))
# DRR: 端数の株数で、金額がちょうど（3,218.8224 株 × 31.0673 = 100,000.00）→ 制度
put("2026-10-21", row("DRR", "dr3", "Dirk", "director", "2026-10-20", "2026-10-21", 3218.8224, 31.0673, 90000))
# MSP: 経営陣が市場で買う「management share purchase plan」→ 印なし。DSP: 取締役の株の買い付け制度でも 10b5-1 の計画なら印なし
put("2026-10-21", row("MSP", "ms1", "Mona", "officer: Chief Executive Officer", "2026-10-20", "2026-10-21", 168894, 1.0137, 900000, notes="The purchase was made as part of the management share purchase plan announced by the Company on June 22, 2026."))
put("2026-10-21", row("DSP", "ds1", "Dana", "director", "2026-10-20", "2026-10-21", 10037, 12.37, 90000, notes="Shares purchased under a director stock purchase plan adopted pursuant to Rule 10b5-1."))
# ---- 役職の読み替え ----
put("2026-10-22", row("ROA", "ro1", "Mind Damian", "officer: Vicepresident", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))          # 副社長（社長ではない）
put("2026-10-22", row("ROB", "ro2", "Ell Adam", "officer: President International", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))  # 部門の社長
put("2026-10-22", row("ROC", "ro3", "New Will", "director, other: Chairman of the Board", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))  # 会長
put("2026-10-22", row("ROD", "ro4", "Will Brian", "officer: See Remarks", "2026-10-21", "2026-10-22", 50000, 3.11, 900000, docNote="Chief Financial Officer & Treasurer"))
put("2026-10-22", row("ROE", "ro5", "Ross Alex", "other: Chief Revenue Officer", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))   # other でも発行会社の役職なら役員
put("2026-10-22", row("ROF", "ro6", "Spen John", "other: CEO - Investment Advisor", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))  # 投資顧問の CEO は関係者
# 上場のときの買いの CEO（市場の外の買いの可能性）は「CEO・CFO・会長が買った」に数えない
put("2026-10-19", row("TOP", "tp1", "Topa", "officer: Chief Executive Officer", "2026-10-18", "2026-10-19", 50000, 12.0, 900000, ipoInFiling=True))
# ---- 預託証券（ADS）の単位 ----
# MNS: 香港で普通株を買い、単価を米ドルに換えた（× 4 で ADS の株価に合う）→ 外さない。COF: 単価が ADS 1 つ（60 株）の値段で、株数は普通株 → 株数を 60 で割る。TEP: 株価が分からない → 外す
put("2026-10-22", row("MNS", "an1", "Zhang Jing", "officer: Chief Financial Officer", "2026-10-21", "2026-10-22", 150600, 2.2583, 394000, sec="Ordinary Shares",
                      notes="Including 174,074 ordinary shares held in the form of American depositary shares (\"ADSs\"), each representing four ordinary shares. | The ordinary shares were purchased in the open market in Hong Kong at prices denominated in Hong Kong dollars ranging from HK$17.55 to HK$18.03 per ordinary share, converted to U.S. dollars."))
put("2026-10-22", row("COF", "an2", "Huang Jack", "officer: Chief Executive Officer", "2026-10-21", "2026-10-22", 1227600, 11.5, 30000000, sec="Class A Ordinary Share",
                      notes="The Class A ordinary shares are held in the form of American depositary shares (\"ADS\"). Each ADS represents sixty Class A ordinary shares. | The price reported is a weighted average price of ADS."))
put("2026-10-22", row("TEP", "an3", "Heine Martin", "director", "2026-10-21", "2026-10-22", 70000, 2.6661, 900000, sec="Class B Shares",
                      notes="The securities reported herein include 34,500 Class B shares held in the form of 6,900 American Depository Receipts. Each ADR represents five Class B shares."))
# ---- 持ち株の増え方（持ち主ごとに数える）と、2 種類の株 ----
put("2026-10-21", row("STK", "sk1", "Lap Mark", "director", "2026-10-14", "2026-10-21", 5040, 84.5, 745588, directOrIndirect="I", natureOfOwnership="By the Lap Family Trust", accession="acc-s1", line=0))
put("2026-10-21", row("STK", "sk1", "Lap Mark", "director", "2026-10-14", "2026-10-21", 5000, 84.5, 170000, directOrIndirect="I", natureOfOwnership="By Lap Descendants' Trust", accession="acc-s1", line=1))
put("2026-10-21", row("MIX", "mx1", "Mix Dam", "director", "2026-10-14", "2026-10-21", 20000, 3.11, 800000, accession="acc-m1", line=0))
put("2026-10-21", row("MIX", "mx1", "Mix Dam", "director", "2026-10-14", "2026-10-21", 1000, 77.63, 50000, sec="American Depositary Shares", accession="acc-m1", line=1))
# 同じ書き方（See footnotes）の 2 つのファンド: 持ち主のつながりで分ける（ADRX の Gordon と同じ）
put("2026-10-21", row("GFN", "gf1", "Gord Carl", "director", "2026-10-14", "2026-10-21", 300249, 17.0, 14325529, directOrIndirect="I", natureOfOwnership="See footnotes", accession="acc-gf", line=0))
put("2026-10-21", row("GFN", "gf1", "Gord Carl", "director", "2026-10-14", "2026-10-21", 588235, 17.0, 588235, directOrIndirect="I", natureOfOwnership="See footnotes", accession="acc-gf", line=1))
# Held by Trust の 2 つの信託と、同じ信託が同じ日に 2 回に分けて買った分（つながる）
put("2026-10-21", row("HBT", "hb1", "Whit George", "director", "2026-10-14", "2026-10-21", 1000, 50.0, 101000, directOrIndirect="I", natureOfOwnership="Held by Trust", accession="acc-hb", line=0))
put("2026-10-21", row("HBT", "hb1", "Whit George", "director", "2026-10-14", "2026-10-21", 2000, 50.0, 103000, directOrIndirect="I", natureOfOwnership="Held by Trust", accession="acc-hb", line=1))
put("2026-10-21", row("HBT", "hb1", "Whit George", "director", "2026-10-14", "2026-10-21", 500, 50.0, 50500, directOrIndirect="I", natureOfOwnership="Held by Trust", accession="acc-hb", line=2))
# ちょうど 50 万ドルでも、株数が 100 株単位（10,000 株 × 50.00 ドル）なら指値のふつうの買いの可能性が高いので「ちょうどの金額」にしない
put("2026-10-20", row("RND", "rd1", "Bail Ed", "director", "2026-10-17", "2026-10-20", 10000, 50.0, 90000))
# 持ち株の説明の文（This total reflects …）は制度の言葉でも数えない。ファンドどうしの入れ替え・出資の取り決めは市場の外
put("2026-10-21", row("PCH", "pc1", "Lars John", "director", "2026-10-20", "2026-10-21", 9500, 12.2, 90000, notes="This total reflects the acquisition of 61.75 Restricted Stock Units (RSUs) on 7/15/2026 pursuant to a dividend reinvestment feature of the 2021 LTIP."))
put("2026-10-21", row("DLH", "dl1", "Mink Brook Asset Management LLC", "10 percent owner", "2026-10-20", "2026-10-21", 199099, 3.7, 900000, notes="These shares were transferred in a rebalancing transaction between funds in one transaction at a price of $3.70 inclusive. No shares were bought or sold in the open market."))
put("2026-10-21", row("STX", "st1", "Tour Group LLC", "10 percent owner", "2026-10-20", "2026-10-21", 120000, 1.15, 900000, notes='On October 5, 2026, the reporting person entered into an agreement (the "Contribution Agreement") relating to the transfer of a contribution.'))
put("2026-10-22", row("DEP", "dp1", "Dep Tony", "officer: SVP and Deputy Co-COO", "2026-10-21", "2026-10-22", 50000, 3.11, 900000))
# 持ち株の説明の文（Consists of …・holdings include …）は、制度の言葉があっても数えない
put("2026-10-21", row("CSO", "cs1", "Cons Amy", "director", "2026-10-20", "2026-10-21", 9000, 12.0, 90000, notes="Consists of 80,000 shares held directly and 10,000 shares held in the Company's 401(k) plan."))
put("2026-10-21", row("HIN", "hi1", "Hold Ian", "director", "2026-10-20", "2026-10-21", 9000, 12.0, 90000, notes="These holdings include 1,234 shares acquired under the Company's Employee Stock Purchase Plan."))
# 相手と決めた取引・「市場では買っていない」は市場の外。「open market で買った」は市場の買いのまま
put("2026-10-21", row("PNT", "pn1", "Priv Nora", "director", "2026-10-20", "2026-10-21", 9000, 12.0, 90000, notes="Shares were purchased from a selling stockholder in a privately negotiated transaction."))
put("2026-10-21", row("NOM", "nm1", "Nopen Mark", "director", "2026-10-20", "2026-10-21", 9000, 12.0, 90000, notes="The shares were not acquired in the open market."))
put("2026-10-21", row("OPM", "op1", "Open Oscar", "director", "2026-10-20", "2026-10-21", 9000, 12.0, 90000, notes="Shares were purchased on the open market in multiple transactions at prices ranging from $11.90 to $12.10."))
# ---- 関係の無い別々の持ち主（同じ条件で買っただけ）は 1 つにしない ----
put("2026-10-23", row("RNT", "rn1", "Nexus Capital Management LP", "director, 10 percent owner", "2026-10-21", "2026-10-23", 632922, 3.55, 4907316, directOrIndirect="I", natureOfOwnership="See Footnotes",
                      notes="Gateway Runway, LLC directly holds 4,907,316 shares.", accession="acc-r1", line=0))
put("2026-10-23", row("RNT", "rn2", "STORY3 Capital Partners, LLC", "director, 10 percent owner", "2026-10-21", "2026-10-23", 632922, 3.55, 4907316, directOrIndirect="I", natureOfOwnership="See Footnote",
                      notes="S3 RR Aggregator, LLC directly holds 4,907,316 shares.", accession="acc-r2", line=0))
# 銘柄コードの形が米国でない（AXIA3）・ユニット（PORT.U）→ 載らない
put("2026-10-23", row("AXIA3", "r1", "Rui", "director", "2026-10-22", "2026-10-23", 100000, 10.0, 100000))
put("2026-10-23", row("PORT.U", "r2", "Rob", "director", "2026-10-22", "2026-10-23", 100000, 10.0, 100000))
# 小さすぎる買い（載らない）
put("2026-10-23", row("ZZZ", "z1", "Zed", "director", "2026-10-22", "2026-10-23", 100, 10.0, 100))

# 日ファイル（平日ぜんぶ、2026-09-27〜10-27）
d = date(2026, 9, 27)
while d <= today:
    if d.weekday() < 5:
        rows = days.get(d.isoformat(), [])
        json.dump({"date": d.isoformat(), "filings_seen": max(1, len(rows)), "filings_parsed": len(rows), "accessions": [], "rows": rows,
                   "complete": True, "errors": []}, open(os.path.join(RAW, d.isoformat() + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    d += timedelta(days=1)

os.makedirs(os.path.join(OUT, "market", "quotes"), exist_ok=True)
json.dump({"QQQ": {"symbol": "QQQ", "name": "Quill Inc.", "ipo_date": "2026-10-16", "fetched": "2026-10-27"},
           "RRR": {"symbol": "RRR", "name": "Ruth Inc.", "ipo_date": "2025-10-16", "fetched": "2026-10-27"},
           "MNS": {"symbol": "MNS", "name": "Mini Co", "price": 9.98, "is_adr": True, "fetched": "2026-10-27"},
           "COF": {"symbol": "COF", "name": "Coe Co", "price": 11.53, "is_adr": True, "fetched": "2026-10-27"}},
          open(os.path.join(OUT, "market", "profiles.json"), "w", encoding="utf-8"))

def run(extra=()):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "build.py"), "--raw", RAW, "--quotes", os.path.join(OUT, "market", "quotes"), "--out", OUT, "--asof", ASOF, *extra], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

code, log = run()
assert code == 0, log
data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
C = {c["symbol"]: c for c in data["companies"]}

a = C["AAA"]
assert a["cluster"] and a["cluster_people"] == 3, a
assert a["insider_buyers"] == 3 and a["fund_buyers"] == 1, a
assert any(b["role"] == "役員（部門の責任者）" for b in a["buys"]), [b["role"] for b in a["buys"]]
assert a["top"] is True
assert "BBB" in C and not C["BBB"]["cluster"], "14 日ちょうど後は幅の外のはず"
c = C["CCC"]
assert len(c["buys"]) == 1 and c["buys"][0]["shares"] == 6000 and c["buys"][0]["amended"], c["buys"]
dd = C["DDD"]
assert len(dd["buys"]) == 1 and dd["buys"][0]["name"] == "Hank" and dd["buys"][0]["joint"] == ["Holdco LLC"], dd["buys"]
assert dd["buys"][0]["late"] is True and dd["buys"][0]["compare_ok"] is False
e = C["EEE"]
flags = {b["name"]: (b["private_hint"], b["plan10b5"], b["compare_ok"]) for b in e["buys"]}
assert flags == {"Ivy": (True, False, False), "Jon": (False, True, False)}, flags
assert "ZZZ" not in C
assert data["counts"]["dropped"].get("訂正で置き換え") == 1 and data["counts"]["dropped"].get("関連会社の同じ買い") == 3, data["counts"]["dropped"]
assert data["partial"] is False and "EDGAR" in data["source"]

f = C["FFF"]
assert len(f["buys"]) == 1 and f["buys"][0]["shares"] == 20000 and f["buys"][0]["lots"] == 2, f["buys"]
g = C["GGG"]
assert len(g["buys"]) == 1 and g["buys"][0]["joint"] == ["Gordon Carl"] or g["buys"][0]["joint"] == ["Chimovits Peter"], g["buys"]
assert "HHH" not in C or all(not b["insider"] for b in C["HHH"]["buys"]), "名誉取締役は役員ではない"
sus = {x["symbol"]: x["reason"] for x in data["suspects"]}
assert "III" in sus and "ドル" in sus["III"] and "JJJ" in sus and "ADS" in sus["JJJ"], sus
assert not any(x in sus for x in ("UUH", "VVA", "WWC")) and all(x in C for x in ("UUH", "VVA", "WWC")), sus
assert C["UUH"]["buys"][0]["foreign_market"] and not C["VVA"]["buys"][0]["foreign_market"] and not C["WWC"]["buys"][0]["foreign_market"]
k = C["KKK"]
assert not k["cluster"] and not k["big"] and all(b["private_hint"] and b["big_raw"] and not b["big"] for b in k["buys"]), k
assert "AXIA3" not in C and "PORT.U" not in C
mm = C["MMM"]["buys"][0]
assert mm["private_hint"] and mm["private_why"] == ["上場"] and mm["big_raw"] and not mm["big"], mm
mn = C["MMN"]["buys"][0]
assert not mn["private_hint"] and mn["private_why"] == [], mn
oo = C["OOO"]["buys"][0]
assert oo["private_hint"] and oo["private_why"] == ["金額"] and not oo["big"], oo
pp = C["PPP"]["buys"]
assert len(pp) == 1 and pp[0]["name"] == "Petra" and pp[0]["private_why"] == ["注記"] and pp[0]["joint"] == ["Pine Capital LLC"], pp
qq, rr = C["QQQ"]["buys"][0], C["RRR"]["buys"][0]
assert qq["private_why"] == ["上場"] and not qq["big"] and rr["private_why"] == [] and rr["big"], (qq, rr)
assert C["SSS"]["otc"] and not C["SSS"]["unknown_exchange"] and C["SSS"]["exchange"] == "OTC", C["SSS"]
assert not C["TTT"]["otc"] and not C["TTT"]["unknown_exchange"] and C["TTT"]["exchange"] == "NASDAQ" and C["TTT"]["exchange_source"] == "SEC", C["TTT"]
assert C["AAA"]["unknown_exchange"] and not C["AAA"]["otc"], C["AAA"]
l = C["LLL"]
assert l["spac"] and not l["big"] and not l["cluster"] and not l["top"] and all(b["spac"] and b["big_raw"] and not b["big"] and not b["compare_ok"] for b in l["buys"]), l
# 報酬や会社の制度での取得
bo = C["BOR"]["buys"][0]
assert bo["private_why"] == ["制度"] and bo["big_raw"] and not bo["big"] and not C["BOR"]["big"], bo
hj = {b["name"]: b for b in C["HEJ"]["buys"]}
assert all(hj[n]["same_price"] for n in hj) and len(hj) == 4, {n: (b["same_price"], b["private_why"]) for n, b in hj.items()}
assert hj["Mend Vic"]["private_why"] == ["制度"] and hj["Schr Alan"]["private_why"] == [] and not C["HEJ"]["cluster"], hj
assert C["DRP"]["buys"][0]["private_why"] == [] and C["DRQ"]["buys"][0]["private_why"] == ["制度"], (C["DRP"]["buys"][0]["private_why"], C["DRQ"]["buys"][0]["private_why"])
assert C["DRR"]["buys"][0]["private_why"] == ["制度"], C["DRR"]["buys"][0]
assert C["MSP"]["buys"][0]["private_why"] == [] and C["DSP"]["buys"][0]["private_why"] == [], (C["MSP"]["buys"][0]["private_why"], C["DSP"]["buys"][0]["private_why"])
# 役職
assert C["ROA"]["buys"][0]["role"] == "役員（部門の責任者）" and not C["ROA"]["top"], C["ROA"]["buys"][0]
assert C["ROB"]["buys"][0]["role"] == "役員（部門の責任者）" and not C["ROB"]["top"]
assert C["ROC"]["buys"][0]["role"] == "会長" and C["ROC"]["top"]
assert C["ROD"]["buys"][0]["role"] == "CFO（財務の責任者）" and C["ROD"]["top"]
assert C["ROE"]["buys"][0]["role"] == "役員（経営幹部）" and C["ROE"]["buys"][0]["insider"] is True
assert C["ROF"]["buys"][0]["insider"] is False and C["ROF"]["buys"][0]["role"].startswith("関係者"), C["ROF"]["buys"][0]
assert "上場" in C["TOP"]["buys"][0]["private_why"] and not C["TOP"]["top"], C["TOP"]
# ADS
assert "MNS" in C and C["MNS"]["buys"][0]["usd"] == round(150600 * 2.2583, 2) and C["MNS"]["buys"][0]["foreign_market"], C.get("MNS")
cf = C["COF"]["buys"][0]
assert cf["shares"] == 20460 and cf["usd"] == round(20460 * 11.5, 2) and cf["ads_converted"] == 60, cf
assert "TEP" in sus and "ADS" in sus["TEP"] and "TEP" not in C, sus
# 持ち株の増え方・2 種類の株
st = C["STK"]["buys"][0]
assert st["shares"] == 10040 and st["stake_increase_pct"] == round(10040 / (740548 + 165000) * 100, 1), st
mx = C["MIX"]["buys"][0]
assert mx["stake_increase_pct"] is None and len(mx["parts"]) == 2 and {p["security"] for p in mx["parts"]} == {"Common Stock", "American Depositary Shares"}, mx
gf = C["GFN"]["buys"][0]
assert gf["stake_increase_pct"] == round(888484 / 14025280 * 100, 1) and gf["owned_after_total"] == 14325529 + 588235, gf
hb = C["HBT"]["buys"][0]
assert hb["stake_increase_pct"] == round(3500 / (100000 + 50000) * 100, 1) and hb["owned_after_total"] == 103000 + 50500, hb
assert C["RND"]["buys"][0]["private_why"] == [], C["RND"]["buys"][0]
assert C["PCH"]["buys"][0]["private_why"] == [], C["PCH"]["buys"][0]["private_why"]
assert C["PNT"]["buys"][0]["private_why"] == ["注記"] and C["NOM"]["buys"][0]["private_why"] == ["注記"] and C["OPM"]["buys"][0]["private_why"] == [], (C["PNT"]["buys"][0]["private_why"], C["NOM"]["buys"][0]["private_why"], C["OPM"]["buys"][0]["private_why"])
assert C["CSO"]["buys"][0]["private_why"] == [] and C["HIN"]["buys"][0]["private_why"] == [], (C["CSO"]["buys"][0]["private_why"], C["HIN"]["buys"][0]["private_why"])
assert C["DLH"]["buys"][0]["private_why"] == ["注記"] and C["STX"]["buys"][0]["private_why"] == ["注記"], (C["DLH"]["buys"][0]["private_why"], C["STX"]["buys"][0]["private_why"])
assert C["DEP"]["buys"][0]["role"] == "役員（経営幹部）", C["DEP"]["buys"][0]["role"]
# 役職の読み替え（Deputy・Assistant・Associate は元の役職より上に出さない）
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("_build_mod", os.path.join(ROOT, "build.py"))
B = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(B)
for typ, want in [("officer: Deputy General Counsel", "役員"), ("officer: General Counsel", "役員"),
                  ("officer: Assistant Treasurer", "役員"), ("officer: Associate Vice President", "役員"),
                  ("officer: Asst. Secretary", "役員"), ("officer: Executive Assistant to the CEO", "役員"),
                  ("officer: Deputy CEO", "役員（経営幹部）"), ("officer: SVP and Deputy Co-COO", "役員（経営幹部）"),
                  ("officer: Deputy Chairman", "副会長"), ("officer: Assistant Chief Financial Officer", "役員"),
                  ("officer: Chief of Staff to the CEO", "役員（経営幹部）"), ("director, officer: President & CEO", "CEO（社長）"),
                  ("officer: Senior Vice President", "役員（部門の責任者）"), ("officer: Co-COB and Co-CEO", "CEO（社長）")]:
    got = B.role_jp(typ, "Doe John")
    assert got == want, (typ, got, want)
assert hj["Neitz Julie"]["same_by"] == "amount" and hj["Mend Vic"]["same_by"] == "price", hj
assert data["counts"]["person_days_all"] == data["counts"]["person_days"] + data["counts"]["suspects"], data["counts"]
# 関係の無い 2 つの持ち主は別々に残る
assert len(C["RNT"]["buys"]) == 2, C["RNT"]["buys"]
# 日ファイルが 1 つ欠けると NG（出口 2）。--partial-ok なら通る
os.remove(os.path.join(RAW, "2026-10-06.json"))
code2, log2 = run()
assert code2 == 2 and "欠け 2026-10-06" in log2, log2
code3, log3 = run(["--partial-ok"])
assert code3 == 0, log3

# 祝日でない平日に索引が無い日（SEC の臨時の休みか遅れ）: 3 日後の 12:00 UTC までは「索引がまだ無い平日」として知らせるが、検査は NG にしない
# （ページ全体を止めないため）。3 日を過ぎて決まった日は、画面で「休」（holiday）
OUT2 = os.path.join(HERE, "_out_build2"); RAW2 = os.path.join(OUT2, "days")
shutil.rmtree(OUT2, ignore_errors=True); os.makedirs(RAW2); os.makedirs(os.path.join(OUT2, "market", "quotes"))
d = date(2026, 9, 27)
while d <= today:
    if d.weekday() < 5:
        k = d.isoformat()
        info = {"date": k, "filings_seen": 1, "filings_parsed": 1, "accessions": [], "rows": days.get(k, []), "complete": True, "errors": [],
                "fetched_at": (d + timedelta(days=1)).isoformat() + "T13:00:00Z"}
        if k == "2026-10-22":   # 木曜: 索引が無いまま 10/26 13:00 に確かめた（3 日後の 12:00 を過ぎた）→ 休み
            info.update(filings_seen=0, filings_parsed=0, rows=[], index_missing=True, confirmed_at="2026-10-26T13:00:00Z")
        if k == "2026-10-23":   # 金曜: 索引がまだ無い（10/26 12:00 より前に確かめただけ）→ 待ち（NG にしない）
            info.update(filings_seen=0, filings_parsed=0, rows=[], index_missing=True, fetched_at="2026-10-24T06:50:00Z", confirmed_at="2026-10-26T06:50:00Z")
        json.dump(info, open(os.path.join(RAW2, k + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    d += timedelta(days=1)
p2 = subprocess.run([sys.executable, os.path.join(ROOT, "build.py"), "--raw", RAW2, "--quotes", os.path.join(OUT2, "market", "quotes"), "--out", OUT2, "--asof", ASOF], capture_output=True, text=True)
assert p2.returncode == 0, p2.stdout + p2.stderr
ck = {c["name"]: c for c in json.load(open(os.path.join(OUT2, "checks.json"), encoding="utf-8"))["checks"]}
assert ck["期間の平日の分が全部そろっている（2日前まで）"]["ok"] is True, ck
assert ck["索引がまだ無い平日（休みか遅れ。3 日後まで確かめる）"]["detail"] == "2026-10-23", ck
dl = {x["date"]: x for x in json.load(open(os.path.join(OUT2, "data.json"), encoding="utf-8"))["daily"]}
assert dl["2026-10-22"]["no_index"] and dl["2026-10-22"]["holiday"] is True, dl["2026-10-22"]
assert dl["2026-10-23"]["no_index"] and dl["2026-10-23"]["holiday"] is False, dl["2026-10-23"]
assert dl["2026-10-12"]["holiday"] is True and dl["2026-10-13"]["holiday"] is False
print("build test OK")
