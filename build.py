#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SECインサイダー v1  集計スクリプト
入力: --raw   生データの置き場。fetch_edgar.py の日ごとのファイル（data/days/日付.json）か、FMP コネクタの返り（*.json）
      --quotes 会社名・現在値（省略時は <raw>/quotes）/ --fx ドル円（省略時は <raw>/usdjpy.json）
出力: <out>/data.json … 画面が読むデータ（自前のかたち）
      <out>/checks.json … 自己検査の結果（1つでも fail があれば出口コード 2＝公開しない）
使い方: python3 build.py --raw data/days --quotes data/market/quotes --fx data/market/usdjpy.json --out docs [--window 30] [--asof 2026-10-27T08:00:00Z]
"""
import argparse, glob, json, math, os, re, sys, hashlib
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict

# ---- 決めた線（変えるときはここだけ） ----
LIST_MIN_USD = 100_000          # 一覧に載る線（1人・1日の合計）
BIG_USD = 1_000_000             # 「大きな買い」の線（1人・1日の合計）
CLUSTER_MIN_PEOPLE = 3          # 「クラスター買い」= この人数以上の役員・取締役が
CLUSTER_WINDOW_DAYS = 14        # この日数の幅の中（買った日で数える。幅は 14 日＝d0 から d0+13 日まで）で買った
CLUSTER_PERSON_MIN_USD = 10_000 # クラスターの人数に数える1人の最低額
WINDOW_DAYS_DEFAULT = 30        # 画面に載せる期間（届け出日ベース）
LATE_FILING_DAYS = 10           # 買った日から届け出までこれより長ければ「遅れた届け出」の印
ROUND_DEAL_MIN_USD = 500_000    # 1回・1つの単価で、合計がちょうど 10万ドル単位（1ドル未満のずれ）の買いがこの金額以上なら「市場の外の買いの可能性」
SUSPECT_ROW_USD = 1_000_000_000 # 1 行でこれを超える金額は「届け出の書き間違いの疑い」として一覧から外す
SUSPECT_PRICE_RATIO = 10        # 届け出の単価が今の株価（FMP）と 10 倍以上ずれたら外す
SAME_PRICE_PEOPLE = 3           # 同じ日・同じ単価で買った役員がこの人数以上なら「報酬などの決まった買いの可能性」（クラスターに数えない）

# 株そのもの以外（オプション・ワラント・優先株・各種ユニット等）は除く
NON_EQUITY_RE = re.compile(
    r"option|warrant|\brights?\b|\bnotes?\b|debenture|preferred|preference|convertible|restricted stock|\brsus?\b|phantom|"
    r"stock appreciation|\bsars?\b|deferred|performance|award|\bbonds?\b|ltip|operating partnership|\bop units?\b|"
    r"llc (interest|unit)|membership|profits interest|incentive",
    re.I,
)
# 残す例: Common Stock / Class A Common Stock / Ordinary Shares / Common Shares of Beneficial Interest / American Depositary Shares / Common Units

# 会社名の後ろに付いた証券の種類（例 "Rapport Therapeutics, Inc. Common Stock"）は、画面の会社名から外す
NAME_TAIL_RE = re.compile(r"\s+(?:-\s*)?(?:Class\s+[A-Z]\s+)?(?:Common\s+Shares\s+of\s+Beneficial\s+Interest|Shares\s+of\s+Beneficial\s+Interest|Common\s+Stock|Common\s+Shares|Ordinary\s+Shares|American\s+Depositary\s+(?:Shares|Receipts)|Depositary\s+Shares|Common\s+Units)\s*$", re.I)


def display_name(n):
    n = str(n or "").strip()
    m = NAME_TAIL_RE.sub("", n).strip().rstrip(",")
    return m or n


def currency_mentioned(text):
    """注記に、単価にかかわる外国のお金の名前があるか（額面 par value・ファンドの名前の中の通貨は数えない）"""
    for m in CURRENCY_RE.finditer(text):
        before = text[max(0, m.start() - 20):m.start()].lower()
        around = text[max(0, m.start() - 30):m.end() + 30].lower()
        if "par value" in before or "fund" in around:
            continue
        return True
    return False


def currency_suspect(text):
    """注記に外国のお金の名前があり、単価がドルでない可能性があるか（「ドルに換えた」と書いてあれば、単価はドル）"""
    return currency_mentioned(text) and not CONVERTED_RE.search(text)


def foreign_market(text):
    """外国の市場で買い、単価を米ドルに換えて届け出た物か"""
    return currency_mentioned(text) and bool(CONVERTED_RE.search(text))


def ads_mismatch(text, security):
    """注記に預託証券（ADS）の説明があり、株数・単価の単位が証券名とちがう可能性があるか（1 つ＝1 株なら同じ）"""
    if ADS_TITLE_RE.search(security or "") or not ADS_RE.search(text):
        return False
    for m in ADS_RATIO_RE.finditer(text):
        v = m.group(1).lower().replace(",", "")
        n = WORD_NUM.get(v)
        if n is None:
            try:
                n = float(v)
            except ValueError:
                n = None
        if n == 1:
            return False
    return True


def plan_hint(notes, doc_note, shares):
    """報酬や会社の制度での取得（権利の行使・報酬の制度・社員や取締役の株の買い付け制度・配当の再投資・報酬の代わり・会社との取り決め・ユニット）の言葉があるか。
    持ち株の説明（「Includes …」「… of which were acquired through …」「This total reflects …」「Consists of …」）の文は、その買いのことではないので数えない。
    ただし株数に端数がある買い（制度での買いに多い）は、持ち株の説明の文でも数える"""
    fractional = shares > 0 and abs(shares - round(shares)) > 1e-6
    for foot in str(notes or "").split(" | ") + [str(doc_note or "")]:
        for sent in re.split(r"(?<=[.;])\s+", foot.strip()):
            if not sent:
                continue
            for m in PLAN_RE.finditer(sent):
                if "purchase plan" in m.group(0).lower() and PLAN_NOT_RE.search(sent):
                    continue   # 10b5-1 の計画（市場での買い）
                holding = bool(INCLUDES_RE.match(sent)) or bool(REFLECTS_HOLDING_RE.match(sent)) or bool(HOLDING_WORDS_RE.search(sent[:m.start()]))
                if holding and not fractional:
                    continue
                return True
    return False


ADS_PRICE_LO, ADS_PRICE_HI = 0.7, 1.3   # 単価と ADS の株価（FMP）を比べるときの幅


def ads_ratio(text):
    """注記に書かれた ADS 1 つが何株か（1 つの数だけ書かれていればその数、無いか 2 つ以上なら None）"""
    ns = set()
    for m in ADS_RATIO_RE.finditer(text):
        v = m.group(1).lower().replace(",", "")
        n = WORD_NUM.get(v)
        if n is None:
            try:
                n = float(v)
            except ValueError:
                n = None
        if n:
            ns.add(n)
    return ns.pop() if len(ns) == 1 else None


def ads_units(text, security, price, pp):
    """預託証券（ADS）の単位を確かめる。("ok", 1)=株数と単価の単位はそろっている /
    ("convert", n)=単価は ADS 1 つの値段で、株数は普通株の数（n で割れば ADS の数）/ ("mismatch", n)=分からない（一覧から外す）。
    pp は FMP の株価（ADR の会社は ADS 1 つの値段）"""
    if ADS_TITLE_RE.search(security or "") or not ADS_RE.search(text):
        return "ok", 1
    n = ads_ratio(text)
    if n == 1:
        return "ok", 1
    if n and pp > 0 and price > 0:
        if ADS_PRICE_LO <= price * n / pp <= ADS_PRICE_HI:
            return "ok", 1          # 単価は普通株 1 株の値段（n 倍すると ADS の株価に合う）
        if ADS_PRICE_LO <= price / pp <= ADS_PRICE_HI:
            return "convert", n     # 単価は ADS 1 つの値段
    return "mismatch", n or 0


def private_why(r, price):
    """市場の外の買い（私募・上場のときの割当など）の可能性の理由。注記=買いの行の脚注・備考の言葉 / 上場=同じ届け出に上場の言葉があり、単価が 0.5 ドル刻み /
    制度=報酬や会社の制度での取得の言葉、または株数に端数があり金額がちょうど（100 ドル単位）"""
    why = []
    shares = to_float(r.get("securitiesTransacted"))
    if "notes" in r or "docNote" in r:
        if OFFMARKET_RE.search(str(r.get("notes") or "") + " " + str(r.get("docNote") or "")):
            why.append("注記")
        if plan_hint(r.get("notes"), r.get("docNote"), shares):
            why.append("制度")
    elif r.get("privateHint"):
        why.append("注記")
    if r.get("ipoInFiling") and price > 0 and abs(price * 2 - round(price * 2)) < 1e-9:
        why.append("上場")
    amt = shares * price
    tol = max(0.10, 0.0005 * price + 0.00005 * shares + 0.01)   # 届け出の株数（小数 3〜4 けた）と単価（4 けた）の丸めの分のずれ
    if "制度" not in why and shares > 0 and abs(shares - round(shares)) > 1e-6 and amt >= 100 and abs(amt - round(amt / 100) * 100) <= tol:
        why.append("制度")
    return why


SPAC_RE = re.compile(r"acquisition corp|acquisition co\b|acquisition company|blank check|\bspac\b", re.I)
# 市場の外の買いの可能性（私募・上場時の割当・会社から直接・信託どうしの移し替えなど）。脚注（notes）と備考（docNote）を見る
OFFMARKET_RE = re.compile(
    r"private placement|securities purchase agreement|subscription agreement|registered direct|\bPIPE\b|public offering|underwritten offering|"
    r"direct offering|purchase agreement|rights offering|at-the-market|pursuant to the offering|in the offering|in connection with the offering|"
    r"initial public offering|\bIPO\b|directed share|offering price|underwriter|concurrent (?:private )?placement|"
    r"directly from the (?:company|issuer)|from the (?:company|issuer) in|purchased from the (?:company|issuer)|issued by the (?:company|issuer)|"
    r"irrevocable trust|between (?:the )?trusts|trust-to-trust|(?:transfer(?:red)?|exchange[ds]?|sold|sale) (?:of shares )?(?:from|to|between) (?:a |the |his |her |their )?(?:\w+ ){0,3}trust|"
    r"exchange agreement|in exchange for|business combination|merger agreement|closing of the (?:merger|transaction|business combination)|"
    r"rebalancing transaction|between (?:the )?(?:affiliated )?funds|(?:no shares were|not) (?:bought|sold|purchased)(?: or (?:bought|sold))? (?:in|on) the open market|contribution agreement|"
    r"privately[- ]negotiated|private (?:transactions?|sales?|purchases?)|\bin[- ]kind\b|pro rata distribution|in settlement of|"
    r"(?:were|was) not (?:\w+ ){0,2}(?:in|on|through) the open market|other than (?:in|on|through) the open market|outside (?:of )?the open market",
    re.I,
)
# 報酬や会社の制度での取得（市場でのふつうの買いではない）。plan_hint() で、持ち株の説明の文を除いて見る
PLAN_RE = re.compile(
    r"cashless exercise|exercise of (?:vested |the |his |her |their )?(?:stock )?options?|options? exercise|net exercise|"
    r"compensation plan|deferred compensation|\b409A\b|"
    r"employee (?:stock|share) purchase|\bESPP\b|(?:director|directors'?|dividend reinvestment and|direct) (?:stock|share) purchase plan|401\(k\)|"
    r"dividend reinvestment|\bDRIP\b|reinvest(?:ed|ment)? (?:of )?(?:the )?dividends?|dividends? (?:were |was )?reinvested|dividend investment plan|"
    r"in lieu of|director(?:'s|s')? (?:fees|compensation)|\bretainer\b|"
    r"shares were issued pursuant to|promissory note|"
    r"\bunits?\b[^.|]{0,120}\bwarrants?\b|\bwarrants?\b[^.|]{0,120}\bunits?\b",
    re.I,
)
PLAN_NOT_RE = re.compile(r"10b5-?1", re.I)
INCLUDES_RE = re.compile(r"^\s*(?:\(?\d+\)?\.?\s*)?(?:the\s+)?(?:reported\s+|total\s+)?(?:amount|balance|total|number of shares|holdings?)?\s*(?:also\s+)?includ(?:e|es|ed|ing)\b", re.I)
# 持ち株の説明の文の目印（買いの言葉より前にあれば、その文は持ち株の説明）。include・consists of・comprised of も同じ
HOLDING_WORDS_RE = re.compile(r"\b(?:of which|includ(?:e|es|ed|ing)|previously|since|consist(?:s|ing)? of|comprised of)\b", re.I)
REFLECTS_HOLDING_RE = re.compile(r"^\s*(?:\(?\d+\)?\.?\s*)?(?:this|the)?\s*(?:reported\s+)?(?:total|balance|amount|number of shares|holdings?)\s+(?:also\s+)?reflects?\b", re.I)
# 単位ちがい（届け出の株数が普通株で、単価が預託証券 ADS 1 口の値段など）の可能性
ADS_RE = re.compile(r"american depositary|\bADSs?\b|\bADRs?\b|depositary shares?|depositary receipts?", re.I)
ADS_TITLE_RE = re.compile(r"american depositary|\bADSs?\b|\bADRs?\b|depositary", re.I)
# 単価がドルでない可能性（ただし、注記に「ドルに換えた」ことが書いてあれば、単価はドル。額面（par value）や、ファンドの名前の中の通貨は数えない）
CONVERTED_RE = re.compile(r"convert|translat|exchange rate|U\.?S\.?\s?\$|US\$|U\.S\. dollars?|\bUSD\b|in dollars|dollar equivalent", re.I)
# 預託証券（ADS）1 つが何株分か（1 つ＝1 株なら、株数と単価の単位は同じ）
ADS_RATIO_RE = re.compile(r"(?:\bADSs?|\bADRs?|depositary (?:shares?|receipts?))\b[^.]{0,80}?represent(?:s|ing)?\s+(?:approximately\s+)?([0-9][0-9,.]*|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty|fifty|sixty|hundred)\b", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
            "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100}
CURRENCY_RE = re.compile(
    r"\breais\b|R\$|\bBRL\b|\byen\b|\bJPY\b|¥|\beuros?\b|€|\bEUR\b|pounds? sterling|\bGBP\b|£|canadian dollars?|\bCAD\b|C\$|"
    r"swiss francs?|\bCHF\b|hong kong dollars?|HK\$|\bHKD\b|\bRMB\b|renminbi|\byuan\b|\bNIS\b|shekels?|\brupees?\b|\bINR\b|"
    r"mexican pesos?|\bMXN\b|argentine pesos?|\bARS\b|korean won|\bKRW\b|\bSEK\b|\bNOK\b|\bDKK\b|australian dollars?|\bAUD\b|A\$|"
    r"new taiwan dollars?|\bNT\$|\bTWD\b|singapore dollars?|\bSGD\b|\bpesos?\b|local currency",
    re.I,
)
CAP_SHARE_LIMIT = 0.05          # 1人・1日の金額が会社の時価総額のこの割合を超え、
CAP_SHARE_MIN_USD = 250_000     # しかもこの金額以上なら「時価総額に比べて大きすぎる（会社から直接の買い・数字の誤りなどの可能性）」として外す
FUND_NAME_RE = re.compile(r"\bfunds?\b|municipal|closed[- ]end|\betf\b|income trust|interval", re.I)
MUTUAL_FUND_TICKER_RE = re.compile(r"^[A-Z]{4}X$")
NO_SYMBOL = {"", "NONE", "N/A", "NA", "N.A.", "[NONE]", "-", "NULL", "NOT APPLICABLE"}
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?$")      # 米国の銘柄コードの形（数字入りの外国のコードは外す）
UNIT_SUFFIX_RE = re.compile(r"[.\-](?:U|UN|W|WS|WT|R|RT)$")       # ユニット・ワラント・権利の銘柄は外す


def norm_symbol(raw):
    """'HEI, HEI.A' → 'HEI'。記号だけ・空・NONE などは '' にする"""
    s = str(raw or "").strip().upper()
    if s in NO_SYMBOL:
        return ""
    first = re.split(r"[,\s/;]+", s)[0].strip()
    if not SYMBOL_RE.match(first) or first in NO_SYMBOL or UNIT_SUFFIX_RE.search(first):
        return ""
    return first


def load_profiles(pattern):
    prof = {}
    for fp in sorted(glob.glob(pattern)):
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    prof[k.strip().upper()] = v
                    if v.get("symbol"):
                        prof.setdefault(str(v["symbol"]).strip().upper(), v)
    return prof

# 届け出人が「人」ではなく会社・ファンド・信託などかを名前で見る（取締役を出しているファンドなどは、役員ではなく「関係する会社」として扱う）
ENTITY_RE = re.compile(
    r"(\bL\.?\s?P\.?$|\bL\.?L\.?C\.?\b|\bINC\.?\b|\bCORP\.?\b|\bCORPORATION\b|\bFUNDS?\b|\bCAPITAL\b|\bPARTNERS\b|\bPARTNERSHIP\b|"
    r"\bMANAGEMENT\b|\bHOLDINGS?\b|\bADVISORS?\b|\bADVISERS?\b|\bLTD\.?\b|\bLIMITED\b|\bGROUP\b|\bINVESTMENTS?\b|"
    r"\bVENTURES?\b|\bS\.A\.|\bN\.V\.|\bPLC\b|\bA\.?G\.?$|\bGMBH\b|\bCOMPANY\b|\bBANK\b|\bINSURANCE\b|\bASSET\b|\bSPV\b|\bOPPORTUNITY\b|\bMASTER\b)",
    re.I,
)


def is_entity(name: str) -> bool:
    """個人の届け出は「姓 名」の順なので、最初の語（姓）は見ない（Bank さん・Master さんなどを会社と間違えないため）"""
    parts = str(name or "").replace(",", " ").split()
    rest = " ".join(parts[1:])
    return bool(rest) and bool(ENTITY_RE.search(rest))


# ---- 役職の読み替え ----
# 届け出の役職の文（officerTitle）を「、」「&」「and」などで区切り、区切りごとに一番上の役職を決めて、その中で一番上の物を出す。
# 「President」は、後ろに部門の名前が付いていれば（President - Foot Locker Intl・President International・Pres. of N. Am. Ops・President, Network Solutions）
# 部門の責任者で、会社の社長ではない。「Vicepresident」（間に空白なし）も副社長。
R_CEO, R_CHAIR, R_PRES, R_CFO, R_COO, R_VCHAIR, R_CHIEF, R_DIV, R_OFFICER = (
    "CEO（社長）", "会長", "社長・プレジデント", "CFO（財務の責任者）", "COO（運営の責任者）", "副会長", "役員（経営幹部）", "役員（部門の責任者）", "役員")
ROLE_RANK = [R_CEO, R_CHAIR, R_PRES, R_CFO, R_COO, R_VCHAIR, R_CHIEF, R_DIV, R_OFFICER]
TOP_ROLES = {R_CEO, R_CFO, R_CHAIR, R_PRES}
ROLE_WORD_RE = re.compile(
    r"chief|\bc[a-z]{1,3}o\b|\bceo\b|president|\bpres\b|\bpres\.|chair|\bcob\b|\bvp\b|\bevp\b|\bsvp\b|vice|principal|\bpeo\b|\bpfo\b|"
    r"officer|treasurer|secretary|counsel|\bgc\b|controller|director|founder|executive|manager|head\b|partner", re.I)
SEE_REMARKS_RE = re.compile(r"^\s*see\s+(?:remarks|explanation|footnote|attached|below)", re.I)
# 役職の文が、発行会社ではなく投資顧問・ファンドなどでの立場を書いた物か（その人は「関係者」のまま）
ADVISER_CTX_RE = re.compile(r"advis|sub-?adv|\bIA\b|portfolio|trustee|emeritus|former|control person|owner group|10 ?%|affiliate|consultant|"
                            r"investment committee|of the fund|\bfund\b|general partner|managing member|limited partner", re.I)


def _segment_role(seg: str):
    """役職の文の 1 区切りの役職（読めなければ None）"""
    t = seg.strip().lower()
    if not t:
        return None
    if re.search(r"vice[\s-]*chair", t):
        return R_VCHAIR
    # 「Deputy」「Assistant」「Associate」の付く役職は、その役職より下（元の役職より上に出さない）。
    # Deputy CEO・Deputy Co-COO・Deputy President は経営幹部、Deputy Chairman は副会長。ほか（Deputy General Counsel・Assistant Treasurer・
    # Associate Vice President・Executive Assistant to the CEO など）は役員
    if re.search(r"\b(?:deputy|assistant|associate|asst|assoc)\b\.?", t):
        if re.search(r"\bdeputy\b", t) and re.search(r"chair|\bcob\b", t) and not re.search(r"\bto the\b", t):
            return R_VCHAIR
        if re.search(r"\bdeputy\b", t) and re.search(r"chief|\bc[a-z]{1,3}o\b|president|\bpres\b", t) and not re.search(r"\bto the\b", t):
            return R_CHIEF
        return R_OFFICER
    if re.search(r"\b(?:to|for|of) the (?:ceo|chief executive|president|chairman)\b", t):
        return R_CHIEF   # Chief of Staff to the CEO など（社長の補佐）
    if re.search(r"chief executive|\bco-?ceo\b|\bceo\b|principal executive|\bpeo\b", t):
        return R_CEO
    if re.search(r"chairman|chairperson|chairwoman|chair of the board|executive chair|\bchair\b|\bco-?cob\b|\bcob\b", t):
        return R_CHAIR
    if re.search(r"chief financial|\bcfo\b|principal financial|\bpfo\b", t):
        return R_CFO
    if re.search(r"chief operating|\bco-?coo\b|\bcoo\b", t):
        return R_COO
    if re.search(r"\bchief\b|\bc(?!eo\b|fo\b|oo\b)[a-z]{1,3}o\b", t):
        return R_CHIEF
    if re.search(r"vice[\s-]*pres|\bvp\b|\bevp\b|\bsvp\b|\bexec(?:utive)?\.?\s+vp\b|\bsenior vp\b", t):
        return R_DIV
    m = re.search(r"\bpres(?:ident)?\b\.?(.*)$", t)
    if m:
        rest = re.sub(r"^[\s\-–—:,]+", "", m.group(1)).strip(" .")
        if not rest or re.fullmatch(r"(?:of|for) (?:the )?(?:company|issuer|corporation|bank|registrant)", rest):
            return R_PRES
        return R_DIV
    if re.search(r"\bdirector of\b|\bhead of\b|\bmanaging director\b", t):
        return R_DIV
    if re.search(r"general counsel|\bgc\b|secretary|treasurer|controller|principal accounting|officer|executive|founder", t):
        return R_OFFICER
    return None


def officer_title_role(title: str):
    """役職の文全体の役職（区切りごとの役職のうち一番上。読めなければ None）"""
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        return None
    segs = []
    for part in re.split(r",|;|/|&|\band\b|\(|\)", t, flags=re.I):
        part = part.strip()
        if not part:
            continue
        # 役職の言葉が無い区切り（例「Network Solutions」）は、前の区切りの続き（部門の名前）として扱う
        if segs and not ROLE_WORD_RE.search(part):
            segs[-1] = segs[-1] + " - " + part
        else:
            segs.append(part)
    roles = [r for r in (_segment_role(x) for x in segs) if r]
    if not roles:
        return None
    return min(roles, key=ROLE_RANK.index)


def owner_tokens(type_of_owner: str):
    """'director, officer: President & CEO, 10 percent owner' → [('director',''), ('officer','President & CEO'), ('10 percent owner','')]"""
    out = []
    for t in re.split(r",\s*(?=(?:director|officer|10 percent owner|other)\b)", str(type_of_owner or "").strip(), flags=re.I):
        t = t.strip()
        if not t:
            continue
        m = re.match(r"(director|officer|10 percent owner|other)\s*:?\s*(.*)$", t, re.I)
        if m:
            out.append((m.group(1).lower(), m.group(2).strip()))
        else:
            out.append(("text", t))
    return out


def _title_text(title: str, remarks: str) -> str:
    """「See Remarks」などなら、届け出の備考（remarks）の文を役職の文として使う（短く、投資顧問などの話でないときだけ）"""
    if SEE_REMARKS_RE.match(title or ""):
        r = str(remarks or "").strip()
        if r and len(r) <= 300 and not ADVISER_CTX_RE.search(r):
            return r
        return ""
    return title


def officer_roles(type_of_owner: str, remarks: str = ""):
    """役員としての役職の一覧。officer の文と、other の文のうち発行会社の役職とはっきり読める物（Chief Revenue Officer・Chairman of the Board など）"""
    roles = []
    has_officer = False
    for k, title in owner_tokens(type_of_owner):
        if k == "officer":
            has_officer = True
            r = officer_title_role(_title_text(title, remarks))
            roles.append(r or R_OFFICER)
        elif k == "other":
            text = _title_text(title, remarks)
            if text and not ADVISER_CTX_RE.search(text):
                r = officer_title_role(text)
                if r:
                    has_officer = True
                    roles.append(r)
    return roles, has_officer


def role_jp(type_of_owner: str, name: str = "", remarks: str = "") -> str:
    toks = owner_tokens(type_of_owner)
    kinds = {k for k, _ in toks}
    if is_entity(name):
        if "officer" in kinds or "director" in kinds:
            return "関係する会社（取締役を出している）"
        if "10 percent owner" in kinds:
            return "大株主（10%以上）"
        return "関係する会社・ファンド"
    roles, has_officer = officer_roles(type_of_owner, remarks)
    if has_officer:
        return min(roles, key=ROLE_RANK.index) if roles else R_OFFICER
    if "director" in kinds:
        return "取締役"
    if "10 percent owner" in kinds:
        return "大株主（10%以上）"
    for k, text in toks:
        if k == "other":
            return "関係者" + ("（" + text[:24] + "）" if text else "")
    # FMP の古い形（'officer: ...' だけ・'Director' だけ）にも合わせる
    r = officer_title_role(type_of_owner)
    if r:
        return r
    if re.search(r"director", type_of_owner or "", re.I):
        return "取締役"
    if re.search(r"10 percent|10% owner|ten percent", type_of_owner or "", re.I):
        return "大株主（10%以上）"
    return "役員・関係者（役職を読み取れず）"


def is_insider_person(type_of_owner: str, name: str = "", remarks: str = "") -> bool:
    """役員か取締役か（会社の中の「人」）。名誉取締役など 'other' だけの人（発行会社の役職がはっきり書いてあれば役員）、10% 大株主だけの人、
    会社・ファンドの名前の届け出人は False"""
    if is_entity(name):
        return False
    kinds = {k for k, _ in owner_tokens(type_of_owner)}
    if kinds & {"officer", "director"}:
        return True
    return officer_roles(type_of_owner, remarks)[1]


def holder_totals(lines):
    """持ち株の線（直接・間接、持ち主の書き方、証券名）ごとに、行（株数, 取引後の持ち株）を持ち主のつながりで分け、
    持ち主ごとの「買う前の持ち株」と「取引後の持ち株」を足す。同じ線の中でも、ある行の「取引後−株数」が別の行の取引後の持ち株と
    同じなら同じ持ち主の続き（同じ日に何回かに分けて買った）。つながらない行は別の持ち主（同じ書き方の別のファンドや信託）。
    取引後の持ち株が同じ数の行は、1 人の持ち主の分（行ごとに最後の持ち株を書く届け出）。戻り値: (買う前の合計, 取引後の合計, 数が合うか)"""
    before = after = 0.0
    ok = True
    for v in lines.values():
        rows = v["rows"]
        owned = sorted({round(o, 4) for _, o in rows})
        groups = defaultdict(float)
        for sh, o in rows:
            groups[round(o, 4)] += sh
        bases = {}
        for o, shs in groups.items():
            bases[o] = o - shs
        for o, base in bases.items():
            if not any(abs(base - o2) < 1.0 for o2 in owned if abs(o2 - o) > 1e-9):
                before += base           # この持ち主の初めの持ち株（つながりの頭）
                if base < -1e-6:
                    ok = False
            if not any(abs(b2 - o) < 1.0 for o2, b2 in bases.items() if abs(o2 - o) > 1e-9):
                after += o               # この持ち主の最後の持ち株（つながりの終わり）
    return before, after, ok


def us_holidays(year):
    """米国の連邦の祝日（振替を含む）。SEC（EDGAR）が休む日。browser/plan_days.py と同じ"""
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
        hs.add(date(year, 12, 31))
    return hs


def is_holiday(d):
    return d in us_holidays(d.year)


def parse_date(s):
    if not s:
        return None
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def to_float(v):
    try:
        if v is None or v == "":
            return 0.0
        x = float(v)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def unwrap(data):
    """MCP の返りや日ファイルから、行の配列を取り出す"""
    if isinstance(data, dict):
        for k in ("data", "result", "results", "rows", "items"):
            if isinstance(data.get(k), list):
                return data[k]
        if "content" in data:
            try:
                return json.loads(data["content"][0]["text"])
            except Exception:
                return None
    return data if isinstance(data, list) else None


def load_rows(raw_dir):
    rows, days = [], {}
    files = sorted(fp for fp in glob.glob(os.path.join(raw_dir, "*.json"))
                   if os.path.basename(fp) not in ("usdjpy.json", "checks.json", "data.json"))
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                print(f"[warn] JSON が読めない: {fp}: {e}", file=sys.stderr)
                continue
        if isinstance(data, dict) and "date" in data and "rows" in data:
            days[data["date"]] = {"complete": bool(data.get("complete")), "index_missing": bool(data.get("index_missing")),
                                  "filings_seen": int(data.get("filings_seen") or 0), "rows": len(data.get("rows") or []),
                                  "fetched_at": str(data.get("fetched_at") or ""), "confirmed_at": str(data.get("confirmed_at") or "")}
        arr = unwrap(data)
        if not isinstance(arr, list):
            print(f"[warn] 行の配列ではない: {fp}", file=sys.stderr)
            continue
        for r in arr:
            if isinstance(r, dict):
                r["_src"] = os.path.basename(fp)
                rows.append(r)
    return rows, files, days


def load_quotes(qdir):
    q = {}
    for fp in sorted(glob.glob(os.path.join(qdir, "*.json"))):
        with open(fp, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue
        arr = unwrap(data)
        if not isinstance(arr, list):
            continue
        for r in arr:
            if isinstance(r, dict) and r.get("symbol"):
                prev = q.get(r["symbol"], {})
                q[r["symbol"]] = {
                    "name": r.get("name") or r.get("companyName") or prev.get("name") or "",
                    "price": to_float(r.get("price")) or prev.get("price") or 0.0,
                    "exchange": r.get("exchange") or prev.get("exchange") or "",
                    "marketCap": to_float(r.get("marketCap")) or prev.get("marketCap") or 0.0,
                }
    return q


def load_usdjpy(fp):
    if not os.path.exists(fp):
        return None, None
    with open(fp, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return None, None
    arr = unwrap(data)
    r = arr[0] if isinstance(arr, list) and arr else (data if isinstance(data, dict) else None)
    if not isinstance(r, dict):
        return None, None
    rate = to_float(r.get("price") or r.get("rate") or r.get("close") or r.get("bid"))
    asof = r.get("date") or r.get("timestamp") or ""
    return (rate if rate > 0 else None), str(asof)


def fx_jst(asof):
    """'2026-10-27 12:49 UTC' や UNIX 秒を、日本時間の 'MM/DD HH:MM' にする。読めなければ空"""
    try:
        s = str(asof or "").strip()
        if re.fullmatch(r"\d{9,11}", s):
            t = datetime.fromtimestamp(int(s), tz=timezone.utc)
        else:
            t = datetime.strptime(s.replace(" UTC", "").replace("Z", "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return (t + timedelta(hours=9)).strftime("%m/%d %H:%M")
    except Exception:
        return ""


def fx_label(asof):
    """画面の円の説明に付ける文。時刻のある値なら '10/28 22:01 日本時間の時点'、日付だけ（frankfurter.dev の欧州中央銀行の参照レート）なら '10/30 の欧州中央銀行の参照レート'。読めなければ空"""
    s = str(asof or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%m/%d") + " の欧州中央銀行の参照レート"
        except Exception:
            return ""
    j = fx_jst(s)
    return (j + " 日本時間の時点") if j else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="raw", help="生データの置き場（*.json。fetch_edgar.py の日ごとのファイルか FMP の返り）")
    ap.add_argument("--quotes", default=None, help="会社名・現在値の置き場（省略時は <raw>/quotes）")
    ap.add_argument("--fx", default=None, help="ドル円のファイル（省略時は <raw>/usdjpy.json）")
    ap.add_argument("--profiles", default=None, help="会社情報（FMP）の置き場の glob（省略時は <quotes の親>/profiles*.json）")
    ap.add_argument("--out", default="out")
    ap.add_argument("--window", type=int, default=WINDOW_DAYS_DEFAULT)
    ap.add_argument("--asof", default=None, help="集計時刻 (ISO, UTC)。省略時は今")
    ap.add_argument("--partial-ok", action="store_true", help="日ファイルの欠けを NG にしない（試し見用。公開には使わない）")
    args = ap.parse_args()

    now = datetime.now(timezone.utc) if not args.asof else datetime.fromisoformat(args.asof.replace("Z", "+00:00"))
    today = now.date()
    cutoff = today - timedelta(days=args.window - 1)   # 窓は今日を入れて window 日（画面の「直近30日」と同じ数え方）

    raw_rows, files, days = load_rows(args.raw)
    quotes = load_quotes(args.quotes or os.path.join(args.raw, "quotes"))
    qdir = args.quotes or os.path.join(args.raw, "quotes")
    profiles = load_profiles(args.profiles or os.path.join(os.path.dirname(os.path.normpath(qdir)), "profiles*.json"))
    ov_fp = os.path.join(os.path.dirname(os.path.normpath(qdir)), "overrides.json")
    overrides = json.load(open(ov_fp, encoding="utf-8")) if os.path.exists(ov_fp) else {}
    # SEC の銘柄と取引所の一覧の控え（取り込み v2.3 より前の行には issuerExchange が無いので、そのときに使う）
    sx_fp = os.path.join(os.path.dirname(os.path.normpath(qdir)), "sec_exchange.json")
    secmap = json.load(open(sx_fp, encoding="utf-8")) if os.path.exists(sx_fp) else {}
    usdjpy, usdjpy_asof = load_usdjpy(args.fx or os.path.join(args.raw, "usdjpy.json"))
    from_edgar = any("accession" in r for r in raw_rows)

    checks = []
    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("生データの読み込み", len(raw_rows) > 0, f"{len(files)} ファイル / {len(raw_rows)} 行")

    # ---- 0. 日ファイルの欠け（EDGAR から取った日ファイルのとき） ----
    if days:
        missing, incomplete, waiting = [], [], []
        d = cutoff
        while d <= today - timedelta(days=1):
            if d.weekday() < 5:
                info = days.get(d.isoformat())
                old_enough = d <= today - timedelta(days=2)
                if info is None:
                    if old_enough:
                        missing.append(d.isoformat())
                elif not info["complete"]:
                    incomplete.append(d.isoformat())
                elif (info["index_missing"] and old_enough and not is_holiday(d)
                      and max(info["fetched_at"], info["confirmed_at"]) < (d + timedelta(days=3)).isoformat() + "T12:00"):
                    # 祝日でない平日に索引が無い（SEC が休んだか、まだ出していない）。3 日後の 12:00 UTC までは毎回確かめる（plan_days.py と同じ）。
                    # ページ全体を止めないため NG にはしない（その日は画面で「前」。2 行目に書く）
                    waiting.append(d.isoformat())
            d += timedelta(days=1)
        ok = (not missing and not incomplete) or args.partial_ok
        check("期間の平日の分が全部そろっている（2日前まで）", ok,
              ("欠け " + ",".join(missing) if missing else "") + (" 不完全 " + ",".join(incomplete) if incomplete else "") or "そろっている")
        check("索引がまだ無い平日（休みか遅れ。3 日後まで確かめる）", True, ",".join(waiting) if waiting else "無し")
        idx_missing = [k for k, v in sorted(days.items()) if v["index_missing"] and parse_date(k) and parse_date(k) >= cutoff]
        check("届け出が1件も無かった平日（休日か SEC の未公開）", True, ",".join(idx_missing) if idx_missing else "無し")

    # ---- 1. 買い（P）だけを残す ----
    kept, dropped = [], defaultdict(int)
    seen = set()
    for r in raw_rows:
        tt = str(r.get("transactionType") or "")
        if not tt.upper().startswith("P"):
            dropped["買い以外"] += 1
            continue
        if str(r.get("acquisitionOrDisposition") or "A").upper() != "A":
            dropped["処分(D)"] += 1
            continue
        shares = to_float(r.get("securitiesTransacted"))
        price = to_float(r.get("price"))
        if shares <= 0 or price <= 0:
            dropped["株数か単価が0"] += 1
            continue
        sec = str(r.get("securityName") or "")
        if NON_EQUITY_RE.search(sec):
            dropped["株以外(オプション等)"] += 1
            continue
        fd = parse_date(r.get("filingDate"))
        td = parse_date(r.get("transactionDate"))
        if not fd:
            dropped["届け出日なし"] += 1
            continue
        if fd < cutoff:
            dropped["期間外"] += 1
            continue
        if fd > today + timedelta(days=1):
            dropped["未来の日付"] += 1
            continue
        sym_raw = str(r.get("symbol") or "").strip().upper()
        sym = norm_symbol(sym_raw)
        if not sym:
            dropped["銘柄なし"] += 1
            continue
        cik = str(r.get("reportingCik") or "").strip() or ("name:" + str(r.get("reportingName") or "").strip().lower())
        form = str(r.get("formType") or "4").strip()
        if r.get("accession") and r.get("line") is not None:
            key = ("acc", str(r.get("accession")), int(r.get("line")))   # 同じ届け出の中の別の取引は、株数・単価が同じでも別に数える
        else:
            key = (sym, cik, str(td), round(shares, 4), round(price, 4), sec.lower(), form, str(fd))
        if key in seen:
            dropped["重複"] += 1
            continue
        seen.add(key)
        kept.append({
            "symbol": sym, "symbol_raw": sym_raw, "cik": cik,
            "name": str(r.get("reportingName") or "").strip(),
            "type_of_owner": str(r.get("typeOfOwner") or "").strip(),
            "filing_date": fd, "transaction_date": td or fd,
            "shares": shares, "price": price, "usd": shares * price,
            "owned_after": to_float(r.get("securitiesOwned")),
            "direct": str(r.get("directOrIndirect") or ""),
            "form": form, "security": sec,
            "url": str(r.get("url") or r.get("link") or ""),
            "issuer_name": str(r.get("issuerName") or "").strip(),
            "issuer_exchange": str(r.get("issuerExchange") or "").strip(),
            "company_cik": str(r.get("companyCik") or "").strip().lstrip("0"),
            "plan10b5": bool(r.get("plan10b5")),
            "notes": str(r.get("notes") or ""),
            "doc_note": str(r.get("docNote") or ""),
            "nature": str(r.get("natureOfOwnership") or "").strip(),
            "private_why": private_why(r, price),
            "joint": [str(x) for x in (r.get("jointNames") or []) if x],
            "joint_types": [str(x) for x in (r.get("jointTypes") or [])],
        })
    check("売り(S)が1件も残っていない", all(k["usd"] > 0 for k in kept), f"残り {len(kept)} 行 / 落とした内訳 {dict(dropped)}")

    # ---- 1b. 訂正（4/A）があれば、同じ人・会社・買った日・証券名の元の届け出（4）は捨てる ----
    amended_keys = {(k["symbol"], k["cik"], k["transaction_date"], k["security"].lower()) for k in kept if k["form"].upper().endswith("/A")}
    before_amend = len(kept)
    kept = [k for k in kept if k["form"].upper().endswith("/A") or (k["symbol"], k["cik"], k["transaction_date"], k["security"].lower()) not in amended_keys]
    dropped["訂正で置き換え"] = before_amend - len(kept)

    # ---- 1c. 関連会社が同じ買いを別々に届け出た物（会社・買った日・株数・単価・取引後の持ち株・証券名が一致）は 1 つにまとめる ----
    by_same = defaultdict(list)
    for k in kept:
        by_same[(k["symbol"], k["transaction_date"], round(k["shares"], 4), round(k["price"], 4), round(k["owned_after"], 4), k["security"].lower())].append(k)
    merged = []

    def _norm(t):
        return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

    def _related(a, b):
        """同じ持ち株を別々に届け出た物か: 同じ届け出・名前（共同の届け出人を含む）が重なる・片方の名前がもう片方の脚注に出てくる"""
        if a["url"] and a["url"] == b["url"]:
            return True
        nat = _norm(a["nature"])
        if a["direct"] == "I" and b["direct"] == "I" and nat and nat == _norm(b["nature"]) and not re.fullmatch(r"(?:see|by)? ?(?:footnotes?|notes?|remarks|explanation of responses|explanation)(?: \d+)*", nat):
            return True   # 同じ持ち主の書き方（例「By ABC Fund LP」）の間接保有
        na, nb = _name_forms(a), _name_forms(b)
        if na & nb:
            return True
        ta, tb = " " + _norm(a["notes"] + " " + a["doc_note"]) + " ", " " + _norm(b["notes"] + " " + b["doc_note"]) + " "
        return any((" " + n + " ") in tb for n in na) or any((" " + n + " ") in ta for n in nb)

    LEGAL_TAIL = {"llc", "lp", "llp", "lllp", "inc", "ltd", "corp", "co", "plc", "nv", "sa", "ag", "gmbh", "l", "p", "c", "n", "v", "s", "a"}

    def _name_forms(k):
        """名前の書き方の候補（会社の LLC・LP などを外した物、人の「姓 名」を「名 姓」にした物）。8 字以上・2 語以上の物だけ"""
        out = set()
        for n in [k["name"]] + k["joint"]:
            w = _norm(n).split()
            tail = False
            while w and w[-1] in LEGAL_TAIL:
                w.pop()
                tail = True
            if not w:
                continue
            if tail or is_entity(n):
                core = " ".join(w)
                if len(core) >= 6:     # 会社の名前（例 Holdco LLC → holdco）
                    out.add(core)
                continue
            if len(w) < 2:
                continue
            forms = {" ".join(w), " ".join([w[1], w[0]] + w[2:]), w[1] + " " + w[0]}   # 人の名前（姓 名 → 名 姓 も）
            out |= {f for f in forms if len(f) >= 8}
        return out

    groups_same = []
    for grp in by_same.values():
        # 株数・単価・持ち株が同じでも、関係の無い別々の持ち主（例: 別々のファンドが同じ条件で買った）は 1 つにしない。関係のある物どうしだけをまとめる
        comp = list(range(len(grp)))
        def find(i):
            while comp[i] != i:
                comp[i] = comp[comp[i]]
                i = comp[i]
            return i
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                if _related(grp[i], grp[j]):
                    comp[find(i)] = find(j)
        parts = defaultdict(list)
        for i, k in enumerate(grp):
            parts[find(i)].append(k)
        groups_same.extend(parts.values())
    for grp in groups_same:
        # 役員・取締役どうし（会社の中の人が2人）なら、ふつうは別の人の同じ買いなので、まとめない。
        # ただし全員が間接保有（I）で、持ち主の書き方（natureOfOwnership）が同じなら、同じファンドなどの株を別々に届け出ただけなので 1 つにする
        same_holder = all(k["direct"] == "I" for k in grp) and len({_norm(k["nature"]) for k in grp}) == 1 and _norm(grp[0]["nature"]) != ""
        if len(grp) == 1 or (all(is_insider_person(k["type_of_owner"], k["name"], k["doc_note"]) for k in grp) and not same_holder):
            merged.extend(grp)
            continue
        grp.sort(key=lambda k: (0 if is_insider_person(k["type_of_owner"], k["name"], k["doc_note"]) else 1, k["name"]))
        head = grp[0]
        for other in grp[1:]:
            if other["name"] and other["name"] != head["name"] and other["name"] not in head["joint"]:
                head["joint"].append(other["name"])
                head["joint_types"].append(other["type_of_owner"])
            for u in (other["url"],):
                if u and u != head["url"] and u not in head.setdefault("more_urls", []):
                    head["more_urls"].append(u)
            # 同じ買いの別の届け出に書かれた印（市場の外の買いの理由・計画での買い・注記）も引き継ぐ
            for w in other["private_why"]:
                if w not in head["private_why"]:
                    head["private_why"].append(w)
            head["plan10b5"] = head["plan10b5"] or other["plan10b5"]
            if other["notes"] and other["notes"] not in head["notes"]:
                head["notes"] = (head["notes"] + " | " + other["notes"]).strip(" |")
            if other["doc_note"] and other["doc_note"] not in head["doc_note"]:
                head["doc_note"] = (head["doc_note"] + " | " + other["doc_note"]).strip(" |")
        dropped["関連会社の同じ買い"] += len(grp) - 1
        merged.append(head)
    kept = merged

    # ---- 2. 1人・1日ごとにまとめる ----
    pd = {}
    for k in kept:
        note_text = k["notes"] + " " + k["doc_note"]
        pp_k = to_float((profiles.get(k["symbol"]) or profiles.get(k["symbol_raw"]) or {}).get("price"))
        ads_st, ads_n = ads_units(note_text, k["security"], k["price"], pp_k)
        if ads_st == "convert" and ads_n > 1 and abs(k["shares"] / ads_n - round(k["shares"] / ads_n)) < 1e-6:
            # 株数が普通株の数で、単価が ADS 1 つの値段 → 株数を ADS の数に直す（金額が n 倍に大きく出ないように）
            k["shares"] = k["shares"] / ads_n
            k["usd"] = k["shares"] * k["price"]
            k["owned_after"] = k["owned_after"] / ads_n
            k["ads_converted"] = ads_n
        elif ads_st != "ok":
            k["ads_bad"] = True
        pk = (k["symbol"], k["cik"], k["transaction_date"])
        a = pd.get(pk)
        if not a:
            a = pd[pk] = {
                "symbol": k["symbol"], "symbol_raw": k["symbol_raw"], "cik": k["cik"], "name": k["name"],
                "roles_raw": k["type_of_owner"], "role": role_jp(k["type_of_owner"], k["name"], k["doc_note"]),
                "insider": is_insider_person(k["type_of_owner"], k["name"], k["doc_note"]),
                "date": k["transaction_date"], "filing_date": k["filing_date"],
                "shares": 0.0, "usd": 0.0, "owned_after": 0.0, "lots": 0,
                "urls": [], "forms": set(), "security": k["security"], "direct": k["direct"],
                "price_min": k["price"], "price_max": k["price"], "row_max_usd": 0.0,
                "issuer_name": k["issuer_name"], "issuer_exchange": k["issuer_exchange"], "company_cik": k["company_cik"], "plan10b5": False, "private_why": [], "joint": [],
                "ads_mismatch": False, "ads_converted": 0, "currency": False, "foreign_market": False, "lines": {}, "by_sec": {},
            }
        ln = a["lines"].setdefault((k["direct"], re.sub(r"[^a-z0-9]+", " ", k["nature"].lower()).strip(), re.sub(r"[^a-z0-9]+", " ", k["security"].lower()).strip()), {"rows": []})
        ln["rows"].append((k["shares"], k["owned_after"]))
        bs = a["by_sec"].setdefault(re.sub(r"[^a-z0-9]+", " ", k["security"].lower()).strip(), {"security": k["security"], "shares": 0.0, "usd": 0.0, "price_min": k["price"], "price_max": k["price"]})
        bs["shares"] += k["shares"]
        bs["usd"] += k["usd"]
        bs["price_min"] = min(bs["price_min"], k["price"])
        bs["price_max"] = max(bs["price_max"], k["price"])
        if k.get("ads_converted"):
            a["ads_converted"] = k["ads_converted"]
        a["shares"] += k["shares"]
        a["usd"] += k["usd"]
        a["row_max_usd"] = max(a["row_max_usd"], k["usd"])
        a["lots"] += 1
        a["owned_after"] = max(a["owned_after"], k["owned_after"])
        a["filing_date"] = max(a["filing_date"], k["filing_date"])
        a["price_min"] = min(a["price_min"], k["price"])
        a["price_max"] = max(a["price_max"], k["price"])
        for u in [k["url"]] + k.get("more_urls", []):
            if u and u not in a["urls"]:
                a["urls"].append(u)
        a["forms"].add(k["form"])
        a["plan10b5"] = a["plan10b5"] or k["plan10b5"]
        for w in k["private_why"]:
            if w not in a["private_why"]:
                a["private_why"].append(w)
        if k.get("ads_bad"):
            a["ads_mismatch"] = True
        if currency_suspect(note_text):
            a["currency"] = True
        if foreign_market(note_text):
            a["foreign_market"] = True
        if not a["issuer_name"] and k["issuer_name"]:
            a["issuer_name"] = k["issuer_name"]
        if not a["issuer_exchange"] and k["issuer_exchange"]:
            a["issuer_exchange"] = k["issuer_exchange"]
        if not a["insider"] and is_insider_person(k["type_of_owner"], k["name"], k["doc_note"]):
            a["insider"] = True
        for j in k["joint"]:
            if j not in a["joint"]:
                a["joint"].append(j)
    person_days = list(pd.values())
    for a in person_days:
        a["avg_price"] = a["usd"] / a["shares"] if a["shares"] else 0.0
        secs = {key[2] for key in a["lines"]}
        before, after_total, ok_base = holder_totals(a["lines"])
        a["stake_increase_pct"] = round(a["shares"] / before * 100, 1) if (len(secs) == 1 and before > 0 and ok_base) else None
        a["owned_after_total"] = after_total if len(secs) == 1 else None   # 持ち主ごとの取引後の持ち株の合計（種類の違う株を買った日は出さない）
        a["big_raw"] = a["usd"] >= BIG_USD
        a["amended"] = any(f.upper().endswith("/A") for f in a["forms"])
        a["late"] = (a["filing_date"] - a["date"]).days > LATE_FILING_DAYS
        # 1回・1つの単価で、合計がちょうど区切りのよい金額（例 100万ドル）→ 会社と決めた値段で直接買った可能性
        if (a["lots"] == 1 and a["price_min"] == a["price_max"] and a["usd"] >= ROUND_DEAL_MIN_USD and abs(a["usd"] - round(a["usd"], -5)) < 1.0
                and abs(a["shares"] / 100 - round(a["shares"] / 100)) > 1e-9):   # 株数が 100 株単位でない（金額から逆に決めた株数）
            if "金額" not in a["private_why"]:
                a["private_why"].append("金額")
        a["private_hint"] = bool(a["private_why"])
        # 市場での買いと比べにくい物（計画買い・遅れ・私募の疑い）は「平均買値との比較」を出さない
        a["compare_ok"] = not (a["plan10b5"] or a["late"] or a["private_hint"])

    sum_rows = sum(k["usd"] for k in kept)
    sum_pd = sum(a["usd"] for a in person_days)
    check("金額の検算（行の合計 = まとめた合計）", abs(sum_rows - sum_pd) < 0.01, f"{sum_rows:,.0f} vs {sum_pd:,.0f}")

    # ---- 2b. 数字が合わない届け出をはじく（一覧から外し、外した物は suspects に残す） ----
    sym_prices = defaultdict(list)
    for k in kept:
        sym_prices[k["symbol"]].append(k["price"])
    suspects, clean = [], []
    for a in person_days:
        reason = ""
        prof = profiles.get(a["symbol"]) or profiles.get(a["symbol_raw"]) or {}
        pp = to_float(prof.get("price"))
        others = sorted(p for p in sym_prices[a["symbol"]])
        med = others[len(others) // 2] if others else 0.0
        mcap = to_float(prof.get("market_cap"))
        if a["row_max_usd"] > SUSPECT_ROW_USD:
            reason = "1件の金額が10億ドルを超える（届け出の書き間違いの疑い）"
        elif a["currency"]:
            reason = "届け出の注記に外国のお金の名前があり、単価がドルでない可能性"
        elif a["ads_mismatch"]:
            reason = "届け出の注記に預託証券（ADS）の説明があり、株数と単価の単位がちがう可能性"
        elif mcap > 0 and a["usd"] > mcap * CAP_SHARE_LIMIT and a["usd"] >= CAP_SHARE_MIN_USD:
            reason = f"1人・1日の金額 ${a['usd']:,.0f} が会社の時価総額 ${mcap:,.0f} の5%を超える（会社から直接の買い・数字の誤りなどの可能性）"
        elif pp > 0 and a["avg_price"] > 0 and not prof.get("is_adr") and (a["avg_price"] / pp > SUSPECT_PRICE_RATIO or pp / a["avg_price"] > SUSPECT_PRICE_RATIO):
            # ADR（預託証券）は 1 単位が現地の株の何株分かなので、現地の株の単価と比べない
            reason = f"届け出の単価 ${a['avg_price']:,.2f} が今の株価 ${pp:,.2f} と10倍以上ずれる（書き間違いの疑い）"
        elif len(others) >= 3 and med > 0 and not prof.get("is_adr") and (a["avg_price"] / med > 20 or med / a["avg_price"] > 20):
            reason = "同じ銘柄のほかの届け出と単価が20倍以上ずれる（書き間違いの疑い）"
        if reason:
            suspects.append({"symbol": a["symbol"], "name": a["name"], "date": a["date"].isoformat(),
                             "usd": round(a["usd"], 2), "avg_price": round(a["avg_price"], 4), "reason": reason, "urls": a["urls"]})
        else:
            clean.append(a)
    dropped["数字が合わない届け出"] = len(suspects)
    person_days = clean

    # ---- 2c. 同じ会社で 3 人以上の役員が、同じ日・同じ単価で買った物は「報酬などの決まった買いの可能性」 ----
    groups = defaultdict(set)
    for a in person_days:
        if a["insider"]:
            groups[(a["symbol"], a["date"], round(a["avg_price"], 4))].add(a["cik"])
    # 同じ日・同じ単価の組（3 人以上）がある日に、その組とほぼ同じ金額（±1%）を、別の単価（別の種類の株など）で買った役員も同じ扱いにする
    # （例: 決まった額の報酬を、取締役ごとにクラス A 株と普通株に分けて受け取った）
    group_usd = defaultdict(list)
    for a in person_days:
        g = groups.get((a["symbol"], a["date"], round(a["avg_price"], 4)), ())
        if a["insider"] and len(g) >= SAME_PRICE_PEOPLE:
            group_usd[(a["symbol"], a["date"])].append(a["usd"])
    for a in person_days:
        a["same_price"] = len(groups.get((a["symbol"], a["date"], round(a["avg_price"], 4)), ())) >= SAME_PRICE_PEOPLE
        a["same_by"] = "price" if a["same_price"] else ""
        if not a["same_price"] and a["insider"] and group_usd.get((a["symbol"], a["date"])):
            vals = sorted(group_usd[(a["symbol"], a["date"])])
            med = vals[len(vals) // 2]
            if med > 0 and abs(a["usd"] - med) <= med * 0.01:
                a["same_price"] = True
                a["same_by"] = "amount"     # 単価は違うが、同じ日の組とほぼ同じ金額
        # SPAC（買収目的の会社）の役員の買いは、上場時の決まった値段（ふつう 10 ドル）での買いが多く、ふつうの会社の役員の買いとは意味が違う
        prof = profiles.get(a["symbol"]) or profiles.get(a["symbol_raw"]) or {}
        prof_nm = str(prof.get("name") or "")
        a["spac"] = bool(SPAC_RE.search(a["issuer_name"] or "") or SPAC_RE.search(prof_nm))
        # 上場日（FMP の ipoDate）の前日〜10 日後に、0.5 ドル刻みの 1 つの単価で買った → 上場のときの割当の可能性
        ipo = parse_date(prof.get("ipo_date"))
        if (ipo and ipo - timedelta(days=1) <= a["date"] <= ipo + timedelta(days=10) and a["price_min"] == a["price_max"]
                and abs(a["avg_price"] * 2 - round(a["avg_price"] * 2)) < 1e-9 and "上場" not in a["private_why"]):
            a["private_why"].append("上場")
        a["private_hint"] = bool(a["private_why"])
        a["compare_ok"] = not (a["plan10b5"] or a["late"] or a["private_hint"] or a["spac"])
        # 「大きな買い」は市場でのふつうの買いだけ（市場の外の買いの可能性・遅れた届け出・決まった買いの可能性・SPAC は外す）
        a["big"] = a["big_raw"] and not (a["private_hint"] or a["late"] or a["same_price"] or a["spac"])

    # ---- 3. 会社ごとにまとめ、クラスター買いを判定（役員・取締役だけを人数に数える。買った日で数える） ----
    by_sym = defaultdict(list)
    for a in person_days:
        by_sym[a["symbol"]].append(a)

    companies = []
    for sym, buys in by_sym.items():
        eligible = [b for b in buys if b["usd"] >= CLUSTER_PERSON_MIN_USD and b["insider"] and not b["same_price"] and not b["private_hint"] and not b["spac"]]
        cluster_people = 0
        if eligible:
            for d0 in sorted(set(b["date"] for b in eligible)):
                ppl = set(b["cik"] for b in eligible if d0 <= b["date"] < d0 + timedelta(days=CLUSTER_WINDOW_DAYS))
                cluster_people = max(cluster_people, len(ppl))
        cluster = cluster_people >= CLUSTER_MIN_PEOPLE
        big = any(b["big"] for b in buys)
        if cluster:
            shown = sorted(buys, key=lambda b: (b["filing_date"], b["usd"]), reverse=True)
        else:
            shown = sorted([b for b in buys if b["usd"] >= LIST_MIN_USD], key=lambda b: (b["filing_date"], b["usd"]), reverse=True)
        if not shown:
            continue
        q = quotes.get(sym, {})
        prof = profiles.get(sym) or profiles.get(buys[0]["symbol_raw"]) or {}
        total = sum(b["usd"] for b in shown)
        issuer_name = next((b["issuer_name"] for b in buys if b["issuer_name"]), "")
        name = prof.get("name") or q.get("name") or issuer_name or ""
        # 取引所: SEC の銘柄と取引所の一覧（行の issuerExchange）を先に使い、無ければ FMP の会社情報。どちらにも無ければ「分からない」
        sec_ex = next((b["issuer_exchange"] for b in buys if b.get("issuer_exchange")), "")
        if not sec_ex and secmap:
            sec_ex = (next((secmap.get("by_cik", {}).get(b["company_cik"]) for b in buys if secmap.get("by_cik", {}).get(b.get("company_cik", ""))), "")
                      or secmap.get("by_ticker", {}).get(sym) or secmap.get("by_ticker", {}).get(sym.replace(".", "-")) or "")
        prof_ok = bool(prof) and not prof.get("not_found")
        exch_fmp = str(prof.get("exchange") or q.get("exchange") or "")
        exch_full = str(prof.get("exchange_full") or "")
        exch = sec_ex.upper() if sec_ex else exch_fmp
        if sec_ex:
            otc = sec_ex.upper() == "OTC"
        else:
            otc = bool(re.search(r"OTC|PNK|PINK|GREY|EXPERT MARKET", (exch_fmp + " " + exch_full).upper()))
        unknown_exchange = not sec_ex and not (prof_ok and (exch_fmp or exch_full)) and not q.get("exchange")
        fund = bool(prof.get("is_fund") or prof.get("is_etf") or FUND_NAME_RE.search(name or "") or MUTUAL_FUND_TICKER_RE.match(sym) or sym in overrides.get("fund", []))
        insider_buyers = len(set(b["cik"] for b in shown if b["insider"]))
        fund_buyers = len(set(b["cik"] for b in shown if not b["insider"]))
        companies.append({
            "symbol": sym,
            "name": display_name(name),
            "exchange": exch,
            "exchange_full": exch_full,
            "otc": otc,
            "unknown_exchange": unknown_exchange,
            "exchange_source": "SEC" if sec_ex else ("FMP" if exch_fmp or exch_full else ""),
            "fund": fund,
            "profile_found": bool(prof) and not prof.get("not_found"),
            "price": None,
            "market_cap": to_float(prof.get("market_cap")) or None,
            "spac": bool(SPAC_RE.search(name)) or any(b["spac"] for b in buys),
            "total_usd": round(total, 2),
            "buyers": len(set(b["cik"] for b in shown)),
            "insider_buyers": insider_buyers,
            "fund_buyers": fund_buyers,
            "cluster": cluster,
            "cluster_people": cluster_people if cluster else 0,
            "big": big,
            "top": any(b["role"] in TOP_ROLES and not b["same_price"] and not b["spac"] and not b["private_hint"] for b in shown),
            "latest_filing": max(b["filing_date"] for b in shown).isoformat(),
            "buys": [{
                "name": b["name"], "pid": b["cik"], "role": b["role"], "roles_raw": b["roles_raw"], "insider": b["insider"],
                "date": b["date"].isoformat(), "filing_date": b["filing_date"].isoformat(),
                "shares": round(b["shares"], 4), "avg_price": round(b["avg_price"], 4),
                "price_min": round(b["price_min"], 4), "price_max": round(b["price_max"], 4),
                "usd": round(b["usd"], 2), "owned_after": round(b["owned_after"], 4),
                "stake_increase_pct": b["stake_increase_pct"], "lots": b["lots"],
                "big": b["big"], "amended": b["amended"], "plan10b5": b["plan10b5"], "late": b["late"],
                "private_hint": b["private_hint"], "private_why": b["private_why"], "compare_ok": b["compare_ok"], "same_price": b["same_price"], "same_by": b["same_by"], "big_raw": b["big_raw"], "spac": b["spac"], "foreign_market": b["foreign_market"],
                "owned_after_total": round(b["owned_after_total"], 4) if b["owned_after_total"] is not None else None,
                "joint": b["joint"], "direct": b["direct"], "security": b["security"], "urls": b["urls"],
                "ads_converted": b["ads_converted"],
                "parts": [{"security": v["security"], "shares": round(v["shares"], 4), "usd": round(v["usd"], 2), "price_min": round(v["price_min"], 4), "price_max": round(v["price_max"], 4)}
                          for v in b["by_sec"].values()] if len(b["by_sec"]) > 1 else [],
            } for b in shown],
        })

    companies.sort(key=lambda c: (c["latest_filing"], c["total_usd"]), reverse=True)

    listed_buys = sum(len(c["buys"]) for c in companies)
    check("載せた行に SEC へのリンクが全部ある", all(all(b["urls"] for b in c["buys"]) for c in companies), f"{listed_buys} 行")
    check("載せた行は全部 買い(P) で金額 > 0", all(all(b["usd"] > 0 for b in c["buys"]) for c in companies))
    check("会社名が付いた割合が 80% 以上", True if not companies else (sum(1 for c in companies if c["name"]) / len(companies)) >= 0.8,
          f"{sum(1 for c in companies if c['name'])}/{len(companies)}")
    check("期間の中に収まっている", all(parse_date(c["latest_filing"]) >= cutoff for c in companies), f"cutoff={cutoff}")
    check("ドル円レート（無くても出す）", True, f"{usdjpy} ({usdjpy_asof})" if usdjpy else "無し（円の表示は出ない）")

    # ---- 4. 日ごとの量（届け出日ごと。画面の一番上の棒グラフ用） ----
    per_day = defaultdict(lambda: {"buys": 0, "usd": 0.0, "insider_buys": 0})
    for a in person_days:
        x = per_day[a["filing_date"].isoformat()]
        x["buys"] += 1
        x["usd"] += a["usd"]
        if a["insider"]:
            x["insider_buys"] += 1
    listed_per_day = defaultdict(int)
    for c in companies:
        for b in c["buys"]:
            listed_per_day[b["filing_date"]] += 1
    daily = []
    d = cutoff
    while d <= today:
        if d.weekday() < 5:
            k = d.isoformat()
            info = days.get(k, {})
            x = per_day.get(k, {"buys": 0, "usd": 0.0, "insider_buys": 0})
            daily.append({"date": k, "buys": x["buys"], "insider_buys": x["insider_buys"], "usd": round(x["usd"], 2),
                          "listed": listed_per_day.get(k, 0), "filings": info.get("filings_seen", 0),
                          # 日ファイルがまだ無い今日・昨日は「まだ集計前」（0 件の日と見分ける）
                          "no_index": bool(info.get("index_missing")) or (k not in days and d >= today - timedelta(days=1)),
                          # 休み: 米国の祝日か、祝日でない平日で 3 日後の 12:00 UTC を過ぎても索引が無いままの日（臨時の休み）
                          "holiday": is_holiday(d) or (bool(info.get("index_missing")) and max(str(info.get("fetched_at") or ""), str(info.get("confirmed_at") or ""))
                                                       >= (d + timedelta(days=3)).isoformat() + "T12:00"),
                          "have": k in days})
        d += timedelta(days=1)

    fds = sorted(a["filing_date"] for a in person_days)
    out = {
        "app": "SECインサイダー",
        "version": "1.6",
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generated_at_jst": (now + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
        "window_days": args.window,
        "window_from": cutoff.isoformat(),
        "window_to": today.isoformat(),
        "data_first_filing": fds[0].isoformat() if fds else None,
        "data_last_filing": fds[-1].isoformat() if fds else None,
        "usdjpy": usdjpy,
        "usdjpy_asof": usdjpy_asof,
        "usdjpy_asof_jst": fx_jst(usdjpy_asof),
        "usdjpy_asof_label": fx_label(usdjpy_asof),
        "source": "SEC の公開データベース（EDGAR）の Form 4" if from_edgar else "SEC の Form 4（FMP 経由・最新 100 件の一部）",
        "partial": (not from_edgar) or bool(args.partial_ok),
        "thresholds": {
            "list_min_usd": LIST_MIN_USD, "big_usd": BIG_USD,
            "cluster_min_people": CLUSTER_MIN_PEOPLE, "cluster_window_days": CLUSTER_WINDOW_DAYS,
            "cluster_person_min_usd": CLUSTER_PERSON_MIN_USD, "late_filing_days": LATE_FILING_DAYS,
            "suspect_row_usd": SUSPECT_ROW_USD, "suspect_price_ratio": SUSPECT_PRICE_RATIO, "same_price_people": SAME_PRICE_PEOPLE,
        },
        "counts": {
            "raw_rows": len(raw_rows), "purchase_rows": len(kept), "person_days_all": len(person_days) + len(suspects), "person_days": len(person_days),
            "listed_buys": listed_buys, "companies": len(companies),
            "listed_small_cluster_buys": sum(1 for c in companies for b in c["buys"] if b["usd"] < LIST_MIN_USD),
            "clusters": sum(1 for c in companies if c["cluster"]),
            "big_buys": sum(1 for c in companies for b in c["buys"] if b["big"]),
            "plan10b5_buys": sum(1 for c in companies for b in c["buys"] if b["plan10b5"]),
            "late_buys": sum(1 for c in companies for b in c["buys"] if b["late"]),
            "private_hint_buys": sum(1 for c in companies for b in c["buys"] if b["private_hint"]),
            "fund_buys": sum(1 for c in companies for b in c["buys"] if not b["insider"]),
            "same_price_buys": sum(1 for c in companies for b in c["buys"] if b["same_price"]),
            "otc_companies": sum(1 for c in companies if c["otc"]),
            "unknown_exchange_companies": sum(1 for c in companies if c["unknown_exchange"]),
            "fund_companies": sum(1 for c in companies if c["fund"]),
            "suspects": len(suspects),
            "dropped": dict(dropped),
        },
        "suspects": suspects,
        "daily": daily,
        "companies": companies,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "data.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    all_ok = all(c["ok"] for c in checks)
    with open(os.path.join(args.out, "checks.json"), "w", encoding="utf-8") as f:
        json.dump({"all_ok": all_ok, "generated_at": out["generated_at"], "checks": checks}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "data.json"), "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print(f"data.json 書き出し: 会社 {len(companies)} / 載せた行 {listed_buys} / クラスター {out['counts']['clusters']} / 大きな買い {out['counts']['big_buys']} / sha256 {sha[:12]}")
    for c in checks:
        print(("  OK  " if c["ok"] else "  NG  ") + c["name"] + ("  " + c["detail"] if c["detail"] else ""))
    sys.exit(0 if all_ok else 2)


if __name__ == "__main__":
    main()
