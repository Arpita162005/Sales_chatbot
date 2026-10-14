/* Tiny, safe markdown renderer for LLM answers.
   Escapes all HTML first, then supports: paragraphs, ### headings, bullet and
   numbered lists, **bold**, *italic*, `code`, ``` code blocks ``` and simple
   pipe tables. No external dependency, works offline. */
(function () {
  "use strict";

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  }

  function render(src) {
    const text = esc(String(src || "").replace(/\r\n/g, "\n"));
    const lines = text.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // fenced code
      if (/^```/.test(line.trim())) {
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) buf.push(lines[i++]);
        i++;
        out.push("<pre><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }
      // heading
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        const lvl = Math.min(4, Math.max(3, h[1].length + 1));
        out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
        i++;
        continue;
      }
      // table (header | sep | rows)
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
        let html = "<table><thead><tr>" + cells(line).map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          html += "<tr>" + cells(lines[i]).map((c) => `<td>${c}</td>`).join("") + "</tr>";
          i++;
        }
        out.push(html + "</tbody></table>");
        continue;
      }
      // lists
      if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]\s+/.test(line);
        const tag = ordered ? "ol" : "ul";
        const items = [];
        while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
          items.push("<li>" + inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, "")) + "</li>");
          i++;
        }
        out.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }
      // blank
      if (!line.trim()) { i++; continue; }
      // paragraph
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^```|^#{1,4}\s|^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        buf.push(inline(lines[i]));
        i++;
      }
      out.push("<p>" + buf.join("<br>") + "</p>");
    }
    return out.join("");
  }

  window.Markdown = { render, escape: esc };
})();
