/* Football Intelligence – dashboard mobile. Nessuna dipendenza esterna. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p, d = 0) => (p == null ? "n.d." : (p * 100).toFixed(d) + "%");
const sgn = (x) => (x == null ? "n.d." : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");
const state = { report: null, history: null, filter: "all", q: "" };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage non disponibile */ } },
};

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).hint || `HTTP ${r.status}`);
  return r.json();
}

function relPill(level) {
  const cls = level === "Alta" ? "ok" : level === "Media" ? "warn" : "bad";
  return `<span class="pill ${cls}">Affidabilità ${esc(level || "n.d.")}</span>`;
}
function riskPill(level) {
  const cls = level === "Basso" ? "ok" : level === "Medio" ? "warn" : "bad";
  return level ? `<span class="pill ${cls}">Rischio ${esc(level)}</span>` : "";
}

/* ---------------------------------------------------------------- OGGI */
function renderToday(r) {
  const passed = r.agents.find((a) => a.agent_id === "A12")?.summary?.selections_qc_passed ?? 0;
  const regions = r.regions.map((g) => `<tr><td>${esc(g.region)}</td><td class="r">${g.matches_today}</td></tr>`).join("");
  const comps = r.competitions_monitored.map(([c, n]) => `${esc(c)} (${n})`).join(" · ");
  const best = r.best_opportunities.length
    ? r.best_opportunities.map(selCard).join("")
    : `<div class="empty">Nessuna selezione valutabile oggi.</div>`;
  const conform = r.best_opportunities.filter((s) => s.qc_passed).length;
  $("#tab-today").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="v">${r.matches_analyzed}</div><div class="l">partite analizzate (pre-match)</div></div>
      <div class="kpi"><div class="v">${r.matches_found}</div><div class="l">partite trovate oggi</div></div>
      <div class="kpi"><div class="v">${r.competitions_monitored.length}</div><div class="l">competizioni</div></div>
      <div class="kpi"><div class="v">${r.agents_active}/${r.agents_total}</div><div class="l">agenti attivi</div></div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="v">${passed}</div><div class="l">selezioni conformi al controllo qualità</div></div>
      <div class="kpi"><div class="v">${r.main_accumulator?.status === "ok" ? r.main_accumulator.legs_count : "—"}</div><div class="l">eventi nella multipla</div></div>
    </div>
    ${(r.changes_since_previous || []).length ? `<div class="card"><h3>Variazioni dall'ultima analisi</h3><ul class="clean">${r.changes_since_previous.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
    <h2>Migliori opportunità</h2>
    ${conform === 0 && r.best_opportunities.length ? `<div class="empty">Nessuna selezione supera tutti i controlli di qualità. Quelle sotto sono mostrate <b>solo a scopo informativo</b>, con il motivo dell'esclusione.</div>` : ""}
    ${best}
    <h2>Copertura geografica</h2>
    <div class="card scroll"><table><tr><th>Area</th><th class="r">Partite oggi</th></tr>${regions}</table>
    <details><summary>Competizioni (${r.competitions_monitored.length})</summary><p class="small muted">${comps}</p></details></div>
    <h2>Fonti dati</h2>
    <div class="card small">${Object.entries(r.data_providers).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span class="muted">${esc(v)}</span></div>`).join("")}
    <p class="muted">Richieste HTTP: ${r.http_stats.requests} · da cache: ${r.http_stats.cache_hits} · errori: ${r.http_stats.errors}</p></div>
    <details><summary>Partite escluse (${r.excluded_matches.length})</summary>
      <div class="card scroll"><table><tr><th>Ora</th><th>Partita</th><th>Motivo</th></tr>
      ${r.excluded_matches.map((x) => `<tr><td>${esc(x.kickoff_local.slice(11))}</td><td>${esc(x.match)}<div class="muted small">${esc(x.competition)}</div></td><td>${esc(x.reason)}</td></tr>`).join("")}
      </table></div></details>
    <h2>Avvertenze</h2><div class="card small"><ul class="clean">${r.disclaimer.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></div>`;
}

function selCard(s) {
  const status = s.qc_passed ? `<span class="pill ok">Conforme</span>` : `<span class="pill bad">Non consigliata</span>`;
  return `<div class="card tap" data-sel="${esc(s.event_id)}|${esc(s.key)}">
    <div class="row"><h3>${esc(s.match)}</h3><span class="muted small num">${esc(s.kickoff_local.slice(11))}</span></div>
    <div class="muted small">${esc(s.competition)}</div>
    <div class="row" style="margin-top:8px"><span><b>${esc(s.label)}</b> @ <b class="num">${s.odds}</b> <span class="muted small">${esc(s.bookmaker)}</span></span>
      <span class="pill acc num">AI ${s.score.final}</span></div>
    <div class="row small" style="margin-top:6px"><span>Prob. stimata <b class="num">${pct(s.p_est)}</b> <span class="muted">(${pct(s.p_est_range_90[0])}–${pct(s.p_est_range_90[1])})</span></span><span class="num">EV ${sgn(s.ev)}</span></div>
    <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">${status}${relPill(s.reliability_level)}${riskPill(s.risk_level)}</div>
    ${!s.qc_passed ? `<div class="small muted" style="margin-top:6px">Motivo: ${esc(s.qc_failures.slice(0, 2).join("; "))}</div>` : ""}
  </div>`;
}

/* ------------------------------------------------------------ MULTIPLE */
function accBlock(title, a, stake) {
  if (!a) return `<h2>${title}</h2><div class="empty">Non calcolata.</div>`;
  if (a.status !== "ok") return `<h2>${title}</h2><div class="empty"><b>Non disponibile.</b> ${esc(a.reason)}</div>`;
  const legs = a.legs.map((l) => `<div class="leg tap" data-sel="${esc(l.event_id)}|${esc(l.key)}">
      <div><b>${esc(l.match)}</b><div class="muted small">${esc(l.competition)} · ${esc(l.kickoff_local.slice(11))}</div>
      <div class="small">${esc(l.label)} · p ${pct(l.p_est)} · AI ${l.score.final} · ${esc(l.bookmaker)}</div></div>
      <div class="big num">${l.odds}</div></div>`).join("");
  return `<h2>${title}</h2><div class="card">${legs}
    <div class="stats">
      <div><b>${a.total_odds}</b><span>quota totale</span></div>
      <div><b>${a.potential_gross_return_eur.toFixed(2)} €</b><span>ritorno lordo potenziale su ${stake} €</span></div>
      <div><b>${pct(a.joint_probability, 1)}</b><span>probabilità congiunta stimata (${pct(a.joint_probability_range_90[0], 1)}–${pct(a.joint_probability_range_90[1], 1)})</span></div>
      <div><b>${pct(a.independent_product, 1)}</b><span>prodotto semplice (indipendenza)</span></div>
      <div><b>${sgn(a.expected_value)}</b><span>valore atteso stimato</span></div>
      <div><b>${pct(1 - a.joint_probability, 0)}</b><span>probabilità di perdere la puntata</span></div>
    </div>
    <p class="small muted">${esc(a.selection_rule)}. ${esc(a.probability_method)}. ${esc(a.push_note || "")}</p>
    ${a.comparison_with_main ? `<p class="small">${esc(a.comparison_with_main.summary)} Gambe in comune: ${a.comparison_with_main.shared_legs}.</p>` : ""}
  </div>`;
}

function renderAccas(r) {
  const singles = r.top_singles.length ? r.top_singles.map(selCard).join("") : `<div class="empty">${esc(r.top_singles_note || "Nessuna selezione conforme.")}</div>`;
  const qc = (r.qc_failure_stats || []).map(([k, n]) => `<tr><td>${esc(k)}</td><td class="r">${n}</td></tr>`).join("");
  $("#tab-accas").innerHTML = accBlock("Multipla principale", r.main_accumulator, r.stake_eur)
    + accBlock("Multipla alternativa (prudente)", r.prudent_accumulator, r.stake_eur)
    + `<h2>5 migliori singole</h2>${singles}${r.top_singles_note ? `<p class="small muted">${esc(r.top_singles_note)}</p>` : ""}
    <h2>Perché le selezioni vengono scartate</h2><div class="card scroll"><table><tr><th>Controllo</th><th class="r">Selezioni</th></tr>${qc}</table>
    <p class="small muted">Soglie: punteggio ≥ ${r.thresholds.min_selection_score}, EV ≥ ${sgn(r.thresholds.min_ev)}, quota ≥ ${r.thresholds.min_leg_odds}, quote aggiornate da ≤ ${r.thresholds.max_odds_age_minutes} min. I criteri non vengono mai abbassati per raggiungere 5 eventi.</p></div>`;
}

/* ------------------------------------------------------------- PARTITE */
function renderMatches(r) {
  const cards = r.match_cards.filter((c) => {
    if (state.filter === "ok" && !c.recommended) return false;
    if (state.filter === "high" && c.reliability_level !== "Alta") return false;
    if (state.q) {
      const t = (c.match + " " + c.competition).toLowerCase();
      if (!t.includes(state.q)) return false;
    }
    return true;
  });
  const f = (k, l) => `<button data-filter="${k}" class="${state.filter === k ? "on" : ""}">${l}</button>`;
  $("#tab-matches").innerHTML = `<input type="search" id="q" placeholder="Cerca squadra o competizione" value="${esc(state.q)}">
    <div class="filters">${f("all", "Tutte")}${f("ok", "Con selezione conforme")}${f("high", "Affidabilità alta")}</div>
    <p class="small muted">${cards.length} partite</p>
    ${cards.map((c) => {
      const b = c.recommended || c.best_candidate;
      return `<div class="card tap" data-match="${esc(c.event_id)}">
        <div class="row"><h3>${esc(c.match)}</h3><span class="muted small num">${esc(c.kickoff_local.slice(11))}</span></div>
        <div class="muted small">${esc(c.competition)}</div>
        ${b ? `<div class="row small" style="margin-top:6px"><span>${c.recommended ? "Consigliata" : "Migliore (non conforme)"}: <b>${esc(b.label)}</b> @ ${b.odds}</span><span class="pill acc">AI ${b.score.final}</span></div>`
            : `<div class="small muted" style="margin-top:6px">Nessuna quota/modello disponibile</div>`}
        <div style="margin-top:6px">${relPill(c.reliability_level)}</div></div>`;
    }).join("")}`;
  $("#q").addEventListener("input", (e) => { state.q = e.target.value.toLowerCase(); renderMatches(r); $("#q").focus(); });
}

function matchDetail(eid, focusKey) {
  const c = state.report.match_cards.find((m) => m.event_id === eid);
  if (!c) return;
  const s = c.selections.find((x) => x.key === focusKey) || c.recommended || c.best_candidate;
  let html = `<h2 style="margin-top:0">${esc(c.match)}</h2><div class="muted small">${esc(c.competition)} · ${esc(c.kickoff_local)} (ora italiana)</div>
    <div style="margin:8px 0">${relPill(c.reliability_level)} <span class="pill">qualità dati ${c.data_quality ?? "n.d."}</span></div>`;
  if (c.data_notes.length) html += `<p class="small muted">${esc(c.data_notes.join(" · "))}</p>`;
  if (s) {
    html += `<div class="card"><div class="row"><span>Mercato: <b>${esc(s.label)}</b></span><span class="big num">${s.odds}</span></div>
      <div class="small muted src">Quota ${esc(s.bookmaker)} · aggiornata ${esc(s.odds_fetched_at)} · <a href="${esc(s.odds_source)}" target="_blank" rel="noopener">fonte</a>${s.best_bookmaker !== s.bookmaker ? ` · migliore ${s.best_odds} (${esc(s.best_bookmaker)})` : ""}</div>
      <div class="stats"><div><b>${pct(s.p_est)}</b><span>prob. stimata (90%: ${pct(s.p_est_range_90[0])}–${pct(s.p_est_range_90[1])})</span></div>
        <div><b>${s.score.final}</b><span>punteggio AI (base ${s.score.base} − contrarian ${s.score.contrarian_penalty})</span></div>
        <div><b>${pct(s.p_model)}</b><span>modello statistico</span></div><div><b>${pct(s.p_market_fair)}</b><span>mercato (senza margine)</span></div>
        <div><b>${sgn(s.ev)}</b><span>Expected Value</span></div><div><b>${esc(s.risk_level || "n.d.")}</b><span>livello di rischio</span></div></div>
      <div style="margin-top:8px">${s.qc_passed ? '<span class="pill ok">Supera il controllo qualità</span>' : `<span class="pill bad">Non conforme</span><ul class="clean small">${s.qc_failures.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`}</div></div>
      <h3>Punteggio (0–100)</h3>${s.score.components.map((k) => `<div class="comp"><div class="row"><span>${esc(k.name)} <span class="muted">(${esc(k.agent)})</span></span><span class="num">${k.points.toFixed(1)}/${k.max}</span></div>
        <div class="bar"><i style="width:${(100 * k.points / k.max).toFixed(0)}%"></i></div><div class="small muted">${esc(k.explanation)}</div></div>`).join("")}
      <h3 style="margin-top:14px">Motivazione</h3><ul class="clean small">${s.rationale.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <h3>Rischi individuati (Contrarian)</h3>${s.risks.length ? `<ul class="clean small">${s.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="small muted">Nessuna obiezione rilevante.</p>'}`;
  }
  html += `<h3 style="margin-top:14px">Tutti i mercati valutati</h3><div class="scroll"><table><tr><th>Mercato</th><th class="r">Quota</th><th class="r">p</th><th class="r">EV</th><th class="r">AI</th></tr>
    ${c.selections.map((x) => `<tr class="tap" data-sel="${esc(x.event_id)}|${esc(x.key)}"><td>${esc(x.label)} ${x.qc_passed ? "✓" : ""}</td><td class="r">${x.odds}</td><td class="r">${pct(x.p_est)}</td><td class="r">${sgn(x.ev)}</td><td class="r">${x.score.final}</td></tr>`).join("")}
    </table></div>
    <p class="small muted">Un punteggio alto non è una garanzia di vincita.</p>`;
  $("#detail-body").innerHTML = html;
  const dlg = $("#detail");
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

/* -------------------------------------------------------------- AGENTI */
function renderAgents(r) {
  const tl = r.timeline || [];
  const total = Math.max(1, ...tl.map((t) => t.end_s));
  $("#tab-agents").innerHTML = `<p class="small muted" style="margin-top:14px">I 12 agenti sono moduli indipendenti: ognuno scarica i propri dati e produce un report JSON. A02–A08 lavorano in parallelo; A10 e A11 in parallelo dopo A09.</p>
    ${r.agents.map((a) => {
      const t = tl.find((x) => x.agent_id === a.agent_id);
      const cls = a.status === "ok" ? "ok" : a.status === "partial" ? "warn" : "bad";
      const summ = Object.entries(a.summary || {}).filter(([, v]) => typeof v !== "object" || v === null).map(([k, v]) => `<div class="row small"><span class="muted">${esc(k)}</span><span>${esc(v)}</span></div>`).join("");
      return `<div class="card"><div class="row"><h3>${esc(a.agent_id)} · ${esc(a.agent_name)}</h3><span class="pill ${cls}">${esc(a.status)}</span></div>
        <p class="small muted" style="margin:4px 0">${esc(a.description)}</p>
        ${t ? `<div class="bar" title="esecuzione ${t.start_s}s → ${t.end_s}s"><i style="margin-left:${(100 * t.start_s / total).toFixed(1)}%;width:${Math.max(1, 100 * (t.end_s - t.start_s) / total).toFixed(1)}%"></i></div>` : ""}
        <div class="small muted">durata ${a.duration_s}s · fonti ${a.sources_count} · dipende da ${esc(a.depends_on.join(", ") || "—")}</div>
        ${summ}
        ${a.warnings.length ? `<details><summary>Avvisi (${a.warnings.length})</summary><ul class="clean small">${a.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
        ${a.errors.length ? `<details><summary>Errori (${a.errors.length})</summary><ul class="clean small">${a.errors.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
        <details data-agent="${esc(a.agent_id)}"><summary>Report JSON completo</summary><pre>caricamento…</pre></details></div>`;
    }).join("")}`;
  document.querySelectorAll("details[data-agent]").forEach((d) => d.addEventListener("toggle", async () => {
    if (!d.open || d.dataset.loaded) return;
    d.dataset.loaded = "1";
    try {
      const j = await getJSON(`/api/agents/${r.analysis_date}/${d.dataset.agent}`);
      $("pre", d).textContent = JSON.stringify(j, null, 1).slice(0, 200000);
    } catch (e) { $("pre", d).textContent = "Non disponibile: " + e.message; }
  }));
}

/* ------------------------------------------------------------- STORICO */
function renderHistory(h) {
  const el = $("#tab-history");
  if (!h || !Object.keys(h.days || {}).length) { el.innerHTML = `<div class="empty" style="margin-top:14px">Nessuno storico: esegui <code>python -m fbi verify</code> dopo le partite.</div>`; return; }
  const s = h.stats || {};
  const cal = Object.entries(s.calibration || {}).map(([b, x]) => `<tr><td>${esc(b)}</td><td class="r">${x.n}</td><td class="r">${pct(x.avg_p)}</td><td class="r">${pct(x.hit_rate)}</td></tr>`).join("");
  const accRow = (k, l) => { const a = s[k] || {}; return `<tr><td>${l}</td><td class="r">${a.settled ?? 0}</td><td class="r">${a.won ?? 0}</td><td class="r">${a.simulated_roi == null ? "n.d." : sgn(a.simulated_roi)}</td></tr>`; };
  const days = Object.entries(h.days).sort((a, b) => b[0].localeCompare(a[0])).map(([d, e]) => {
    const acc = (a, t) => !a ? "" : a.status !== "ok" ? `<div class="small muted">${t}: non costruita</div>` :
      `<div class="small">${t}: quota ${a.total_odds} · ${a.outcome ? `<span class="pill ${a.outcome === "won" ? "ok" : a.outcome === "lost" ? "bad" : "warn"}">${esc(a.outcome)}</span>` : '<span class="pill">da verificare</span>'}
       <ul class="clean">${a.legs.map((l) => `<li>${esc(l.match)} – ${esc(l.label)} @ ${l.odds} ${l.final_score ? `(${esc(l.final_score)})` : ""} ${l.outcome ? `<b>${esc(l.outcome)}</b>` : ""}</li>`).join("")}</ul></div>`;
    return `<div class="card"><div class="row"><h3>${esc(d)}</h3><span class="small muted">${e.verified ? "verificata" : "in attesa"}</span></div>
      ${acc(e.main, "Principale")}${acc(e.prudent, "Prudente")}
      ${e.singles?.length ? `<div class="small">Singole: ${e.singles.map((l) => `${esc(l.match)} ${esc(l.label)} ${l.outcome ? `<b>${esc(l.outcome)}</b>` : ""}`).join(" · ")}</div>` : ""}</div>`;
  }).join("");
  el.innerHTML = `<div class="kpis">
      <div class="kpi"><div class="v">${s.legs_settled ?? 0}</div><div class="l">selezioni verificate</div></div>
      <div class="kpi"><div class="v">${s.hit_rate == null ? "—" : pct(s.hit_rate)}</div><div class="l">tasso di successo</div></div>
      <div class="kpi"><div class="v">${s.avg_predicted_prob == null ? "—" : pct(s.avg_predicted_prob)}</div><div class="l">probabilità media prevista</div></div>
      <div class="kpi"><div class="v">${s.brier_score ?? "—"}</div><div class="l">Brier score (più basso = meglio)</div></div></div>
    <h2>Multiple (P/L simulato)</h2><div class="card scroll"><table><tr><th></th><th class="r">Verificate</th><th class="r">Vinte</th><th class="r">ROI simulato</th></tr>${accRow("main", "Principale")}${accRow("prudent", "Prudente")}</table>
    <p class="small muted">${esc(s.note || "")}</p></div>
    ${cal ? `<h2>Calibrazione</h2><div class="card scroll"><table><tr><th>Fascia prob.</th><th class="r">N</th><th class="r">Prevista</th><th class="r">Reale</th></tr>${cal}</table></div>` : ""}
    <h2>Giornate</h2>${days}`;
}

/* --------------------------------------------------------------- SETUP */
function renderAll() {
  const r = state.report;
  $("#meta").textContent = `Analisi del ${r.analysis_date} · ${r.generated_at_local.slice(11)} (${r.timezone})${r.run_type === "refresh" ? " · aggiornamento" : ""}`;
  renderToday(r); renderAccas(r); renderMatches(r); renderAgents(r); renderHistory(state.history);
}

async function load() {
  $("#meta").textContent = "Caricamento…";
  try {
    state.report = await getJSON("/api/latest");
    state.history = await getJSON("/api/history").catch(() => null);
    renderAll();
  } catch (e) {
    $("#meta").textContent = "Nessun report disponibile";
    $("#tab-today").innerHTML = `<div class="empty" style="margin-top:14px">${esc(e.message)}<br>Esegui <code>python -m fbi run</code> sul server.</div>`;
  }
  try {
    const st = await getJSON("/api/status");
    $("#btn-run").hidden = !st.actions_enabled;
    if (st.running) { $("#meta").textContent += " · analisi in corso…"; setTimeout(load, 8000); }
  } catch { /* status opzionale */ }
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b === tab));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + tab.dataset.tab));
    store.set("fbi-tab", tab.dataset.tab);
    window.scrollTo(0, 0);
    return;
  }
  const f = e.target.closest("[data-filter]");
  if (f) { state.filter = f.dataset.filter; renderMatches(state.report); return; }
  const sel = e.target.closest("[data-sel]");
  if (sel) { const [eid, key] = sel.dataset.sel.split(/\|(.+)/); matchDetail(eid, key); return; }
  const m = e.target.closest("[data-match]");
  if (m) matchDetail(m.dataset.match);
});

$("#btn-reload").addEventListener("click", load);
$("#btn-run").addEventListener("click", async () => {
  let token = store.get("fbi-token");
  if (!token) { token = prompt("Token dashboard (DASHBOARD_TOKEN)"); if (!token) return; store.set("fbi-token", token); }
  const kind = confirm("OK = refresh quote/formazioni · Annulla = nuova analisi completa") ? "refresh" : "run";
  const r = await fetch(`/api/${kind}`, { method: "POST", headers: { "X-Token": token } });
  const j = await r.json().catch(() => ({}));
  if (r.status === 403) store.set("fbi-token", "");
  alert(r.ok ? "Analisi avviata: la dashboard si aggiornerà da sola." : j.error || "Errore");
  if (r.ok) setTimeout(load, 8000);
});

const saved = store.get("fbi-tab");
if (saved) $(`[data-tab="${saved}"]`)?.click();
load();
