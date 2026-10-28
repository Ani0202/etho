/* Ethogram timeline (SVG). One row per behavior; thin grey marks under a row are the rule-level bouts
   that tier 1 later changed or removed. Bouts are clickable and keyboard-focusable. */
"use strict";

function niceStep(dur, maxTicks) {
  for (const st of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]) if (dur / st <= maxTicks) return st;
  return 900;
}

function overlaps(a, b) { return a[0] < b[1] && b[0] < a[1]; }

/* opts: names, colorIdx, bouts{name:[[s0,s1]]}, ruleBouts, n, fps, selected{behavior,s0,s1}, pending[{behavior,s0,s1}],
         onBout(behavior,s0,s1), onSeek(t), compact */
function ethogram(opts) {
  const W = 1000, left = 112, right = 12, top = 6, rowH = opts.compact ? 18 : 22, gap = opts.compact ? 8 : 14;
  const dur = opts.n / opts.fps, plotW = W - left - right;
  const rows = opts.names.length;
  const H = top + rows * (rowH + gap) + 24;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Ethogram timeline" });
  const x = sample => left + plotW * sample / opts.n;
  const axis = s("g", { class: "axis" });
  const step = niceStep(dur, 12);
  for (let t = 0; t <= dur + 1e-9; t += step) {
    const xx = left + plotW * t / dur;
    axis.append(s("line", { x1: xx, x2: xx, y1: top, y2: H - 22, stroke: "var(--grid)", "stroke-width": 1 }),
      s("text", { x: xx, y: H - 7, "text-anchor": "middle" }, `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`));
  }
  svg.append(axis);
  const hit = s("rect", { x: left, y: top, width: plotW, height: H - 28, fill: "transparent" });
  svg.append(hit);
  opts.names.forEach((b, i) => {
    const y = top + i * (rowH + gap);
    const color = colorVar(opts.colorIdx[b]);
    svg.append(s("text", { x: left - 10, y: y + rowH * 0.7, "text-anchor": "end" }, b));
    const own = opts.bouts[b] || [];
    for (const rb of (opts.ruleBouts && opts.ruleBouts[b]) || []) {
      if (own.some(fb => fb[0] === rb[0] && fb[1] === rb[1])) continue;   // unchanged by tier 1
      svg.append(s("rect", { class: "ghost", x: x(rb[0]), y: y + rowH + 2, width: Math.max(1.5, x(rb[1]) - x(rb[0])), height: 4, rx: 1 },
        s("title", {}, `rules said ${b} ${(rb[0] / opts.fps).toFixed(1)}-${(rb[1] / opts.fps).toFixed(1)} s; changed by the tier-1 check`)));
    }
    for (const [s0, s1] of own) {
      const sel = opts.selected && opts.selected.behavior === b && opts.selected.s0 === s0 && opts.selected.s1 === s1;
      const r = s("rect", { class: "bout" + (sel ? " sel" : ""), x: x(s0), y, width: Math.max(2, x(s1) - x(s0)), height: rowH, rx: 2,
        fill: color, tabindex: opts.onBout ? 0 : null, role: opts.onBout ? "button" : null,
        "aria-label": `${b} ${(s0 / opts.fps).toFixed(1)} to ${(s1 / opts.fps).toFixed(1)} seconds` },
        s("title", {}, `${b}: ${(s0 / opts.fps).toFixed(1)}-${(s1 / opts.fps).toFixed(1)} s (${((s1 - s0) / opts.fps).toFixed(1)} s)`));
      if (opts.onBout) {
        r.addEventListener("click", ev => { ev.stopPropagation(); opts.onBout(b, s0, s1); });
        r.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onBout(b, s0, s1); } });
      }
      svg.append(r);
    }
    for (const p of (opts.pending || []).filter(p => p.behavior === b)) {
      svg.append(s("rect", { class: "pend", x: x(p.s0) - 1, y: y - 2, width: Math.max(4, x(p.s1) - x(p.s0)) + 2, height: rowH + 4, rx: 3, fill: "none" },
        s("title", {}, `your correction: ${p.label}`)));
    }
  });
  const head = s("line", { x1: left, x2: left, y1: top, y2: H - 22, stroke: "var(--text-primary)", "stroke-width": 1.5, "pointer-events": "none" });
  if (opts.onSeek) svg.append(head);
  if (opts.onSeek) hit.addEventListener("click", ev => {
    const box = svg.getBoundingClientRect();
    const px = (ev.clientX - box.left) / box.width * W;
    opts.onSeek(Math.min(dur, Math.max(0, (px - left) / plotW * dur)));
  });
  const wrap = h("div", { class: "etho" }, svg);
  wrap.setTime = t => { const xx = left + plotW * Math.min(1, t / dur); head.setAttribute("x1", xx); head.setAttribute("x2", xx); };
  return wrap;
}
