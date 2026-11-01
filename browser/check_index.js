// SECインサイダー  索引の確かめ check-2（collect_edgar.js の index_sig と同じ計算。sec.gov のタブで実行する）
// 使い方: この全文を実行（返事 "check loaded check-2 src=…"。src は receive_day.py --js-hash check_index.js の答えと同じなら写し間違いなし）
//         → await window.__ykCheck(["2026-10-27", ...]) → 日ごとの {status, n, sig} または {status, missing} の JSON の文字
(function ykCheck() {
  const hex = (buf) => Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
  const HEAD = /CIK\|Company Name\|Form Type\|Date Filed\|File ?name/i;   // 索引の見出しの行（無ければ索引ではない）
  window.__ykCheck = async (dates) => {
    const out = {};
    for (const d of dates) {
      const ymd = d.replace(/-/g, ""), q = "QTR" + (Math.floor((+d.slice(5, 7) - 1) / 3) + 1);
      try {
        const r = await fetch(`/Archives/edgar/daily-index/${d.slice(0, 4)}/${q}/master.${ymd}.idx`, { cache: "no-store" });
        const t = await r.text();
        if (r.status !== 200) {
          out[d] = { status: r.status, missing: r.status === 404 || t.includes("<Code>AccessDenied</Code>") };
        } else if (!HEAD.test(t)) {
          out[d] = { status: -1, error: "not an index" };
        } else {
          const seen = new Set();
          for (const ln of t.split(/\r?\n/)) {
            const p = ln.split("|"); if (p.length !== 5) continue;
            const form = p[2].trim(), fnm = p[4].trim(), filed = p[3].trim();
            if ((form === "4" || form === "4/A") && fnm.endsWith(".txt") && /^\d{8}$/.test(filed)) seen.add(fnm.split("/").pop().replace(/\.txt$/, ""));
          }
          const s = [...seen].sort().join("\n");
          out[d] = { status: 200, n: seen.size, sig: hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))).slice(0, 16) };
        }
      } catch (e) { out[d] = { status: 0, error: String(e).slice(0, 80) }; }
      await new Promise((res) => setTimeout(res, 500));
    }
    return JSON.stringify(out);
  };
  const srcHash = (() => { const t = ykCheck.toString(); let h = 0x811c9dc5; for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(16).padStart(8, "0"); })();
  return "check loaded check-2 src=" + srcHash;
})();
