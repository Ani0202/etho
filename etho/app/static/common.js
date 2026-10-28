/* Shared helpers. All user- and model-supplied text goes through textContent (never innerHTML). */
"use strict";

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
  return el;
}

const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}

/* Refusals that mean "live use is not available to you right now": the page then falls back to the showcase and says why. */
const LIVE_OFF = new Set(["daily_cap", "total_cap", "read_only", "killed", "expired", "access_required", "access_unavailable"]);

/* Every failure becomes a sentence for the page, never raw JSON, an HTTP status line or a proxy's error page. */
async function api(path, opts) {
  let r;
  try { r = await fetch(path, opts); } catch (e) {
    const err = new Error("The server can't be reached right now. Pages that are already open keep working; please reload in a minute.");
    err.status = 0; err.code = "network"; throw err;
  }
  let body = null;
  try { body = await r.json(); } catch (e) { /* not JSON: a proxy or error page */ }
  if (!r.ok) {
    const said = body && typeof body === "object" && (body.friendly || body.message);
    const err = new Error(typeof said === "string" && said ? said
      : r.status >= 500 ? "The server is temporarily unavailable (a short outage or a restart). Please reload in a minute; the showcase will be back."
      : "That request could not be completed. Please reload the page and try again.");
    err.status = r.status; err.code = body && typeof body === "object" ? body.error : null; throw err;
  }
  return body;
}

const fmt = {
  usd(x) { if (x === null || x === undefined) return "n/a"; return x < 0.1 && x > 0 ? "$" + x.toFixed(3) : "$" + x.toFixed(2); },
  time(sec) {
    if (sec === null || sec === undefined) return "n/a";
    sec = Math.max(0, sec);
    if (sec < 60) return sec.toFixed(sec < 10 ? 1 : 0) + " s";
    return Math.floor(sec / 60) + ":" + String(Math.floor(sec % 60)).padStart(2, "0");
  },
  clock(sec) { return clockText(sec); },
  mb(bytes) { return (bytes / 1e6).toFixed(1) + " MB"; },
  x(v) { return v === null || v === undefined ? "n/a" : v.toFixed(v < 10 ? 2 : 1) + "×"; },
};

function colorVar(idx) { return `var(--b${idx})`; }

const STAGE_LABEL = {
  validate: "Validate", plan: "Plan", track: "Track", synthesize: "Write detectors", predict: "Score",
  tier1: "Tier-1 check", report: "Report", overlay: "Overlay video", revise: "Revise rules",
};
const SRC_LABEL = { seed: "reused: benchmark detector", cache: "reused: earlier upload", fresh: "written now", revised: "revised" };

/* Presentation mode (?present=1, remembered for the tab; ?present=0 turns it off): larger type for recording. */
(function () {
  try {
    const q = new URLSearchParams(location.search).get("present");
    if (q === "1") sessionStorage.setItem("etho.present", "1");
    if (q === "0") sessionStorage.removeItem("etho.present");
    const on = sessionStorage.getItem("etho.present") === "1";
    if (on) document.documentElement.classList.add("present");
    const tg = document.getElementById("present-toggle");
    if (tg) { tg.href = on ? "?present=0" : "?present=1"; tg.textContent = on ? "normal size" : "presentation mode"; }
  } catch (e) { /* storage blocked: the page just stays at normal size */ }
})();

/* "m:ss.t" for the elapsed-time clock. */
function clockText(sec) {
  const t = Math.round(Math.max(0, sec) * 10);       // round to tenths first, then split: 59.96 s is 1:00.0, never 0:60.0
  const m = Math.floor(t / 600);
  return `${m}:${((t - 600 * m) / 10).toFixed(1).padStart(4, "0")}`;
}

const FALLBACK_FAILURE = "Scoring this video did not finish, so there is no result for it. The precomputed showcase runs stay open.";

/* Live progress for a job: five phases, a large elapsed-time clock (real time since the job started, counted from the
   server's own elapsed value), the status message of each phase, running cost. Calls onEnd(snapshot) when it ends.
   Failures show the server's plain-language sentence, never an error text. */
function progressCard(jobId, onEnd, opts) {
  opts = opts || {};
  const clock = h("div", { class: "clock", role: "timer", "aria-label": "Elapsed time since the job started" }, "0:00.0");
  const clockCap = h("div", { class: "clock-cap" }, "elapsed since the job started");
  const status = h("div", { class: "status-big", "aria-live": "polite" }, "Connecting...");
  const meta = h("div", { class: "progress-meta" });
  const list = h("ul", { class: "stages phases", "aria-live": "polite" });
  const fail = h("div", { class: "note fail", role: "alert", hidden: true });
  const conn = h("div", { class: "hint", hidden: true }, "Connection to the server lost; reconnecting. The job keeps running on the server.");
  const cancel = h("button", { class: "small", onclick: async () => { cancel.disabled = true; try { await api(`/api/jobs/${jobId}/cancel`, { method: "POST" }); } catch (e) { /* ignore */ } } }, "Cancel");
  const card = h("div", { class: opts.bare ? "" : "card" }, opts.title ? h("h2", {}, opts.title) : null,
    h("div", { class: "clock-row" }, h("div", {}, clock, clockCap), h("div", { class: "grow" }, status, meta)),
    fail, conn, list, h("div", { style: { marginTop: "10px" } }, cancel));
  let base = 0, at = performance.now(), running = false, shown = 0;
  const tick = () => { if (!running) return; shown = Math.max(shown, base + (performance.now() - at) / 1000); clock.textContent = clockText(shown); };
  const timer = setInterval(tick, 100);
  const PH_LABEL = { running: "in progress", done: "done", pending: "waiting", failed: "failed", skipped: "skipped" };
  function render(sn) {
    conn.hidden = true;
    running = sn.status === "running";
    base = sn.elapsed_s; at = performance.now();
    if (running) shown = Math.max(shown, base); else shown = base;
    clock.textContent = clockText(shown);
    clock.classList.toggle("live", running);
    const phases = sn.phases && sn.phases.length ? sn.phases : sn.stages.map(st => ({ key: st.name, label: STAGE_LABEL[st.name] || st.name, status: st.status, message: st.message, elapsed_s: st.elapsed_s, cost_usd: st.cost_usd }));
    const cur = phases.find(p => p.status === "running");
    status.textContent = sn.status === "queued" ? `Queued, position ${sn.queue_position}. It starts as soon as the job ahead finishes.` :
      sn.status === "running" ? (cur ? `${cur.label}: ${cur.message}` : "Working") :
      sn.status === "done" ? "Done. Opening the results." : sn.status === "cancelled" ? "Cancelled" : "Did not finish";
    meta.textContent = `cost so far ${fmt.usd(sn.cost_usd)} (estimate before start ${fmt.usd(sn.est_cost_usd)})`;
    list.replaceChildren(...phases.map((p, i) => h("li", { class: p.status },
      h("span", { class: `dot ${p.status}`, title: PH_LABEL[p.status] || p.status }),
      h("span", { class: "ph" }, `${i + 1}. ${p.label}`),
      h("span", { class: "msg" }, p.status === "failed" ? "did not finish" : p.message),
      h("span", { class: "state" }, PH_LABEL[p.status] || p.status),
      h("span", { class: "num" }, p.status === "pending" ? "" : fmt.time(p.elapsed_s)),
      h("span", { class: "num cost" }, p.status === "pending" ? "" : fmt.usd(p.cost_usd)))));
    const bad = sn.status === "failed" || sn.status === "cancelled";
    fail.hidden = !bad;
    if (bad) fail.replaceChildren(sn.friendly || FALLBACK_FAILURE, " ", h("a", { href: "/" }, "Open the showcase runs"));
    cancel.hidden = !(sn.status === "queued" || sn.status === "running");
  }
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.addEventListener("progress", e => render(JSON.parse(e.data)));
  es.addEventListener("end", e => { const sn = JSON.parse(e.data); render(sn); es.close(); clearInterval(timer); onEnd && onEnd(sn); });
  es.onerror = () => { conn.hidden = false; /* the browser retries; the final state is also reachable by reloading */ };
  card.close = () => { es.close(); clearInterval(timer); };
  return card;
}

async function loadHeader() {
  try {
    const c = await api("/api/config");
    const el = document.getElementById("spend");
    if (el) el.textContent = c.live && c.live.mode !== "live" ? "" : `live budget left today ${fmt.usd(c.cap.remaining_usd)}`;
    const t = document.getElementById("tag");
    if (t) t.textContent = c.text.tagline;
    const t2 = document.getElementById("tagline");
    if (t2) t2.textContent = c.text.tagline;
    window.ETHO_CFG = c;
    return c;
  } catch (e) { return null; }
}
