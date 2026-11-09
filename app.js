"use strict";
const POLL_MS = 5000;
const HIDDEN_POLL_MS = 15000;
const CLOCK_TICK_MS = 30000;
const STATUS_LABEL = {
  "completed":"Completed","in-progress":"In progress","awaiting-approval":"Awaiting approval",
  "revising":"Revising","pending":"Pending","skipped":"Skipped","out":"Not in scope","unknown":"Unknown"
};
const AGENT_STATE_LABEL = {
  "working":"Working now","awaiting-approval":"Waiting for approval","contributed":"Has worked",
  "planned":"Upcoming","no-record":"Assigned, no activity recorded","idle":"Not in this run"
};
const TABS = ["overview", "workflow", "timeline", "agents", "audit"];
const AUDIT_PAGE = 200;
const ui = { record: null, stage: null, paused: false, data: null, artifact: null, filter: "", tab: "overview",
  audit: null, af: { q: "", event: "", stage: "", agent: "" }, auditShown: AUDIT_PAGE, openEvents: new Set(),
  wf: { showSkipped: false, replay: null }, rawView: false, seen: null };
/* Deep links: /?record=<space>/<intent>&stage=<slug>#workflow|#agents|#audit */
{ const p = new URLSearchParams(location.search); ui.record = p.get("record"); ui.stage = p.get("stage");
  const t = location.hash.slice(1); if (TABS.includes(t)) ui.tab = t; }

/* DOM helper: all text goes through textContent, never innerHTML, so project file contents can't inject markup. */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const pill = (status) => h("span", { class: "status-pill p-" + status }, STATUS_LABEL[status] || status);
const stageStatus = (s) => (s.decision === "SKIP" && s.status === "pending" ? "out" : s.status);
const fmtTime = (iso) => { if (!iso) return "—"; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(); };
function duration(a, b) {
  if (!a || !b) return null;
  const ms = new Date(b) - new Date(a); if (!(ms >= 0)) return null;
  const m = Math.round(ms / 60000); return m < 60 ? m + " min" : (m / 60).toFixed(1) + " h";
}
function allStages(d) { return (d.phases || []).flatMap((p) => p.stages); }

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

async function refresh() {
  try {
    const q = ui.record ? "?record=" + encodeURIComponent(ui.record) : "";
    const next = await getJSON("/api/status" + q);
    let changed = stableKey(next) !== stableKey(ui.data);
    ui.data = next;
    if (!ui.record && ui.data.record) ui.record = ui.data.record.id;
    if (ui.tab === "audit" && ui.data.record) changed = (await refreshAudit()) || changed;
    setLive(true, "checked " + new Date().toLocaleTimeString());
    if (changed) render(); // skip re-render when nothing moved, so scroll/selection stay put
    checkAlerts(ui.data);
  } catch (e) {
    if (ui.record && /Unknown record/.test(e.message)) { ui.record = null; ui.stage = null; return refresh(); }
    setLive(false, "offline: " + e.message);
  }
}
/* Values that change with the clock alone (idle time, open-segment lengths) don't count as "something moved". */
const CLOCK_KEYS = new Set(["serverNow", "idleSeconds", "waitSeconds", "now", "timing", "stalled"]);
function stableKey(d) {
  if (!d) return "";
  return JSON.stringify(d, (k, v) => (CLOCK_KEYS.has(k) ? undefined : v))
    + "|" + (d.health ? d.health.stalled : "") + "|" + (d.timing ? d.timing.stages.map((r) => r.slug + r.status + r.segments.length).join(",") : "");
}

async function refreshAudit() {
  const next = await getJSON("/api/audit?record=" + encodeURIComponent(ui.record));
  const changed = !ui.audit || ui.audit.record !== next.record || ui.audit.total !== next.total;
  ui.audit = next;
  return changed;
}
function setTab(tab) {
  ui.tab = tab;
  history.replaceState(null, "", location.pathname + location.search + "#" + tab);
  if (tab === "audit" && (!ui.audit || ui.audit.record !== ui.record)) { ui.audit = null; refresh(); }
  render();
}
function showAuditFor(filters) {
  ui.af = { q: "", event: "", stage: "", agent: "", ...filters };
  ui.auditShown = AUDIT_PAGE;
  setTab("audit");
}
function setLive(ok, text) {
  document.getElementById("liveDot").className = "dot" + (ok ? "" : " off");
  document.getElementById("updated").textContent = text;
}

function render() {
  const d = ui.data;
  if (!d) return;
  document.getElementById("proj").textContent = d.project + (d.harness ? "  ·  " + d.harness : "");
  renderRecordPicker(d);
  renderTabs(d);
  const views = { overview: renderOverview, workflow: renderWorkflowTab, timeline: renderTimelineTab, agents: renderAgentsTab, audit: renderAuditTab };
  document.getElementById("app").replaceChildren(...[].concat(views[ui.tab](d)).flat(), artifactModal());
  updateTitle(d);
}

function renderTabs(d) {
  const working = (d.agents || []).filter((a) => a.state === "working").length;
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.className = b.dataset.tab === ui.tab ? "on" : "";
    const label = { overview: "Overview", workflow: "Workflow", timeline: "Timeline", agents: "Agents", audit: "Audit trail" }[b.dataset.tab];
    const n = b.dataset.tab === "agents" ? working : b.dataset.tab === "audit" ? d.eventCount
      : b.dataset.tab === "overview" ? needsCount(d) : 0;
    b.replaceChildren(label, n ? h("span", { class: "n" + (b.dataset.tab === "overview" ? " n-alert" : "") }, String(n)) : "");
  });
}

function renderOverview(d) {
  return [
    d.record ? needsYou(d) : "",
    d.record ? renderKpis(d) : h("div", { class: "banner" }, d.message || "No AI-DLC record yet.",
      " Showing the stage catalog only. Start a run with /aidlc to create one."),
    renderLanes(d),
    d.record ? unitProgressCard(d, true) : "",
    h("div", { class: "grid2" }, renderDetail(d), renderEvents(d)),
    d.record ? errorsCard(d) : "",
    renderSessions(d)
  ];
}

function renderRecordPicker(d) {
  const sel = document.getElementById("recordSel");
  sel.replaceChildren(...(d.records.length ? d.records : [{ id: "", label: "(none)" }]).map((r) =>
    h("option", { value: r.id, selected: r.id === ui.record }, r.label + (r.status ? " — " + r.status : ""))));
}

function renderKpis(d) {
  const s = d.summary, cur = d.sections["Current Status"] || {}, info = d.sections["Project Information"] || {};
  const scope = d.sections["Scope Configuration"] || {};
  const kpi = (label, value, extra) => h("div", { class: "kpi" }, h("div", { class: "v" }, value), h("div", { class: "l" }, label), extra);
  return h("section", { class: "kpis" },
    kpi("Progress", s.percent + "%", h("div", { class: "bar" }, h("i", { style: "width:" + s.percent + "%" }))),
    kpi("Stages done / in scope", s.done + " / " + s.inScope),
    kpi("Workflow status", cur["Status"] || "—"),
    kpi("Current stage", cur["Current Stage"] || "—", h("div", { class: "l" }, "next: " + (cur["Next Stage"] || "—"))),
    kpi("Awaiting approval", String(s.awaitingApproval.length), h("div", { class: "l" }, s.awaitingApproval.join(", ") || "none")),
    kpi("Scope · depth · tests", info["Scope"] || d.record.scope || "—", h("div", { class: "l" }, (scope["Depth"] || "—") + " · " + (scope["Test Strategy"] || "—"))),
    kpi("Working now", (d.agents || []).filter((a) => a.state === "working").map((a) => a.name.replace(/^aidlc-/, "")).join(", ") || "—",
      h("button", { type: "button", class: "link", onclick: () => setTab("agents") }, "state file: " + (info["Active Agent"] || "—") + " →")),
    kpi("Last updated", fmtTime(cur["Last Updated"]))
  );
}

function phaseState(stages) {
  const st = stages.map(stageStatus);
  if (st.every((x) => x === "out")) return "out";
  if (st.some((x) => x === "awaiting-approval")) return "awaiting-approval";
  if (st.some((x) => x === "in-progress" || x === "revising")) return "in-progress";
  if (st.every((x) => ["completed", "skipped", "out"].includes(x))) return "completed";
  return "pending";
}

function renderLanes(d) {
  const lanes = (d.phases || []).map((p) => {
    const inScope = p.stages.filter((s) => s.decision === "EXECUTE");
    const done = inScope.filter((s) => ["completed", "skipped"].includes(s.status)).length;
    return h("div", { class: "lane" },
      h("div", { class: "lh" }, h("span", {}, p.name), h("span", {}, pill(phaseState(p.stages)), " ", h("small", {}, done + "/" + inScope.length))),
      h("div", { class: "ls" }, p.stages.map(stageChip)));
  });
  return h("section", { class: "card" }, h("h2", {}, "Lifecycle"), h("div", { class: "lanes" }, lanes));
}

function stageChip(s) {
  const st = stageStatus(s);
  return h("button", {
    type: "button", class: "stage st-" + st + (ui.stage === s.slug ? " sel" : ""),
    title: STATUS_LABEL[st], onclick: () => { ui.stage = s.slug; ui.artifact = null; render(); }
  },
    h("div", { class: "t" }, (s.id ? s.id + "  " : "") + s.slug),
    h("div", { class: "s" }, h("span", {}, STATUS_LABEL[st]), h("span", {}, s.artifacts.length ? s.artifacts.length + " files" : "")));
}

function renderDetail(d) {
  const s = allStages(d).find((x) => x.slug === ui.stage);
  const card = h("section", { class: "card" }, h("h2", {}, "Stage detail"));
  if (!s) { card.append(h("p", { class: "empty" }, "Click a stage to see its agents, timing and artifacts.")); return card; }
  const m = s.meta || {}, t = s.timeline || {};
  card.append(
    h("div", { style: "display:flex;gap:10px;align-items:center;margin-bottom:10px" },
      h("strong", { style: "font-size:16px" }, (s.id ? s.id + " " : "") + s.slug), pill(stageStatus(s))),
    h("dl", {},
      ...row("Decision", s.decision), ...row("Lead agent", m.lead), ...row("Support", (m.support || []).join(", ")),
      ...row("Reviewer", m.reviewer), ...row("Execution", m.execution), ...row("Condition", m.condition),
      ...row("Per", m.forEach), ...row("Declared outputs", (m.produces || []).join(", ")),
      ...row("Started", fmtTime(t.startedAt)), ...row("Completed", fmtTime(t.completedAt)),
      ...row("Duration", duration(t.startedAt, t.completedAt)),
      ...row("Gates / approvals / revisions", t.gates != null ? `${t.gates} / ${t.approvals} / ${t.revisions}` : null),
      ...row("Agents seen in audit", (t.agents || []).join(", "))),
    t.startedAt ? h("button", { type: "button", class: "link", style: "margin-top:8px", onclick: () => showAuditFor({ stage: s.slug }) }, "View this stage's audit events →") : "",
    h("h2", { style: "margin-top:14px" }, "Artifacts (" + s.artifacts.length + ")"),
    s.artifacts.length ? h("ul", { class: "arts" }, s.artifacts.map((a) =>
      h("li", {}, h("button", { type: "button", onclick: () => openArtifact(a.path) }, a.path), h("span", { class: "muted" }, "  " + Math.ceil(a.size / 1024) + " KB"))))
      : h("p", { class: "empty" }, "No artifacts on disk for this stage.")
  );
  return card;
}
function row(k, v) { return v ? [h("dt", {}, k), h("dd", {}, v)] : []; }

async function openArtifact(path) {
  try {
    ui.artifact = await getJSON("/api/artifact?record=" + encodeURIComponent(ui.record || "") + "&path=" + encodeURIComponent(path));
  } catch (e) { ui.artifact = { path, text: "", error: "Could not open: " + e.message }; }
  render();
}
function artifactModal() {
  if (!ui.artifact) return "";
  return h("div", { class: "modal", onclick: (e) => { if (e.target.classList.contains("modal")) { ui.artifact = null; render(); } } },
    h("div", { class: "modal-box", role: "dialog", "aria-label": "Artifact viewer" }, artifactPanel(ui.artifact)));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && ui.artifact) { ui.artifact = null; render(); } });

function renderEvents(d) {
  const f = ui.filter.toLowerCase();
  const evs = (d.events || []).filter((e) => !f || [e.event, e.stage, e.title].join(" ").toLowerCase().includes(f));
  const input = h("input", { placeholder: "Filter events (e.g. GATE, code-generation)", value: ui.filter,
    oninput: (e) => { ui.filter = e.target.value; const pos = e.target.selectionStart; render(); const el = document.querySelector(".filter input"); el.focus(); el.setSelectionRange(pos, pos); } });
  return h("section", { class: "card" },
    h("h2", {}, "Recent audit events (latest " + (d.events || []).length + " of " + (d.eventCount || 0) + ")",
      " ", h("button", { type: "button", class: "link", style: "text-transform:none;letter-spacing:0", onclick: () => setTab("audit") }, "open full audit trail →")),
    h("div", { class: "filter" }, input),
    evs.length ? h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Event"), h("th", {}, "Stage / details"))),
      h("tbody", {}, evs.map((e) => h("tr", {},
        h("td", { class: "muted", style: "white-space:nowrap" }, fmtTime(e.timestamp)),
        h("td", {}, h("span", { class: "ev" }, e.event)),
        h("td", {}, e.stage || "", eventDetail(e)))))))
      : h("p", { class: "empty" }, "No audit events."));
}
function eventDetail(e) {
  const keys = ["Agent", "Agent Type", "Reviewer", "Verdict", "Phase", "Scope", "Reason", "Details", "Choice", "Context", "Request"];
  const parts = keys.filter((k) => e.fields[k]).map((k) => k + ": " + e.fields[k]);
  return parts.length ? h("div", { class: "muted", style: "font-size:11.5px" }, parts.join(" · ").slice(0, 240)) : null;
}

function renderSessions(d) {
  const s = d.sessions || {}, b = s.binding || {};
  return h("section", { class: "card" }, h("h2", {}, "Sessions & engine questions"),
    h("dl", {}, ...row("Active space", s.activeSpace), ...row("Current session", s.currentSession),
      ...row("Bound intent", b.intent === null ? "none (no intent bound yet)" : b.intent), ...row("Bound at", fmtTime(b.boundAt)),
      ...row("Record path", d.record && d.record.path)),
    h("h2", { style: "margin-top:14px" }, "Pending questions (" + (s.questions || []).length + ")"),
    (s.questions || []).length ? h("table", {}, h("tbody", {}, s.questions.map((q) => h("tr", {},
      h("td", { class: "muted" }, fmtTime(q.createdAt)), h("td", {}, q.origin || ""), h("td", {}, q.text || h("span", { class: "empty" }, "(empty)")),
      h("td", {}, q.proposedScope || "")))))
      : h("p", { class: "empty" }, "None."),
    h("h2", { style: "margin-top:14px" }, "Recent prompts"),
    (s.messages || []).length ? h("table", {}, h("tbody", {}, s.messages.slice().reverse().map((m) => h("tr", {},
      h("td", { class: "muted", style: "white-space:nowrap" }, fmtTime(m.at)), h("td", { class: "mono" }, m.text || "")))))
      : h("p", { class: "empty" }, "None."));
}

/* ------------------------------------------------------------------ Agents tab */
const agentPill = (state) => h("span", { class: "status-pill p-" + state }, AGENT_STATE_LABEL[state] || state);
const stageRefChip = (s) => h("span", { class: "chip c-" + s.status, title: STATUS_LABEL[s.status] || s.status }, (s.id ? s.id + " " : "") + s.slug);

function renderAgentsTab(d) {
  const agents = d.agents || [];
  const by = (st) => agents.filter((a) => st.includes(a.state));
  const section = (title, list, hint) => list.length ? h("section", { class: "card" },
    h("h2", {}, title + " (" + list.length + ")"), hint ? h("p", { class: "muted", style: "margin:-6px 0 12px;font-size:12px" }, hint) : "",
    h("div", { class: "agents" }, list.map((a) => agentCard(a, d)))) : "";
  if (!d.record) return h("div", { class: "banner" }, "No run yet, so no agent activity to show.");
  return [
    section("Working now", by(["working", "awaiting-approval"]), "Leads a stage that is in progress, or has a dispatch with no completion yet."),
    section("Has worked on this run", by(["contributed"]), "Recorded in the audit trail as starting a stage, being dispatched, or reviewing."),
    section("Upcoming", by(["planned"]), "Assigned (lead, support or reviewer) to in-scope stages that haven't run yet."),
    section("Assigned, no activity recorded", by(["no-record"]),
      "Named as support or reviewer on finished stages, but the audit trail has no dispatch or review from them. Older runs did not log support agents."),
    section("Not in this run", by(["idle"]), "Installed, but no in-scope stage uses them."),
  ];
}

function agentCard(a, d) {
  const stages = Object.fromEntries(allStages(d).map((s) => [s.slug, s]));
  const ranRefs = a.ran.map((slug) => ({ slug, id: stages[slug] && stages[slug].id, status: stages[slug] ? stages[slug].status : "completed" }));
  const nowStages = a.leads.filter((s) => ["in-progress", "revising", "awaiting-approval"].includes(s.status));
  const roleRow = (label, refs) => refs.length ? h("div", {}, h("span", { class: "k" }, label), h("span", { class: "roles", style: "display:inline-flex" }, refs.map(stageRefChip))) : "";
  return h("div", { class: "agent a-" + a.state },
    h("h3", {}, h("span", {}, a.name), agentPill(a.state)),
    a.description ? h("div", { class: "desc" }, a.description) : "",
    nowStages.length ? h("div", {}, h("strong", {}, "Now: "), nowStages.map((s) => (s.id ? s.id + " " : "") + s.slug + " (" + (STATUS_LABEL[s.status] || s.status) + ")").join(", ")) : "",
    roleRow("Ran", ranRefs),
    roleRow("Leads", a.leads), roleRow("Supports", a.supports), roleRow("Reviews", a.reviews),
    h("div", { class: "muted", style: "font-size:12px" },
      [a.tier ? "tier: " + a.tier : "", a.dispatches ? "dispatched " + a.dispatches + "× · finished " + a.completions + "×" : "",
       a.lastSeen ? "last seen " + fmtTime(a.lastSeen) : ""].filter(Boolean).join(" · ")),
    a.events ? h("button", { type: "button", class: "link", onclick: () => showAuditFor({ agent: a.name }) }, "View " + a.events + " audit events →") : "");
}

/* ------------------------------------------------------------------ Audit tab */
function renderAuditTab(d) {
  if (!d.record) return h("div", { class: "banner" }, "No run yet, so there is no audit trail.");
  if (!ui.audit) return h("div", { class: "card empty" }, "Loading audit trail…");
  const all = ui.audit.events, f = ui.af, q = f.q.toLowerCase();
  const rows = all.filter((e) => (!f.event || e.event === f.event) && (!f.stage || e.stage === f.stage) && (!f.agent || e.agent === f.agent)
    && (!q || JSON.stringify(e).toLowerCase().includes(q)));
  return h("section", { class: "card" },
    h("h2", {}, "Audit trail — " + rows.length + " of " + ui.audit.total + " events" + (all.length < ui.audit.total ? " (newest " + all.length + " loaded)" : "")),
    auditToolbar(all),
    rows.length ? h("div", {}, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Event"), h("th", {}, "Stage"), h("th", {}, "Agent"), h("th", {}, "Summary"))),
      h("tbody", {}, rows.slice(0, ui.auditShown).flatMap(auditRow))),
      rows.length > ui.auditShown ? h("button", { type: "button", style: "margin-top:10px",
        onclick: () => { ui.auditShown += AUDIT_PAGE; render(); } }, "Show " + Math.min(AUDIT_PAGE, rows.length - ui.auditShown) + " more") : "")
      : h("p", { class: "empty" }, "No events match these filters."));
}

function auditToolbar(all) {
  const counts = (key) => { const c = {}; all.forEach((e) => { if (e[key]) c[e[key]] = (c[e[key]] || 0) + 1; }); return c; };
  const select = (key, label) => {
    const c = counts(key);
    return h("select", { onchange: (e) => { ui.af[key] = e.target.value; ui.auditShown = AUDIT_PAGE; render(); } },
      h("option", { value: "" }, "All " + label),
      Object.keys(c).sort().map((k) => h("option", { value: k, selected: ui.af[key] === k }, k + " (" + c[k] + ")")));
  };
  const search = h("input", { id: "auditSearch", placeholder: "Search all fields…", value: ui.af.q, oninput: (e) => {
    ui.af.q = e.target.value; const pos = e.target.selectionStart; render();
    const el = document.getElementById("auditSearch"); el.focus(); el.setSelectionRange(pos, pos); } });
  return h("div", { class: "toolbar" }, search, select("event", "events"), select("stage", "stages"), select("agent", "agents"),
    h("button", { type: "button", onclick: () => showAuditFor({}) }, "Clear"),
    h("button", { type: "button", onclick: exportAudit }, "Download JSON"));
}

function auditRow(e) {
  const key = e.timestamp + "|" + e.event + "|" + (e.stage || "") + "|" + JSON.stringify(e.fields).length;
  const open = ui.openEvents.has(key);
  const toggle = () => { open ? ui.openEvents.delete(key) : ui.openEvents.add(key); render(); };
  const tr = h("tr", { class: "ev-row" + (open ? " ev-open" : ""), onclick: toggle },
    h("td", { class: "muted", style: "white-space:nowrap" }, fmtTime(e.timestamp)),
    h("td", {}, h("span", { class: "ev" }, e.event)),
    h("td", {}, e.stage ? h("span", { class: e.stageInferred ? "inf" : "", title: e.stageInferred ? "No Stage field; the stage running at the time" : "" }, e.stage) : ""),
    h("td", { class: "mono" }, e.agent || ""),
    h("td", {}, eventDetail(e) || h("span", { class: "muted" }, e.title || "")));
  if (!open) return [tr];
  const fields = Object.entries(e.fields);
  return [tr, h("tr", { class: "ev-open" }, h("td", { colspan: "5" },
    fields.length ? h("dl", { class: "fields" }, fields.flatMap(([k, v]) => [h("dt", {}, k), h("dd", { class: "mono" }, v)]))
      : h("span", { class: "empty" }, "No other fields.")))];
}

function exportAudit() {
  const blob = new Blob([JSON.stringify(ui.audit, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: (ui.record || "audit").replace(/\//g, "_") + "-audit.json" });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------------ Workflow tab (animated) */
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false && kid !== "") el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const WF = { colW: 232, nodeW: 200, nodeH: 42, gapY: 22, top: 44, left: 20, replayMs: 700 };
const PHASE_ORDER = ["initialization", "ideation", "inception", "construction", "operation"];
const isActive = (st) => ["in-progress", "revising", "awaiting-approval"].includes(st);

function wfDefs() {
  const marker = (id, color) => s("marker", { id, markerWidth: 8, markerHeight: 8, refX: 6, refY: 3, orient: "auto" },
    s("path", { d: "M0,0 L0,6 L8,3 Z", fill: color }));
  return s("defs", {},
    marker("wfArrow", "var(--border)"), marker("wfArrowDone", "var(--done)"), marker("wfArrowLive", "var(--active)"),
    s("filter", { id: "wfGlow", x: "-30%", y: "-60%", width: "160%", height: "220%" }, s("feGaussianBlur", { stdDeviation: 6 })));
}

/* Stage status at a replay instant, derived from audit start/finish times. */
function statusAt(st, t) {
  if (t == null) return stageStatus(st);
  const tl = st.timeline || {};
  if (tl.completedAt && tl.completedAt <= t) return "completed";
  if (tl.startedAt && tl.startedAt <= t) return "in-progress";
  return st.decision === "SKIP" ? "out" : "pending";
}

function renderWorkflowTab(d) {
  if (!d.record) return h("div", { class: "banner" }, "No run yet. Start one with /aidlc and the workflow appears here.");
  const card = h("section", { class: "card" },
    h("div", { class: "wf-head" },
      h("h2", { style: "margin:0" }, "Lifecycle flow"),
      h("label", { class: "muted" }, h("input", { type: "checkbox", checked: ui.wf.showSkipped,
        onchange: (e) => { ui.wf.showSkipped = e.target.checked; render(); } }), " show out-of-scope stages"),
      replayControls(d)),
    replayCaption(),
    lifecycleSvg(d),
    wfLegend());
  const units = (d.units || {}).units || [];
  return [card, units.length ? [unitProgressCard(d), unitsCard(d)] : h("section", { class: "card" }, h("h2", {}, "Units of work"),
    h("p", { class: "empty" }, "No unit dependency plan yet. It appears after 2.7 units-generation writes unit-of-work-dependency.md."))];
}

function lifecycleSvg(d) {
  const t = ui.wf.replay ? ui.wf.replay.times[ui.wf.replay.i] : null;
  const phases = (d.phases || []).filter((p) => PHASE_ORDER.includes(p.name));
  const pos = {}, nodes = [], order = [];
  let maxRows = 1;
  phases.forEach((p, ci) => {
    const list = p.stages.filter((st) => ui.wf.showSkipped || st.decision === "EXECUTE");
    maxRows = Math.max(maxRows, list.length);
    list.forEach((st, ri) => {
      const x = WF.left + ci * WF.colW, y = WF.top + ri * (WF.nodeH + WF.gapY);
      pos[st.slug] = { x, y }; nodes.push([st, x, y]);
      if (st.decision === "EXECUTE") order.push(st);
    });
  });
  const width = WF.left * 2 + phases.length * WF.colW - (WF.colW - WF.nodeW);
  const height = WF.top + maxRows * (WF.nodeH + WF.gapY) + 10;
  const edges = order.slice(1).map((st, i) => wfEdge(order[i], st, pos, t));
  const heads = phases.map((p, ci) => s("text", { x: WF.left + ci * WF.colW, y: 24, class: "wf-phase" }, p.name.toUpperCase()));
  return s("svg", { viewBox: `0 0 ${width} ${height}`, class: "wf-svg", role: "img", "aria-label": "Animated stage flow for this run" },
    wfDefs(), heads, s("g", {}, edges), s("g", {}, nodes.map(([st, x, y]) => wfNode(st, x, y, statusAt(st, t)))));
}

function wfEdge(a, b, pos, t) {
  const pa = pos[a.slug], pb = pos[b.slug];
  const sa = statusAt(a, t), sb = statusAt(b, t);
  const d = pa.x === pb.x
    ? `M${pa.x + WF.nodeW / 2} ${pa.y + WF.nodeH} V${pb.y - 2}`
    : `M${pa.x + WF.nodeW} ${pa.y + WF.nodeH / 2} C${pa.x + WF.nodeW + 22} ${pa.y + WF.nodeH / 2} ${pb.x - 22} ${pb.y + WF.nodeH / 2} ${pb.x - 3} ${pb.y + WF.nodeH / 2}`;
  const live = sa === "completed" && isActive(sb);
  const done = sa === "completed" && (sb === "completed" || sb === "skipped");
  const cls = live ? "wf-edge live" : done ? "wf-edge done" : "wf-edge";
  const marker = live ? "url(#wfArrowLive)" : done ? "url(#wfArrowDone)" : "url(#wfArrow)";
  const path = s("path", { d, class: cls, "marker-end": marker });
  if (!live) return path;
  const id = "wfp-" + b.slug;
  return s("g", {}, s("path", { id, d, fill: "none", stroke: "none" }), path,
    s("circle", { r: 5, class: "wf-token" }, s("animateMotion", { dur: "1.6s", repeatCount: "indefinite" }, s("mpath", { href: "#" + id }))));
}

function wfNode(st, x, y, status) {
  const label = (st.id ? st.id + "  " : "") + st.slug;
  const sub = status === "awaiting-approval" ? "GATE OPEN — awaiting approval" : (STATUS_LABEL[status] || status) +
    (st.meta && st.meta.lead ? " · " + st.meta.lead.replace(/^aidlc-|-agent$/g, "") : "");
  return s("g", { transform: `translate(${x},${y})`, class: "wf-node n-" + status, tabindex: 0,
      onclick: () => { ui.stage = st.slug; ui.artifact = null; setTab("overview"); } },
    s("title", {}, label + " — " + (STATUS_LABEL[status] || status)),
    isActive(status) ? s("rect", { class: "wf-halo", x: -4, y: -4, width: WF.nodeW + 8, height: WF.nodeH + 8, rx: 14, filter: "url(#wfGlow)" }) : "",
    s("rect", { class: "wf-box", width: WF.nodeW, height: WF.nodeH, rx: 10 }),
    status === "completed" ? s("path", { class: "wf-check", d: `M${WF.nodeW - 22} 15 l4 4 l8 -9` }) : "",
    status === "awaiting-approval" ? s("circle", { class: "wf-gate", cx: WF.nodeW - 16, cy: 14, r: 5 }) : "",
    s("text", { x: 10, y: 17, class: "wf-t" }, label.length > 27 ? label.slice(0, 26) + "…" : label),
    s("text", { x: 10, y: 33, class: "wf-s" }, sub.length > 34 ? sub.slice(0, 33) + "…" : sub));
}

function wfLegend() {
  const item = (cls, text) => h("span", { class: "wf-leg" }, h("i", { class: cls }), text);
  return h("div", { class: "wf-legend" }, item("lg-done", "Completed"), item("lg-live", "Running (animated flow)"),
    item("lg-gate", "Gate awaiting approval"), item("lg-pend", "Upcoming"), item("lg-out", "Not in scope"),
    h("span", { class: "muted" }, "Click a stage to open its detail."));
}

/* ---- Replay: steps through the audit's stage start/finish times. */
function replayControls(d) {
  const r = ui.wf.replay;
  const unitTimes = unitInfo(d).units.flatMap((u) => [...(u.history || []).map((x) => x.t), u.doneAt]);
  const times = [...new Set(allStages(d).flatMap((st) => [st.timeline && st.timeline.startedAt, st.timeline && st.timeline.completedAt])
    .concat(unitTimes).filter(Boolean))].sort();
  if (!times.length) return h("span", { class: "muted" }, "No stage timings to replay yet.");
  if (!r) return h("button", { type: "button", onclick: () => startReplay(times) }, "▶ Replay run");
  return h("span", { class: "wf-replay" },
    h("button", { type: "button", onclick: toggleReplay }, r.timer ? "❚❚ Pause" : "▶ Play"),
    h("input", { type: "range", min: 0, max: r.times.length - 1, value: r.i, oninput: (e) => { r.i = +e.target.value; render(); } }),
    h("button", { type: "button", onclick: stopReplay }, "■ Live view"));
}
function replayCaption() {
  const r = ui.wf.replay;
  return r ? h("div", { class: "wf-caption" }, "Replay · step " + (r.i + 1) + " of " + r.times.length + " · " + fmtTime(r.times[r.i])) : "";
}
function startReplay(times) { ui.wf.replay = { times, i: 0, timer: null }; toggleReplay(); }
function toggleReplay() {
  const r = ui.wf.replay; if (!r) return;
  if (r.timer) { clearInterval(r.timer); r.timer = null; render(); return; }
  if (r.i >= r.times.length - 1) r.i = 0;
  r.timer = setInterval(() => { if (r.i >= r.times.length - 1) { clearInterval(r.timer); r.timer = null; } else r.i++; render(); }, WF.replayMs);
  render();
}
function stopReplay() { stopReplayQuiet(); render(); }
function stopReplayQuiet() { const r = ui.wf.replay; if (r && r.timer) clearInterval(r.timer); ui.wf.replay = null; }

/* ---- Units of work DAG (like the parallel-construction slide). */
/* ---- Units of work: progress model helpers (status keyed by unit name). */
const UNIT_LABEL = { completed: "Done", "in-progress": "In progress", paused: "Paused", pending: "Not started",
  skipped: "Skipped", files: "Files written", merged: "Merged" };
const unitInfo = (d) => d.units || { units: [] };
const unitKicker = (u, i) => (/^u\d+/i.test(u.name) ? u.name.match(/^u\d+/i)[0].toUpperCase() : "UNIT " + String(i + 1).padStart(2, "0"));

/* A unit's status at replay instant t, from its own receipts; live status when not replaying. */
function unitStatusAt(u, d, t) {
  if (t == null) return u.status;
  if (u.doneAt && u.doneAt <= t) return "completed";
  const past = (u.history || []).filter((x) => x.t <= t);
  if (!past.length) {
    if (unitInfo(d).tracked) return "pending";
    const cg = allStages(d).find((x) => x.slug === "code-generation");
    const st = cg ? statusAt(cg, t) : "pending";
    return st === "completed" ? "completed" : st === "pending" || st === "out" ? "pending" : "in-progress";
  }
  return past[past.length - 1].status === "paused" ? "paused" : "in-progress";
}

function unitProgressCard(d, compact) {
  const info = unitInfo(d), sm = info.summary;
  if (!info.units.length || !sm) return compact ? noUnitsCard(d) : "";
  const cur = sm.current;
  const basis = info.doneBy === "checkpoint" ? "A unit counts as done when its construction checkpoint is approved."
    : info.doneBy === "stages" ? "A unit counts as done when all its per-unit stages are completed or skipped."
    : "This run logged no per-unit receipts, so each unit takes its stages' overall status.";
  return h("section", { class: "card" },
    h("div", { class: "up-head" },
      h("h2", { style: "margin:0" }, "Units of work"),
      h("span", { class: "up-big" }, sm.done + " of " + sm.total + " done"),
      h("span", { class: "bar up-bar" }, h("i", { style: "width:" + sm.percent + "%" })),
      h("span", { class: "muted" }, [sm.inProgress ? sm.inProgress + " in progress" : "", sm.paused ? sm.paused + " paused" : "",
        sm.pending ? sm.pending + " not started" : ""].filter(Boolean).join(" · ")),
      compact ? h("button", { type: "button", class: "link", onclick: () => setTab("workflow") }, "unit flow →") : ""),
    cur ? h("div", { class: "up-cur" }, "▶ Working on ", h("strong", {}, cur.label), cur.stage ? " at " : "", cur.stage ? h("strong", {}, cur.stage) : "") : "",
    sm.pausedUnits.map((p) => h("div", { class: "need need-stall", style: "margin-top:8px" },
      h("div", { class: "need-t" }, "⏸ Paused: ", h("strong", {}, p.label), p.stage ? " at " + p.stage : ""),
      h("div", { class: "muted" }, (p.reason ? "Why: " + p.reason : "") + (p.nextAction ? " · Next: " + p.nextAction : "")
        + (p.setAsideFor ? " · set aside for " + p.setAsideFor : "")))),
    unitGrid(d),
    h("p", { class: "muted", style: "margin:8px 0 0;font-size:12px" },
      basis + (info.iteration ? " Construction iteration: " + info.iteration + "." : "") + (info.source ? " Plan from " + info.source + "." : "")));
}

/* Why a run has no units, and where to find one that does. */
function noUnitsCard(d) {
  const ug = allStages(d).find((x) => x.slug === "units-generation");
  const scope = (d.sections["Project Information"] || {})["Scope"] || (d.record && d.record.scope) || "this";
  const why = !ug || ug.decision === "SKIP"
    ? "The " + scope + " scope skips 2.7 units-generation, so this run is built as one piece with no units of work."
    : ug.status === "completed" ? "2.7 units-generation finished but no unit plan was found in unit-of-work-dependency.md or runtime-graph.json."
    : "Units appear once 2.7 units-generation writes the unit plan.";
  const others = (d.records || []).filter((r) => r.id !== (d.record && d.record.id));
  return h("section", { class: "card" },
    h("div", { class: "up-head" }, h("h2", { style: "margin:0" }, "Units of work"), h("span", { class: "muted" }, why)),
    others.length ? h("div", { class: "muted", style: "font-size:12.5px" }, "Other runs: ",
      others.flatMap((r, i) => [i ? ", " : "", h("button", { type: "button", class: "link", onclick: () => switchRecord(r.id) }, r.label + " (" + (r.scope || "?") + ")")])) : "");
}
function switchRecord(id) {
  stopReplayQuiet(); ui.record = id; ui.stage = null; ui.artifact = null; ui.audit = null; ui.openEvents.clear(); refresh();
}

function unitGrid(d) {
  const info = unitInfo(d), cols = info.perUnitStages || [];
  if (!cols.length) return h("p", { class: "empty" }, "No per-unit Construction stages are in scope for this run.");
  return h("div", { class: "md-table" }, h("table", { class: "ugrid" },
    h("thead", {}, h("tr", {}, h("th", {}, "Unit"), h("th", {}, "Status"),
      cols.map((c) => h("th", { title: c.slug }, (c.id ? c.id + " " : "") + c.slug)), h("th", {}, "Depends on"))),
    h("tbody", {}, info.units.map((u, i) => h("tr", { class: u.current ? "ug-cur" : "" },
      h("td", {}, h("div", { class: "mono", style: "font-weight:700" }, unitKicker(u, i)), u.label, u.kind ? h("span", { class: "chip", style: "margin-left:6px" }, u.kind) : ""),
      h("td", {}, h("span", { class: "status-pill p-" + u.status }, UNIT_LABEL[u.status] || u.status)),
      cols.map((c) => { const st = u.stages[c.slug] || "pending";
        return h("td", { class: "ug-cell ug-" + st, title: c.slug + ": " + (UNIT_LABEL[st] || st) }, ugIcon(st)); }),
      h("td", { class: "muted mono", style: "font-size:11.5px" }, u.deps.join(", ") || "—"))))));
}
function ugIcon(st) {
  return { completed: "✓", "in-progress": "●", paused: "⏸", skipped: "–", files: "◐", pending: "" }[st] ?? "";
}

function unitsCard(d) {
  const info = unitInfo(d);
  return h("section", { class: "card" },
    h("h2", {}, "Units of work — dependency flow (" + info.units.length + ")"),
    h("p", { class: "muted", style: "margin:-6px 0 10px;font-size:12px" },
      "From " + info.source + ". " + (info.tracked ? "Unit status comes from the run's per-unit receipts; replay follows them too."
        : "This run logged no per-unit events, so every unit takes the code-generation stage's status.")),
    unitsSvg(d));
}

function unitLayers(units) {
  const byName = Object.fromEntries(units.map((u) => [u.name, u])), depth = {};
  const depthOf = (u, seen = new Set()) => {
    if (depth[u.name] != null) return depth[u.name];
    if (seen.has(u.name)) return 0; seen.add(u.name);
    depth[u.name] = u.deps.length ? 1 + Math.max(...u.deps.map((n) => (byName[n] ? depthOf(byName[n], seen) : 0))) : 0;
    return depth[u.name];
  };
  units.forEach((u) => depthOf(u));
  const layers = [];
  units.forEach((u) => (layers[depth[u.name]] = layers[depth[u.name]] || []).push(u));
  return layers.filter(Boolean);
}

/* Drop dependency edges already implied by a longer path (1→4 when 1→2→3→4 exists). */
function directDeps(units) {
  const byName = Object.fromEntries(units.map((u) => [u.name, u]));
  const reach = (from, target, seen = new Set()) => (byName[from] ? byName[from].deps : []).some((n) =>
    n === target || (!seen.has(n) && (seen.add(n), reach(n, target, seen))));
  return Object.fromEntries(units.map((u) => [u.name, u.deps.filter((dep) => !u.deps.some((o) => o !== dep && reach(o, dep)))]));
}

function unitsSvg(d) {
  const t = ui.wf.replay ? ui.wf.replay.times[ui.wf.replay.i] : null;
  const stageSt = (slug) => { const st = allStages(d).find((x) => x.slug === slug); return st ? statusAt(st, t) : "out"; };
  const units = unitInfo(d).units.map((u) => ({ ...u, status: unitStatusAt(u, d, t) }));
  const byName = Object.fromEntries(units.map((u) => [u.name, u]));
  const layers = unitLayers(units), direct = directDeps(units);
  const inception = stageSt("delivery-planning") === "completed" || stageSt("units-generation") === "completed" ? "completed" : stageSt("units-generation");
  const W = 172, H = 58, colW = 208, rowH = 76, top = 40, left = 20;
  const rows = Math.max(...layers.map((l) => l.length), 2);
  const height = top + rows * rowH + 10, midY = top + (rows * rowH) / 2 - H / 2;
  const pos = {}, nodes = [];
  layers.forEach((layer, ci) => layer.forEach((u, ri) => {
    const off = (rows - layer.length) * rowH / 2;
    pos[u.name] = { x: left + (ci + 1) * colW, y: top + off + ri * rowH };
  }));
  const tailX = left + (layers.length + 1) * colW, width = tailX + 2 * colW - 30;
  const gate = { x: left, y: midY }, bt = { x: tailX, y: midY }, ci = { x: tailX + colW, y: midY };
  const hasDependents = new Set(units.flatMap((u) => u.deps));
  const edge = (a, b, sa, sb, key) => wfCurve(a, b, W, H, sa, sb, "u" + key.replace(/[^\w-]/g, "_"));
  const edges = [
    ...units.filter((u) => !u.deps.length).map((u) => edge(gate, pos[u.name], inception, u.status, "g-" + u.name)),
    ...units.flatMap((u) => direct[u.name].filter((n) => byName[n]).map((n) => edge(pos[n], pos[u.name], byName[n].status, u.status, n + "--" + u.name))),
    ...units.filter((u) => !hasDependents.has(u.name)).map((u) => edge(pos[u.name], bt, u.status, stageSt("build-and-test"), "b-" + u.name)),
    wfCurve(bt, ci, W, H, stageSt("build-and-test"), stageSt("ci-pipeline"), "bci"),
  ];
  units.forEach((u, i) => nodes.push(unitNode(pos[u.name], W, H, unitKicker(u, i) + (u.current && t == null ? " · " + u.current : ""), u.label,
    u.status === "paused" ? "PAUSED" + (u.paused && u.paused.reason ? ": " + u.paused.reason : "")
      : u.deps.length ? "depends on " + u.deps.join(", ") : "no dependencies", u.status === "paused" ? "awaiting-approval" : u.status)));
  nodes.push(unitNode(gate, W, H, "INCEPTION", "Approved plan", "units + dependency DAG", inception, true));
  nodes.push(unitNode(bt, W, H, "3.6", "Build & Test", "whole-system evidence", stageSt("build-and-test"), true));
  nodes.push(unitNode(ci, W, H, "3.7 → 4.x", "CI / deploy tail", "gated release path", stageSt("ci-pipeline"), true));
  const heads = [["SHARED GATE", left], ["UNITS BY DEPENDENCY DEPTH", left + colW], ["INTEGRATION + DELIVERY", tailX]]
    .map(([txt, x]) => s("text", { x, y: 22, class: "wf-phase" }, txt));
  return s("svg", { viewBox: `0 0 ${width} ${height}`, class: "wf-svg", role: "img", "aria-label": "Units of work dependency flow" },
    wfDefs(), heads, s("g", {}, edges), s("g", {}, nodes));
}

function wfCurve(a, b, W, H, sa, sb, key) {
  const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 3, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
  const d = `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
  const live = sa === "completed" && isActive(sb), done = sa === "completed" && sb === "completed";
  const path = s("path", { d, class: live ? "wf-edge live" : done ? "wf-edge done" : "wf-edge",
    "marker-end": live ? "url(#wfArrowLive)" : done ? "url(#wfArrowDone)" : "url(#wfArrow)" });
  if (!live) return path;
  const id = "wfu-" + key;
  return s("g", {}, s("path", { id, d, fill: "none", stroke: "none" }), path,
    s("circle", { r: 5, class: "wf-token" }, s("animateMotion", { dur: "1.6s", repeatCount: "indefinite" }, s("mpath", { href: "#" + id }))));
}

function unitNode(p, W, H, kicker, name, sub, status, accent) {
  return s("g", { transform: `translate(${p.x},${p.y})`, class: "wf-node n-" + status + (accent ? " wf-accent" : "") },
    s("title", {}, kicker + " " + name + " — " + (STATUS_LABEL[status] || status)),
    isActive(status) ? s("rect", { class: "wf-halo", x: -4, y: -4, width: W + 8, height: H + 8, rx: 16, filter: "url(#wfGlow)" }) : "",
    s("rect", { class: "wf-box", width: W, height: H, rx: 12 }),
    status === "completed" ? s("path", { class: "wf-check", d: `M${W - 22} 15 l4 4 l8 -9` }) : "",
    s("text", { x: 12, y: 17, class: "wf-k" }, kicker),
    s("text", { x: 12, y: 34, class: "wf-t" }, name.length > 22 ? name.slice(0, 21) + "…" : name),
    s("text", { x: 12, y: 49, class: "wf-s" }, sub.length > 28 ? sub.slice(0, 27) + "…" : sub));
}

/* ------------------------------------------------------------------ Markdown -> DOM (safe: never uses innerHTML) */
const MD_SAFE_LINK = /^(https?:\/\/|#|\.{0,2}\/?[\w.\-\/]+(#[\w\-]*)?$)/i;

function renderMarkdown(src, basePath) {
  const lines = src.replace(/\r\n?/g, "\n").replace(/<!--[\s\S]*?-->/g, "").split("\n");
  const root = h("div", { class: "md" });
  let i = 0;
  while (i < lines.length) i = mdBlock(lines, i, root, basePath);
  return root;
}

function mdBlock(lines, i, out, base) {
  const line = lines[i];
  if (!line.trim()) return i + 1;
  let m;
  if ((m = line.match(/^\s*(```+|~~~+)\s*([\w+-]*)/))) return mdFence(lines, i, out, m[1]);
  if ((m = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) { out.append(h("h" + m[1].length, {}, mdInline(m[2], base))); return i + 1; }
  if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.append(h("hr")); return i + 1; }
  if (/^\s*>/.test(line)) return mdQuote(lines, i, out, base);
  if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) return mdTable(lines, i, out, base);
  if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) return mdList(lines, i, out, base);
  return mdPara(lines, i, out, base);
}

function mdFence(lines, i, out, fence) {
  const body = [];
  let j = i + 1;
  while (j < lines.length && !lines[j].trim().startsWith(fence)) body.push(lines[j++]);
  out.append(h("pre", { class: "md-code" }, h("code", {}, body.join("\n"))));
  return j + 1;
}

function mdQuote(lines, i, out, base) {
  const body = [];
  while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
  const q = h("blockquote", {});
  let j = 0;
  while (j < body.length) j = mdBlock(body, j, q, base);
  out.append(q);
  return i;
}

function mdTable(lines, i, out, base) {
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  const head = cells(lines[i]);
  const rows = [];
  let j = i + 2;
  while (j < lines.length && /^\s*\|/.test(lines[j])) rows.push(cells(lines[j++]));
  out.append(h("div", { class: "md-table" }, h("table", {},
    h("thead", {}, h("tr", {}, head.map((c) => h("th", {}, mdInline(c, base))))),
    h("tbody", {}, rows.map((r) => h("tr", {}, head.map((_, k) => h("td", {}, mdInline(r[k] || "", base)))))))));
  return j;
}

function mdList(lines, i, out, base) {
  const indent = (l) => l.match(/^\s*/)[0].replace(/\t/g, "  ").length;
  const level = indent(lines[i]);
  const ordered = /^\s*\d+[.)]\s/.test(lines[i]);
  const list = h(ordered ? "ol" : "ul", {});
  while (i < lines.length) {
    const l = lines[i];
    const m = l.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
    if (!m || indent(l) < level) break;
    if (indent(l) > level) { i = mdList(lines, i, list.lastChild || list, base); continue; }
    const task = m[2].match(/^\[([ xX])\]\s+(.*)$/);
    const li = h("li", { class: task ? "md-task" : "" },
      task ? h("input", { type: "checkbox", disabled: true, checked: task[1] !== " " }) : "", mdInline(task ? task[2] : m[2], base));
    list.append(li);
    i++;
    while (i < lines.length && lines[i].trim() && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && indent(lines[i]) > level) {
      li.append(" ", mdInline(lines[i++].trim(), base));
    }
  }
  out.append(list);
  return i;
}

function mdPara(lines, i, out, base) {
  const body = [];
  while (i < lines.length && lines[i].trim() && !/^\s*(#{1,6}\s|```|~~~|>|([-*+]|\d+[.)])\s+|\|.*\|\s*$)/.test(lines[i])) body.push(lines[i++].trim());
  if (!body.length) body.push(lines[i++].trim());
  out.append(h("p", {}, mdInline(body.join(" "), base)));
  return i;
}

/* Inline: `code`, **bold**, *italic*, ~~strike~~, [text](url), <https://autolinks>. Text nodes only. */
function mdInline(text, base) {
  const out = [];
  const re = /(`+)([^`]+?)\1|\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)|~~(.+?)~~|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|<(https?:\/\/[^>\s]+)>/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2] != null) out.push(h("code", {}, m[2]));
    else if (m[3] != null || m[4] != null) out.push(h("strong", {}, mdInline(m[3] ?? m[4], base)));
    else if (m[5] != null || m[6] != null) out.push(h("em", {}, mdInline(m[5] ?? m[6], base)));
    else if (m[7] != null) out.push(h("del", {}, mdInline(m[7], base)));
    else if (m[8] != null) out.push(mdLink(m[8], m[9], base));
    else if (m[10] != null) out.push(mdLink(m[10], m[10], base));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function mdLink(label, href, base) {
  if (!MD_SAFE_LINK.test(href)) return label;
  if (/^https?:\/\//i.test(href)) return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, label);
  if (href.startsWith("#") || !base) return h("span", { class: "md-ref" }, label);
  const target = resolveRelative(base, href.split("#")[0]);
  return target ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openArtifact(target); } }, label) : label;
}

/* Resolve a relative artifact link against the current artifact's directory; null if it climbs out of the record. */
function resolveRelative(base, rel) {
  const parts = base.split("/").slice(0, -1);
  for (const seg of rel.split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") { if (!parts.length) return null; parts.pop(); } else parts.push(seg);
  }
  return parts.length ? parts.join("/") : null;
}

function artifactBody(a) {
  if (a.error) return h("p", { class: "empty" }, a.error);
  if (ui.rawView || !/\.(md|json)$/i.test(a.path)) return h("pre", { class: "viewer" }, a.text);
  if (/\.json$/i.test(a.path)) { try { return h("pre", { class: "viewer" }, JSON.stringify(JSON.parse(a.text), null, 2)); } catch { return h("pre", { class: "viewer" }, a.text); } }
  return renderMarkdown(a.text, a.path);
}
function artifactPanel(a) {
  return h("div", { class: "art-panel" },
    h("div", { class: "art-bar" }, h("span", { class: "mono" }, a.path + (a.truncated ? " (truncated)" : "")),
      h("span", {}, h("button", { type: "button", onclick: () => { ui.rawView = !ui.rawView; render(); } }, ui.rawView ? "Formatted" : "Raw"),
        " ", h("button", { type: "button", onclick: () => { ui.artifact = null; render(); } }, "Close"))),
    artifactBody(a));
}

/* ------------------------------------------------------------------ 1+2. Needs you, stalls, errors, alerts */
function fmtDur(sec) {
  if (sec == null || !isFinite(sec)) return "—";
  sec = Math.round(sec);
  if (sec < 60) return sec + "s";
  const m = Math.round(sec / 60);
  if (m < 60) return m + " min";
  const hrs = Math.floor(m / 60), rem = m % 60;
  return hrs < 24 ? hrs + " h" + (rem ? " " + rem + " min" : "") : (hrs / 24).toFixed(1) + " days";
}
function sinceSec(iso) { const t = Date.parse(iso); return isNaN(t) ? null : Math.max((Date.now() - t) / 1000, 0); }
function pausedUnits(d) { const sm = unitInfo(d).summary; return sm ? sm.pausedUnits : []; }
function needsCount(d) {
  if (!d.record) return 0;
  const hl = d.health || {};
  return (d.attention ? d.attention.count : 0) + (hl.stalled ? 1 : 0) + (hl.activeStageErrors ? 1 : 0) + pausedUnits(d).length;
}

function needsYou(d) {
  const a = d.attention || { gates: [], questions: [] }, hl = d.health || {};
  const items = [
    ...a.gates.map((g) => h("div", { class: "need need-gate" },
      h("div", { class: "need-t" }, "⏸ Approval needed: ", h("strong", {}, (g.id ? g.id + " " : "") + g.slug)),
      h("div", { class: "muted" }, "Gate open for " + fmtDur(sinceSec(g.since)) + (g.since ? " (since " + fmtTime(g.since) + ")" : "")
        + ". Answer in your Kiro chat to approve or request changes."),
      g.questionFiles.length ? h("div", { class: "need-actions" }, g.questionFiles.map((p) =>
        h("button", { type: "button", onclick: () => openArtifact(p) }, "Open " + p.split("/").pop()))) : "")),
    ...a.questions.map((q) => h("div", { class: "need need-q" },
      h("div", { class: "need-t" }, "❓ AI-DLC is asking: ", h("strong", {}, q.text)),
      h("div", { class: "muted" }, "Asked " + fmtTime(q.createdAt) + (q.origin ? " · " + q.origin : "")))),
    ...pausedUnits(d).map((p) => h("div", { class: "need need-stall" },
      h("div", { class: "need-t" }, "⏸ Unit paused: ", h("strong", {}, p.label), p.stage ? " at " + p.stage : ""),
      h("div", { class: "muted" }, (p.reason ? "Why: " + p.reason + ". " : "") + (p.nextAction ? "Next: " + p.nextAction + ". " : "")
        + "AI-DLC won't start other work until it's resumed."))),
    hl.stalled ? h("div", { class: "need need-stall" },
      h("div", { class: "need-t" }, "⚠ Possibly stalled: ", h("strong", {}, hl.workingStages.join(", "))),
      h("div", { class: "muted" }, "No audit activity for " + fmtDur(sinceSec(hl.lastEventAt)) + " (threshold " + hl.stallMinutes
        + " min). Check the Kiro session is still running; long code generation can also be quiet.")) : "",
    hl.activeStageErrors ? h("div", { class: "need need-err" },
      h("div", { class: "need-t" }, "✖ " + hl.activeStageErrors + " error(s) logged in the current stage"),
      h("div", { class: "muted" }, "Latest: " + ((hl.errors[0] || {}).error || "").slice(0, 200))) : "",
  ].filter(Boolean);
  if (!items.length) {
    const msg = d.summary && d.summary.percent === 100 ? "✓ Run complete. Nothing needs you."
      : "✓ Nothing needs you right now" + (hl.workingStages && hl.workingStages.length ? ". Agents are working on " + hl.workingStages.join(", ") + "." : ".");
    return h("section", { class: "card needs ok" }, h("span", {}, msg),
      hl.lastEventAt ? h("span", { class: "muted" }, " Last activity " + fmtDur(sinceSec(hl.lastEventAt)) + " ago.") : "");
  }
  return h("section", { class: "card needs" }, h("h2", {}, "Needs you (" + items.length + ")"), h("div", { class: "need-list" }, items));
}

function errorsCard(d) {
  const hl = d.health || {};
  if (!hl.errorCount) return "";
  return h("section", { class: "card" },
    h("h2", {}, "Errors logged (" + hl.errorCount + ")", " ",
      h("button", { type: "button", class: "link", style: "text-transform:none;letter-spacing:0", onclick: () => showAuditFor({ event: "ERROR_LOGGED" }) }, "view in audit trail →")),
    h("p", { class: "muted", style: "margin:-6px 0 10px;font-size:12px" },
      "A tool exited with an error. The agent often retries and recovers; repeated errors in one stage are worth a look."),
    h("div", { class: "scroll", style: "max-height:300px" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Stage"), h("th", {}, "Tool"), h("th", {}, "Error"))),
      h("tbody", {}, hl.errors.map((e) => h("tr", {},
        h("td", { class: "muted", style: "white-space:nowrap" }, fmtTime(e.timestamp)), h("td", {}, e.stage || ""),
        h("td", { class: "mono" }, e.tool || ""),
        h("td", {}, e.error || "", e.command ? h("div", { class: "mono muted", style: "font-size:11px" }, e.command.slice(0, 220)) : "")))))));
}

const BASE_TITLE = "AI-DLC Pulse";
function updateTitle(d) { const n = needsCount(d); document.title = (n ? "(" + n + ") " : "") + BASE_TITLE; }

/* Browser notifications for new gates, stalls and errors. Baseline on first load so old items don't fire. */
function alertKeys(d) {
  if (!d.record) return new Set();
  const a = d.attention || { gates: [], questions: [] }, hl = d.health || {};
  return new Set([
    ...a.gates.map((g) => "gate|" + d.record.id + "|" + g.slug + "|" + g.since),
    ...a.questions.map((q) => "q|" + q.id),
    ...(hl.stalled ? ["stall|" + d.record.id + "|" + hl.lastEventAt] : []),
    ...pausedUnits(d).map((p) => "unit|" + d.record.id + "|" + p.unit + "|" + p.since),
    ...(hl.errors || []).map((e) => "err|" + d.record.id + "|" + e.timestamp + "|" + (e.error || "").length),
  ]);
}
function alertText(key, d) {
  const [kind, , slug] = key.split("|");
  if (kind === "gate") return ["Approval needed", "Stage " + slug + " is waiting for you."];
  if (kind === "q") return ["AI-DLC has a question", ((d.attention.questions.find((q) => "q|" + q.id === key) || {}).text || "").slice(0, 120)];
  if (kind === "unit") { const p = pausedUnits(d).find((x) => key.includes("|" + x.unit + "|")) || {};
    return ["Unit paused", (p.label || slug) + (p.reason ? ": " + p.reason : "")]; }
  if (kind === "stall") return ["Stage may be stalled", d.health.workingStages.join(", ") + ": no activity for " + fmtDur(sinceSec(d.health.lastEventAt))];
  return ["Error logged", ((d.health.errors[0] || {}).error || "").slice(0, 140)];
}
function checkAlerts(d) {
  const keys = alertKeys(d);
  if (!ui.seen || ui.seen.record !== (d.record && d.record.id)) { ui.seen = { record: d.record && d.record.id, keys }; return; }
  const fresh = [...keys].filter((k) => !ui.seen.keys.has(k));
  ui.seen.keys = keys;
  if (!fresh.length || !("Notification" in window) || Notification.permission !== "granted") return;
  const [title, body] = alertText(fresh[0], d);
  const n = new Notification(title + (fresh.length > 1 ? " (+" + (fresh.length - 1) + " more)" : ""), { body, tag: "aidlc-pulse" });
  n.onclick = () => { window.focus(); setTab("overview"); n.close(); };
}
function setupAlertButton() {
  const btn = document.getElementById("alertBtn");
  const sync = () => {
    if (!("Notification" in window)) { btn.textContent = "🔕 Alerts unsupported"; btn.disabled = true; return; }
    const p = Notification.permission;
    btn.textContent = p === "granted" ? "🔔 Alerts on" : p === "denied" ? "🔕 Alerts blocked" : "🔔 Enable alerts";
    btn.title = p === "denied" ? "Allow notifications for this site in your browser settings" : btn.title;
  };
  btn.addEventListener("click", async () => {
    if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    else if ("Notification" in window && Notification.permission === "granted") new Notification("AI-DLC alerts are on", { body: "You'll be told when a gate opens, a stage stalls or an error is logged.", tag: "aidlc-pulse" });
    sync();
  });
  sync();
}

/* ------------------------------------------------------------------ 4. Timeline tab */
const SEG_LABEL = { agent: "Agent working", human: "Waiting on you", rework: "Rework after changes requested" };

function renderTimelineTab(d) {
  if (!d.record) return h("div", { class: "banner" }, "No run yet, so there is no timeline.");
  const tm = d.timing || { stages: [], totals: {} }, tot = tm.totals || {};
  if (!tm.stages.length) return h("div", { class: "card empty" }, "No stage start/finish events recorded yet.");
  const kpi = (label, value, extra) => h("div", { class: "kpi" }, h("div", { class: "v" }, value), h("div", { class: "l" }, label), extra || "");
  const eta = tm.eta;
  const etaText = !eta ? "Not enough finished stages yet" : eta.remainingStages === 0 ? "Done" : "~" + fmtDur(eta.seconds);
  return [
    h("section", { class: "kpis" },
      kpi("Agent working time", fmtDur(tot.agent)),
      kpi("Waiting on you", fmtDur(tot.human), h("div", { class: "l" }, tot.total ? Math.round(100 * tot.human / tot.total) + "% of tracked time" : "")),
      kpi("Rework", fmtDur(tot.rework), h("div", { class: "l" }, (tot.rejections || 0) + " change request(s)")),
      kpi("Longest stage", tot.longest ? tot.longest.slug : "—", h("div", { class: "l" }, tot.longest ? fmtDur(tot.longest.seconds) : "")),
      kpi("Estimated time left", etaText, h("div", { class: "l" }, eta && eta.finishAt ? "finish ≈ " + fmtTime(eta.finishAt) + " · " + eta.remainingStages + " stages left"
        : eta ? eta.basis : "needs 2+ finished stages")),
    ),
    phaseBreakdown(tot.byPhase || {}),
    unitTimes(d),
    h("section", { class: "card" },
      h("h2", {}, "Stage timeline"),
      h("p", { class: "muted", style: "margin:-6px 0 10px;font-size:12px" },
        "Each bar runs from the stage's start to its finish, coloured by who had the ball. Gaps are time between stages (often between sessions). Click a bar for the stage's audit events."),
      ganttSvg(tm), segLegend(),
      eta && eta.basis && eta.remainingStages ? h("p", { class: "muted", style: "font-size:12px;margin:8px 0 0" }, "Estimate is rough: " + eta.basis + ".") : ""),
  ];
}

function unitTimes(d) {
  const info = unitInfo(d);
  if (!info.units.length) return "";
  const idleSince = d.timing && d.timing.idleSince;
  const rows = info.units.map((u, i) => {
    // A paused unit stops at its pause; on an idle run, an unfinished unit stops at the last audit event.
    const end = u.doneAt || (u.status === "completed" || !u.startedAt ? null
      : u.paused ? u.paused.since : idleSince || new Date().toISOString());
    const running = !u.doneAt && !u.paused && !idleSince;
    return { u, i, start: u.startedAt, end, running, secs: u.startedAt && end ? Math.max((Date.parse(end) - Date.parse(u.startedAt)) / 1000, 0) : null };
  });
  return h("section", { class: "card" }, h("h2", {}, "Time per unit"),
    info.tracked ? "" : h("p", { class: "muted", style: "margin:-6px 0 10px;font-size:12px" }, "This run logged no per-unit receipts, so unit times aren't available."),
    h("table", {}, h("thead", {}, h("tr", {}, ["Unit", "Status", "Started", "Done", "Time so far"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, h("span", { class: "mono", style: "font-weight:700" }, unitKicker(r.u, r.i)), " ", r.u.label),
        h("td", {}, h("span", { class: "status-pill p-" + r.u.status }, UNIT_LABEL[r.u.status] || r.u.status)),
        h("td", { class: "muted" }, fmtTime(r.start)), h("td", { class: "muted" }, fmtTime(r.u.doneAt)),
        h("td", {}, r.secs != null ? fmtDur(r.secs) + (r.running ? " (running)" : r.u.doneAt ? "" : r.u.paused ? " (paused)" : " (idle)") : "—"))))));
}

function segLegend() {
  return h("div", { class: "wf-legend" }, Object.entries(SEG_LABEL).map(([k, v]) => h("span", { class: "wf-leg" }, h("i", { class: "seg-" + k }), v)),
    h("span", { class: "wf-leg" }, h("i", { class: "seg-open" }), "Still running"));
}

function phaseBreakdown(byPhase) {
  const rows = PHASE_ORDER.filter((p) => byPhase[p]).map((p) => [p, byPhase[p]]);
  const max = Math.max(...rows.map(([, v]) => v.agent + v.human + v.rework), 1);
  return h("section", { class: "card" }, h("h2", {}, "Time by phase"),
    h("div", { class: "pb" }, rows.map(([p, v]) => {
      const total = v.agent + v.human + v.rework;
      const seg = (k) => v[k] ? h("i", { class: "seg-" + k, style: "width:" + (100 * v[k] / max) + "%", title: SEG_LABEL[k] + ": " + fmtDur(v[k]) }) : "";
      return h("div", { class: "pb-row" }, h("span", { class: "pb-l" }, p), h("span", { class: "pb-bar" }, seg("agent"), seg("human"), seg("rework")),
        h("span", { class: "pb-v" }, fmtDur(total)));
    })));
}

function ganttSvg(tm) {
  const rows = tm.stages.filter((r) => r.segments.length);
  const times = rows.flatMap((r) => r.segments.flatMap((s) => [Date.parse(s.start), Date.parse(s.end)])).filter((t) => !isNaN(t));
  const t0 = Math.min(...times), t1 = Math.max(...times, t0 + 60000);
  const labelW = 230, W = 1100, rowH = 24, top = 30, plotW = W - labelW - 80;
  const x = (iso) => labelW + ((Date.parse(iso) - t0) / (t1 - t0)) * plotW;
  const ticks = timeTicks(t0, t1).map((t) => s("g", {},
    s("line", { x1: labelW + ((t - t0) / (t1 - t0)) * plotW, x2: labelW + ((t - t0) / (t1 - t0)) * plotW, y1: top - 6, y2: top + rows.length * rowH, class: "g-grid" }),
    s("text", { x: labelW + ((t - t0) / (t1 - t0)) * plotW, y: top - 10, class: "g-tick" }, tickLabel(t, t1 - t0))));
  const bars = rows.map((r, i) => {
    const y = top + i * rowH;
    return s("g", { class: "g-row", onclick: () => showAuditFor({ stage: r.slug }) },
      s("title", {}, (r.id ? r.id + " " : "") + r.slug + " — agent " + fmtDur(r.agent) + ", waiting on you " + fmtDur(r.human) + (r.rework ? ", rework " + fmtDur(r.rework) : "")),
      s("rect", { x: 0, y, width: W, height: rowH, class: i % 2 ? "g-band" : "g-band alt" }),
      s("text", { x: 8, y: y + 16, class: "g-label" }, ((r.id ? r.id + "  " : "") + r.slug).slice(0, 30)),
      r.segments.map((sg) => s("rect", { x: x(sg.start), y: y + 5, width: Math.max(x(sg.end) - x(sg.start), 2), height: rowH - 10, rx: 3,
        class: "g-seg seg-" + sg.kind + (sg.open ? " g-open" : "") })),
      s("text", { x: W - 8, y: y + 16, class: "g-dur" }, fmtDur(r.total)));
  });
  return s("svg", { viewBox: `0 0 ${W} ${top + rows.length * rowH + 6}`, class: "wf-svg gantt", role: "img", "aria-label": "Stage timeline" },
    ticks, bars);
}
function timeTicks(t0, t1) {
  const span = t1 - t0, steps = [60e3, 300e3, 900e3, 1800e3, 3600e3, 7200e3, 21600e3, 86400e3, 7 * 86400e3, 30 * 86400e3];
  const step = steps.find((st) => span / st <= 8) || steps[steps.length - 1];
  const out = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t);
  return out;
}
function tickLabel(t, span) {
  const dt = new Date(t);
  return span > 2 * 86400e3 ? dt.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

document.getElementById("recordSel").addEventListener("change", (e) => { stopReplayQuiet(); ui.record = e.target.value || null; ui.stage = null; ui.artifact = null; ui.audit = null; ui.openEvents.clear(); refresh(); });
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
document.getElementById("pauseBtn").addEventListener("click", (e) => { ui.paused = !ui.paused; e.target.textContent = ui.paused ? "Resume" : "Pause"; });
setupAlertButton();
refresh();
/* Keep polling in a background tab (slower) so alerts still fire. */
let lastPoll = 0;
setInterval(() => {
  if (ui.paused) return;
  const gap = document.hidden ? HIDDEN_POLL_MS : POLL_MS;
  if (Date.now() - lastPoll >= gap - 100) { lastPoll = Date.now(); refresh(); }
}, POLL_MS);
/* Clock-only values (wait times, open timeline bars) refresh on a slow tick, never while reading or typing. */
setInterval(() => {
  const typing = document.activeElement && document.activeElement.tagName === "INPUT" && document.activeElement.type !== "checkbox";
  if (!ui.paused && !document.hidden && !ui.artifact && !typing && !(ui.wf.replay && ui.wf.replay.timer)) render();
}, CLOCK_TICK_MS);
