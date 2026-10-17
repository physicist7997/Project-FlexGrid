/* FlexLadder operator console */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const LABEL = {ev:"Pause EV charging", pool:"Pause pool pump", water_heater:"Delay water heater",
  battery:"Partner battery discharge", tier2_site:"Critical-site Tier-2 trim", thermostat:"Thermostat nudge (+°F)"};
const ORDER = ["ev","pool","water_heater","battery","tier2_site","thermostat"];
const COLORS = {ev:"#2b8cbe", pool:"#7fcdbb", water_heater:"#41ab5d", battery:"#f39c12", tier2_site:"#9b59b6", thermostat:"#e74c3c"};
const HOME_COL = {dispatched:"#1b8a4b", enrolled:"#3b7ddd", silent:"#e09a17", not_enrolled:"#bfc5c1", dark:"#141414"};
const SITE_ICON = {hospital:"🏥", fire:"🚒", water:"💧", cooling:"❄️", senior:"🧓"};

let P = null, GEO = null, TPL = null, K = 0, VIEW = "flex", LEVEL = {type: "district"};
let map, fdLayer, nbLayer, homeLayer, siteLayer, chart, openPopup = null;
let decisions = [], dbFilters = new Set(), activeTpl = null;

const f1 = (x, d = 1) => Number(x).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const f0 = x => Math.round(Number(x)).toLocaleString();
const hr = h => { const H = Math.floor(h), M = Math.round((h - H) * 60); return `${(H - 1) % 12 + 1}:${String(M).padStart(2, "0")} ${H < 12 ? "AM" : "PM"}`; };
async function api(path, opts) { const r = await fetch(path, opts); if (!r.ok) throw new Error(await r.text()); return r.json(); }
const post = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 3200); }
function mix(a, b, t) {
  const p = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
  const A = p(a), B = p(b); return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
const IV = () => P.intervals[Math.min(K, P.intervals.length - 1)];
const META = () => Object.fromEntries(P.feeder_meta.map(f => [f.feeder, f]));

/* ---------------------------------------------------------------- feeder / neighborhood state */
function feederState(fid) {
  const I = IV(), open = (VIEW === "old" ? I.old_open : I.flex_open).includes(fid);
  const share = I.shares[fid] || 0, got = VIEW === "old" ? 0 : (I.by_feeder[fid] || 0);
  return {open, share, got, frac: share ? Math.min(1, got / share) : 0, inArea: fid in I.shares, load: I.feeder_load[fid] || 0};
}
function feederColor(s) {
  if (s.open) return HOME_COL.dark;
  if (!s.inArea) return "#d5dbd6";
  if (s.got <= 0) return "#f1f4f1";
  return mix("#cdeed8", "#167a40", s.frac);
}
function nbState(nb) {
  const m = META(); let share = 0, got = 0, dark = 0, homes = 0;
  P.feeder_meta.filter(f => f.neighborhood === nb).forEach(f => {
    const s = feederState(f.feeder); share += s.share; got += s.got; homes += f.homes; if (s.open) dark += f.homes;
  });
  return {share, got, dark, homes};
}

/* ---------------------------------------------------------------- map */
function initMap() {
  map = L.map("map", {zoomControl: true, preferCanvas: true}).setView([34.225, -118.49], 12);
  // CARTO basemaps now require an API key; OSM standard tiles are keyless (light use, attribution required)
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19, className: "basemap"}).addTo(map);
  fdLayer = L.layerGroup().addTo(map); nbLayer = L.layerGroup().addTo(map);
  homeLayer = L.layerGroup().addTo(map); siteLayer = L.layerGroup().addTo(map);
  $("#backBtn").onclick = () => {
    if (LEVEL.type === "feeder") goNb(LEVEL.nb); else if (LEVEL.type === "nb") goDistrict();
  };
  $$("#viewSeg button").forEach(b => b.onclick = () => {
    $$("#viewSeg button").forEach(x => x.classList.toggle("on", x === b)); VIEW = b.dataset.v; renderMap(); renderBanner();
  });
  $("#randHome").onclick = () => randomHome(false);
  $("#randDisp").onclick = () => randomHome(true);
  renderLegend();
}
function renderLegend() {
  $("#legend").innerHTML = LEVEL.type === "feeder"
    ? `<i style="background:${HOME_COL.dispatched}"></i>Dispatched &nbsp;<i style="background:${HOME_COL.enrolled}"></i>Enrolled, idle &nbsp;
       <i style="background:${HOME_COL.silent}"></i>Not reporting &nbsp;<i style="background:${HOME_COL.not_enrolled}"></i>Not enrolled &nbsp;
       <i style="background:#fff;border:2px solid #d23c3c"></i>Medical &nbsp;<i style="background:${HOME_COL.dark}"></i>Breaker open`
    : `<i style="background:#167a40"></i>Feeder share covered &nbsp;<i style="background:#cdeed8"></i>Partly &nbsp;
       <i style="background:#f1f4f1;border:1px solid #ccc"></i>None &nbsp;<i style="background:${HOME_COL.dark}"></i>Breaker open (dark) &nbsp;🏥 critical site`;
}
function goDistrict() { map.closePopup(); LEVEL = {type: "district"}; map.flyToBounds(L.latLngBounds(GEO.neighborhoods.flatMap(n => n.bounds)), {duration: .6}); renderMap(); }
function goNb(nb) { map.closePopup(); LEVEL = {type: "nb", nb}; const n = GEO.neighborhoods.find(x => x.name === nb); map.flyToBounds(n.bounds, {duration: .6, padding: [20, 20]}); renderMap(); }
function goFeeder(fid, then) {
  const f = GEO.feeders.find(x => x.feeder === fid);
  LEVEL = {type: "feeder", nb: f.neighborhood, feeder: fid};
  map.flyToBounds(f.bounds, {duration: .6, padding: [10, 10]});
  renderMap(then);
}
function crumb() {
  const m = META();
  $("#crumb").innerHTML = LEVEL.type === "district" ? "<b>District</b> · click a neighborhood"
    : LEVEL.type === "nb" ? `District › <b>${LEVEL.nb}</b> · click a feeder subsector`
    : `District › ${LEVEL.nb} › <b>${m[LEVEL.feeder].name} (${LEVEL.feeder})</b> · click a house`;
  $("#backBtn").style.display = LEVEL.type === "district" ? "none" : "";
}
function renderMap(then) {
  fdLayer.clearLayers(); nbLayer.clearLayers(); homeLayer.clearLayers(); siteLayer.clearLayers();
  crumb(); renderLegend();
  const m = META();
  GEO.feeders.forEach(f => {
    const s = feederState(f.feeder), dim = LEVEL.type !== "district" && f.neighborhood !== LEVEL.nb;
    const meta = m[f.feeder];
    const poly = L.polygon(f.poly, {color: "#ffffff", weight: 1, fillColor: feederColor(s), fillOpacity: dim ? .25 : .78});
    poly.bindTooltip(`<b>${meta.name}</b> (${f.feeder})<br>${f0(meta.homes)} homes · load ${f1(s.load / 1000, 2)} MW<br>`
      + (s.open ? "<b>BREAKER OPEN — all homes dark</b>" : `flexibility ${f0(s.got)} / share ${f0(s.share)} kW`)
      + (meta.critical ? `<br>🏥 ${meta.critical_site} (exempt from rotation)` : "") + (meta.ufls ? "<br>⚙ relay-reserved feeder" : ""),
      {sticky: true});
    poly.on("click", () => goFeeder(f.feeder));
    if (LEVEL.type === "nb" && f.neighborhood === LEVEL.nb) {
      poly.bindTooltip(`<b>${meta.name.replace(LEVEL.nb + " ", "")}</b><br>${s.open ? "DARK" : f0(s.got) + " / " + f0(s.share) + " kW"}`,
        {permanent: true, direction: "center", className: "nbLabel"});
    }
    if (!(LEVEL.type === "feeder" && f.feeder === LEVEL.feeder)) poly.addTo(fdLayer);
    else L.polygon(f.poly, {color: s.open ? "#141414" : "#167a40", weight: 3, fill: false}).addTo(fdLayer);
  });
  if (LEVEL.type === "district") {
    GEO.neighborhoods.forEach(n => {
      const s = nbState(n.name);
      const pol = L.polygon(n.poly, {color: "#22312a", weight: 2, fillOpacity: 0});
      pol.bindTooltip(`<b>${n.name}</b><br>${f1(s.got / 1000)} / ${f1(s.share / 1000)} MW`
        + (s.dark ? `<br><span style="color:#d23c3c">${f0(s.dark)} homes dark</span>` : ""),
        {permanent: true, direction: "center", className: "nbLabel"});
      pol.on("click", () => goNb(n.name));
      pol.addTo(nbLayer);
    });
  } else {
    const n = GEO.neighborhoods.find(x => x.name === LEVEL.nb);
    L.polygon(n.poly, {color: "#22312a", weight: 2.5, fill: false, interactive: false}).addTo(nbLayer);
  }
  GEO.sites.forEach(s => {
    L.marker([s.lat, s.lon], {icon: L.divIcon({className: "", html: `<div style="font-size:20px">${SITE_ICON[s.kind] || "🏥"}</div>`, iconSize: [22, 22]})})
      .bindTooltip(`<b>${s.site}</b><br>Critical site (Tier 0 never touched)<br>Tier-2 flexible load offered: ${s.tier2_kw} kW`)
      .addTo(siteLayer);
  });
  if (LEVEL.type === "feeder") loadHomes(LEVEL.feeder).then(() => then && then());
  else if (then) then();
}
async function loadHomes(fid) {
  const r = await api(`/api/homes?feeder=${fid}&k=${K}`);
  const open = feederState(fid).open;
  homeLayer.clearLayers();
  r.homes.forEach(h => {
    const st = open ? "dark" : (VIEW === "old" && h.s === "dispatched" ? "enrolled" : h.s);
    const c = L.circleMarker([h.lat, h.lon], {radius: h.s === "dispatched" && !open ? 5 : 3.6,
      color: h.m ? "#d23c3c" : "#ffffff", weight: h.m ? 2 : .6, fillColor: HOME_COL[st], fillOpacity: .95});
    c.on("click", () => openHome(h.id));
    c.addTo(homeLayer);
  });
}

/* ---------------------------------------------------------------- house popup */
async function openHome(id) {
  let rec;
  try { rec = await api(`/api/home/${id}?k=${K}`); } catch (e) { toast("Home not found"); return; }
  const show = () => { openPopup = L.popup({maxWidth: 360, autoPan: true}).setLatLng([rec.lat, rec.lon]).setContent(homeHtml(rec)).openOn(map); };
  if (LEVEL.type !== "feeder" || LEVEL.feeder !== rec.feeder) goFeeder(rec.feeder, () => setTimeout(show, 650)); else show();
}
function yn(b) { return b ? "✅" : "—"; }
function homeHtml(r) {
  const st = `<span class="st ${r.status}">${r.status.replace("_", " ")}</span>`;
  const dev = [];
  if (r.battery) dev.push(`<div><span>Battery</span> ${r.battery_kwh} kWh · ${r.battery_inverter_kw} kW · SoC ${r.battery_soc_pct}%</div>`);
  if (r.ev) dev.push(`<div><span>EV</span> ${r.ev_model} (${r.ev_battery_kwh} kWh) · ${r.ev_charger_kw} kW charger · ${r.ev_soc_pct}%</div>`);
  if (r.tstat_brand !== "none") dev.push(`<div><span>Thermostat</span> ${r.tstat_brand} · set ${r.setpoint_f}°F</div>`);
  if (r.has_ac) dev.push(`<div><span>Central AC</span> ${r.ac_kw} kW ${r.ac_on ? "(running)" : "(off)"}</div>`);
  dev.push(`<div><span>Water heater</span> ${r.water_heater}</div>`);
  if (r.pool_pump) dev.push(`<div><span>Pool pump</span> 1.5 kW ${r.pool_runs_evening ? "(evening)" : ""}</div>`);
  if (r.solar_kw) dev.push(`<div><span>Solar</span> ${r.solar_kw} kW</div>`);
  if (r.medical) dev.push(`<div style="color:#d23c3c"><span>Medical</span> ${r.medical_device} (protected)</div>`);
  const prefs = [
    `<div><span>Flex Rewards (appliances)</span> ${yn(r.enrolled)}</div>`, `<div><span>Mode</span> ${r.mode}</div>`,
    r.ev ? `<div><span>EV enrolled</span> ${yn(r.enroll_ev)}</div>` : "",
    r.ev ? `<div><span>EV ready by</span> ${hr(r.ev_depart_hour)} · ≥${r.ev_min_charge_pct}%</div>` : "",
    r.tstat_brand !== "none" ? `<div><span>Thermostat enrolled</span> ${yn(r.enroll_thermostat)}</div>` : "",
    r.tstat_brand !== "none" ? `<div><span>Comfort ceiling</span> ${r.comfort_max_f}°F</div>` : "",
    r.battery ? `<div><span>Battery participates</span> ${yn(r.battery_participates)}</div>` : "",
    r.battery ? `<div><span>Backup reserve</span> ${r.battery_reserve_pct}%</div>` : "",
    r.wh_kw ? `<div><span>Water heater enrolled</span> ${yn(r.enroll_water_heater)}</div>` : "",
    r.pool_pump ? `<div><span>Pool enrolled</span> ${yn(r.enroll_pool)}</div>` : "",
    `<div><span>Never touch</span> ${r.never_touch || "—"}</div>`, `<div><span>Notify</span> ${r.notify} · ${r.language}</div>`,
  ].join("");
  const acts = r.actions.map(a => `<tr><td>${a.action}</td><td class="${a.status === "DISPATCHED" ? "disp" : ""}">${a.status}${a.kw ? " · " + a.kw + " kW" : ""}</td></tr>`
    + (a.reason ? `<tr><td></td><td class="muted">${a.reason}</td></tr>` : "")).join("");
  return `<div class="hp"><h4>${r.address} ${st}</h4>
    <div class="sub">${r.home_id} · ${r.neighborhood} · feeder ${r.feeder} · ${r.sqft} sqft · ${r.residents} residents</div>
    <div class="sec">Right now (${IV().label})</div>
    <div class="grid"><div><span>Load</span> ${r.load_now_kw} kW</div><div><span>Giving</span> <b>${r.relief_kw} kW</b></div>
      <div><span>Telemetry</span> ${r.telemetry_age_s}s ago (${r.comms})</div><div><span>Events this season</span> ${r.events_this_season}</div></div>
    <div class="sec">Devices</div><div class="grid">${dev.join("")}</div>
    <div class="sec">Homeowner settings (from the FlexLadder app)</div><div class="grid">${prefs}</div>
    <div class="sec">Dispatch decision</div><table>${acts}</table>
    <div style="margin-top:6px"><a href="/homeowner?home=${r.home_id}" target="_blank">Open in homeowner app ↗</a></div></div>`;
}
async function randomHome(dispatched) {
  const q = new URLSearchParams({k: K, dispatched});
  if (LEVEL.type === "feeder") q.set("feeder", LEVEL.feeder); else if (LEVEL.type === "nb") q.set("neighborhood", LEVEL.nb);
  try { const r = await api(`/api/random_home?${q}`); openHome(r.id); } catch (e) { toast("No matching house here"); }
}

/* ---------------------------------------------------------------- KPIs, banner, chart */
function renderKPIs() {
  const s = P.summary, I = IV(), em = s.kind === "emergency";
  const card = (l, v, sub, cls = "") => `<div class="kpi"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="s">${sub}</div></div>`;
  const value = em
    ? card("Outage avoided", `${f0(s.home_hours_old - s.home_hours_flex)} home-h`, `≈ $${f0(s.voll_avoided_usd)} lost-load value (Brattle residential)`, "good")
    : (P.request.label.includes("4CP")
      ? card("4CP value / yr", `$${f0(s.min_mw * 40)}k–$${f0(s.min_mw * 76)}k`, "if this hour is one of the 4 peaks", "good")
      : card("Paid to participants", `$${f0(s.payout_usd)}`, "at $1/kWh (ELRP residential, illustrative)", "good"));
  $("#kpis").innerHTML = [
    card("Needed", `${f1(I.target_kw / 1000)} MW`, `${em ? "EMERGENCY" : "ROUTINE"} · ${P.request.duration_h} h · respond ≤ ${P.request.deadline_min} min`),
    card(`Relief @ ${I.label}`, `${f1(I.planned_kw / 1000, 2)} MW`, `first hour ${f1(s.first_mw)} · lowest ${f1(s.min_mw)} MW`, I.short_kw > 0 ? "" : "good"),
    card("Shortfall now", `${f1(I.short_kw / 1000, 2)} MW`, `worst ${f1(s.max_short_mw)} MW`, I.short_kw > 0 ? "bad" : "good"),
    em ? card("Homes dark at peak", `${f0(s.peak_dark_flex)}`, `old way: ${f0(s.peak_dark_old)}`, s.peak_dark_flex < s.peak_dark_old ? "good" : "bad")
       : card("Breakers opened", "0", "routine events never cut power", "good"),
    card("Homes contributing", `${f0(Object.entries(I.count_by_action).filter(([a]) => a !== "tier2_site").reduce((x, [, c]) => x + c, 0))}`,
      `${f0(s.participants)} over the event · avg ${f1(I.planned_kw / Math.max(1, s.participants), 2)} kW`),
    value,
  ].join("");
}
function renderBanner() {
  const I = IV(), b = $("#banner"), em = P.summary.kind === "emergency", m = META();
  if (VIEW === "old" && em) {
    b.className = "banner warn";
    b.innerHTML = `<b>Old way at ${I.label}:</b> the rotation list opens ${I.old_open.map(f => m[f].name).join(", ") || "nothing"} — <b>${f0(I.dark_old)} homes</b> fully dark, every load including fridges and medical devices.`;
    return;
  }
  if (I.short_kw > 50) {
    b.className = "banner warn";
    if (em) {
      b.innerHTML = `<b>${f1(I.short_kw / 1000, 2)} MW short at ${I.label}.</b> Verified flexibility can't cover it. Fallback from the utility's rotation list: open
        <b>${I.flex_open.map(f => m[f].name).join(", ")}</b> (${f0(I.dark_flex)} homes). <b>Operator decides:</b>
        <button class="btn small" data-dec="fallback">Approve feeder fallback</button>
        <button class="btn small" data-dec="reserve">Ask partner to lower battery reserve</button>
        <button class="btn small" data-dec="appeal">Send voluntary conservation text</button>`;
      $$("#banner [data-dec]").forEach(x => x.onclick = () => decide(x.dataset.dec));
    } else b.innerHTML = `<b>${f1(I.short_kw / 1000, 2)} MW short at ${I.label}.</b> Routine event: the gap is reported to the program operator, no breakers are ever opened.`;
  } else {
    b.className = "banner ok";
    b.innerHTML = `<b>Target met at ${I.label}</b> with ${f1(I.planned_kw / 1000, 2)} MW from ${f0(Object.values(I.count_by_action).reduce((a, c) => a + c, 0))} actions. No feeder opened.`;
  }
}
function decide(kind) {
  const I = IV(), txt = {fallback: `Operator approved feeder fallback at ${I.label}: ${I.flex_open.join(", ")}`,
    reserve: `Operator asked the battery partner to lower backup reserve by 10 pts at ${I.label} (partner decides)`,
    appeal: `Operator sent a voluntary conservation text to non-enrolled homes in the area at ${I.label}`}[kind];
  decisions.unshift({step: `👤 Operator decision @ ${new Date().toLocaleTimeString()}`, detail: txt});
  renderTrace(); toast(txt);
}
function renderChart() {
  const labels = P.intervals.map(i => i.label);
  const ds = ORDER.map(a => ({label: LABEL[a], data: P.intervals.map(i => (i.by_action[a] || 0) / 1000), fill: true,
    backgroundColor: COLORS[a] + "cc", borderColor: COLORS[a], borderWidth: 1, pointRadius: 0, stack: "s", tension: .25}));
  ds.push({label: "Needed", data: P.intervals.map(i => i.target_kw / 1000), borderColor: "#111", borderDash: [6, 4],
    borderWidth: 2, pointRadius: 0, fill: false, stack: "t"});
  const cursor = {id: "cursor", afterDraw(c) {
    const x = c.scales.x.getPixelForValue(K), {top, bottom} = c.chartArea, ctx = c.ctx;
    ctx.save(); ctx.strokeStyle = "rgba(0,0,0,.45)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
  }};
  if (chart) chart.destroy();
  chart = new Chart($("#chart"), {type: "line", data: {labels, datasets: ds}, plugins: [cursor],
    options: {responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: "index", intersect: false},
      plugins: {legend: {position: "right", labels: {boxWidth: 10, font: {size: 11}}}},
      scales: {y: {stacked: true, title: {display: true, text: "MW"}}, x: {ticks: {font: {size: 10}}}},
      onClick: (e, els, c) => { const x = c.scales.x.getValueForPixel(e.x); if (x >= 0) { $("#tSlider").value = Math.round(x); setK(Math.round(x)); } }}});
}

/* ---------------------------------------------------------------- dispatch tab */
function renderTemplates() {
  $("#templates").innerHTML = Object.entries(TPL.templates).map(([k, t]) =>
    `<button data-k="${k}" class="${k === activeTpl ? "on" : ""}"><b>${t.label}</b><span>${t.target_mw} MW · ${hr(t.start_hour)} · ${t.duration_h} h · ≤${t.deadline_min} min · ${t.kind}${t.area.length ? " · " + t.area.join(", ") : ""}</span></button>`).join("")
    + `<button data-k="custom" class="${activeTpl === "custom" ? "on" : ""}"><b>Custom dispatch</b><span>Edit the request below and press Find resources</span></button>`;
  $$("#templates button").forEach(b => b.onclick = () => {
    if (b.dataset.k === "custom") { activeTpl = "custom"; $("#reqForm [name=label]").value = "Custom dispatch"; renderTemplates(); $("#tplWhy").textContent = ""; return; }
    activeTpl = b.dataset.k; fillForm(TPL.templates[activeTpl]); renderTemplates(); runPlan();
  });
}
function fillForm(t) {
  const F = $("#reqForm");
  F.label.value = t.label; F.target_mw.value = t.target_mw; F.unit.value = "MW"; F.start_hour.value = t.start_hour;
  F.duration_h.value = String(t.duration_h).replace(/\.0$/, ""); F.deadline_min.value = t.deadline_min; F.kind.value = t.kind;
  $$("#areaBox input").forEach(c => c.checked = (t.area || []).includes(c.value));
  $("#tplWhy").textContent = t.why || "";
}
function readForm() {
  const F = $("#reqForm");
  return {label: F.label.value || "Custom dispatch", target_mw: +F.target_mw.value / (F.unit.value === "kW" ? 1000 : 1), start_hour: +F.start_hour.value,
    duration_h: +F.duration_h.value, deadline_min: +F.deadline_min.value, kind: F.kind.value,
    area: $$("#areaBox input").filter(c => c.checked).map(c => c.value)};
}
async function runPlan() {
  $("#reqForm button[type=submit]").textContent = "⏳ Planning…";
  try { P = await post("/api/plan", readForm()); K = 0; decisions = []; renderAll(); toast(`Plan ready in ${P.ms} ms`); }
  catch (e) { toast("Plan failed: " + e.message.slice(0, 80)); }
  $("#reqForm button[type=submit]").textContent = "🔎 Find resources";
}
function renderDispatchTable() {
  const I = IV();
  const rows = ORDER.map(a => `<tr><td><span style="color:${COLORS[a]}">■</span> ${LABEL[a]}</td><td>${f1((I.by_action[a] || 0) / 1000, 2)} MW</td><td>${f0(I.count_by_action[a] || 0)}</td></tr>`).join("");
  $("#dispatchTable").innerHTML = `<table><tr><th>Action (least disruptive first)</th><th>Relief</th><th># devices</th></tr>${rows}
    <tr><td><b>Total</b></td><td><b>${f1(I.planned_kw / 1000, 2)} MW</b></td><td></td></tr>
    <tr><td>Needed</td><td>${f1(I.target_kw / 1000, 2)} MW</td><td></td></tr></table>`;
  $("#exportBtn").href = `/api/export.csv?k=${K}`;
}

/* ---------------------------------------------------------------- database tab */
let qTimer;
async function searchHomes() {
  const q = new URLSearchParams({q: $("#q").value, neighborhood: $("#qNb").value, filters: [...dbFilters].join(","), k: K});
  if (LEVEL.type === "feeder" && !$("#q").value) q.set("feeder", LEVEL.feeder);
  const r = await api(`/api/search?${q}`);
  $("#qCount").textContent = `${f0(r.total)} homes match${r.total > r.rows.length ? ` (showing ${r.rows.length})` : ""}${q.get("feeder") ? " in feeder " + q.get("feeder") : ""}`;
  $("#homesTbl").innerHTML = `<tr><th>Home</th><th>Address</th><th>Feeder</th><th>Status</th><th>kW</th><th>Devices</th></tr>`
    + r.rows.map(x => `<tr class="click" data-id="${x.id}"><td>${x.id}</td><td>${x.address}</td><td>${x.feeder}</td>
      <td><span class="st ${x.status}">${x.status.replace("_", " ")}</span></td><td>${x.relief_kw || ""}</td><td>${x.devices}</td></tr>`).join("");
  $$("#homesTbl tr.click").forEach(tr => tr.onclick = () => openHome(tr.dataset.id));
}
async function renderFeederTable() {
  const r = await api(`/api/feeders?k=${K}`);
  $("#feedersTbl").innerHTML = `<tr><th>Feeder</th><th>Homes</th><th>Load MW</th><th>Share kW</th><th>Flex kW</th><th>Dispatched</th><th>Status</th><th>Critical</th></tr>`
    + r.rows.map(x => `<tr class="click" data-f="${x.feeder}"><td><b>${x.feeder}</b> ${x.name}</td><td>${f0(x.homes)}</td><td>${x.load_mw}</td>
      <td>${f0(x.share_kw)}</td><td>${f0(x.flex_kw)}</td><td>${f0(x.homes_dispatched)}</td>
      <td>${x.status.startsWith("OPEN") ? '<span class="st dark">OPEN</span>' : x.status}</td><td>${x.critical}</td></tr>`).join("");
  $$("#feedersTbl tr.click").forEach(tr => tr.onclick = () => goFeeder(tr.dataset.f));
}
function initDb() {
  ["enrolled", "dispatched", "silent", "battery", "ev", "thermostat", "pool", "medical"].forEach(f => {
    const b = document.createElement("button"); b.textContent = f; b.onclick = () => {
      dbFilters.has(f) ? dbFilters.delete(f) : dbFilters.add(f); b.classList.toggle("on"); searchHomes(); };
    $("#chips").appendChild(b);
  });
  $("#q").oninput = () => { clearTimeout(qTimer); qTimer = setTimeout(searchHomes, 250); };
  $("#qNb").onchange = searchHomes;
  $$("#dbSeg button").forEach(b => b.onclick = () => {
    $$("#dbSeg button").forEach(x => x.classList.toggle("on", x === b));
    $("#dbHomes").style.display = b.dataset.d === "homes" ? "" : "none";
    $("#dbFeeders").style.display = b.dataset.d === "feeders" ? "" : "none";
    b.dataset.d === "homes" ? searchHomes() : renderFeederTable();
  });
}

/* ---------------------------------------------------------------- failures + dataset */
async function inject(type) {
  const body = {type};
  if (type === "comms_outage" && $("#fNb").value) body.neighborhood = $("#fNb").value;
  if (type === "thermostat_cloud" && $("#fBrand").value) body.brand = $("#fBrand").value;
  const before = P.summary;
  P = await post("/api/failure", body); renderAll();
  toast(`${P.trace[0].detail} → relief ${f1(before.first_mw)} → ${f1(P.summary.first_mw)} MW`);
}
function initFailures() {
  $$("[data-f]").forEach(b => b.onclick = () => inject(b.dataset.f));
  $("#randFail").onclick = () => { const t = ["comms_outage", "thermostat_cloud", "battery_api", "overrides", "dropouts", "hotter"]; inject(t[Math.floor(Math.random() * t.length)]); };
  $("#resetFail").onclick = async () => { P = await post("/api/failures/reset"); renderAll(); toast("Failures cleared"); };
  $("#regen").onclick = async () => {
    $("#regen").textContent = "⏳ Generating…";
    const seed = $("#dsSeed").value ? +$("#dsSeed").value : null;
    P = await post("/api/dataset", {n_homes: +$("#dsN").value, enrollment: +$("#dsE").value / 100, seed});
    GEO = await api("/api/geo"); renderAll(); $("#regen").textContent = "♻ Generate new dataset";
    toast(`New dataset: ${f0(P.dataset.homes)} homes (seed ${P.dataset.seed})`);
  };
}
function renderTrace() {
  $("#trace").innerHTML = [...decisions, ...P.trace].map(t =>
    `<li class="${/⚠|↻|👤|Homeowner|New dataset|cleared/.test(t.step) ? "ev" : ""}"><b>${t.step}</b>${t.detail}</li>`).join("");
  $("#activeFails").innerHTML = P.failures.length ? P.failures.map(f => `<li>${f}</li>`).join("") : "<li class='muted'>None — all systems nominal</li>";
}

/* ---------------------------------------------------------------- custom simulation (corner widget) */
function initSim() {
  const fmt = {sN: f0, sE: v => v + "%", sB: v => v + "%", sV: v => v + "%", sT: v => v + "%"}, lab = {sN: "vN", sE: "vE", sB: "vB", sV: "vV", sT: "vT"};
  Object.keys(fmt).forEach(s => { const upd = () => $("#" + lab[s]).textContent = fmt[s](+$("#" + s).value); $("#" + s).oninput = upd; upd(); });
  L.DomEvent.disableClickPropagation($("#simBox")); L.DomEvent.disableScrollPropagation($("#simBox"));
  $("#simToggle").onclick = () => {
    const open = $("#simBox").classList.toggle("open"); $("#simToggle").setAttribute("aria-expanded", open);
  };
  $("#simGo").onclick = async () => {
    const b = $("#simGo"); b.textContent = "⏳ Generating…"; b.disabled = true;
    try {
      P = await post("/api/dataset", {seed: null, n_homes: +$("#sN").value, enrollment: +$("#sE").value / 100,
        battery_share: +$("#sB").value / 100, ev_share: +$("#sV").value / 100, thermostat_share: +$("#sT").value / 100});
      GEO = await api("/api/geo"); K = 0; goDistrict(); renderAll();
      toast(`New neighborhood: ${f0(P.dataset.homes)} homes · ${f0(P.dataset.enrolled)} signed up · ${f0(P.dataset.batteries)} batteries`);
    } catch (e) { toast("Could not generate: " + e.message); }
    b.textContent = "🏘 Generate neighborhood"; b.disabled = false;
  };
}

/* ---------------------------------------------------------------- orchestration */
function setK(k) { K = k; renderAll(false); }
function renderAll(full = true) {
  const sl = $("#tSlider"); sl.max = P.intervals.length - 1; sl.value = K;
  $("#tLabel").textContent = IV().label;
  $("#tInfo").textContent = `elapsed ${f1(IV().elapsed, 2)} h of ${P.request.duration_h} h · plan computed in ${P.ms} ms`;
  $("#dsChip").textContent = `${f0(P.dataset.homes)} homes · seed ${P.dataset.seed} · ${Math.round(P.dataset.enrollment * 100)}% enrolled`;
  renderKPIs(); renderBanner(); renderMap(); renderDispatchTable(); renderTrace();
  if (full) renderChart(); else if (chart) chart.update();
  if ($("#tab-db").classList.contains("on")) { $("#dbFeeders").style.display === "none" ? searchHomes() : renderFeederTable(); }
}
async function init() {
  [GEO, TPL, P] = await Promise.all([api("/api/geo"), api("/api/templates"), api("/api/plan")]);
  const F = $("#reqForm");
  for (let h = 12; h <= 22; h += .25) F.start_hour.insertAdjacentHTML("beforeend", `<option value="${h}">${hr(h)}</option>`);
  $("#areaBox").innerHTML = TPL.neighborhoods.map(n => `<label><input type="checkbox" value="${n}"> ${n}</label>`).join("");
  TPL.neighborhoods.forEach(n => { $("#qNb").insertAdjacentHTML("beforeend", `<option>${n}</option>`); $("#fNb").insertAdjacentHTML("beforeend", `<option>${n}</option>`); });
  TPL.brands.forEach(b => $("#fBrand").insertAdjacentHTML("beforeend", `<option>${b}</option>`));
  activeTpl = Object.keys(TPL.templates).find(k => TPL.templates[k].label === P.request.label) || "custom";
  fillForm(activeTpl !== "custom" ? TPL.templates[activeTpl] : P.request);
  renderTemplates();
  F.onsubmit = e => { e.preventDefault(); activeTpl = "custom"; renderTemplates(); runPlan(); };
  $$(".tabs button").forEach(b => b.onclick = () => {
    $$(".tabs button").forEach(x => x.classList.toggle("on", x === b));
    $$(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "db") searchHomes();
  });
  $("#tSlider").oninput = e => setK(+e.target.value);
  initMap(); initDb(); initFailures(); initSim();
  $("#sN").value = Math.min(100000, P.dataset.homes); $("#sE").value = Math.round(P.dataset.enrollment * 100 / 5) * 5; $("#sN").oninput(); $("#sE").oninput();
  map.fitBounds(L.latLngBounds(GEO.neighborhoods.flatMap(n => n.bounds)), {padding: [10, 10]});
  renderAll();
}
init().catch(e => { document.body.insertAdjacentHTML("afterbegin", `<pre style="color:red;padding:10px">${e}</pre>`); });
