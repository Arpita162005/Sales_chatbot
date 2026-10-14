/* Sales Insight — workspace client.
   Each question becomes a "finding": question heading, progress steps, answer,
   numbered figure and table, and a "How this was calculated" section.
   Conversations are saved in this browser (localStorage) and listed in the rail.
   Backend: POST /api/chat streams NDJSON events
   (route → sql | sql_error → table → chart → sources → token… → done). */
(function () {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (s, r = document) => r.querySelector(s);
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return n;
  };
  const esc = window.Markdown.escape;
  const F = window.Charts;
  const ICON = {
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
    redo: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
    code: '<svg viewBox="0 0 24 24"><path d="M8 8l-4 4 4 4M16 8l4 4-4 4"/></svg>',
    chev: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
    expand: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    image: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 11v6M14 11v6M6 7l1 12h10l1-12M9 7V4h6v3"/></svg>',
  };
  const ROUTE_LABEL = { analytics: "Sales data", knowledge: "Knowledge base", general: "Assistant" };
  const STEPS = {
    pending: ["Understand the question"],
    analytics: ["Understand the question", "Write and run the query", "Write the answer"],
    knowledge: ["Understand the question", "Search the knowledge base", "Write the answer"],
    general: ["Understand the question", "Write the answer"],
  };

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), 1800);
  }
  function copyText(text, label) {
    const done = () => toast(label || "Copied");
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    else fallbackCopy(text, done);
  }
  function fallbackCopy(text, done) {
    const ta = h("textarea", { style: "position:fixed;opacity:0" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
    ta.remove();
  }
  function download(name, content, type) {
    const a = h("a", { download: name });
    a.href = typeof content === "string" && content.startsWith("data:") ? content : URL.createObjectURL(new Blob([content], { type }));
    document.body.append(a);
    a.click();
    a.remove();
  }
  const timeStr = (iso) => new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

  // ---------------------------------------------------------- persistence
  const KEY = "sia-conversations-v1";
  const MAX_CONVS = 40;
  function loadConvs() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
  }
  function saveConvs() {
    const list = convs.slice(0, MAX_CONVS);
    for (let n = list.length; n > 0; n--) {
      try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, n))); return; } catch (e) { /* quota: drop oldest */ }
    }
  }

  // ---------------------------------------------------------------- state
  let convs = loadConvs();
  let current = null;           // active conversation object
  let figNo = 0, tabNo = 0;     // per-conversation numbering
  const state = { busy: false, controller: null };

  const scroll = $("#scroll");
  const thread = $("#thread");
  const overview = $("#overview");
  const input = $("#input");
  const sendBtn = $("#sendBtn");

  // ---------------------------------------------------------------- theme
  $("#themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("sia-theme", next); } catch (e) { /* ignore */ }
    F.rerenderAll();
  });

  // ------------------------------------------------------------------ rail
  const rail = $("#rail"), scrim = $("#scrim");
  const closeRail = () => { rail.classList.remove("open"); scrim.classList.remove("show"); };
  $("#menuBtn").addEventListener("click", () => { rail.classList.add("open"); scrim.classList.add("show"); });
  scrim.addEventListener("click", closeRail);
  $("#historySearch").addEventListener("input", renderHistory);

  function renderHistory() {
    const nav = $("#history");
    const term = $("#historySearch").value.trim().toLowerCase();
    nav.innerHTML = "";
    const list = convs.filter((c) => !term || c.title.toLowerCase().includes(term) ||
      c.items.some((it) => it.q.toLowerCase().includes(term)));
    if (!list.length) {
      nav.append(h("p", { class: "history-empty" }, term ? "No conversations match." : "Your saved conversations will appear here."));
      return;
    }
    const startToday = new Date(); startToday.setHours(0, 0, 0, 0);
    const weekAgo = startToday.getTime() - 6 * 864e5;
    const groups = [["Today", []], ["Previous 7 days", []], ["Older", []]];
    list.forEach((c) => {
      const t = new Date(c.updated).getTime();
      (t >= startToday.getTime() ? groups[0] : t >= weekAgo ? groups[1] : groups[2])[1].push(c);
    });
    groups.forEach(([label, items]) => {
      if (!items.length) return;
      nav.append(h("h3", {}, label));
      items.forEach((c) => {
        const del = h("span", { class: "h-del", role: "button", tabindex: "0", title: "Delete conversation", "aria-label": "Delete " + c.title, html: ICON.trash });
        const btn = h("button", { class: "h-item" + (current && current.id === c.id ? " active" : ""), type: "button", title: c.title },
          h("span", {}, c.title), del);
        btn.addEventListener("click", (e) => {
          if (e.target.closest(".h-del")) return;
          openConversation(c.id);
          closeRail();
        });
        const doDelete = (e) => {
          e.stopPropagation();
          if (!confirm(`Delete "${c.title}"?`)) return;
          convs = convs.filter((x) => x.id !== c.id);
          saveConvs();
          if (current && current.id === c.id) newAnalysis();
          else renderHistory();
        };
        del.addEventListener("click", doDelete);
        del.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") doDelete(e); });
        nav.append(btn);
      });
    });
  }

  // --------------------------------------------------------- conversations
  function newAnalysis() {
    if (state.busy && state.controller) state.controller.abort();
    current = null;
    figNo = tabNo = 0;
    thread.innerHTML = "";
    overview.hidden = false;
    $("#convTitle").textContent = "Overview";
    renderHistory();
    scroll.scrollTop = 0;
    closeRail();
    input.focus();
  }
  $("#newBtn").addEventListener("click", newAnalysis);

  function openConversation(id) {
    if (state.busy) return toast("Wait for the current answer to finish");
    const c = convs.find((x) => x.id === id);
    if (!c) return;
    current = c;
    figNo = tabNo = 0;
    thread.innerHTML = "";
    overview.hidden = true;
    $("#convTitle").textContent = c.title;
    c.items.forEach((item) => renderStoredItem(item));
    renderHistory();
    requestAnimationFrame(() => (scroll.scrollTop = scroll.scrollHeight));
  }

  function ensureConversation(q) {
    if (current) return current;
    const title = q.length > 64 ? q.slice(0, 61).trim() + "…" : q;
    current = { id: uid(), title, created: new Date().toISOString(), updated: new Date().toISOString(), items: [] };
    convs.unshift(current);
    $("#convTitle").textContent = title;
    return current;
  }

  function historyForBackend() {
    if (!current) return [];
    const out = [];
    current.items.slice(-4).forEach((it) => {
      out.push({ role: "user", content: it.q });
      out.push({ role: "assistant", content: it.text || "", route: it.route || null, sql: it.sql || null });
    });
    return out;
  }

  // ----------------------------------------------------- finding: building
  function buildFinding(q, timeIso) {
    const el = h("section", { class: "finding" });
    const meta = h("div", { class: "finding-meta" });
    const tag = h("span", { class: "src-tag", hidden: true });
    const time = h("time", { datetime: timeIso }, timeStr(timeIso));
    const dur = h("span", { class: "tnum", hidden: true });
    meta.append(tag, time, dur);
    const stepper = h("ol", { class: "stepper", "aria-label": "Progress" });
    const answer = h("div", { class: "answer md" });
    const figs = h("div");
    const method = h("div");
    const actions = h("div", { class: "finding-actions" });
    el.append(h("header", {}, h("h2", { class: "finding-q" }, q), meta), stepper, answer, figs, method, actions);
    return { el, tag, dur, stepper, answer, figs, method, actions, raf: 0 };
  }

  function setRoute(f, route) {
    f.tag.hidden = false;
    f.tag.className = "src-tag " + route;
    f.tag.textContent = ROUTE_LABEL[route] || route;
  }

  function setSteps(f, kind, activeIndex) {
    const steps = STEPS[kind] || STEPS.pending;
    f.stepper.innerHTML = "";
    steps.forEach((s, i) => {
      const li = h("li", { class: i < activeIndex ? "done" : i === activeIndex ? "active" : "" }, s);
      if (i === activeIndex) li.setAttribute("aria-current", "step");
      f.stepper.append(li);
    });
  }

  function setAnswer(f, text, streaming) {
    cancelAnimationFrame(f.raf);
    f.raf = requestAnimationFrame(() => {
      f.answer.innerHTML = window.Markdown.render(text);
      f.answer.classList.toggle("streaming", !!streaming);
      followIfNear();
    });
  }

  function addNotice(f, msg) {
    f.answer.after(h("div", { class: "notice", role: "alert" }, msg));
  }

  // chart figure
  function addChart(f, spec) {
    figNo += 1;
    const box = h("div", { class: "chart-box", "data-auto-height": "" });
    const canvas = h("canvas");
    box.append(canvas);
    const types = spec.datasets.length === 1 && spec.labels.length <= 8
      ? ["bar", "horizontalBar", "line", "doughnut"] : ["bar", "horizontalBar", "line"];
    const names = { bar: "Columns", horizontalBar: "Rows", line: "Line", doughnut: "Donut" };
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Chart type" });
    types.forEach((t) => {
      const b = h("button", { type: "button", "aria-pressed": String(t === spec.type) }, names[t]);
      b.addEventListener("click", () => {
        seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        F.render(canvas, spec, t);
      });
      seg.append(b);
    });
    const caption = spec.title || "Result";
    const full = h("button", { class: "tool-btn", type: "button", title: "Full screen", "aria-label": "Show chart full screen", html: ICON.expand });
    full.addEventListener("click", () => openChartDialog(caption, spec, canvas._type || spec.type));
    const png = h("button", { class: "tool-btn", type: "button", title: "Download PNG", "aria-label": "Download chart as PNG", html: ICON.image });
    png.addEventListener("click", () => download(caption.replace(/\W+/g, "_") + ".png", canvas.toDataURL("image/png")));
    const fig = h("figure", { class: "figure" },
      h("div", { class: "figure-head" },
        h("figcaption", { class: "figure-cap" }, h("span", { class: "fig-no" }, `Figure ${figNo}`), caption),
        h("div", { class: "fig-tools" }, seg, full, png)),
      box);
    f.figs.prepend(fig);
    F.render(canvas, spec);
  }

  function openChartDialog(title, spec, type) {
    const dlg = $("#chartDialog");
    $("#dlgTitle").textContent = title;
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    requestAnimationFrame(() => F.render($("#dlgCanvas"), spec, type));
  }
  $("#dlgClose").addEventListener("click", () => $("#chartDialog").close());
  $("#chartDialog").addEventListener("click", (e) => { if (e.target.id === "chartDialog") e.target.close(); });

  // table figure
  function prettyCol(c) { return c.replace(/_pct$/, " %").replace(/_/g, " ").replace(/^./, (m) => m.toUpperCase()); }
  function formatCell(v, col, isNum) {
    if (v === null || v === undefined) return "–";
    if (!isNum) return String(v);
    const lc = col.toLowerCase();
    if (/revenue|amount|aov|sales|order_value/.test(lc)) return F.inrExact.format(v);
    if (/pct|rate|share/.test(lc)) return F.numIN.format(v) + "%";
    return F.numIN.format(v);
  }
  function toCSV(t) {
    const q = (v) => {
      const s = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [t.columns.map(q).join(",")].concat(t.rows.map((r) => r.map(q).join(","))).join("\n");
  }

  function addTable(f, t) {
    tabNo += 1;
    const numeric = new Set(t.numeric_columns || []);
    let rows = t.rows.slice();
    let sortCol = -1, sortDir = 1;
    const table = h("table", { class: "data" });
    const thead = h("thead");
    const tbody = h("tbody");
    const headRow = h("tr");
    t.columns.forEach((c, i) => {
      const th = h("th", { class: numeric.has(c) ? "num" : "", scope: "col" });
      const b = h("button", { type: "button" }, prettyCol(c), h("span", { class: "sort", "aria-hidden": "true" }, "↕"));
      b.addEventListener("click", () => {
        sortDir = sortCol === i ? -sortDir : (numeric.has(c) ? -1 : 1);
        sortCol = i;
        rows.sort((a, z) => {
          const x = a[i], y = z[i];
          if (x === y) return 0;
          if (x === null) return 1;
          if (y === null) return -1;
          return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sortDir;
        });
        headRow.querySelectorAll("th").forEach((x) => { x.removeAttribute("aria-sort"); x.querySelector(".sort").textContent = "↕"; });
        th.setAttribute("aria-sort", sortDir === 1 ? "ascending" : "descending");
        th.querySelector(".sort").textContent = sortDir === 1 ? "↑" : "↓";
        fillBody();
      });
      th.append(b);
      headRow.append(th);
    });
    thead.append(headRow);
    function fillBody() {
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.append(h("tr", {}, h("td", { colspan: t.columns.length }, "No rows matched this question.")));
        return;
      }
      rows.forEach((r) => {
        tbody.append(h("tr", {}, r.map((v, i) => {
          const isNum = numeric.has(t.columns[i]);
          return h("td", { class: isNum ? "num" : "" }, formatCell(v, t.columns[i], isNum));
        })));
      });
    }
    fillBody();
    table.append(thead, tbody);
    const csv = h("button", { class: "tool-btn", type: "button", title: "Download CSV", "aria-label": "Download table as CSV", html: ICON.download });
    csv.addEventListener("click", () => download(`table_${tabNo}.csv`, toCSV(t), "text/csv"));
    const metaTxt = `${t.row_count.toLocaleString("en-IN")} row${t.row_count === 1 ? "" : "s"}${t.truncated ? ", capped" : ""}`;
    const wrap = h("div", { class: "tablefig" },
      h("div", { class: "tablefig-head" },
        h("div", { class: "figure-cap" }, h("span", { class: "fig-no" }, `Table ${tabNo}`), "Query result"),
        h("div", { class: "fig-tools" }, h("span", { class: "tablefig-meta" }, metaTxt), csv)),
      h("div", { class: "table-wrap" }, table));
    if (t.row_count > t.rows.length) wrap.append(h("div", { class: "table-more" }, `Showing the first ${t.rows.length} rows. The CSV has the same rows; ask a narrower question for the rest.`));
    f.figs.append(wrap);
  }

  function highlightSQL(sql) {
    let s = esc(sql);
    s = s.replace(/'([^']*)'/g, '<span class="str">\'$1\'</span>');
    s = s.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="num">$1</span>');
    s = s.replace(/\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|AS|AND|OR|NOT|IN|WITH|SUM|COUNT|AVG|MIN|MAX|ROUND|DISTINCT|FILTER|OVER|PARTITION BY|CASE|WHEN|THEN|ELSE|END|DESC|ASC|HAVING|LAG|ROW_NUMBER|date_trunc|ILIKE|LIKE|BETWEEN|IS|NULL)\b/gi, '<span class="kw">$1</span>');
    return s;
  }

  // "How this was calculated"
  function renderMethod(f, item) {
    f.method.innerHTML = "";
    const body = h("div", { class: "method-body" });
    let label = "Where this answer comes from";
    if (item.route === "analytics" && item.sql && !item.sqlError) {
      label = "How this was calculated";
      body.append(h("p", {}, `The assistant wrote this SQL query and DuckDB ran it on the cleaned sales table${item.table ? `, returning ${item.table.row_count.toLocaleString("en-IN")} row${item.table.row_count === 1 ? "" : "s"}` : ""}. Every figure above comes from that result.`));
      const pre = h("pre", { class: "sql", html: highlightSQL(item.sql) });
      body.append(pre);
    } else if (item.route === "analytics" && item.sqlError) {
      label = "Why there is no table";
      body.append(h("p", {}, "The database query could not be run, so the answer uses the knowledge base instead. Error: " + item.sqlError));
      if (item.sql) body.append(h("pre", { class: "sql failed", html: highlightSQL(item.sql) }));
    } else if (item.route === "knowledge") {
      body.append(h("p", {}, "Answered from these sections of the knowledge base (schema, KPI definitions, data-cleaning notes and EDA insights):"));
    } else if (item.route === "general") {
      body.append(h("p", {}, "Answered by the language model alone, without looking up sales data."));
    } else {
      return;
    }
    if (item.sources && item.sources.length && item.route !== "general") {
      const seen = new Set();
      const ul = h("ul", { class: "sources" });
      item.sources.forEach((s) => {
        const name = s.section || s.title;
        if (seen.has(name)) return;
        seen.add(name);
        ul.append(h("li", { title: `${s.source || ""} (similarity ${s.score})` }, name));
      });
      if (item.route === "analytics") body.append(h("p", { style: "margin:12px 0 0" }, "Definitions given to the model:"));
      body.append(ul);
    }
    const dl = h("dl");
    const add = (k, v) => { if (v !== undefined && v !== null && v !== "") dl.append(h("dt", {}, k), h("dd", {}, v)); };
    if (item.route === "analytics") add("Query attempts", item.attempts);
    if (item.sqlMs !== undefined) add("Query time", `${item.sqlMs} ms`);
    if (item.timings && item.timings.total_ms) add("Total time", `${(item.timings.total_ms / 1000).toFixed(1)} s`);
    if (item.routeMethod) add("Routed by", { rule: "keyword rules", "follow-up": "follow-up of previous question", "semantic+rules": "similarity search and rules", llm: "language model", forced: "your selection" }[item.routeMethod] || item.routeMethod);
    if (dl.children.length) body.append(dl);
    const det = h("details", { class: "method" }, h("summary", { html: ICON.chev + esc(label) }), body);
    f.method.append(det);
  }

  function renderActions(f, item) {
    f.actions.innerHTML = "";
    const copyA = h("button", { class: "link-btn", type: "button", html: ICON.copy + "Copy answer" });
    copyA.addEventListener("click", () => copyText(item.text || "", "Answer copied"));
    const again = h("button", { class: "link-btn", type: "button", html: ICON.redo + "Ask again" });
    again.addEventListener("click", () => ask(item.q));
    f.actions.append(copyA, again);
    if (item.sql && !item.sqlError) {
      const copyS = h("button", { class: "link-btn", type: "button", html: ICON.code + "Copy SQL" });
      copyS.addEventListener("click", () => copyText(item.sql, "SQL copied"));
      f.actions.append(copyS);
    }
  }

  function finalize(f, item) {
    f.stepper.remove();
    setAnswer(f, item.text || (item.error ? "" : "_No answer was returned._"), false);
    if (item.timings && item.timings.total_ms) {
      f.dur.hidden = false;
      f.dur.textContent = `${(item.timings.total_ms / 1000).toFixed(1)} s`;
    }
    renderMethod(f, item);
    renderActions(f, item);
  }

  function renderStoredItem(item) {
    const f = buildFinding(item.q, item.time);
    thread.append(f.el);
    if (item.route) setRoute(f, item.route);
    if (item.chart) addChart(f, item.chart);
    if (item.table) addTable(f, item.table);
    if (item.error) addNotice(f, item.error);
    finalize(f, item);
  }

  // ----------------------------------------------------------- scrolling
  function nearBottom() { return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160; }
  function followIfNear() { if (nearBottom()) scroll.scrollTop = scroll.scrollHeight; }

  // ---------------------------------------------------------------- asking
  async function ask(question) {
    question = (question || "").trim();
    if (!question) return;
    if (state.busy) return toast("Wait for the current answer to finish");
    const history = historyForBackend();
    const conv = ensureConversation(question);
    overview.hidden = true;
    input.value = "";
    autosize();

    const item = { q: question, time: new Date().toISOString(), text: "" };
    const f = buildFinding(question, item.time);
    thread.append(f.el);
    setSteps(f, "pending", 0);
    f.el.scrollIntoView({ behavior: "smooth", block: "start" });

    setBusy(true);
    state.controller = new AbortController();
    const body = { message: question, history };
    const forced = $("#routeSelect").value;
    if (forced) body.route = forced;

    const onEvent = (ev) => {
      switch (ev.type) {
        case "route":
          item.route = ev.route;
          item.routeMethod = ev.method;
          setRoute(f, ev.route);
          setSteps(f, ev.route, 1);
          break;
        case "sql":
          item.sql = ev.sql;
          item.attempts = ev.attempts;
          item.sqlMs = ev.elapsed_ms;
          setSteps(f, "analytics", 2);
          break;
        case "sql_error":
          item.sql = ev.sql;
          item.sqlError = ev.error;
          item.attempts = ev.attempts;
          setSteps(f, "analytics", 2);
          break;
        case "table":
          item.table = { columns: ev.columns, rows: ev.rows, row_count: ev.row_count, truncated: ev.truncated, numeric_columns: ev.numeric_columns };
          addTable(f, item.table);
          break;
        case "chart":
          item.chart = ev.chart;
          addChart(f, ev.chart);
          break;
        case "sources":
          item.sources = ev.items;
          if (item.route === "knowledge") setSteps(f, "knowledge", 2);
          break;
        case "token": {
          if (!item.text) {
            const steps = STEPS[item.route] || STEPS.general;
            setSteps(f, item.route || "general", steps.length - 1);
          }
          item.text += ev.text;
          setAnswer(f, item.text, true);
          break;
        }
        case "error":
          item.error = ev.message;
          addNotice(f, ev.message);
          break;
        case "done":
          item.timings = ev.timings;
          break;
      }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: state.controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`the server returned ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (line) onEvent(JSON.parse(line));
        }
      }
      if (buf.trim()) onEvent(JSON.parse(buf));
    } catch (err) {
      if (err.name === "AbortError") item.text += item.text ? "\n\n_Stopped._" : "_Stopped._";
      else onEvent({ type: "error", message: "The assistant could not be reached: " + err.message + ". Check that run.py is still running." });
    } finally {
      finalize(f, item);
      conv.items.push(item);
      conv.updated = new Date().toISOString();
      convs = [conv].concat(convs.filter((c) => c.id !== conv.id));
      saveConvs();
      renderHistory();
      setBusy(false);
      input.focus();
    }
  }

  function setBusy(b) {
    state.busy = b;
    sendBtn.classList.toggle("busy", b);
    sendBtn.setAttribute("aria-label", b ? "Stop answering" : "Send question");
  }

  // -------------------------------------------------------------- composer
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(input.value); }
  });
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) { state.controller && state.controller.abort(); return; }
    ask(input.value);
  });
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); input.focus(); }
    if (e.key === "Escape") {
      if (state.busy && state.controller) state.controller.abort();
      closeRail();
    }
  });

  // ---------------------------------------------------------------- export
  $("#exportBtn").addEventListener("click", () => {
    let head = $(".print-head");
    if (!head) {
      head = h("div", { class: "print-head" }, h("h1"), h("p"));
      scroll.prepend(head);
    }
    head.querySelector("h1").textContent = current ? current.title : "Q2 2022 sales overview";
    head.querySelector("p").textContent = `Sales Insight report, Amazon India apparel, 1 April to 29 June 2022. Exported ${new Date().toLocaleString("en-IN")}.`;
    document.querySelectorAll("details.method").forEach((d) => (d.open = true));
    window.print();
  });

  // ------------------------------------------------------------- overview
  async function loadSuggestions() {
    let data;
    try { data = await (await fetch("/api/suggestions")).json(); } catch (e) { return; }
    const box = $("#suggestGroups");
    box.innerHTML = "";
    const groups = data.groups || { Questions: data.suggestions || [] };
    Object.entries(groups).forEach(([name, qs]) => {
      const ul = h("ul");
      qs.forEach((q) => ul.append(h("li", {}, h("button", { type: "button", onclick: () => ask(q) }, q))));
      box.append(h("div", { class: "sg" }, h("h4", {}, name), ul));
    });
  }

  function kpiCell(label, value, monthly, key, kind, lowerIsBetter) {
    const first = monthly[0][key], last = monthly[monthly.length - 1][key];
    let deltaTxt, cls;
    if (kind === "pts") {
      const d = last - first;
      deltaTxt = `${d >= 0 ? "▲ +" : "▼ "}${d.toFixed(2)} pts`;
      cls = Math.abs(d) < 0.005 ? "flat" : (d > 0) !== !!lowerIsBetter ? "up" : "down";
    } else {
      const d = ((last - first) / first) * 100;
      deltaTxt = `${d >= 0 ? "▲ +" : "▼ "}${d.toFixed(1)}%`;
      cls = Math.abs(d) < 0.05 ? "flat" : (d > 0) !== !!lowerIsBetter ? "up" : "down";
    }
    const max = Math.max(...monthly.map((m) => m[key]));
    const spark = h("span", { class: "spark", "aria-hidden": "true" },
      monthly.map((m) => h("i", { style: `height:${Math.max(3, (m[key] / max) * 24)}px`, title: `${m.month}` })));
    return h("div", { class: "kpi" },
      h("span", { class: "kpi-label" }, label),
      h("span", { class: "kpi-value" }, value),
      h("span", { class: "kpi-foot" },
        h("span", { class: "delta " + cls, title: `${monthly[0].month} to ${monthly[monthly.length - 1].month}` }, deltaTxt),
        spark));
  }

  async function loadOverview() {
    let ov;
    try { ov = await (await fetch("/api/overview")).json(); } catch (e) { return; }
    const k = ov.kpis, m = ov.monthly || [];
    const strip = $("#kpiStrip");
    strip.innerHTML = "";
    if (m.length >= 2) {
      strip.append(
        kpiCell("Revenue", F.compactINR(k.revenue), m, "revenue"),
        kpiCell("Orders", F.numIN.format(k.orders), m, "orders"),
        kpiCell("Units sold", F.numIN.format(k.units), m, "units"),
        kpiCell("Average order value", F.inrExact.format(k.aov), m, "aov"),
        kpiCell("Cancellation rate", k.cancel_rate.toFixed(2) + "%", m, "cancel_rate", "pts", true),
        kpiCell("Return rate", k.return_rate.toFixed(2) + "%", m, "return_rate", "pts", true));
    }

    const C = ov.charts;
    F.render($("#ovMonth"), {
      type: "bar", title: "Revenue by month", format: "inr",
      labels: C.revenue_by_month.rows.map((r) => r[0]), datasets: [{ label: "Revenue", data: C.revenue_by_month.rows.map((r) => r[1]) }],
    });
    const cat = C.revenue_by_category.rows;
    const top = cat.slice(0, 5);
    const other = cat.slice(5).reduce((a, r) => a + r[1], 0);
    const rows = other > 0 ? top.concat([["Other", other]]) : top;
    F.render($("#ovCat"), {
      type: "doughnut", title: "Share of revenue by category", format: "inr",
      labels: rows.map((r) => r[0]), datasets: [{ label: "Revenue", data: rows.map((r) => r[1]) }],
    });
    F.render($("#ovDaily"), {
      type: "line", title: "Daily revenue", format: "inr",
      labels: C.daily_revenue.rows.map((r) => r[0]), datasets: [{ label: "Revenue", data: C.daily_revenue.rows.map((r) => r[1]) }],
    });
    F.render($("#ovStates"), {
      type: "horizontalBar", title: "Top 10 states by revenue", format: "inr",
      labels: C.top_states.rows.map((r) => r[0]), datasets: [{ label: "Revenue", data: C.top_states.rows.map((r) => r[1]) }],
    });
  }

  // ---------------------------------------------------------------- health
  async function loadHealth() {
    const pill = $("#modelPill"), text = $("#modelText");
    const set = (sel, txt, ok) => {
      const n = $(sel);
      n.className = ok === undefined ? "" : ok ? "ok" : "bad";
      n.innerHTML = (ok === undefined ? "" : '<i class="dot"></i>') + esc(txt);
      n.title = txt;
    };
    try {
      const s = await (await fetch("/api/health")).json();
      const llm = s.llm;
      const ok = llm.reachable && llm.model_installed;
      pill.className = "model-pill " + (ok ? "ok" : "bad");
      text.textContent = ok ? `${llm.model}${llm.model_loaded ? ", on GPU" : ", ready"}`
        : !llm.reachable ? "Ollama is not running" : `Model not installed: ${llm.model}`;
      set("#stLlm", ok ? llm.model : llm.reachable ? "not installed" : "offline", ok);
      set("#stVram", llm.vram_gb != null ? `${llm.vram_gb} GB in use` : llm.model_loaded ? "loaded" : "not loaded");
      set("#stVec", `${s.vector_store.engine === "faiss" ? "FAISS" : "NumPy"}, ${s.vector_store.counts.docs} sections`);
      set("#stDb", "DuckDB, ready", true);
      $("#dsRows").textContent = Number(s.database.rows).toLocaleString("en-IN");
    } catch (e) {
      pill.className = "model-pill bad";
      text.textContent = "Server not reachable";
      set("#stLlm", "unknown", false);
    }
  }

  // ------------------------------------------------------------------ init
  renderHistory();
  loadSuggestions();
  loadOverview();
  loadHealth();
  setInterval(loadHealth, 20000);
  input.focus();
})();
