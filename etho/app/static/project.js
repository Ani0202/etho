"use strict";
(async function () {
  const pid = decodeURIComponent(location.pathname.split("/").pop());
  const root = document.getElementById("root");
  const cfgP = loadHeader();

  const S = { proj: null, ver: 0, sel: null, pending: [], progress: null, seekAfterLoad: 0 };

  function fail(msg) { root.replaceChildren(h("div", { class: "card" }, h("h1", {}, "Not available"), h("p", { class: "sub" }, msg), h("a", { class: "btn", href: "/" }, "Home"))); }

  async function boot() {
    await cfgP;
    let p;
    try { p = await api(`/api/projects/${encodeURIComponent(pid)}`); } catch (e) { return fail(e.status === 404 ? "No such project. Uploads are kept only on the machine that ran them." : e.message); }
    S.proj = p;
    if (p.status !== "done") return showJob(p);
    if (S.ver > p.latest_version) S.ver = p.latest_version;
    render();
    if (p.active_job) watchRevision(p.active_job.id);
  }

  function showJob(p) {
    const title = h("div", { class: "card" }, h("h1", {}, p.status === "queued" ? "Queued" : p.status === "running" ? "Scoring your video" : p.status === "failed" ? "The job failed" : "Job " + p.status),
      h("p", { class: "sub" }, "Progress streams live. You can close this tab and come back to this address."));
    const card = progressCard(pid, sn => { if (sn.status === "done") boot(); }, {});
    root.replaceChildren(title, card);
  }

  // ---------------------------------------------------------------- results
  const TXT = () => (window.ETHO_CFG && window.ETHO_CFG.text) || { tile_title: "", tile_value: "", tile_sub: "" };
  const P = () => S.proj;
  const V = () => P().versions[S.ver];
  const names = () => P().behaviors.names;
  const cidx = () => P().behaviors.color_index;
  const fps = () => P().fps;
  const isLatest = () => S.ver === P().latest_version;

  function render() {
    const p = P();
    root.replaceChildren(
      header(), tiles(), playerCard(), h("div", { class: "grid2", style: { marginBottom: "16px" } }, metricsCard(), stagesCard()),
      correctionsCard(), compareCard(), reportCard(), auditCard());
    if (S.progress) document.getElementById("rev-slot").replaceChildren(S.progress);
  }

  function header() {
    const p = P();
    return h("div", { class: "card" },
      h("div", {}, h("span", { class: "badge" }, p.kind === "showcase" ? "showcase (public data)" : p.kind === "showcase_copy" ? "your copy of a showcase" : "your upload")),
      h("h1", {}, p.title),
      h("div", { class: "sub" }, `${p.video.name || ""}  ${fmt.time(p.video.duration_s)}, ${p.video.width}x${p.video.height}, scored at ${p.fps} fps. Subject: ${p.subject}. Partner: ${p.partner}.`),
      p.description ? h("div", { class: "sub" }, p.description) : null,
      p.dataset ? h("div", { class: "sub" }, `Data: ${p.dataset.name}, licence ${p.dataset.license}. `, h("a", { href: p.dataset.url, target: "_blank", rel: "noopener" }, "source")) : null,
      ...p.warnings.map(w => h("div", { class: "note", style: { marginTop: "8px" } }, w)));
  }

  function tiles() {
    const p = P(), t = p.timing, c = p.cost;
    const bouts = names().reduce((a, b) => a + V().metrics[b].bouts, 0);
    const mins = t.video_s / 60;
    return h("div", { class: "tiles" },
      h("div", { class: "tile" }, h("div", { class: "k" }, "End-to-end speed"),
        h("div", { class: "v" }, t.x_realtime === null ? "n/a" : fmt.x(t.x_realtime) + " real time"),
        h("div", { class: "s" }, t.x_realtime === null ? "Precomputed run: wall-clock not recorded. Upload a video to measure it." :
          `${fmt.time(t.wall_s)} wall-clock to score ${fmt.time(t.video_s)} of video` + (t.complete ? "" : " (partial)"))),
      h("div", { class: "tile" }, h("div", { class: "k" }, "Model cost"), h("div", { class: "v" }, fmt.usd(c.total_usd)),
        h("div", { class: "s" }, `${fmt.usd(c.total_usd / Math.max(mins, 1e-9))} per video-minute` + (c.estimated ? " (list-price estimate)" : c.ledger_usd !== null ? " (matches the gateway ledger)" : ""))),
      h("div", { class: "tile" }, h("div", { class: "k" }, "Bouts scored"), h("div", { class: "v" }, String(bouts)),
        h("div", { class: "s" }, `${names().length} behaviors, ${S.ver ? "revision " + S.ver : "original rules"}`)),
      h("div", { class: "tile" }, h("div", { class: "k" }, TXT().tile_title), h("div", { class: "v" }, TXT().tile_value),
        h("div", { class: "s" }, TXT().tile_sub)));
  }

  // ---- player + ethogram
  let video, etho;
  function playerCard() {
    const p = P();
    const vers = h("div", { class: "vers", role: "group", "aria-label": "Version" }, h("span", { class: "sub" }, "Version:"),
      ...p.versions.map((v, i) => h("button", { class: "small", "aria-pressed": String(i === S.ver), onclick: () => switchVersion(i) }, v.label)));
    video = h("video", { controls: true, preload: "metadata", playsinline: true, src: `/api/projects/${encodeURIComponent(pid)}/overlay?v=${S.ver}#t=0.05`, "aria-label": "Overlay video with scored behavior" });
    video.addEventListener("loadedmetadata", () => { if (S.seekAfterLoad) { video.currentTime = S.seekAfterLoad; S.seekAfterLoad = 0; } });
    const tick = () => { if (etho) etho.setTime(video.currentTime); if (!video.paused && !video.ended) requestAnimationFrame(tick); };
    video.addEventListener("play", tick);
    video.addEventListener("seeked", () => etho && etho.setTime(video.currentTime));
    video.addEventListener("timeupdate", () => etho && etho.setTime(video.currentTime));
    const selBox = h("div", { id: "selbox" });
    const card = h("div", { class: "card" }, h("h2", {}, "Ethogram synced with the overlay video"), vers,
      h("div", { class: "player" },
        h("div", {}, video,
          h("div", { class: "legend" }, h("span", {}, h("i", { style: { borderColor: "#ff0000" } }), "resident outline"),
            h("span", {}, h("i", { style: { borderColor: "#00e000" } }), "intruder outline"), h("span", {}, "behavior label burned into each frame"))),
        h("div", {}, h("div", { class: "sub", style: { marginBottom: "6px" } }, "Behavior definitions"),
          ...names().map(b => h("div", { style: { marginBottom: "8px", fontSize: "13px" } },
            h("span", { class: "sw", style: { background: colorVar(cidx()[b]) } }), " ", h("strong", {}, b), ": ", P().behaviors.definitions[b])))),
      h("div", { id: "etho-slot", style: { marginTop: "12px" } }),
      h("div", { class: "hint" }, "Click a bout to jump to it in the video. Click empty space on the timeline to seek. ",
        "Grey marks under a row are rule-level bouts that the tier-1 check removed or changed."),
      selBox);
    queueMicrotask(drawEtho);
    return card;
  }

  function drawEtho() {
    const slot = document.getElementById("etho-slot");
    if (!slot) return;
    etho = ethogram({
      names: names(), colorIdx: cidx(), bouts: V().bouts, ruleBouts: V().rule_bouts, n: P().n_samples, fps: fps(),
      selected: S.sel, pending: S.pending.map(c => ({ behavior: c.behavior, s0: c.start_s * fps(), s1: c.end_s * fps(), label: c.text })),
      onBout: (b, s0, s1) => { S.sel = { behavior: b, s0, s1 }; seek(s0 / fps(), true); drawEtho(); drawSel(); },
      onSeek: t => seek(t, false),
    });
    slot.replaceChildren(etho);
    etho.setTime(video ? video.currentTime : 0);
    drawSel();
  }
  function seek(t, play) { if (!video) return; video.currentTime = t; if (play) video.play().catch(() => {}); }

  function switchVersion(i) {
    S.seekAfterLoad = video ? video.currentTime : 0;
    S.ver = i; S.sel = null; render();
  }

  // ---- metrics + stages
  function metricsCard() {
    const m = V().metrics;
    return h("div", { class: "card" }, h("h2", {}, "Per-behavior metrics"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "behavior"), h("th", {}, "bouts"), h("th", {}, "total s"), h("th", {}, "% of video"), h("th", {}, "first at s"), h("th", {}, "mean bout s"))),
        h("tbody", {}, ...names().map(b => h("tr", {}, h("td", {}, h("span", { class: "sw", style: { background: colorVar(cidx()[b]) } }), " ", b),
          h("td", {}, m[b].bouts), h("td", {}, m[b].total_s), h("td", {}, m[b].pct_time), h("td", {}, m[b].latency_s ?? "-"), h("td", {}, m[b].mean_bout_s ?? "-"))))));
  }

  function stagesCard() {
    const src = S.ver ? V() : P();
    const stages = src.stages, tot = Math.max(1e-9, ...stages.map(s => s.elapsed_s || 0));
    const timing = S.ver ? V().timing : P().timing;
    const rows = stages.map(st => h("tr", {}, h("td", { class: "l" }, STAGE_LABEL[st.name] || st.name, st.status === "skipped" ? h("span", { class: "chip", style: { marginLeft: "6px" } }, "skipped") : null),
      h("td", { class: "l" }, h("div", { class: "bar", title: st.message }, h("i", { style: { width: `${100 * (st.elapsed_s || 0) / tot}%` } }))),
      h("td", {}, st.elapsed_s === null ? "-" : fmt.time(st.elapsed_s)), h("td", {}, fmt.usd(st.cost_usd))));
    return h("div", { class: "card" }, h("h2", {}, S.ver ? `Timing and cost of revision ${S.ver}` : "Per-stage timing and cost"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "stage"), h("th", { class: "l" }, ""), h("th", {}, "time"), h("th", {}, "cost"))),
        h("tbody", {}, ...rows),
        h("tfoot", {}, h("tr", {}, h("td", {}, h("strong", {}, "total")), h("td", {}), h("td", {}, h("strong", {}, timing.wall_s === null ? "n/a" : fmt.time(timing.wall_s))),
          h("td", {}, h("strong", {}, fmt.usd((S.ver ? V() : P()).cost.total_usd)))))),
      h("div", { class: "hint" }, timing.x_realtime === null ? "Timings were not recorded for this precomputed run." :
        `${fmt.time(timing.wall_s)} wall-clock for ${fmt.time(timing.video_s)} of video = ${fmt.x(timing.x_realtime)} real time (queue wait excluded).`));
  }

  // ---- corrections
  function correctionsCard() {
    const p = P();
    const slot = h("div", { id: "rev-slot" });
    const busy = !!p.active_job || !!S.progress;
    const card = h("div", { class: "card" }, h("h2", {}, "Correct a few bouts, then revise the rules"),
      h("p", { class: "sub", style: { margin: "0 0 8px" } }, "Mark bouts that are wrong, or add a behavior the scorer missed. Muse Spark rewrites the detector for the affected behaviors, " +
        "re-scores the video and re-runs the tier-1 check. The page then shows what changed and how many of your corrections are now satisfied."),
      !isLatest() ? h("div", { class: "note" }, "You are viewing an earlier version. Switch to the latest version to add corrections.") : null,
      h("div", { id: "selpanel" }), missedForm(busy || !isLatest()), h("ul", { class: "pending", id: "pendlist", "aria-live": "polite" }), reviseBar(busy || !isLatest()), slot);
    queueMicrotask(() => { drawPending(); });
    return card;
  }

  function drawSel() {
    const el = document.getElementById("selpanel");
    if (!el) return;
    const sel = S.sel;
    if (!sel) { el.replaceChildren(h("div", { class: "hint", style: { margin: "0 0 8px" } }, "Select a bout in the ethogram above to mark it wrong.")); return; }
    const rule = (V().rule_bouts[sel.behavior] || []).some(b => b[0] < sel.s1 && sel.s0 < b[1]);
    const choose = h("select", { "aria-label": "Correct label" }, h("option", { value: "none" }, "none (no behavior)"),
      ...names().filter(b => b !== sel.behavior).map(b => h("option", { value: b }, b)));
    const dis = !isLatest() || !!P().active_job || !!S.progress;
    el.replaceChildren(h("div", { class: "sel-panel" },
      h("div", {}, h("strong", {}, sel.behavior), `  ${fmt.clock(sel.s0 / fps())} to ${fmt.clock(sel.s1 / fps())} (${((sel.s1 - sel.s0) / fps()).toFixed(1)} s). `,
        h("span", { class: "sub" }, rule ? "Found by the rules." : "Added by the tier-1 check.")),
      h("div", { class: "row", style: { marginTop: "8px" } }, h("div", {}, h("label", {}, "This bout is actually"), choose),
        h("div", { class: "fit" }, h("button", { disabled: dis, onclick: () => addCorrection({ kind: "wrong", behavior: sel.behavior, start_s: sel.s0 / fps(), end_s: sel.s1 / fps(), correct_label: choose.value,
          text: `${sel.behavior} ${fmt.clock(sel.s0 / fps())}-${fmt.clock(sel.s1 / fps())} is wrong (actually ${choose.value})` }) }, "Mark wrong"),
          " ", h("button", { class: "link", onclick: () => { S.sel = null; drawEtho(); } }, "clear")))));
  }

  function missedForm(disabled) {
    const beh = h("select", { "aria-label": "Behavior that was missed" }, ...names().map(b => h("option", { value: b }, b)));
    const a = h("input", { type: "number", step: "0.1", min: "0", "aria-label": "Start (s)", placeholder: "start s" });
    const b2 = h("input", { type: "number", step: "0.1", min: "0", "aria-label": "End (s)", placeholder: "end s" });
    const err = h("div", { class: "err", role: "alert", style: { margin: "4px 0 0" } });
    const use = h("button", { type: "button", class: "small", disabled, onclick: () => { const t = video.currentTime; if (a.value === "") a.value = t.toFixed(1); else b2.value = t.toFixed(1); } }, "Use video time");
    return h("div", { style: { margin: "8px 0" } }, h("label", {}, "Add a missed bout"),
      h("div", { class: "row" }, h("div", {}, beh), h("div", {}, a), h("div", {}, b2), h("div", { class: "fit" }, use, " ",
        h("button", { type: "button", disabled, onclick: () => {
          const s0 = parseFloat(a.value), s1 = parseFloat(b2.value);
          if (!(s0 >= 0) || !(s1 > s0)) { err.textContent = "Enter a start and an end (seconds), end after start."; return; }
          if (s1 > P().video.duration_s) { err.textContent = `The video is ${P().video.duration_s.toFixed(1)} s long.`; return; }
          err.textContent = "";
          addCorrection({ kind: "missed", behavior: beh.value, start_s: s0, end_s: s1, text: `missed ${beh.value} ${fmt.clock(s0)}-${fmt.clock(s1)}` });
          a.value = ""; b2.value = "";
        } }, "Add"))), err);
  }

  function addCorrection(c) {
    S.pending.push(c); S.sel = null; drawPending(); drawEtho();
  }
  function drawPending() {
    const ul = document.getElementById("pendlist");
    if (!ul) return;
    ul.replaceChildren(...S.pending.map((c, i) => h("li", {}, h("span", {}, c.text), h("button", { class: "link", onclick: () => { S.pending.splice(i, 1); drawPending(); drawEtho(); } }, "remove"))));
    const btn = document.getElementById("revbtn");
    if (btn) { btn.disabled = !S.pending.length || !isLatest() || !!P().active_job || !!S.progress; btn.textContent = S.pending.length ? `Revise rules (${S.pending.length})` : "Revise rules"; }
  }

  function reviseBar(disabled) {
    const err = h("div", { class: "err", role: "alert", id: "reverr" });
    const btn = h("button", { class: "primary", id: "revbtn", disabled: true, onclick: async () => {
      err.textContent = ""; btn.disabled = true;
      try {
        const r = await api(`/api/projects/${encodeURIComponent(pid)}/revise`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ corrections: S.pending.map(({ text, ...c }) => c) }) });
        if (r.project_id !== pid) { location.href = `/p/${r.project_id}`; return; }   // showcase: continue in your own copy
        S.pending = []; drawPending(); drawEtho();
        watchRevision(r.job_id);
      } catch (e) { err.textContent = e.message; btn.disabled = false; }
    } }, "Revise rules");
    return h("div", {}, h("div", { class: "row" }, h("div", { class: "fit" }, btn),
      h("div", { class: "sub" }, `Calls Muse Spark (effort high) once per affected behavior, then the tier-1 check on the new windows. Costs are shown live.${P().kind === "showcase" ? " Revising a showcase makes your own private copy first; the showcase itself never changes." : ""}`)), err);
  }

  function watchRevision(jobId) {
    const slot = document.getElementById("rev-slot");
    if (S.progress && S.progress.close) S.progress.close();
    S.progress = progressCard(jobId, async sn => {
      S.progress = null;
      if (sn.status === "done") { S.ver = Infinity; S.sel = null; await boot(); document.getElementById("compare")?.scrollIntoView({ behavior: "smooth" }); }
      else { await boot(); }
    }, { bare: true, title: "Revision in progress" });
    if (slot) slot.replaceChildren(S.progress);
    drawPending();
  }

  // ---- before / after
  function compareCard() {
    if (!S.ver) return h("div");
    const cur = V(), prev = P().versions[S.ver - 1], d = cur.diff, cc = d.corrections;
    const rows = names().map(b => {
      const pb = d.per_behavior[b], delta = pb.bouts_after - pb.bouts_before;
      return h("tr", {}, h("td", {}, h("span", { class: "sw", style: { background: colorVar(cidx()[b]) } }), " ", b),
        h("td", {}, pb.bouts_before), h("td", {}, pb.bouts_after), h("td", { class: delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "delta-0" }, (delta > 0 ? "+" : "") + delta),
        h("td", {}, pb.total_s_before), h("td", {}, pb.total_s_after));
    });
    const mk = (v, title) => h("div", {}, h("div", { class: "sub" }, title), ethogram({ names: names(), colorIdx: cidx(), bouts: v.bouts, n: P().n_samples, fps: fps(), compact: true }));
    return h("div", { class: "card", id: "compare" }, h("h2", {}, `What changed in revision ${S.ver}`),
      h("div", { class: "tiles" },
        h("div", { class: "tile" }, h("div", { class: "k" }, "Your corrections now satisfied"), h("div", { class: "v" }, `${cc.final_after} of ${cc.n}`),
          h("div", { class: "s" }, `final labels, after tier 1; was ${cc.final_before} of ${cc.n}`)),
        h("div", { class: "tile" }, h("div", { class: "k" }, "Rule-level self-check"), h("div", { class: "v" }, `${cc.rule_after} of ${cc.rule_n}`),
          h("div", { class: "s" }, `detector output only; was ${cc.rule_before} of ${cc.rule_n}`)),
        h("div", { class: "tile" }, h("div", { class: "k" }, "Video relabeled"), h("div", { class: "v" }, fmt.time(d.relabeled_s)),
          h("div", { class: "s" }, "seconds whose final label changed"))),
      mk(prev, "before: " + prev.label), mk(cur, "after: " + cur.label),
      h("h3", {}, "Bout counts per behavior"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "behavior"), h("th", {}, "bouts before"), h("th", {}, "bouts after"), h("th", {}, "change"), h("th", {}, "total s before"), h("th", {}, "total s after"))), h("tbody", {}, rows)),
      h("h3", {}, "Rule revision"),
      h("div", { class: "chips" }, ...cur.revise.map(r => h("span", { class: "chip", title: r.status === "rejected" ?
        "The revision did not pass the self-check (it must handle more of your corrections without shifting the overall rate by more than 20%), so the original detector was kept." : "" },
        `${r.behavior}: ${r.status}`, r.check ? ` (handles ${r.check.fixed_old} -> ${r.check.fixed_new} of ${r.check.n})` : ""))),
      h("div", { class: "hint" }, "There are no labels in the app, so this is not an accuracy number. It shows what moved and how many of your own corrections the new scoring satisfies."));
  }

  // ---- report + audit
  function reportCard() {
    const url = `/api/projects/${encodeURIComponent(pid)}/report?v=${S.ver}`;
    const frame = h("iframe", { class: "report", src: url, title: "Etho report", sandbox: "allow-same-origin" });
    frame.addEventListener("load", () => { try { frame.style.height = Math.min(1400, frame.contentDocument.documentElement.scrollHeight + 8) + "px"; } catch (e) { /* keep default */ } });
    return h("div", { class: "card" }, h("div", { class: "row", style: { alignItems: "baseline" } }, h("h2", {}, "Report"), h("div", { class: "fit" }, h("a", { href: url, target: "_blank", rel: "noopener" }, "Open the self-contained HTML"))), frame);
  }

  function auditCard() {
    const body = h("div", {}, h("div", { class: "sub" }, "Loading..."));
    const det = h("details", { ontoggle: async () => {
      if (!det.open || det.dataset.loaded === String(S.ver)) return;
      det.dataset.loaded = String(S.ver);
      try { body.replaceChildren(renderAudit(await api(`/api/projects/${encodeURIComponent(pid)}/audit?v=${S.ver}`))); } catch (e) { body.replaceChildren(h("div", { class: "err" }, e.message)); }
    } }, h("summary", {}, "Audit trail: models, efforts, prompts, detector code, every model call's cost"), body);
    return h("div", { class: "card" }, det);
  }

  function renderAudit(a) {
    const pre = (t) => h("pre", { class: "mono" }, t);
    const groups = {};
    for (const c of a.calls) { const k = `${c.stage}|${c.model}|${c.effort ?? "n/a"}`; (groups[k] ||= { stage: c.stage, model: c.model, effort: c.effort, provider: c.provider, n: 0, cost: 0, hits: 0 }); const g = groups[k]; g.n++; g.cost += c.cost_usd; g.hits += c.cache_hit ? 1 : 0; }
    const out = [];
    if (a.note) out.push(h("div", { class: "note" }, a.note));
    out.push(h("h3", {}, "Models and reasoning effort by stage"),
      Object.keys(groups).length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "stage"), h("th", { class: "l" }, "provider / model"), h("th", {}, "effort"), h("th", {}, "calls"), h("th", {}, "cache hits"), h("th", {}, "cost"))),
        h("tbody", {}, ...Object.values(groups).map(g => h("tr", {}, h("td", {}, g.stage), h("td", { class: "l" }, `${g.provider} / ${g.model}`), h("td", {}, g.effort ?? "n/a"), h("td", {}, g.n), h("td", {}, g.hits), h("td", {}, fmt.usd(g.cost)))))) : h("div", { class: "sub" }, "No per-call records for this run."),
      h("div", { class: "hint" }, `Ledger reconciliation: ${a.ledger.rows} calls attributed to this project in ops/spend.jsonl, ${fmt.usd(a.ledger.total_usd)}.`),
      h("h3", {}, "What you wrote"), h("div", { class: "sub" }, `Subject: ${a.subject}. Partner: ${a.partner}.`),
      ...Object.entries(a.definitions).map(([k, v]) => h("div", { style: { fontSize: "13px" } }, h("strong", {}, k), ": ", v)));
    if (a.plan && a.plan.steps && a.plan.steps.length) out.push(h("h3", {}, `Perception plan (SAM phrase "${a.plan.phrase}")`), pre(JSON.stringify(a.plan.steps, null, 1)));
    out.push(h("h3", {}, "Detector code (runs in a sandbox, written by the model from your definitions)"),
      ...a.detectors.map(d => h("details", {}, h("summary", {}, `${d.behavior}  (${d.model}, prompt ${d.prompt_hash || "n/a"})`), pre(d.code),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "attempt"), h("th", {}, "cost"), h("th", {}, "latency"), h("th", {}, "tokens in/out"), h("th", {}, "check"))),
          h("tbody", {}, ...d.calls.map(c => h("tr", {}, h("td", {}, c.attempt ?? "-"), h("td", {}, fmt.usd(c.cost_usd)), h("td", {}, c.latency_s ?? "-"),
            h("td", {}, c.usage ? `${c.usage.input_tokens}/${c.usage.billed_output_tokens ?? c.usage.output_tokens}` : "-"),
            h("td", {}, c.revision_rejected ? "rejected" : c.check ? `${c.check.fixed_old}->${c.check.fixed_new} of ${c.check.n}` : "-"))))))));
    if (a.calls.length) out.push(h("h3", {}, "Every model call (click a row for the prompt and the answer)"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "v"), h("th", {}, "stage"), h("th", { class: "l" }, "model"), h("th", {}, "effort"), h("th", {}, "tokens in/out"), h("th", {}, "latency"), h("th", {}, "cached"), h("th", {}, "cost"))),
        h("tbody", {}, ...a.calls.flatMap(c => {
          const detail = h("tr", { hidden: true }, h("td", { colspan: 9, class: "l" }, c.prompt ? [h("div", { class: "sub" }, "prompt"), pre(c.prompt)] : null, c.response ? [h("div", { class: "sub" }, "response"), pre(c.response)] : null,
            c.phrase ? h("div", { class: "sub" }, `SAM phrase "${c.phrase}", ${c.frames_billed ?? "?"} frames billed`) : null));
          const u = c.usage || {};
          return [h("tr", { class: "click", tabindex: 0, onclick: () => { detail.hidden = !detail.hidden; } }, h("td", {}, c.n), h("td", {}, c.version), h("td", {}, c.stage), h("td", { class: "l" }, c.model), h("td", {}, c.effort ?? "n/a"),
            h("td", {}, u.input_tokens !== undefined ? `${u.input_tokens}/${u.billed_output_tokens ?? u.output_tokens}` : "-"), h("td", {}, c.latency_s ?? "-"), h("td", {}, c.cache_hit ? "yes" : ""), h("td", {}, fmt.usd(c.cost_usd))), detail];
        }))));
    if (a.verdicts.length) {
      const thumb = h("div", {});
      out.push(h("h3", {}, `Tier-1 verdicts (Muse Spark Open on Fireworks, frame strips; ${a.verdicts.length} windows)`),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "window s"), h("th", { class: "l" }, "rules said"), h("th", { class: "l" }, "tier 1 said"), h("th", {}, "confidence"), h("th", { class: "l" }, "reason"))),
          h("tbody", {}, ...a.verdicts.map(v => h("tr", { class: "click", tabindex: 0, onclick: () => {
            const img = h("img", { class: "thumb", alt: "tier-1 strip", src: `/api/projects/${encodeURIComponent(pid)}/strip/${v.strip}` });
            img.onerror = () => thumb.replaceChildren(h("div", { class: "sub" }, "Strip image not stored for this window."));
            thumb.replaceChildren(h("div", { class: "sub" }, `strip shown to the model for ${(v.s0 / fps()).toFixed(1)}-${(v.s1 / fps()).toFixed(1)} s`), img);
          } }, h("td", {}, `${(v.s0 / fps()).toFixed(1)}-${(v.s1 / fps()).toFixed(1)}`), h("td", { class: "l" }, v.rule_label), h("td", { class: "l" }, v.label), h("td", {}, v.confidence), h("td", { class: "l" }, v.reason))))), thumb);
    }
    return h("div", {}, out);
  }

  boot();
})();
