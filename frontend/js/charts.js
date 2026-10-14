/* Chart.js helpers: turns the backend chart spec into a Chart.js config.
   Colours: teal-led categorical palette in fixed order, validated for
   colour-blind separation on the light (#ffffff) and dark (#131d2d) chart
   surfaces. "Other" is always neutral grey. One value axis only. */
(function () {
  "use strict";

  const PALETTE = {
    light: ["#008f86", "#e0703a", "#4f5bc4", "#c99a00", "#c9508a", "#4c8f2f", "#2f86c9", "#b5483f"],
    dark: ["#1f9b94", "#d9672f", "#6f7be0", "#b58600", "#cc5a92", "#4f9a33", "#3b8fd4", "#d0605a"],
  };

  if (window.Chart) {
    Chart.defaults.font.family = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif';
    Chart.defaults.font.size = 12;
  }

  function theme() {
    return document.documentElement.dataset.theme === "light" ? "light" : "dark";
  }
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function colors() { return PALETTE[theme()]; }

  // ---- number formatting (Indian system) -------------------------------
  const inrFull = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
  const inrExact = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const numIN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

  function compactINR(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return "₹" + (v / 1e7).toFixed(a >= 1e8 ? 1 : 2) + " Cr";
    if (a >= 1e5) return "₹" + (v / 1e5).toFixed(1) + " L";
    if (a >= 1e3) return "₹" + (v / 1e3).toFixed(1) + " K";
    return "₹" + numIN.format(v);
  }
  function compactNum(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return (v / 1e7).toFixed(1) + " Cr";
    if (a >= 1e5) return (v / 1e5).toFixed(1) + " L";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return numIN.format(v);
  }
  function fmt(v, kind, exact) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    if (kind === "inr") return exact ? inrExact.format(v) : compactINR(v);
    if (kind === "pct") return numIN.format(v) + "%";
    return exact ? numIN.format(v) : compactNum(v);
  }

  function shortLabel(l) {
    const s = String(l);
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      const d = new Date(s.slice(0, 10) + "T00:00:00");
      return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
    }
    return s.length > 18 ? s.slice(0, 17) + "…" : s;
  }

  // ---- config builder ----------------------------------------------------
  function build(spec, typeOverride) {
    const type = typeOverride || spec.type;
    const pal = colors();
    const surface = css("--chart-surface");
    const text2 = css("--ink-2");
    const muted = css("--muted");
    const grid = css("--grid");
    const isRound = type === "pie" || type === "doughnut";
    const horizontal = type === "horizontalBar";
    const cjsType = horizontal ? "bar" : type;
    const multi = spec.datasets.length > 1;

    const datasets = spec.datasets.map((ds, i) => {
      const c = pal[i % pal.length];
      if (isRound) {
        return {
          label: ds.label, data: ds.data,
          backgroundColor: spec.labels.map((l, j) => (String(l) === "Other" ? (theme() === "light" ? "#aab2bf" : "#55617a") : pal[j % pal.length])),
          borderColor: surface, borderWidth: 2, hoverOffset: 6,
        };
      }
      if (type === "line") {
        return {
          label: ds.label, data: ds.data, borderColor: c, backgroundColor: c,
          borderWidth: 2, tension: 0.25, fill: false,
          pointRadius: spec.labels.length > 40 ? 0 : 3, pointHoverRadius: 5,
          pointBackgroundColor: c, pointBorderColor: surface, pointBorderWidth: 2,
        };
      }
      return {
        label: ds.label, data: ds.data, backgroundColor: c,
        borderRadius: 4, borderSkipped: "start", borderWidth: 0,
        maxBarThickness: 46, categoryPercentage: multi ? 0.75 : 0.7, barPercentage: 0.9,
      };
    });

    const valueAxis = {
      beginAtZero: true,
      grid: { color: grid, drawTicks: false },
      border: { display: false },
      ticks: { color: muted, padding: 6, maxTicksLimit: 6, callback: (v) => fmt(v, spec.format, false) },
    };
    const catAxis = {
      grid: { display: false },
      border: { color: grid },
      ticks: {
        color: muted, autoSkip: type === "line" || spec.labels.length > 30, maxRotation: horizontal ? 0 : 40, padding: 4,
        maxTicksLimit: type === "line" ? 10 : 30,
        callback: function (val) { return shortLabel(this.getLabelForValue(val)); },
      },
    };

    return {
      type: cjsType,
      data: { labels: spec.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: horizontal ? "y" : "x",
        animation: { duration: 450 },
        interaction: type === "line" ? { mode: "index", intersect: false } : { mode: "nearest", intersect: true },
        layout: { padding: { top: 4, right: 8 } },
        cutout: type === "doughnut" ? "62%" : undefined,
        scales: isRound ? {} : (horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis }),
        plugins: {
          legend: {
            display: multi || isRound,
            position: isRound && window.innerWidth >= 600 ? "right" : (isRound ? "bottom" : "top"),
            align: "start",
            labels: { color: text2, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", padding: 12 },
          },
          tooltip: {
            backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: text2,
            borderColor: css("--line-strong"), borderWidth: 1, padding: 10, cornerRadius: 8,
            usePointStyle: true, boxPadding: 4,
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed && typeof ctx.parsed === "object" ? (horizontal ? ctx.parsed.x : ctx.parsed.y) : ctx.parsed;
                let s = ` ${ctx.dataset.label}: ${fmt(v, spec.format, true)}`;
                if (isRound) {
                  const total = ctx.dataset.data.reduce((a, b) => a + (+b || 0), 0);
                  if (total) s += `  (${((v / total) * 100).toFixed(1)}%)`;
                }
                return s;
              },
            },
          },
        },
      },
    };
  }

  function render(canvas, spec, typeOverride) {
    if (canvas._chart) canvas._chart.destroy();
    const box = canvas.parentElement;
    if (box && box.hasAttribute("data-auto-height")) {
      // Row charts grow with the number of rows so labels never collide.
      const t = typeOverride || spec.type;
      box.style.height = t === "horizontalBar" ? Math.max(260, Math.min(1000, spec.labels.length * 26 + 50)) + "px" : "";
    }
    canvas._chart = new Chart(canvas, build(spec, typeOverride));
    canvas._spec = spec;
    canvas._type = typeOverride || spec.type;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", spec.title + " - " + spec.labels.length + " points; the data table below lists every value.");
    return canvas._chart;
  }

  // Re-draw every chart when the theme changes (colours are theme-specific).
  function rerenderAll() {
    document.querySelectorAll("canvas").forEach((c) => {
      if (c._spec) render(c, c._spec, c._type);
    });
  }

  window.Charts = { render, rerenderAll, fmt, compactINR, inrFull, inrExact, numIN, colors };
})();
