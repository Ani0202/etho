/* Ethogram timeline (SVG). One row per behavior; thin grey marks under a row are the rule-level bouts that tier 1
   later changed or removed; a triangle under a row marks a tier-1 verdict that was set aside by the restriction.
   Bouts and marks are clickable and keyboard-focusable. Behaviors are told apart by row label, legend text and a
   fill pattern, never by colour alone. With `expert`, a second block "Expert (CalMS21)" shares the same time axis. */
"use strict";

function niceStep(dur, maxTicks) {
  for (const st of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]) if (dur / st <= maxTicks) return st;
  return 900;
}

function overlaps(a, b) { return a[0] < b[1] && b[0] < a[1]; }

let ETHO_SVG_N = 0;

/* A pattern per behavior slot, so rows stay distinguishable without colour: solid, diagonals, dots, lines, hatch. */
function patternDefs(uid, slots) {
  const defs = s("defs", {});
  const mark = (idx, kids) => {
    const p = s("pattern", { id: `${uid}-p${idx}`, patternUnits: "userSpaceOnUse", width: 8, height: 8 },
      s("rect", { width: 8, height: 8, fill: colorVar(idx) }), ...kids);
    defs.append(p);
  };
  const ink = { stroke: "rgba(255,255,255,.8)", "stroke-width": 1.6, fill: "none" };
  const dark = { stroke: "rgba(0,0,0,.55)", "stroke-width": 1.6, fill: "none" };
  const shapes = [
    [],                                                                    // solid
    [s("path", { d: "M-2 2 L2 -2 M0 8 L8 0 M6 10 L10 6", ...ink })],        // diagonal /
    [s("circle", { cx: 2, cy: 2, r: 1.3, fill: "rgba(255,255,255,.85)" }), s("circle", { cx: 6, cy: 6, r: 1.3, fill: "rgba(255,255,255,.85)" })],
    [s("path", { d: "M-2 6 L2 10 M0 0 L8 8 M6 -2 L10 2", ...dark })],       // diagonal \
    [s("path", { d: "M0 2 H8 M0 6 H8", ...ink })],                         // horizontal
    [s("path", { d: "M2 0 V8 M6 0 V8", ...dark })],                        // vertical
    [s("path", { d: "M0 0 L8 8 M8 0 L0 8", ...ink })],                     // cross-hatch
    [s("rect", { x: 0, y: 0, width: 8, height: 8, fill: "rgba(255,255,255,.35)" })],
  ];
  for (const i of new Set(slots)) mark(i, shapes[i % shapes.length]);
  return defs;
}

/* opts: names, colorIdx, bouts{name:[[s0,s1]]}, ruleBouts, n, fps, selected{behavior,s0,s1}, pending[{behavior,s0,s1}],
         expert{label,bouts}, marks[{behavior,s0,s1,text}], onBout(behavior,s0,s1), onMark(behavior,s0,s1), onSeek(t),
         compact */
function ethogram(opts) {
  const c = !!opts.compact;
  const uid = `eg${++ETHO_SVG_N}`;
  const W = 1000, left = c ? 120 : 160, right = 14, top = 6;
  const rowH = c ? 20 : 28, gap = c ? 8 : 24, fs = c ? 13 : 16, tickFs = c ? 12 : 15;
  const dur = opts.n / opts.fps, plotW = W - left - right;
  const rows = opts.names.length;
  const blocks = opts.expert ? 2 : 1;
  const headH = opts.expert ? 24 : 0;
  const blockH = rows * (rowH + gap);
  const plotBottom = top + blocks * (headH + blockH);
  const legendH = c ? 0 : 30;
  const H = plotBottom + 28 + legendH;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.expert ? "Ethogram timeline: Etho and expert rows" : "Ethogram timeline" });
  svg.append(patternDefs(uid, opts.names.map(b => opts.colorIdx[b])));
  const x = sample => left + plotW * sample / opts.n;
  const fill = b => `url(#${uid}-p${opts.colorIdx[b]})`;
  const axis = s("g", { class: "axis" });
  const step = niceStep(dur, c ? 10 : 9);
  for (let t = 0; t <= dur + 1e-9; t += step) {
    const xx = left + plotW * t / dur;
    axis.append(s("line", { x1: xx, x2: xx, y1: top, y2: plotBottom, stroke: "var(--grid)", "stroke-width": 1 }),
      s("text", { x: xx, y: plotBottom + tickFs + 6, "text-anchor": "middle", "font-size": tickFs }, `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`));
  }
  svg.append(axis);
  const hit = s("rect", { x: left, y: top, width: plotW, height: plotBottom - top, fill: "transparent" });
  svg.append(hit);

  const interactive = !!opts.onBout;
  function block(y0, title, bouts, own) {
    if (title) svg.append(s("text", { x: 4, y: y0 + 16, class: "blocktitle", "font-size": fs, "font-weight": 700 }, title));
    opts.names.forEach((b, i) => {
      const y = y0 + headH + i * (rowH + gap);
      svg.append(s("text", { x: left - 10, y: y + rowH * 0.7, "text-anchor": "end", "font-size": fs }, b));
      svg.append(s("line", { x1: left, x2: left + plotW, y1: y + rowH + 1, y2: y + rowH + 1, stroke: "var(--grid)", "stroke-width": 1 }));
      const mine = (bouts && bouts[b]) || [];
      if (own) for (const rb of (opts.ruleBouts && opts.ruleBouts[b]) || []) {
        if (mine.some(fb => fb[0] === rb[0] && fb[1] === rb[1])) continue;   // unchanged by tier 1
        svg.append(s("rect", { class: "ghost", x: x(rb[0]), y: y + rowH + 3, width: Math.max(1.5, x(rb[1]) - x(rb[0])), height: 4, rx: 1 },
          s("title", {}, `rules said ${b} ${(rb[0] / opts.fps).toFixed(1)}-${(rb[1] / opts.fps).toFixed(1)} s; changed by the tier-1 check`)));
      }
      for (const [s0, s1] of mine) {
        const sel = own && opts.selected && opts.selected.behavior === b && opts.selected.s0 === s0 && opts.selected.s1 === s1;
        const act = own && interactive;
        const r = s("rect", { class: "bout" + (sel ? " sel" : "") + (act ? "" : " static"), x: x(s0), y, width: Math.max(2, x(s1) - x(s0)), height: rowH, rx: 2,
          fill: fill(b), tabindex: act ? 0 : null, role: act ? "button" : null,
          "aria-label": `${own ? "Etho" : title || "expert"}: ${b} ${(s0 / opts.fps).toFixed(1)} to ${(s1 / opts.fps).toFixed(1)} seconds` },
          s("title", {}, `${own ? "Etho" : title}: ${b} ${(s0 / opts.fps).toFixed(1)}-${(s1 / opts.fps).toFixed(1)} s (${((s1 - s0) / opts.fps).toFixed(1)} s)`));
        if (act) {
          r.addEventListener("click", ev => { ev.stopPropagation(); opts.onBout(b, s0, s1); });
          r.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onBout(b, s0, s1); } });
        }
        svg.append(r);
      }
      if (!own) return;
      for (const p of (opts.pending || []).filter(p => p.behavior === b)) {
        svg.append(s("rect", { class: "pend", x: x(p.s0) - 1, y: y - 2, width: Math.max(4, x(p.s1) - x(p.s0)) + 2, height: rowH + 4, rx: 3, fill: "none" },
          s("title", {}, `your correction: ${p.label}`)));
      }
      for (const m of (opts.marks || []).filter(m => m.behavior === b)) {
        const cx = x((m.s0 + m.s1) / 2), cy = y + rowH + 17;
        const tri = s("path", { class: "mark", d: `M${cx - 7} ${cy + 6} L${cx + 7} ${cy + 6} L${cx} ${cy - 7} Z`, tabindex: opts.onMark ? 0 : null,
          role: opts.onMark ? "button" : null, "aria-label": m.text }, s("title", {}, m.text));
        if (opts.onMark) {
          tri.addEventListener("click", ev => { ev.stopPropagation(); opts.onMark(m.behavior, m.s0, m.s1); });
          tri.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onMark(m.behavior, m.s0, m.s1); } });
        }
        svg.append(tri);
      }
    });
  }
  block(top, opts.expert ? "Etho" : null, opts.bouts, true);
  if (opts.expert) block(top + headH + blockH, opts.expert.label || "Expert", opts.expert.bouts, false);

  if (!c) {                    // legend in text, with the same fill patterns
    const ly = plotBottom + tickFs + 6 + 22;
    let lx = 4;
    opts.names.forEach(b => {
      svg.append(s("rect", { x: lx, y: ly - 12, width: 18, height: 14, rx: 2, fill: fill(b) }), s("text", { x: lx + 24, y: ly, "font-size": tickFs }, b));
      lx += 24 + b.length * 8.6 + 16;
    });
    if ((opts.marks || []).length || opts.onMark) {
      svg.append(s("path", { class: "mark", d: `M${lx} ${ly + 2} L${lx + 14} ${ly + 2} L${lx + 7} ${ly - 12} Z` }),
        s("text", { x: lx + 20, y: ly, "font-size": tickFs }, "tier-1 verdict set aside (click)"));
    }
  }
  const head = s("line", { x1: left, x2: left, y1: top, y2: plotBottom, stroke: "var(--text-primary)", "stroke-width": 2, "pointer-events": "none" });
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
