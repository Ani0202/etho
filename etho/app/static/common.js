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
  clock(sec) { sec = Math.max(0, sec); return Math.floor(sec / 60) + ":" + (sec % 60).toFixed(1).padStart(4, "0"); },
  mb(bytes) { return (bytes / 1e6).toFixed(1) + " MB"; },
  x(v) { return v === null || v === undefined ? "n/a" : v.toFixed(v < 10 ? 2 : 1) + "×"; },
};

function colorVar(idx) { return `var(--b${idx})`; }

const STAGE_LABEL = {
  validate: "Validate", plan: "Plan", track: "Track", synthesize: "Write detectors", predict: "Score",
  tier1: "Tier-1 check", report: "Report", overlay: "Overlay video", revise: "Revise rules",
};
const SRC_LABEL = { seed: "reused: benchmark detector", cache: "reused: earlier upload", fresh: "written now", revised: "revised" };

/* Live progress for a job: stage list, elapsed, running cost. Calls onEnd(snapshot) when the job ends. */
function progressCard(jobId, onEnd, opts) {
  opts = opts || {};
  const head = h("div", { class: "progress-head" });
  const list = h("ul", { class: "stages", "aria-live": "polite" });
  const cancel = h("button", { class: "small", onclick: async () => { cancel.disabled = true; try { await api(`/api/jobs/${jobId}/cancel`, { method: "POST" }); } catch (e) { /* ignore */ } } }, "Cancel");
  const card = h("div", { class: opts.bare ? "" : "card" }, opts.title ? h("h2", {}, opts.title) : null, head, list, h("div", { style: { marginTop: "10px" } }, cancel));
  function render(sn) {
    head.replaceChildren(
      h("span", { class: "big" }, sn.status === "queued" ? `Queued (position ${sn.queue_position})` :
        sn.status === "running" ? `${(sn.running_stages && sn.running_stages.length ? sn.running_stages : [sn.stage]).map(n => STAGE_LABEL[n] || n || "Working").join(" + ")}  (${sn.stage_index}/${sn.n_stages})` :
        sn.status === "done" ? "Done" : sn.status === "cancelled" ? "Cancelled" : "Failed"),
      h("span", { class: "m" }, `elapsed ${fmt.time(sn.elapsed_s)}`),
      h("span", { class: "m" }, `cost so far ${fmt.usd(sn.cost_usd)} (est. ${fmt.usd(sn.est_cost_usd)})`),
      ...(sn.error ? [h("span", { class: "err", role: "alert" }, sn.error)] : []));
    list.replaceChildren(...sn.stages.map(st => h("li", {},
      h("span", { class: `dot ${st.status}`, title: st.status }),
      h("span", {}, STAGE_LABEL[st.name] || st.name),
      h("span", { class: "msg", title: st.message }, st.message),
      h("span", { class: "num" }, st.status === "pending" ? "" : fmt.time(st.elapsed_s)),
      h("span", { class: "num cost" }, st.status === "pending" ? "" : fmt.usd(st.cost_usd)))));
    cancel.hidden = !(sn.status === "queued" || sn.status === "running");
  }
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.addEventListener("progress", e => render(JSON.parse(e.data)));
  es.addEventListener("end", e => { const sn = JSON.parse(e.data); render(sn); es.close(); onEnd && onEnd(sn); });
  es.onerror = () => { /* the browser retries; the final state is also reachable by reloading */ };
  card.close = () => es.close();
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
