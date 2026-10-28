"use strict";
(async function () {
  const $ = id => document.getElementById(id);
  const cfg = await loadHeader();
  let duration = null, file = null;

  // ---- showcases ----
  api("/api/showcases").then(({ showcases }) => {
    const box = $("showcases");
    if (!showcases.length) { box.replaceChildren(h("div", { class: "sub" }, "No showcase runs are packaged on this machine (scripts/make_showcase.py).")); return; }
    box.replaceChildren(...showcases.map(sc => h("div", { class: "card" },
      h("div", {}, h("span", { class: "badge" }, sc.dataset ? sc.dataset.name : "showcase")),
      h("h3", {}, sc.title),
      h("div", { class: "sub" }, sc.description),
      h("div", { class: "chips" }, sc.behaviors.map(b => h("span", { class: "chip" }, b, " ", sc.metrics[b] ? `${sc.metrics[b].bouts} bouts` : ""))),
      h("div", { class: "sub" }, `${fmt.time(sc.video.duration_s)} video, ${sc.wall_s ? `scored in ${fmt.time(sc.wall_s)}, ` : ""}model cost ${fmt.usd(sc.cost_usd)}${sc.cost_estimated ? " (list-price estimate)" : sc.cost_source ? ` (${sc.cost_source})` : ""}`),
      h("a", { class: "btn primary", href: `/p/${sc.id}` }, "Open results"))));
  }).catch(() => { $("showcases").replaceChildren(h("div", { class: "err" }, "Could not load showcases.")); });

  // ---- sample clip for judges (served read-only from the app's data dir; attribution is also in the page itself)
  api("/api/sample").then(sm => {
    $("sample-defs").replaceChildren(...Object.entries(sm.definitions).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
    $("sample-subject").textContent = sm.subject; $("sample-partner").textContent = sm.partner;
    $("sample-trim").textContent = sm.trimmed_note + (sm.clip ? ` (${sm.clip.trimmed})` : "");
    const dl = $("sample-dl");
    if (sm.available) {
      dl.hidden = false;
      $("sample-info").textContent = `${sm.clip.sequence}, ${fmt.time(sm.clip.duration_s)}, ${fmt.mb(sm.clip.size_bytes)}, mp4.`;
    } else $("sample-info").textContent = sm.message;
  }).catch(() => { $("sample-info").textContent = "The sample clip is not available right now."; });

  // ---- what this visitor may do: read-only, needs the code, or live ----
  function applyMode(extra) {
    const lv = (cfg && cfg.live) || { mode: "live", can_submit: true, access_required: false };
    const note = $("mode-note");
    const text = lv.message || (!lv.can_submit && extra) || "";   // the server's own sentence for this state
    note.textContent = text; note.hidden = !text;
    $("access-card").hidden = !(lv.mode === "live" && lv.access_required && !lv.access_granted);
    $("form").hidden = !lv.can_submit;
    if (!lv.can_submit) $("recent-card").hidden = true;
  }
  $("access-form").addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("access-err"); err.textContent = ""; $("unlock").disabled = true;
    try {
      await api("/api/access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: $("code").value }) });
      $("code").value = "";
      const c = await loadHeader();
      if (c) { cfg.live = c.live; cfg.cap = c.cap; }
      applyMode();
    } catch (ex) { err.textContent = ex.message; } finally { $("unlock").disabled = false; }
  });
  $("ret-h").textContent = cfg ? cfg.retention_hours : "24";

  // ---- form ----
  if (cfg) {
    $("limits").textContent = `mp4, mov or avi; up to ${cfg.limits.max_upload_mb} MB and ${Math.round(cfg.limits.max_duration_s / 60)} minutes. ` +
      `${cfg.limits.rate_limit_jobs} jobs per hour per visitor.`;
    $("subject").value = cfg.defaults.subject;
    $("partner").value = cfg.defaults.partner;
    for (const [name, text] of Object.entries(cfg.defaults.behaviors)) addBehavior(name, text);
  }
  if (!document.querySelector(".beh")) addBehavior("", "");
  applyMode();

  function addBehavior(name, text) {
    const n = h("input", { type: "text", value: name, maxlength: 32, placeholder: "behavior name", "aria-label": "Behavior name" });
    const t = h("textarea", { "aria-label": "Behavior definition", placeholder: "Describe the behavior in plain English" }, text);
    t.value = text;
    const grow = () => { t.style.height = "auto"; t.style.height = Math.max(78, t.scrollHeight + 4) + "px"; };
    t.addEventListener("input", grow);
    requestAnimationFrame(grow);
    const rm = h("button", { type: "button", class: "small", title: "Remove behavior", onclick: () => { row.remove(); updateEst(); } }, "Remove");
    const row = h("div", { class: "beh" }, n, t, rm);
    n.addEventListener("input", updateEst);
    $("behs").append(row);
  }
  $("add").addEventListener("click", () => { addBehavior("", ""); document.querySelector(".beh:last-child input").focus(); updateEst(); });

  function definitions() {
    return [...document.querySelectorAll(".beh")].map(r => ({ name: r.querySelector("input").value, definition: r.querySelector("textarea").value }))
      .filter(d => d.name.trim() || d.definition.trim());
  }

  function updateEst() {
    if (!cfg || duration === null) { $("est").textContent = ""; return; }
    const e = cfg.estimate, nb = Math.max(1, definitions().length);
    const est = duration * (e.sam_usd_per_s + e.tier1_usd_per_s) + e.fixed_usd + nb * e.per_behavior_usd;
    $("est").textContent = `Estimated cost before start: ${fmt.usd(est)} (budget left today ${fmt.usd(cfg.cap.remaining_usd)})`;
  }

  function pick(f) {
    file = f; duration = null; $("err").textContent = "";
    if (!f) { $("picked").textContent = ""; return; }
    $("picked").textContent = `${f.name}, ${fmt.mb(f.size)}`;
    const v = document.createElement("video");
    v.preload = "metadata";
    v.onloadedmetadata = () => { duration = v.duration; $("picked").textContent = `${f.name}, ${fmt.mb(f.size)}, ${fmt.time(duration)}`; URL.revokeObjectURL(v.src); updateEst(); };
    v.onerror = () => { URL.revokeObjectURL(v.src); };
    v.src = URL.createObjectURL(f);
  }
  const drop = $("drop"), input = $("file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => pick(input.files[0]));
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => { if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); });

  $("form").addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("err"); err.textContent = "";
    if (!file) { err.textContent = "Choose a video first."; return; }
    const defs = definitions();
    if (!defs.length) { err.textContent = "Write at least one behavior definition."; return; }
    if (cfg && file.size > cfg.limits.max_upload_mb * 1e6) { err.textContent = `That file is larger than ${cfg.limits.max_upload_mb} MB.`; return; }
    if (cfg && duration !== null && duration > cfg.limits.max_duration_s) { err.textContent = `That video is longer than ${Math.round(cfg.limits.max_duration_s / 60)} minutes.`; return; }
    const fd = new FormData();
    fd.append("video", file);
    fd.append("definitions", JSON.stringify(defs));
    fd.append("subject", $("subject").value);
    fd.append("partner", $("partner").value);
    $("go").disabled = true; $("go").textContent = "Uploading...";
    try {
      const r = await api("/api/jobs", { method: "POST", body: fd });
      const recent = JSON.parse(localStorage.getItem("etho.recent") || "[]");
      recent.unshift({ id: r.id, name: file.name, at: Date.now() });
      localStorage.setItem("etho.recent", JSON.stringify(recent.slice(0, 10)));
      location.href = r.url;
    } catch (ex) {
      err.textContent = ex.message;
      $("go").disabled = false; $("go").textContent = "Score video";
      if (LIVE_OFF.has(ex.code)) {         // live use just became unavailable: fall back to the showcase with the reason
        const c = await loadHeader();
        if (c) { cfg.live = c.live; cfg.cap = c.cap; }
        if (cfg && cfg.live && cfg.live.can_submit) cfg.live = { ...cfg.live, can_submit: false, message: ex.message };
        err.textContent = "";
        applyMode(ex.message);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
  });

  // ---- recent jobs (kept in this browser only) ----
  const recent = JSON.parse(localStorage.getItem("etho.recent") || "[]");
  if (recent.length) {
    $("recent-card").hidden = !(cfg && cfg.live ? cfg.live.can_submit : true);
    $("recent").replaceChildren(h("table", {}, h("tbody", {}, ...recent.map(r => h("tr", {},
      h("td", { class: "l" }, h("a", { href: `/p/${r.id}` }, r.name)), h("td", {}, new Date(r.at).toLocaleString()))))));
  }
})();
