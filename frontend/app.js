const state = { view: "monitor", filter: "all", data: { dashboard: null, zones: [], alarms: [], lots: [], exceptions: [], audit: [] } };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const number = (value) => new Intl.NumberFormat("en-US").format(Number(value || 0));
const niceDate = (value, withTime = true) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat(undefined, withTime ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" } : { month: "short", day: "numeric" }).format(date);
};
const statusName = (status) => String(status || "open").replaceAll("_", " ");

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function toast(message, failed = false) {
  const el = $("#toast");
  el.textContent = message;
  el.style.background = failed ? "#8f3938" : "#243e33";
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3000);
}

function setView(view) {
  state.view = view;
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  $$(".nav-link").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  const names = { monitor: "Monitor", alarms: "Alarm queue", inventory: "Inventory impact", exceptions: "Exceptions", history: "Event history" };
  $("#breadcrumb-current").textContent = names[view] || "Monitor";
}

async function refreshAll() {
  const [dashboard, zones, alarms, lots, exceptions, audit] = await Promise.all([
    request("/api/dashboard"), request("/api/zones"), request("/api/alarms"), request("/api/lots/at-risk"), request("/api/exceptions"), request("/api/audit?limit=100")
  ]);
  state.data = { dashboard, zones, alarms, lots, exceptions, audit };
  renderAll();
}

function renderAll() {
  const { dashboard: d, zones, alarms, lots, exceptions, audit } = state.data;
  renderMetrics(d);
  renderZones(zones, d);
  renderAlarms(alarms);
  renderLots(lots);
  renderExceptions(exceptions);
  renderAudit(audit);
  $("#last-update").textContent = d.as_of ? `Source time ${niceDate(d.as_of)}` : "No source readings";
  $("#footer-as-of").textContent = d.as_of ? `Source data through ${niceDate(d.as_of)}` : "";
  $("#nav-alarm-count").textContent = number(d.active_alarm_count);
  $("#nav-exception-dot").style.display = exceptions.length ? "block" : "none";
}

function renderMetrics(d) {
  $("#metric-alarms").textContent = number(d.active_alarm_count);
  $("#metric-critical").textContent = `${number(d.critical_alarm_count)} critical`;
  $("#metric-coverage").textContent = Number(d.sensor_coverage_pct || 0).toFixed(1);
  $("#metric-fresh").textContent = `${number(d.fresh_sensor_count)} of ${number(d.sensor_count)} sensors`;
  $("#coverage-bar").style.width = `${Math.max(0, Math.min(100, d.sensor_coverage_pct || 0))}%`;
  $("#metric-lots").textContent = number(d.impacted_lot_count);
  $("#metric-units").textContent = `${number(d.impacted_units)} units`;
  $("#metric-inspections").textContent = number(d.open_inspection_count);
  $("#metric-completed").textContent = `${number(d.completed_inspection_count)} completed`;
  $("#signal-score").textContent = Number(d.sensor_coverage_pct || 0).toFixed(1);
  $("#signal-bar").style.width = `${Math.max(0, Math.min(100, d.sensor_coverage_pct || 0))}%`;
  $("#signal-fresh").textContent = number(d.fresh_sensor_count);
  $("#signal-stale").textContent = number(d.stale_sensor_count);
}

function renderZones(zones, d) {
  const rows = zones.slice(0, 7).map((zone) => {
    const risky = zone.high_risk_count > 0;
    const active = zone.active_alarm_count > 0;
    const status = risky ? "Risk" : active ? "Watch" : "Normal";
    const klass = risky ? "status-risk" : active ? "status-watch" : "status-ok";
    const signal = (value, suffix, digits = 1) => value == null ? "—" : `${Number(value).toFixed(digits)}${suffix}`;
    const conditions = `T ${signal(zone.latest_temp_c, "°C")} · RH ${signal(zone.latest_humidity_pct, "%")} · Occ ${signal(zone.latest_occupancy_pct, "%", 0)}`;
    const door = zone.last_door_at ? `Door ${number(zone.last_door_duration_seconds)}s · ${niceDate(zone.last_door_at)}` : "No door event";
    return `<tr><td><span class="entity-primary">${escapeHtml(zone.warehouse_name)}</span><span class="entity-secondary">${escapeHtml(zone.zone_id)}</span></td><td><span class="zone-type">${escapeHtml(zone.zone_type)}</span></td><td><span class="entity-primary condition-values">${conditions}</span><span class="entity-secondary">${door}</span></td><td>${niceDate(zone.last_reading_at)}</td><td>${active ? `<strong>${number(zone.active_alarm_count)}</strong>` : "—"}</td><td><span class="status-pill ${klass}">${status}</span></td></tr>`;
  }).join("");
  $("#zone-rows").innerHTML = rows || `<tr><td colspan="6" class="empty-row">No zones available.</td></tr>`;
  $("#zone-summary").textContent = `${number(d.zone_count)} zones monitored across ${number(d.warehouse_count)} warehouses`;
}

function alarmActionButtons(alarm) {
  const buttons = [];
  if (!alarm.has_impact_link && !["resolved", "false_positive"].includes(alarm.status)) {
    buttons.push(`<button class="action-button" data-action="reconcile" data-id="${escapeHtml(alarm.alarm_id)}">Link lots</button>`);
  } else if (alarm.inspection_status === "planned") {
    buttons.push(`<button class="action-button" data-action="start-inspection" data-id="${escapeHtml(alarm.inspection_id)}">Start inspection</button>`);
  } else if (alarm.inspection_status === "in_progress") {
    buttons.push(`<button class="action-button" data-action="complete-inspection" data-id="${escapeHtml(alarm.inspection_id)}">Complete inspection</button>`);
  } else if (alarm.inspection_status === "completed" && !alarm.remediation_id) {
    buttons.push(`<button class="action-button" data-action="plan-remediation" data-id="${escapeHtml(alarm.inspection_id)}" data-code="${escapeHtml(alarm.alarm_code)}">Plan remediation</button>`);
  } else if (["planned", "in_progress"].includes(alarm.remediation_status)) {
    buttons.push(`<button class="action-button resolve" data-action="complete-remediation" data-id="${escapeHtml(alarm.remediation_id)}">Complete remediation</button>`);
  } else if (!alarm.inspection_id && ["open", "acknowledged"].includes(alarm.status)) {
    buttons.push(`<button class="action-button" data-action="inspect" data-id="${escapeHtml(alarm.alarm_id)}">Start inspection</button>`);
  }
  if (alarm.status === "open" && alarm.has_impact_link) {
    buttons.unshift(`<button class="action-button" data-action="ack" data-id="${escapeHtml(alarm.alarm_id)}">Acknowledge</button>`);
  }
  return buttons.join(" ") || `<span class="workflow-state">${escapeHtml(statusName(alarm.status))}</span>`;
}

function alarmRow(alarm, full = false) {
  const location = `${escapeHtml(alarm.warehouse_name)}<span class="entity-secondary">${escapeHtml(alarm.zone_id)}</span>`;
  const impact = alarm.has_impact_link ? `${number(alarm.impacted_lot_count)} lot${alarm.impacted_lot_count === 1 ? "" : "s"}<span class="entity-secondary">${number(alarm.linked_units)} units</span>` : `<span class="no-impact">No lot link</span><span class="entity-secondary">Traceability review required</span>`;
  const workflow = `<span class="workflow-state">${escapeHtml(statusName(alarm.status))}</span>${alarm.inspection_status ? `<span class="workflow-sub">Inspection ${escapeHtml(statusName(alarm.inspection_status))}</span>` : ""}${alarm.remediation_status ? `<span class="workflow-sub">Remediation ${escapeHtml(statusName(alarm.remediation_status))}</span>` : ""}`;
  const summary = full ? `<span class="entity-secondary">${escapeHtml(alarm.summary)}</span>` : "";
  return `<tr><td><span class="severity-pill severity-${escapeHtml(alarm.severity)}">${escapeHtml(alarm.severity)}</span></td><td><span class="alarm-code">${escapeHtml(alarm.alarm_code)}</span><span class="entity-secondary">${escapeHtml(alarm.alarm_id)} · ${niceDate(alarm.triggered_at)}</span>${summary}</td><td>${location}</td><td>${impact}</td><td class="workflow-cell">${workflow}</td><td><div class="row-actions">${alarmActionButtons(alarm)}</div></td></tr>`;
}

function activeAlarms() {
  return state.data.alarms.filter((alarm) => !["resolved", "false_positive"].includes(alarm.status));
}

function renderAlarms(alarms) {
  const active = activeAlarms();
  const home = active.slice(0, 5);
  $("#alarm-rows").innerHTML = home.length ? home.map((a) => alarmRow(a)).join("") : `<tr><td colspan="6" class="empty-row">No active alarms. Run an evaluation to check source records.</td></tr>`;
  let filtered = alarms;
  if (state.filter === "critical" || state.filter === "high") filtered = alarms.filter((a) => a.severity === state.filter && !["resolved", "false_positive"].includes(a.status));
  if (state.filter === "open") filtered = alarms.filter((a) => a.status === "open");
  $("#alarm-rows-full").innerHTML = filtered.length ? filtered.map((a) => alarmRow(a, true)).join("") : `<tr><td colspan="6" class="empty-row">No alarms match this filter.</td></tr>`;
  $("#alarm-filter-summary").textContent = `${number(filtered.length)} records`;
}

function renderLots(lots) {
  const impactedUnits = new Set(lots.map((l) => l.lot_id)).size;
  const totalUnits = [...new Map(lots.map((l) => [l.lot_id, Number(l.quantity_units)])).values()].reduce((sum, value) => sum + value, 0);
  $("#inventory-summary").innerHTML = `<div class="summary-card"><span>Lots with active impact links</span><strong>${number(impactedUnits)}</strong></div><div class="summary-card"><span>Units under active review</span><strong>${number(totalUnits)}</strong></div><div class="summary-card"><span>Storage assignments</span><strong>${number(state.data.dashboard?.incompatible_lot_count)}</strong><small>incompatibility cases</small></div>`;
  $("#lot-count").textContent = `${number(lots.length)} links`;
  $("#lot-rows").innerHTML = lots.length ? lots.map((lot) => `<tr><td><span class="entity-primary">${escapeHtml(lot.lot_id)}</span><span class="entity-secondary">${escapeHtml(lot.product_name)}</span></td><td><span class="entity-primary">${escapeHtml(lot.warehouse_name)}</span><span class="entity-secondary">${escapeHtml(lot.zone_id)}</span></td><td><span class="zone-type">${escapeHtml(lot.zone_type)}</span></td><td><span class="alarm-code">${escapeHtml(lot.alarm_code)}</span><span class="entity-secondary">${escapeHtml(lot.triggered_at ? niceDate(lot.triggered_at) : "")}</span></td><td><span class="severity-pill severity-${escapeHtml(lot.severity)}">${escapeHtml(lot.severity)}</span></td><td>${number(lot.quantity_units)}</td><td><span class="issue-tag">${escapeHtml(statusName(lot.lifecycle_state))}</span></td></tr>`).join("") : `<tr><td colspan="7" class="empty-row">No active inventory impact links.</td></tr>`;
}

function renderExceptions(items) {
  const counts = { high: items.filter((x) => x.severity === "high").length, medium: items.filter((x) => x.severity === "medium").length, other: items.filter((x) => !["high", "medium"].includes(x.severity)).length };
  $("#exception-summary").innerHTML = `<div class="summary-card"><span>Total review items</span><strong>${number(items.length)}</strong></div><div class="summary-card"><span>High priority</span><strong>${number(counts.high)}</strong></div><div class="summary-card"><span>Configuration review</span><strong>${number(items.filter((x) => x.issue_type === "unjustified_sensor_mapping").length)}</strong></div><div class="summary-card"><span>Inventory placement</span><strong>${number(items.filter((x) => x.issue_type === "incompatible_zone").length)}</strong></div>`;
  $("#exception-count").textContent = `${number(items.length)} open`;
  $("#exception-rows").innerHTML = items.length ? items.map((item) => `<tr><td><span class="severity-pill severity-${escapeHtml(item.severity)}">${escapeHtml(item.severity)}</span></td><td><span class="exception-id">${escapeHtml(item.record_id)}</span></td><td><span class="issue-tag">${escapeHtml(item.issue_type.replaceAll("_", " "))}</span></td><td><span class="detail-text">${escapeHtml(item.description)}</span></td><td>${niceDate(item.occurred_at)}</td><td>${item.issue_type === "alarm_missing_lot_link" ? `<button class="action-button" data-action="reconcile" data-id="${escapeHtml(item.record_id)}">Find affected lots</button>` : `<span class="workflow-state">Review</span>`}</td></tr>`).join("") : `<tr><td colspan="6" class="empty-row">No active exceptions. Configuration and alarm impact links are complete.</td></tr>`;
}

function renderAudit(items) {
  $("#audit-rows").innerHTML = items.length ? items.map((event) => {
    const transition = event.old_state ? `${statusName(event.old_state)} <span>→</span> ${statusName(event.new_state)}` : event.new_state ? `Created as ${statusName(event.new_state)}` : "Impact links updated";
    return `<tr><td>${niceDate(event.occurred_at)}</td><td><span class="issue-tag">${escapeHtml(event.entity_type)}</span></td><td><span class="exception-id">${escapeHtml(event.entity_id)}</span></td><td><span class="audit-action">${escapeHtml(event.action.replaceAll("_", " "))}</span></td><td class="state-change">${transition}</td><td>${escapeHtml(event.actor)}</td></tr>`;
  }).join("") : `<tr><td colspan="6" class="empty-row">No audit events recorded.</td></tr>`;
}

async function performAction(button) {
  const { action, id, code } = button.dataset;
  button.disabled = true;
  try {
    if (action === "ack") {
      await request(`/api/alarms/${encodeURIComponent(id)}/acknowledge`, { method: "POST", body: JSON.stringify({ actor: "plant_operator" }) });
      toast("Alarm acknowledged and recorded in event history.");
    } else if (action === "inspect") {
      await request(`/api/alarms/${encodeURIComponent(id)}/inspect`, { method: "POST", body: JSON.stringify({ assigned_role: "quality_engineer", actor: "plant_operator" }) });
      toast("Inspection started and assigned to quality engineering.");
    } else if (action === "reconcile") {
      const result = await request(`/api/alarms/${encodeURIComponent(id)}/reconcile-impact`, { method: "POST", body: JSON.stringify({ actor: "quality_engineer" }) });
      toast(`Linked ${number(result.linked_lot_count)} affected lot(s) by zone and event time.`);
    } else if (action === "start-inspection") {
      await request(`/api/inspections/${encodeURIComponent(id)}/start`, { method: "POST", body: JSON.stringify({ actor: "quality_engineer" }) });
      toast("Inspection started.");
    } else if (action === "complete-inspection") {
      await request(`/api/inspections/${encodeURIComponent(id)}/complete`, { method: "POST", body: JSON.stringify({ findings: "Condition reviewed; inventory impact and source event checked.", actor: "quality_engineer" }) });
      toast("Inspection completed. Plan a remediation action next.");
    } else if (action === "plan-remediation") {
      const actionType = ["STORAGE_MISMATCH", "TEMP_HIGH", "TEMP_LOW", "HUMIDITY_HIGH", "HUMIDITY_LOW"].includes(code) ? "quarantine_lot" : "verify_calibration";
      const result = await request(`/api/inspections/${encodeURIComponent(id)}/remediations`, { method: "POST", body: JSON.stringify({ action_type: actionType, notes: "Risk action planned from the linked alarm impact.", actor: "quality_engineer" }) });
      toast(`Remediation planned for ${number(result.inventory_impact_quantity_units)} inventory units.`);
    } else if (action === "complete-remediation") {
      await request(`/api/remediations/${encodeURIComponent(id)}/complete`, { method: "POST", body: JSON.stringify({ actor: "quality_engineer" }) });
      toast("Remediation completed and alarm resolved.");
    }
    await refreshAll();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

function exportCsv(filename, rows) {
  if (!rows.length) return toast("There are no records to export.", true);
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const csv = [columns.join(","), ...rows.map((row) => columns.map((key) => `"${String(row[key] ?? "").replaceAll('"', '""')}"`).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-view]");
  if (nav) return setView(nav.dataset.view);
  const go = event.target.closest("[data-go]");
  if (go) return setView(go.dataset.go);
  const action = event.target.closest("[data-action]");
  if (action) return performAction(action);
  const filter = event.target.closest("[data-filter]");
  if (filter) {
    state.filter = filter.dataset.filter;
    $$(".filter-chip").forEach((chip) => chip.classList.toggle("active", chip === filter));
    return renderAlarms(state.data.alarms);
  }
});

$("#refresh-button").addEventListener("click", () => refreshAll().then(() => toast("Dashboard refreshed.")).catch((error) => toast(error.message, true)));
$("#evaluate-button").addEventListener("click", runEvaluation);
$("#evaluate-button-2").addEventListener("click", runEvaluation);
$("#export-button").addEventListener("click", () => exportCsv("warehouse-risk-snapshot.csv", activeAlarms()));
$("#inventory-export").addEventListener("click", () => exportCsv("inventory-impact.csv", state.data.lots));
$("#audit-export").addEventListener("click", () => exportCsv("warehouse-event-history.csv", state.data.audit));

async function runEvaluation() {
  const buttons = [$ ("#evaluate-button"), $("#evaluate-button-2")].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; button.textContent = "Evaluating…"; });
  try {
    const result = await request("/api/evaluate", { method: "POST", body: "{}" });
    await refreshAll();
    toast(result.created ? `${number(result.created)} new alarm(s) recorded.` : "Evaluation complete. No new alarms.");
  } catch (error) { toast(error.message, true); }
  finally {
    const primary = $("#evaluate-button"); primary.innerHTML = `<span class="play-icon">▶</span> Evaluate now`;
    const secondary = $("#evaluate-button-2"); secondary.textContent = "▶ Evaluate now";
    buttons.forEach((button) => { button.disabled = false; });
  }
}

refreshAll().catch((error) => {
  toast(`Cannot load monitor data: ${error.message}`, true);
  $("#zone-rows").innerHTML = `<tr><td colspan="5" class="empty-row">Backend not ready. Restart the server to initialize its database.</td></tr>`;
});
