# SECインサイダー（sec-insider）

米国株で、会社の役員や大株主が自分の会社の株を自分のお金で買ったときに SEC へ出す届け出（Form 4、取引コード P）を毎日集め、
まとまった買いを一覧にする。売りは出さない。使うのは公開情報だけ。数字は道具（Python）が作り、人も AI も手で作らない・直さない。
発行: 株式会社チャートマスター（投資助言・代理業 関東財務局長（金商）第2086号）。役員の買いは参考の一つで、売買をすすめる物ではない。

## 仕組み（2026-11-01 から。GitHub の機械が毎日動かす）
1. `fetch_edgar.py`（fetch_edgar-2.8。Python の標準ライブラリだけ・鍵なし）が SEC EDGAR の日ごとの索引（daily-index）を読み、Form 4・4/A の届け出から買い（P）の行だけを `data/days/YYYY-MM-DD.json` に保存する。同じ日をもう一度動かしても、新しく増えた届け出だけ取る。取引所は SEC の銘柄と取引所の一覧（/files/company_tickers_exchange.json）から付ける。
2. `fetch_market.py` がドル円（frankfurter.dev。欧州中央銀行の参照レート）を `data/market/usdjpy.json` に保存する。取れなくても止まらない。
3. `build.py`（集計 1.5）が 30 日分をまとめて `docs/data.json` と自己検査の結果 `docs/checks.json` を書く。検査が 1 つでも NG なら出口コード 2 で止まり、画面は更新しない。
4. `inject.py` が画面の型 `app.template.html` にデータを埋め込み、`docs/index.html`（GitHub Pages 用。検索エンジンに載せない印つき）と `docs/app.html` を作る。
5. 以上を `.github/workflows/yakuin-kai.yml` が火〜土の 06:25 と 12:25 UTC（13:25・19:25 バンコク、15:25・21:25 日本）に自動で行い、結果を git に保存する。毎回「米国東部の昨日」までの直近 14 日を見直す。手で動かすときは Actions の Run workflow（date_from で取り直しの開始日を指定できる）。
- 会社情報（会社名・時価総額・取引所・上場日・ファンドかどうか）は `data/market/profiles.json`。FMP の会社情報を `need_profiles.py`（足りない銘柄を出す）と `add_profile.py`（答えを足す）で、別の係が少しずつ足す。無い銘柄も一覧には出る（時価総額の表示などが付かないだけ）。
- SEC の決まり: 連絡先の分かる User-Agent が要る。リポジトリの Secrets `EDGAR_UA` に「組織名 連絡先メール」を入れる。通信は 1 秒 8 回まで（SEC の上限 10 回の内側）。

## 中身
| ファイル | 役目 |
|---|---|
| fetch_edgar.py | 取り込み（Python 版・鍵なし。fetch_edgar-2.8） |
| fetch_market.py | ドル円（と現在値。現在値は使っていないので --no-prices） |
| build.py | 集計と自己検査。data.json と checks.json を書く。検査が 1 つでも NG なら出口コード 2 |
| inject.py | app.template.html に data.json を埋め込んで 1 枚の HTML にする（--standalone で GitHub Pages 用、--noindex で検索エンジンに載せない印） |
| app.template.html | 画面（1 枚の HTML。データ埋め込み） |
| need_profiles.py / add_profile.py | 会社情報がまだ無い銘柄を出す（無い→古い→上場日の無いの順）／FMP の答え（JSON のファイル）から必要な項目だけを足す |
| data/market/overrides.json | 手での上書き（例: 投資信託として扱う銘柄 PSUS） |
| data/market/sec_exchange.json | SEC の銘柄と取引所の一覧の控え（行に取引所が無いときに使う） |
| .github/workflows/yakuin-kai.yml | 毎日の自動実行 |
| browser/ | 予備。パソコンのブラウザで sec.gov を読む版の取り込み（collect_edgar.js・check_index.js・receive_day.py・plan_days.py・run_status.py）。GitHub の機械が使えないときだけ |
| tools/publish_map.py | 画面を Claude の公開ページ（artifact）にも置くときの、ファイルの対応表 |
| tests/ | 本物の Form 4 の XML 1 件と、ネット無しの試験 5 本（集計の決まり・Python 版の取り込み・ブラウザ版と Python 版が同じ行と同じ索引の印を作るか・毎日の道具の決まり・回の種類と報告の 2 行目） |

## 集計の決まり（build.py の先頭で変える）
### 残す行
- 取引コード P で、取得（A）の行。株数>0、単価>0、株そのもの（オプション・ワラント・優先株・RSU などは除く）、届け出日が直近 30 日以内（今日を入れて 30 日）
- 銘柄コード: 「HEI, HEI.A」のように並んでいれば最初の物。米国の形（英字 1〜5 字、後ろに .A や -B が付いてもよい）でない物（数字入りの AXIA3、ASX:LNW など）と、ユニット・ワラント・権利の銘柄（.U・.WS・.R など）は外す
- 同じ届け出の中の別の取引（行番号が違う）は、株数・単価が同じでも別に数える

### まとめ方
- 訂正（4/A）があれば、同じ人・会社・買った日・証券名の元の届け出は捨てて訂正だけ使う
- 関連会社が同じ買いを別々に届け出た物（会社・買った日・株数・単価・取引後の持ち株・証券名が一致）は、関係があるときだけ 1 つにまとめる。関係がある＝同じ届け出・同じ持ち主の書き方の間接保有（「See footnote」などは数えない）・名前（連名を含む。会社の LLC・LP などは外し、人の「姓 名」は「名 姓」も見る）が重なる・片方の名前がもう片方の脚注に出てくる。同じ条件で買っただけの別々の持ち主（例 RENT の Nexus と STORY3）は別々に残す
- 役員・取締役どうしはふつう別の人の買いなのでまとめない。ただし全員が間接保有で、持ち主の書き方が同じなら（同じファンドの株を別々に届け出ただけ）1 つにする（ADRX・ESEA・EDRY など）
- 1人・1日でまとめる（同じ人が同じ日に何回かに分けて買った分は 1 件）

### 人と役職
- 役員・取締役（officer か director）で、名前が会社・ファンドでない人を「会社の中の人」とする。会社・ファンドの名前の届け出人は「関係する会社」「大株主」。other の役職でも、発行会社の役職とはっきり読める物（Chief Revenue Officer・Chairman of the Board など）は役員。投資顧問・ファンド・信託・名誉職などの立場は「関係者」
- 役職の文を「、」「&」「and」などで区切り、区切りごとに一番上の役職を出す（CEO ＞ 会長 ＞ 社長 ＞ CFO ＞ COO ＞ 副会長 ＞ 経営幹部 ＞ 部門の責任者 ＞ 役員）。「Vicepresident」「VP」「EVP」「SVP」は部門の責任者。「President」は、後ろに部門の名前が付けば（President - Foot Locker Intl・President International・Pres. of N. Am. Ops・President, Network Solutions）部門の責任者。「Principal Executive Officer」「PEO」は CEO、「Principal Financial Officer」は CFO、「COB」は会長。「Deputy」「Assistant」「Associate」の付く役職は元の役職より上に出さない（Deputy CEO・Deputy Co-COO は経営幹部、Deputy Chairman は副会長、Deputy General Counsel・Assistant Treasurer・Associate Vice President などは役員）。役職が「See Remarks」なら、届け出の備考（300 字まで、投資顧問の話でないとき）を役職の文として読む
- 人を数えるとき（クラスターの人数・「N人」）は、届け出人の番号（CIK）で数える。画面の数え直しも同じ

### 外す物（数字が合わない届け出。一覧から外し、画面の下に理由を書く）
- 1 行で 10 億ドル超／単価が FMP の株価と 10 倍以上ずれる／同じ銘柄のほかの届け出と 20 倍以上ずれる（ADR は株価の比べをしない）
- 1人・1日の金額が時価総額の 5% 超（25 万ドル以上）＝「時価総額に比べて大きすぎる（会社から直接の買い・数字の誤りなどの可能性）」
- 注記に外国のお金の名前があり、ドルに換えたと書いていない（額面 par value・ファンドの名前の中の通貨は数えない）
- 注記に預託証券（ADS）の説明があり、単位が分からない物。ADS 1 つが何株か（n）が書いてあれば、FMP の株価（ADR の会社は ADS 1 つの値段）と比べる: 単価 × n が株価の 0.7〜1.3 倍なら単価は普通株 1 株の値段なので外さない（例 MNSO の香港での買い）。単価そのものが株価の 0.7〜1.3 倍で、株数が n で割り切れるなら、単価は ADS 1 つの値段で株数は普通株の数なので、株数を n で割って載せ、「株数は預託証券（ADS）の数に直した」の印（例 COE）。どちらでもなければ外す。証券名が ADS の行と、1 つ＝1 株の物は外さない

### 印
- 訂正（4/A）、計画での買い（10b5-1）、遅れた届け出（買いから 10 日超）、市場の外の買いの可能性、大株主・関係する会社、間接保有、連名、SPAC（会社名か会社情報に acquisition corp・blank check など）、店頭（OTC）、取引所が分からない、ファンド、決まった買いの可能性、外国の市場での買い（注記に外国のお金の名前と「ドルに換えた」ことが両方ある）
- ファンド: FMP の会社情報で投資信託・ETF、会社名に fund・municipal・closed-end・ETF・income trust・interval、銘柄コードが英字 4 字＋X（投資信託の形）、overrides.json の fund
- 市場の外の買いの可能性（理由も画面に出す）:
  - 「注記の言葉」＝買いの行の脚注・備考に private placement・securities purchase agreement・subscription agreement・registered direct・PIPE・public / underwritten / direct / rights offering・at-the-market・in the offering・initial public offering・IPO・directed share・offering price・underwriter・concurrent placement・directly from the company・purchased from the company・issued by the company・irrevocable trust・信託どうしの移し替え・exchange agreement・in exchange for・business combination・merger agreement・ファンドどうしの入れ替え（rebalancing transaction・between funds）・「市場では売り買いしていない」（not bought or sold in the open market）・出資の取り決め（contribution agreement）・相手と決めた取引（privately negotiated・private transaction / sale / purchase）・現物での受け渡し（in-kind・pro rata distribution）・in settlement of・「市場では買っていない」（were not … in the open market・other than / outside the open market）などの言葉
  - 「報酬や会社の制度」＝ストックオプションの行使（cashless exercise など）・報酬の制度（compensation plan・deferred compensation・409A）・社員や取締役の株の買い付け制度（employee stock purchase・ESPP・director stock purchase plan・401(k)）・配当の再投資（dividend reinvestment・DRIP）・報酬の代わり（in lieu of・director fees / compensation・retainer）・会社との取り決め（shares were issued pursuant to・promissory note）・株とワラントのセット（unit と warrant）。持ち株の説明の文（Includes …・… holdings include …・… of which were acquired through …・This total reflects …・Consists of …）は数えない（株数に端数がある買いは数える）。10b5-1 の計画の買い付け制度は数えない。株数に端数があり、金額がちょうど 100 ドル単位（丸めのずれまで）の買いも入れる
  - 「上場のとき」＝同じ届け出のどこかに上場（initial public offering / IPO）の言葉があり、単価が 0.5 ドル刻み。または、FMP の上場日の前日〜10 日後に、1 つの 0.5 ドル刻みの単価で買った（上場日は 2026-10-28 からの会社情報に入る。前に取った会社情報は、毎日の更新で FMP の枠の残りを使って少しずつ取り直すので、それまではこちらの決まりは働かない）
  - 「ちょうどの金額」＝1回・1つの単価で、合計がちょうど 10 万ドル単位（1 ドル未満のずれ）かつ 50 万ドル以上。株数が 100 株単位なら数えない（切りのよい株数を切りのよい指値で買っても合計はちょうどになるため。例 FUL の Bailly 10,000 株 × 50 ドル）
  - 関連会社と同じ買いをまとめたときは、相手の届け出の印も引き継ぐ
- 決まった買いの可能性: 同じ会社で 3 人以上の役員が同じ日・同じ単価で買った物。その日に、別の種類の株でほぼ同じ金額（±1%）を買った役員も同じ扱い（例 HEI の Neitzel）。画面には「同じ日・同じ単価」か「同じ日・ほぼ同じ金額」と出す

### 線と数え方
- 載せる基準: 1人・1日の合計 10 万ドル以上。大きな買い: 100 万ドル以上
- クラスター買い: 同じ会社の役員・取締役 3 人以上（1人 1 万ドル以上）が、買った日で数えて 14 日の幅（d0 ≤ 日 < d0+14）に買った。クラスターの会社は 10 万ドル未満の買いも全部載せる
- 数えない物（build.py と画面で同じ）: 「大きな買い」＝市場の外の買いの可能性・遅れた届け出・決まった買いの可能性・SPAC を数えない／クラスター買いの人数＝市場の外の買いの可能性・決まった買いの可能性・SPAC・大株主と関係する会社を数えない／「CEO・CFO・会長が買った」＝市場の外の買いの可能性・決まった買いの可能性・SPAC を数えない（遅れた届け出は市場での買いなので数える）
- 持ち株の増え方 = 今回の株数の合計 ÷ 今回の前の持ち株の合計。持ち株の線（直接・間接の別、持ち主の書き方、証券名）ごとに、行を持ち主のつながりで分けて「買う前の持ち株」を足す。ある行の「取引後の持ち株 − 株数」が別の行の取引後の持ち株と同じなら同じ持ち主の続き（同じ日に何回かに分けて買った）、つながらなければ同じ書き方の別の持ち主（See footnotes の 2 つのファンド、Held by Trust の 2 つの信託など）。取引後の持ち株が同じ数の行は 1 人の持ち主の分（例 ADRX の George Simeon は 3 つのファンドで 24.6%、Gordon は 2 つのファンドで 6.3%）。種類の違う株（普通株と ADS など）を同じ日に買ったときは出さず、画面に証券ごとの株数と単価を並べる（例 PAM の Mindlin Damian）
- 画面の会社名は、後ろに付いた証券の種類（Common Stock・Ordinary Shares・American Depositary Shares など）を外して出す

## 画面の決まり
- 最初は「役員・取締役の買い」だけ、「米国株（店頭の銘柄・取引所が分からない銘柄・投資信託を除く）」だけ、30 日を表示。スイッチで大株主・店頭など・投資信託も出せる。覚えておくのは並び順だけで、期間とスイッチは開くたびに最初の決まりに戻る
- 「役員の買いは参考の一つ。いつものポートフォリオのルールと合わせて使う。」を最初の画面に入れる。違法なインサイダー取引・自社株買いとは別であることも同じ枠に書く
- 印の付いた買い（市場の外の買いの可能性・決まった買いの可能性・遅れた届け出・SPAC）: 会社の買いが全部これなら閉じたカードに「印の付いた買いだけ（…）」、金額の半分以上なら「金額の多くは印の付いた買い（…）」。カードの金額の下に「うち印付き」、一覧の見出しに「うち印の付いた買い」。「金額が大きい順」「買った人が多い順」と注目の横並びは、印の付いていない買いの金額と人数で並べる
- 「大きな買い」「CEO・CFO・会長」で絞ったときの見出しは「大きな買いがある N 社（その会社のほかの買いも表示）」のように出す（上の文の件数は大きな買いそのものの数）
- 棒を押した日の絞り込みは一覧のすぐ上に出し、「前の日」「次の日」「すべての日に戻す」のボタンを付ける（指で押す画面では高さ 44px）。棒を指やマウスで押すとそこへ動き、キーボードで押したときは棒の場所にとどまる。ボタンを押しても、上の注目の並びの高さの変わりで押した場所がずれないようにする。「すべての日に戻す」のあとは一覧の見出しにとどまる。持ち株の増え方が 0.1% 未満なら「0.1% 未満増えた」と出す
- 足元に【重要】の文・発行者・登録番号・※広告・評価基準日。SEC が作った画面ではないことも書く。足元の数は「届け出の買いの行」「1人・1日（数字が合わない物を外す前と後）」「一覧に載る買い（クラスター買いの会社の 10 万ドル未満の買い N 件を含む）」
- 株価は表示しない（無料で取れる口が無いため。FMP の株価は書き間違いの検査と預託証券の単位の確かめにだけ使う）

## 自己検査（checks.json。毎日の更新で 1 つでも NG なら出し直さない）
生データが読めた／期間の平日の日ファイルが全部そろっている（2 日前まで）。祝日でない平日に索引が無いままの日は、NG にせずに「索引がまだ無い平日」として出し（ページ全体を止めないため。画面ではその日を「前」）、3 日後の 12:00 UTC を過ぎても無いままなら SEC の休みとして決まった日にする（画面で「休」）／売りが残っていない／金額の検算／載せた行に SEC のリンクが全部ある／会社名が 80% 以上に付いている／期間に収まっている

## 研究（research/。画面とは別。生徒には配らない）
- 目的: 役員の買いの「その後の成績」を 2006 年からの SEC のデータで総当たりに測り、期待値の高い段の順位を出す。決まりは research/PREREG_v1.md（測る前に凍結。v1.1）。
- 道具: fetch_sec_insider.py（SEC の Insider Transactions Data Sets の四半期 zip → 買いの行）→ fetch_prices.py（stooq の一括日足。無ければ yfinance）→ fetch_shares.py（SEC の XBRL の発行株数）→ build_events.py（会社 × 届け出日のイベントと特徴）→ analyze.py（事前登録どおりの評価と summary.md）。
- 動かす所: GitHub Actions「SECインサイダー 検証（総当たり・研究）」を手で起こす（Secret EDGAR_UA が要る）。結果は成果物（Artifacts）research-out-N に置き、リポジトリには書かない。
- 試験: `python3 research/tests/test_research_offline.py`（作り物のデータでネット無しに通す。6 項目）。
- 数字は道具だけが書く。人も AI も手で直さない。

## 手元で動かす
```
python3 build.py --raw data/days --quotes data/market/quotes --fx data/market/usdjpy.json --out out --window 30
python3 inject.py --template app.template.html --data out/data.json --out out/app.html
for t in tests/test_offline.py tests/test_build.py tests/test_daily_tools.py tests/test_run_status.py tests/test_collector_parity.py; do python3 $t; done   # 最後の 1 本は playwright（Chromium）が要る
```

## 取り込みの版（browser/collect_edgar.js）
- v2.0: 取引ごとの行番号（line）と、その取引に付いた脚注の文（notes）・届け出の備考（docNote）を残す。取れなかった届け出（fetch_failed）と読めなかった届け出（gave_up）を分ける
- v2.1: 脚注の文を 4,000 字まで・備考を 1,000 字まで残す。届け出のどこかの脚注・備考に上場の言葉があれば ipoInFiling
- v2.2: 索引にあるのに SEC に無い届け出（404・AccessDenied）は not_found。完全 = 取れなかった届け出が 0 件で、読めなかった物と無かった物を足して 1%（最低 1 件）まで
- v2.3: SEC の銘柄と取引所の一覧から issuerExchange。part() は部分ごとの sha256（先頭 16 字）を h に入れる
- v2.4: 読み込みの返事に決まった数・自己試験・コードの文字の FNV（src=）
- v2.5: 日ファイルに索引の印（index_sig＝Form 4・4/A の届け出番号を並べ替えて改行でつないだ文字の sha256 の先頭 16 字）と index_n。check_index.js と fetch_edgar.py も同じ計算
- v2.6: 200 で返っても索引の見出しの行（CIK|Company Name|Form Type|Date Filed|File Name）が無ければ索引として読まない（保守の画面などで、届け出のあった日を空にしないため）。fetch_edgar.py は行を \n だけで分ける（会社名の中の 0x85 などで行を割らない）

## 分かっている限界
- P（買い）には市場での買いのほか、私募や制度で会社から直接受け取った物も混ざる（脚注の言葉で印は付けるが、全部は見分けられない）
- 役職の日本語は機械の読み替え。読めないものは「役員・関係者（役職を読み取れず）」
- 円は更新時のドル円（欧州中央銀行の参照レート。frankfurter.dev）でのおよその値
- Form 4 を出さない会社（米国外の多くの会社）は載らない
- GitHub の機械（Actions）が止まった日は更新されない（直近 14 日を毎回見直すので、動き出せば抜けた日も自分で埋める）
