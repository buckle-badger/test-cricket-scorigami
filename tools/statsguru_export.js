// Statsguru → CSV export for the Test scorigami.
//
// 1. In your normal browser, open this page (all men's Test team innings, 200 per page, oldest first):
//    https://stats.espncricinfo.com/ci/engine/stats/index.html?class=1;filter=advanced;orderby=start;size=200;template=results;type=team;view=innings
// 2. Open the developer console (Cmd+Option+J on Mac, Ctrl+Shift+J on Windows).
//    Chrome may ask you to type "allow pasting" first.
// 3. Paste this whole file and press Enter. It reads each page in turn (a couple of seconds apart),
//    then downloads statsguru_team_innings.csv. Put that file in the project's data/ folder.
//
// If the run stops part-way (e.g. Cricinfo shows a check page), solve it in the tab, then
// run the snippet again from the page it stopped on. Each run downloads what it read; drop every file
// into data/ (any name starting "statsguru") and the build reads them all.

(async () => {
  const DELAY_MS = 2500;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const clean = s => (s || "").replace(/\s+/g, " ").trim();

  function readPage(doc) {
    const table = [...doc.querySelectorAll("table.engineTable")].find(t => {
      const h = clean(t.querySelector("tr")?.textContent);
      return h.includes("Score") && h.includes("Inns");
    });
    if (!table) return null;
    const head = [...table.querySelector("tr").children].map((c, i) => clean(c.textContent) || `col_${i}`);
    const rows = [...table.querySelectorAll("tr.data1")].map(tr => {
      const cells = [...tr.children].map(td => clean(td.textContent));
      const link = [...tr.querySelectorAll("a")].map(a => a.getAttribute("href") || "").find(h => /\/match\//.test(h)) || "";
      return { cells, link };
    });
    const next = [...doc.querySelectorAll("a")].find(a => clean(a.textContent) === "Next");
    const pageInfo = clean([...doc.querySelectorAll("td")].map(td => td.textContent).find(t => /Page \d+ of \d+/.test(t)) || "");
    return { head, rows, next: next ? new URL(next.getAttribute("href"), location.href).href : null, pageInfo };
  }

  let doc = document, url = location.href, head = null, out = [], page = 0;
  while (true) {
    const p = readPage(doc);
    if (!p) { console.warn("No results table found on", url, "- stopping here."); break; }
    head = head || p.head;
    p.rows.forEach(r => out.push([...r.cells, r.link]));
    page++;
    console.log(`Read page ${page} (${p.pageInfo.match(/Page \d+ of \d+/)?.[0] || "?"}): ${out.length} innings so far`);
    if (!p.next || p.rows.length === 0) break;
    await sleep(DELAY_MS);
    const res = await fetch(p.next, { credentials: "include" });
    if (!res.ok) { console.warn(`Stopped: ${res.status} on ${p.next}`); break; }
    url = p.next;
    doc = new DOMParser().parseFromString(await res.text(), "text/html");
  }

  const esc = v => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const csv = [[...head, "match_link"], ...out].map(r => r.map(v => esc(String(v))).join(",")).join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
    download: "statsguru_team_innings.csv",
  });
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Done: ${out.length} innings from ${page} page(s). Last page read: ${url}`);
})();
