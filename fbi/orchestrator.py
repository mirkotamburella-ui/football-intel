"""Orchestrazione centralizzata: esegue gli agenti come DAG di dipendenze.

Stadio 1: A01 (scout)            → elenco partite
Stadio 2: A02–A08 in PARALLELO   → analisi indipendenti (ognuno scarica i propri dati)
Stadio 3: A09 (valore)           → richiede quote (A07) e modelli (A08)
Stadio 4: A10 e A11 in PARALLELO → rischio e obiezioni
Stadio 5: A12 (strategist)       → scoring, QC, multiple

Un agente parte appena tutte le sue dipendenze sono concluse. Se un agente fallisce,
quelli che dipendono da lui non vengono eseguiti e il report lo dichiara.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from . import __version__
from .agents.a01_fixture_scout import FixtureScout
from .agents.a02_form import TeamFormAnalyst
from .agents.a03_offdef import OffDefAnalyst
from .agents.a04_h2h import HeadToHeadAnalyst
from .agents.a05_lineup import LineupInjuryScout
from .agents.a06_motivation import MotivationAnalyst
from .agents.a07_odds import OddsIntelligence
from .agents.a08_math import MathematicalPrediction
from .agents.a09_value import ValueBettingAnalyst
from .agents.a10_risk import RiskManager
from .agents.a11_contrarian import ContrarianAnalyst
from .agents.a12_strategist import FinalBettingStrategist
from .accumulator import diversify
from .agents.base import validate_report
from .context import RunContext, utc_iso
from .quality import qc_report

log = logging.getLogger("fbi.orchestrator")

AGENT_CLASSES = [FixtureScout, TeamFormAnalyst, OffDefAnalyst, HeadToHeadAnalyst, LineupInjuryScout, MotivationAnalyst,
                 OddsIntelligence, MathematicalPrediction, ValueBettingAnalyst, RiskManager, ContrarianAnalyst, FinalBettingStrategist]

DISCLAIMER = [
    "Analisi statistica a scopo informativo e di simulazione: il sistema non piazza scommesse e non è collegato a operatori.",
    "Le probabilità sono stime con incertezza: è possibile perdere l'intera puntata. Nessuna strategia garantisce vincite.",
    "Budget massimo di riferimento 10 €. Non aumentare la puntata dopo una perdita e non inseguire le perdite (niente raddoppi/martingala).",
    "Gioca solo presso operatori autorizzati ADM, se maggiorenne. Se il gioco smette di essere un divertimento: Telefono Verde Nazionale 800 558822 (ISS).",
]


def run_dag(ctx: RunContext, agent_classes=AGENT_CLASSES, max_parallel: int = 8) -> tuple[dict, list[dict]]:
    agents = {cls.id: cls() for cls in agent_classes}
    reports: dict[str, dict] = {}
    timeline: list[dict] = []
    pending = dict(agents)
    running = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="agent") as pool:
        while pending or running:
            for aid, ag in list(pending.items()):
                deps = ag.depends_on
                if any(d in reports and reports[d]["status"] == "failed" for d in deps) or any(d not in agents for d in deps):
                    reports[aid] = _skipped(ctx, ag, "dipendenza fallita o mancante")
                    del pending[aid]
                elif all(d in reports for d in deps):
                    inputs = {d: reports[d] for d in deps}
                    log.info("avvio %s %s", aid, ag.name)
                    running[pool.submit(ag.execute, ctx, inputs)] = (aid, time.time() - t0)
                    del pending[aid]
            if not running:
                if pending:  # dipendenze cicliche o irrisolvibili
                    for aid, ag in pending.items():
                        reports[aid] = _skipped(ctx, ag, "dipendenze non risolvibili")
                    pending.clear()
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                aid, start = running.pop(fut)
                reports[aid] = fut.result()
                problems = validate_report(reports[aid])
                if problems:
                    log.error("report %s non conforme: %s", aid, problems)
                timeline.append({"agent_id": aid, "start_s": round(start, 2), "end_s": round(time.time() - t0, 2), "status": reports[aid]["status"]})
                log.info("fine %s (%s, %.1fs)", aid, reports[aid]["status"], reports[aid]["duration_s"])
    return reports, timeline


def _skipped(ctx, ag, why):
    now = utc_iso(ctx.now_utc)
    return {"agent_id": ag.id, "agent_name": ag.name, "version": ag.version, "description": ag.description, "depends_on": list(ag.depends_on),
            "status": "failed", "started_at": now, "finished_at": now, "duration_s": 0, "summary": {}, "matches": {},
            "sources": [], "warnings": [], "errors": [f"non eseguito: {why}"]}


def build_report(ctx: RunContext, reports: dict, timeline: list[dict], started: float) -> dict:
    a1 = reports.get("A01", {}).get("summary", {})
    a12 = reports.get("A12", {}).get("summary", {})
    cards = reports.get("A12", {}).get("matches", {})
    # migliori opportunità: prima le conformi al QC, poi (solo informative) le migliori per punteggio
    cand = [c["best_candidate"] for c in cards.values() if c.get("best_candidate")]
    best = diversify(sorted(cand, key=lambda x: (-x["qc_passed"], len(x["qc_failures"]), -x["score"]["final"])), ctx.settings.max_per_league)[:10]
    report = {
        "schema_version": 1,
        "system_version": __version__,
        "run_id": ctx.run_id,
        "analysis_date": ctx.today.isoformat(),
        "generated_at_utc": utc_iso(ctx.now_utc),
        "generated_at_local": ctx.now_utc.astimezone(ctx.tz).strftime("%Y-%m-%d %H:%M"),
        "timezone": ctx.settings.timezone,
        "duration_s": round(time.time() - started, 1),
        "agents_active": sum(1 for r in reports.values() if r["status"] != "failed"),
        "agents_total": len(reports),
        "matches_found": a1.get("total_found", 0),
        "matches_analyzed": a1.get("eligible_prematch", 0),
        "matches_excluded": a1.get("excluded", 0),
        "competitions_monitored": a1.get("competitions", []),
        "regions": a1.get("regions", []),
        "excluded_matches": a1.get("excluded_list", []),
        "stake_eur": ctx.settings.stake_eur,
        "thresholds": {k: getattr(ctx.settings, k) for k in ("min_selection_score", "min_ev", "min_leg_odds", "max_odds_age_minutes",
                                                              "min_team_games", "min_data_quality", "acca_extra_leg_min_score",
                                                              "acca_min_joint_prob", "prudent_min_leg_prob", "model_weight_max")},
        "data_providers": {
            "espn": "attivo (pubblico, senza chiave)",
            "the_odds_api": "attivo" if ctx.odds_api.enabled else "non configurato (ODDS_API_KEY)",
            "api_football": "attivo" if ctx.api_football.enabled else "non configurato (API_FOOTBALL_KEY)",
        },
        "http_stats": dict(ctx.http.stats),
        "best_opportunities": best,
        "main_accumulator": a12.get("main_accumulator"),
        "prudent_accumulator": a12.get("prudent_accumulator"),
        "top_singles": a12.get("top_singles", []),
        "top_singles_note": a12.get("top_singles_note"),
        "qc_failure_stats": a12.get("qc_failure_stats", []),
        "match_cards": sorted(cards.values(), key=lambda c: c["kickoff_local"]),
        "agents": [{k: r[k] for k in ("agent_id", "agent_name", "description", "depends_on", "status", "started_at", "finished_at",
                                      "duration_s", "summary", "warnings", "errors")} | {"sources_count": len(r["sources"])}
                   for r in sorted(reports.values(), key=lambda r: r["agent_id"])],
        "timeline": timeline,
        "disclaimer": DISCLAIMER,
    }
    # niente duplicati di dati fra summary dell'A12 e del report
    for ag in report["agents"]:
        if ag["agent_id"] == "A12":
            ag["summary"] = {k: v for k, v in ag["summary"].items() if k in ("selections_evaluated", "selections_qc_passed")}
        if ag["agent_id"] == "A01":
            ag["summary"] = {k: v for k, v in ag["summary"].items() if k not in ("fixtures", "excluded_list")}
    report["quality_checks"] = qc_report(report)
    return report


def save(ctx: RunContext, report: dict, reports: dict) -> Path:
    s = ctx.settings
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    day_dir = s.reports_dir / report["analysis_date"]
    (day_dir / "agents").mkdir(parents=True, exist_ok=True)
    for aid, r in reports.items():
        (day_dir / "agents" / f"{aid}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    path = day_dir / f"report_{report['run_id']}.json"
    blob = json.dumps(report, ensure_ascii=False)
    path.write_text(blob, encoding="utf-8")
    (day_dir / "report.json").write_text(blob, encoding="utf-8")
    (s.data_dir / "latest.json").write_text(blob, encoding="utf-8")
    idx_path = s.reports_dir / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        idx = []
    idx = [i for i in idx if i["run_id"] != report["run_id"]] + [{
        "run_id": report["run_id"], "date": report["analysis_date"], "generated_at_local": report["generated_at_local"],
        "path": str(path.relative_to(s.data_dir)), "matches_analyzed": report["matches_analyzed"],
        "main_status": (report["main_accumulator"] or {}).get("status")}]
    idx_path.write_text(json.dumps(idx[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def run_analysis(ctx: RunContext) -> tuple[dict, dict]:
    """Esegue i 12 agenti e assembla il report finale. Ritorna (report, report_dei_singoli_agenti)."""
    started = time.time()
    reports, timeline = run_dag(ctx)
    return build_report(ctx, reports, timeline, started), reports


def persist(ctx: RunContext, report: dict, reports: dict) -> Path:
    from .history import record_predictions

    path = save(ctx, report, reports)
    record_predictions(ctx.settings, report, ctx.now_utc)
    log.info("report salvato in %s", path)
    return path
