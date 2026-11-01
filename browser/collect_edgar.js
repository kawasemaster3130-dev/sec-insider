// SECインサイダー  ブラウザ版の EDGAR 取り込み v2.6（fetch_edgar.py と同じ決まり。版ごとの変わり目は README の「取り込みの版」）
// 使い方: sec.gov のタブでこの全文を実行 → __yk.start(["2026-10-23", ...]) → __yk.status() → __yk.part("2026-10-23", 0) で 6,000 字ずつ取り出す
(function ykMain() {
  const Y = (window.__yk = window.__yk || {});
  if (Y.running) return "already running (" + Y.version + ")";
  Y.version = "collector-2.6";
  const C = 3;            // 同時に取る数
  const MIN_GAP = 130;    // 通信の間の最短ミリ秒（毎秒 8 回未満。SEC の上限は 10 回）
  const PART = 6000;      // 写すときの 1 回分の文字数
  const NOTE_MAX = 4000, DOCNOTE_MAX = 1000;
  const IPO_RE = /initial public offering|\bIPO\b/i;
  const INDEX_HEAD_RE = /CIK\|Company Name\|Form Type\|Date Filed\|File ?name/i;   // 索引の見出しの行（check_index.js・fetch_edgar.py と同じ）

  let sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  try {
    const src = "onmessage=e=>{setTimeout(()=>postMessage(e.data.id),e.data.ms)}";
    const w = new Worker(URL.createObjectURL(new Blob([src], { type: "text/javascript" })));
    const waits = new Map(); let seq = 0;
    w.onmessage = (e) => { const r = waits.get(e.data); waits.delete(e.data); if (r) r(); };
    sleep = (ms) => new Promise((r) => { const id = ++seq; waits.set(id, r); w.postMessage({ id, ms }); });
    Y.timer = "worker";
  } catch (e) { Y.timer = "setTimeout"; }
  Y.sleep = (ms) => sleep(ms);

  let nextAt = 0;
  async function gate() {
    const now = performance.now();
    const wait = Math.max(0, nextAt - now);
    nextAt = Math.max(now, nextAt) + MIN_GAP;
    if (wait > 0) await sleep(wait);
  }
  Y.stats = { requests: 0, http: {}, neterr: 0, not_found: 0, missing_filings: 0, gave_up: 0, fetch_failed: 0, started_at: new Date().toISOString() };
  Y.abort = false; Y.abort_reason = ""; Y.error = null;

  async function get(url) {
    for (let a = 0; a < 4; a++) {
      if (Y.abort) return { status: 0 };
      await gate();
      try {
        const r = await fetch(url, { cache: "no-store" });
        Y.stats.requests++;
        if (r.status === 404) { Y.stats.not_found++; return { status: 404 }; }
        if (r.ok) return { status: 200, text: await r.text() };
        if (r.status === 403) {
          // SEC の保管庫は「無いファイル」を 403 AccessDenied（XML）で返す。休日や未公開の日の索引もこれ。断られた（速すぎる）ときは HTML のページになる
          const body = await r.text();
          if (body.includes("<Code>AccessDenied</Code>")) { Y.stats.not_found++; return { status: 404 }; }
        }
        Y.stats.http[r.status] = (Y.stats.http[r.status] || 0) + 1;
        if ((Y.stats.http[403] || 0) + (Y.stats.http[429] || 0) > 30) { Y.abort = true; Y.abort_reason = "SEC に断られた回数が多い"; return { status: 0 }; }
        await sleep(2 ** a * 2000 + Math.random() * 500);
      } catch (e) {
        Y.stats.neterr++;
        await sleep(2 ** a * 2000);
      }
    }
    return { status: 0 };
  }

  // ---- XML の読み方（fetch_edgar.py の parse_form4 と同じ） ----
  const child = (el, name) => { if (!el) return null; for (const c of el.children) if (c.localName === name) return c; return null; };
  const path = (el, p) => p.split("/").reduce((x, n) => child(x, n), el);
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const txt = (el) => (el ? clean(el.textContent) : "");
  function textOf(el, p) { const x = path(el, p); if (!x) return ""; const v = child(x, "value"); return v ? txt(v) : txt(x); }
  const num = (s) => { const v = Number(String(s == null ? "" : s).replace(/,/g, "").trim()); return Number.isFinite(v) ? v : 0; };
  const cap = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  function noteIds(el) { return el ? Array.from(el.getElementsByTagName("footnoteId")).map((f) => f.getAttribute("id")).filter(Boolean) : []; }

  function extractXml(t) {
    const m = t.match(/<XML>\s*(<\?xml[^>]*>)?\s*(<ownershipDocument(?:\s[^>]*)?>[\s\S]*?<\/ownershipDocument>)/);
    return m ? m[2] : null;
  }

  function parseForm4(x) {
    const doc = new DOMParser().parseFromString(x, "text/xml");
    if (doc.getElementsByTagName("parsererror").length) return null;
    const root = doc.documentElement;
    if (!root || root.localName !== "ownershipDocument") return null;
    const docType = txt(child(root, "documentType"));
    const issuer = child(root, "issuer");
    const sym = txt(child(issuer, "issuerTradingSymbol")).toUpperCase();
    const issuerName = txt(child(issuer, "issuerName"));
    const issuerCik = txt(child(issuer, "issuerCik"));
    const owners = [];
    for (const ro of root.children) {
      if (ro.localName !== "reportingOwner") continue;
      const rid = child(ro, "reportingOwnerId"), rel = child(ro, "reportingOwnerRelationship");
      const parts = [];
      if (rel) {
        const flag = (t) => ["1", "true"].includes(txt(child(rel, t)).toLowerCase());
        const title = txt(child(rel, "officerTitle")), other = txt(child(rel, "otherText"));
        if (flag("isOfficer")) parts.push(title ? "officer: " + title : "officer");
        if (flag("isDirector")) parts.push("director");
        if (flag("isTenPercentOwner")) parts.push("10 percent owner");
        if (flag("isOther")) parts.push(other ? "other: " + other : "other");
      }
      owners.push({ name: txt(child(rid, "rptOwnerName")), cik: txt(child(rid, "rptOwnerCik")), type: parts.join(", ") });
    }
    const plan = ["1", "true"].includes(txt(child(root, "aff10b5One")).toLowerCase());
    const fn = {};
    for (const f of root.getElementsByTagName("footnote")) { const id = f.getAttribute("id"); if (id) fn[id] = clean(f.textContent); }
    const remarks = txt(child(root, "remarks"));
    const docNote = cap(remarks, DOCNOTE_MAX);
    const ipoInFiling = IPO_RE.test(Object.values(fn).join(" ") + " " + remarks);
    const rows = [];
    const table = child(root, "nonDerivativeTable");
    if (!table) return rows;
    let line = -1;
    for (const tr of table.children) {
      if (tr.localName !== "nonDerivativeTransaction") continue;
      line++;
      if (textOf(tr, "transactionCoding/transactionCode") !== "P") continue;
      const ids = [...new Set(noteIds(tr))];
      const notes = cap(ids.map((i) => fn[i]).filter(Boolean).join(" | "), NOTE_MAX);
      let nature = textOf(tr, "ownershipNature/natureOfOwnership");
      if (!nature) { const nid = noteIds(path(tr, "ownershipNature/natureOfOwnership")); nature = cap(nid.map((i) => fn[i]).filter(Boolean).join(" | "), 200); }
      rows.push({
        line, ad: textOf(tr, "transactionAmounts/transactionAcquiredDisposedCode") || "A",
        td: textOf(tr, "transactionDate"),
        sec: textOf(tr, "securityTitle"),
        shares: textOf(tr, "transactionAmounts/transactionShares"),
        price: textOf(tr, "transactionAmounts/transactionPricePerShare"),
        owned: textOf(tr, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
        di: textOf(tr, "ownershipNature/directOrIndirectOwnership"),
        nature, notes, docNote, ipoInFiling,
        docType, sym, issuerName, issuerCik, owners, plan,
      });
    }
    return rows;
  }

  // SEC の銘柄と取引所の一覧（1 回だけ読む。読めなければ空のまま＝issuerExchange は ""）
  Y.exmap = Y.exmap || null;
  async function loadExchanges() {
    if (Y.exmap) return;
    const r = await get("/files/company_tickers_exchange.json");
    const byCik = {}, byTicker = {};
    if (r.status === 200) {
      try {
        const j = JSON.parse(r.text), fi = j.fields || [], ic = fi.indexOf("cik"), it = fi.indexOf("ticker"), ie = fi.indexOf("exchange");
        for (const row of j.data || []) {
          const ex = row[ie] || "";
          const cik = String(row[ic] || "").replace(/^0+/, "");
          if (cik && ex && !byCik[cik]) byCik[cik] = ex;
          const t = String(row[it] || "").toUpperCase();
          if (t && ex && !byTicker[t]) byTicker[t] = ex;
        }
      } catch (e) { /* 形が変わっていたら使わない */ }
    }
    Y.exmap = { byCik, byTicker, n: Object.keys(byCik).length };
  }
  function exchangeOf(cik, sym) {
    const m = Y.exmap; if (!m) return "";
    const c = String(cik || "").replace(/^0+/, "");
    const s = String(sym || "").toUpperCase();
    return m.byCik[c] || m.byTicker[s] || m.byTicker[s.replace(/\./g, "-")] || "";
  }

  function toRows(parsed, m) {
    const acc = m.file.split("/").pop().replace(/\.txt$/, "");
    const parts = m.file.split("/");
    const folderCik = parts.length >= 4 ? parts[2] : m.cik;
    const url = `https://www.sec.gov/Archives/edgar/data/${folderCik}/${acc.replace(/-/g, "")}/${acc}-index.htm`;
    return parsed.map((t) => {
      const owners = t.owners.length ? t.owners : [{ name: m.name, cik: m.cik, type: "" }];
      const first = owners[0];
      return {
        symbol: t.sym, issuerName: t.issuerName, filingDate: m.filed, transactionDate: t.td || m.filed,
        reportingCik: first.cik, companyCik: t.issuerCik || m.cik, transactionType: "P-Purchase",
        securitiesOwned: num(t.owned), reportingName: first.name, typeOfOwner: first.type,
        acquisitionOrDisposition: t.ad, directOrIndirect: t.di, natureOfOwnership: t.nature,
        formType: t.docType || m.form, securitiesTransacted: num(t.shares), price: num(t.price),
        securityName: t.sec, url, accession: acc, line: t.line, plan10b5: t.plan,
        notes: t.notes, docNote: t.docNote, ipoInFiling: t.ipoInFiling, issuerExchange: exchangeOf(t.issuerCik || m.cik, t.sym),
        jointNames: owners.slice(1).map((o) => o.name), jointTypes: owners.slice(1).map((o) => o.type),
      };
    });
  }

  const iso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const hex = (buf) => Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");

  async function pack(day) {
    const json = JSON.stringify(day);
    const bytes = new TextEncoder().encode(json);
    const sha = hex(await crypto.subtle.digest("SHA-256", bytes));
    const gz = new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
    let bin = ""; for (let i = 0; i < gz.length; i += 0x8000) bin += String.fromCharCode.apply(null, gz.subarray(i, i + 0x8000));
    const b64 = btoa(bin), ph = [];
    for (let i = 0; i < b64.length; i += PART) ph.push(hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(b64.slice(i, i + PART)))).slice(0, 16));
    return { sha, bytes: bytes.length, gz: gz.length, b64, ph };
  }

  // 索引（master.idx）から Form 4・4/A の届け出を 1 件ずつ（同じ届け出は 1 つに）。check_index.js と fetch_edgar.py も同じ決まり
  function indexList(text) {
    const seen = new Set(), list = [];
    for (const ln of text.split(/\r?\n/)) {
      const p = ln.split("|"); if (p.length !== 5) continue;
      const form = p[2].trim(), fnm = p[4].trim(), filed = p[3].trim();
      if ((form === "4" || form === "4/A") && fnm.endsWith(".txt") && /^\d{8}$/.test(filed)) {
        const acc = fnm.split("/").pop().replace(/\.txt$/, "");
        if (seen.has(acc)) continue; seen.add(acc);
        list.push({ cik: p[0].trim(), name: p[1].trim(), form, filed: `${filed.slice(0, 4)}-${filed.slice(4, 6)}-${filed.slice(6)}`, file: fnm });
      }
    }
    return list;
  }
  // 索引の印: 届け出番号を並べ替えて改行でつないだ文字の sha256 の先頭 16 字（あとで索引が変わったかを 1 回の通信で確かめるため）
  async function sigOf(list) {
    const s = list.map((m) => m.file.split("/").pop().replace(/\.txt$/, "")).sort().join("\n");
    return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))).slice(0, 16);
  }

  async function doDay(d) {
    const ymd = d.replace(/-/g, ""), y = d.slice(0, 4), q = "QTR" + (Math.floor((+d.slice(5, 7) - 1) / 3) + 1);
    const day = { date: d, filings_seen: 0, filings_parsed: 0, accessions_count: 0, rows: [], complete: false, errors: [], gave_up: [], fetch_failed: [], not_found: [], collector: Y.version };
    const idx = await get(`/Archives/edgar/daily-index/${y}/${q}/master.${ymd}.idx`);
    if (idx.status === 404) { day.index_missing = true; day.complete = true; }
    else if (idx.status !== 200 || !INDEX_HEAD_RE.test(idx.text)) { day.errors.push("INDEX"); }   // 200 でも索引の見出しが無ければ（保守の画面など）索引として読まない
    else {
      const list = indexList(idx.text);
      day.filings_seen = list.length;
      day.index_n = list.length;
      day.index_sig = await sigOf(list);
      const got = new Array(list.length);
      let i = 0;
      Y.cur = { date: d, total: list.length, done: 0 };
      async function worker() {
        while (i < list.length && !Y.abort) {
          const k = i++, m = list[k];
          const acc = m.file.split("/").pop().replace(/\.txt$/, "");
          let parsed = null, fetched = false, missing = false;
          for (let t = 0; t < 3 && !fetched; t++) {
            const r = await get("/Archives/" + m.file);
            if (r.status === 200) { fetched = true; const x = extractXml(r.text); parsed = x ? parseForm4(x) : null; }
            else if (r.status === 404) { missing = true; break; }
          }
          if (missing) got[k] = { acc, missing: true };
          else if (!fetched) got[k] = { acc, fail: true };
          else if (parsed === null) got[k] = { acc, bad: true };
          else got[k] = { acc, rows: toRows(parsed, m) };
          Y.cur.done++;
        }
      }
      await Promise.all(Array.from({ length: C }, worker));
      for (const g of got) {
        if (!g) continue;
        day.accessions_count++;
        if (g.missing) { day.not_found.push(g.acc); Y.stats.missing_filings++; continue; }
        if (g.fail) { day.fetch_failed.push(g.acc); Y.stats.fetch_failed++; continue; }
        if (g.bad) { day.gave_up.push(g.acc); Y.stats.gave_up++; continue; }
        day.filings_parsed++;
        for (const r of g.rows) day.rows.push(r);
      }
      const notReached = got.filter((g) => !g).length;
      if (notReached) day.errors.push(`NOT_REACHED_${notReached}`);
      // 完全 = 取れなかった届け出が 0 件、読めなかった物と SEC に無かった物を足して 1%（最低 1 件）まで、途中で止めていない
      day.complete = !Y.abort && notReached === 0 && day.fetch_failed.length === 0 && (day.gave_up.length + day.not_found.length) <= Math.max(1, Math.floor(list.length * 0.01));
    }
    day.fetched_at = iso();
    const p = await pack(day);
    Y.out[d] = { ...p, rows: day.rows.length, seen: day.filings_seen, parsed: day.filings_parsed, gave_up: day.gave_up.length, fetch_failed: day.fetch_failed.length, not_found: day.not_found.length, complete: day.complete, index_missing: !!day.index_missing, errors: day.errors };
  }

  // 自己試験: 売り 1 行・買い 1 行（脚注つき・別の脚注に上場の言葉）の小さな届け出を読み、決まった答えが出るか
  function selftest() {
    try {
      const tx = (code, ad, sh, pr, own, fid) => "<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>"
        + "<transactionDate><value>2026-01-02</value></transactionDate><transactionCoding><transactionCode>" + code + "</transactionCode></transactionCoding>"
        + "<transactionAmounts><transactionShares><value>" + sh + "</value></transactionShares><transactionPricePerShare><value>" + pr + "</value>"
        + (fid ? '<footnoteId id="' + fid + '"/>' : "") + "</transactionPricePerShare><transactionAcquiredDisposedCode><value>" + ad + "</value></transactionAcquiredDisposedCode></transactionAmounts>"
        + "<postTransactionAmounts><sharesOwnedFollowingTransaction><value>" + own + "</value></sharesOwnedFollowingTransaction></postTransactionAmounts>"
        + "<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>";
      const x = "<ownershipDocument><documentType>4</documentType><issuer><issuerCik>0000000001</issuerCik><issuerName>Test Co</issuerName><issuerTradingSymbol>tst</issuerTradingSymbol></issuer>"
        + "<reportingOwner><reportingOwnerId><rptOwnerCik>0000000002</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>"
        + "<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner><aff10b5One>0</aff10b5One>"
        + "<nonDerivativeTable>" + tx("S", "D", "5", "9", "95", "") + tx("P", "A", "1,000", "10.5", "1,100", "F1") + "</nonDerivativeTable>"
        + '<footnotes><footnote id="F1">Weighted  average price.</footnote><footnote id="F2">Converted upon the closing of the initial public offering.</footnote></footnotes>'
        + "<remarks>r</remarks></ownershipDocument>";
      const rows = toRows(parseForm4(extractXml("<XML>\n" + x + "\n</XML>")), { file: "edgar/data/2/0000000002-26-000001.txt", cik: "2", name: "Doe Jane", form: "4", filed: "2026-01-03" });
      const r = rows[0] || {};
      const ok = rows.length === 1 && r.symbol === "TST" && r.line === 1 && r.price === 10.5 && r.securitiesTransacted === 1000 && r.securitiesOwned === 1100
        && r.ipoInFiling === true && r.notes === "Weighted average price." && r.docNote === "r" && r.typeOfOwner === "director" && r.reportingCik === "0000000002"
        && r.companyCik === "0000000001" && r.transactionDate === "2026-01-02" && r.filingDate === "2026-01-03" && r.acquisitionOrDisposition === "A"
        && r.url === "https://www.sec.gov/Archives/edgar/data/2/000000000226000001/0000000002-26-000001-index.htm" && r.accession === "0000000002-26-000001";
      return ok ? "ok" : "NG";
    } catch (e) { return "NG:" + String(e).slice(0, 80); }
  }

  Y.out = Y.out || {};
  Y.start = (dates) => {
    if (Y.running) return "already running";
    Y.running = true; Y.queue = dates.slice(); Y.error = null;
    Y.run = (async () => {
      try { await loadExchanges(); for (const d of dates) { if (Y.abort) break; await doDay(d); } }
      catch (e) { Y.error = String((e && e.stack) || e); }
      finally { Y.running = false; Y.stats.finished_at = new Date().toISOString(); }
    })();
    return "started " + dates.length + " days, timer=" + Y.timer;
  };
  Y.part = (d, i) => { const o = Y.out[d]; if (!o) return { pending: true }; const n = Math.ceil(o.b64.length / PART) || 1; return { date: d, sha: o.sha, n, i, h: (o.ph || [])[i] || "", s: o.b64.slice(i * PART, (i + 1) * PART), rows: o.rows, complete: o.complete }; };
  Y.status = () => ({
    version: Y.version, running: !!Y.running, cur: Y.cur, error: Y.error, abort: Y.abort, abort_reason: Y.abort_reason, stats: Y.stats, exchanges: Y.exmap ? Y.exmap.n : null,
    done: Object.fromEntries(Object.entries(Y.out).map(([k, v]) => [k, { rows: v.rows, seen: v.seen, parsed: v.parsed, gave_up: v.gave_up, fetch_failed: v.fetch_failed, not_found: v.not_found, complete: v.complete, index_missing: v.index_missing, parts: Math.ceil(v.b64.length / PART), sha: v.sha.slice(0, 12) }])),
  });
  const srcHash = (() => { const t = ykMain.toString(); let h = 0x811c9dc5; for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(16).padStart(8, "0"); })();
  return "collector loaded " + Y.version + " timer=" + Y.timer + " C=" + C + " gap=" + MIN_GAP + " part=" + PART + " notes=" + NOTE_MAX + "/" + DOCNOTE_MAX + " selftest=" + selftest() + " src=" + srcHash;
})();
