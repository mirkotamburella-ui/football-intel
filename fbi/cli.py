"""Interfaccia a riga di comando.

  python -m fbi run         analisi completa della giornata (12 agenti) + report
  python -m fbi refresh     aggiorna quote e formazioni prima del calcio d'inizio e segnala le variazioni
  python -m fbi verify      verifica i risultati delle previsioni salvate e aggiorna l'accuratezza
  python -m fbi serve       dashboard web mobile (http://localhost:8365)
  python -m fbi daemon      automazione quotidiana: analisi al mattino, refresh orari, verifica serale
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from .config import get_settings
from .context import build_context


def setup_logging(settings, verbose: bool = False) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not root.handlers:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        ch.setLevel(logging.INFO if verbose else logging.WARNING)
        fh = RotatingFileHandler(settings.logs_dir / "fbi.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(ch)
        root.addHandler(fh)


def print_summary(r: dict) -> None:
    p = print
    period = r["analysis_date"] if r.get("horizon_days", 1) == 1 else f"{r['analysis_date']} → {r['date_to']}"
    p(f"\n=== FOOTBALL BETTING INTELLIGENCE — {period} (analisi delle {r['generated_at_local']} {r['timezone']}) ===")
    p(f"Partite trovate: {r['matches_found']} | analizzate pre-match: {r['matches_analyzed']} | escluse: {r['matches_excluded']}")
    p(f"Competizioni: {len(r['competitions_monitored'])} | agenti attivi: {r['agents_active']}/{r['agents_total']} | durata {r['duration_s']}s")
    for name, title in (("main_accumulator", "MULTIPLA PRINCIPALE"), ("prudent_accumulator", "MULTIPLA ALTERNATIVA (prudente)")):
        a = r.get(name) or {}
        p(f"\n--- {title} ---")
        if a.get("status") != "ok":
            p(f"  Non disponibile: {a.get('reason')}")
            continue
        for l in a["legs"]:
            p(f"  {l['kickoff_local'][5:]}  {l['match']:<45.45} {l['label']:<26} @ {l['odds']:<5} p≈{l['p_est']:.0%}  score {l['score']['final']}")
        lo, hi = a["joint_probability_range_90"]
        p(f"  Quota totale {a['total_odds']} | probabilità stimata {a['joint_probability']:.1%} (90%: {lo:.1%}–{hi:.1%}) "
          f"| ritorno lordo potenziale su {a['stake_eur']:.0f} €: {a['potential_gross_return_eur']} €")
    p("\n--- 5 MIGLIORI SINGOLE ---")
    for l in r.get("top_singles", []):
        p(f"  {l['match']:<45.45} {l['label']:<26} @ {l['odds']:<5} p≈{l['p_est']:.0%} EV {l['ev']:+.1%} score {l['score']['final']}")
    if r.get("top_singles_note"):
        p(f"  {r['top_singles_note']}")
    if r.get("quality_checks"):
        p(f"\n!! Controlli qualità: {r['quality_checks']}")
    p("\n" + "\n".join("• " + d for d in r["disclaimer"]))


def diff_reports(old: dict | None, new: dict) -> list[str]:
    if not old:
        return []
    changes = []
    old_legs = {(l["event_id"], l["key"]): l for a in ("main_accumulator", "prudent_accumulator") for l in (old.get(a) or {}).get("legs", [])}
    new_sel = {}
    for c in new.get("match_cards", []):
        for s in c.get("selections", []):
            new_sel[(s["event_id"], s["key"])] = s
    for k, l in old_legs.items():
        n = new_sel.get(k)
        if not n:
            changes.append(f"{l['match']} {l['label']}: non più valutabile (partita iniziata o quota ritirata)")
            continue
        if abs(n["odds"] / l["odds"] - 1) >= 0.05:
            changes.append(f"{l['match']} {l['label']}: quota {l['odds']} → {n['odds']}")
        if l.get("qc_passed") and not n["qc_passed"]:
            changes.append(f"{l['match']} {l['label']}: non supera più il controllo qualità ({'; '.join(n['qc_failures'][:2])})")
    return changes


def cmd_run(args, refresh=False):
    s = get_settings()
    if args.max_matches is not None:
        s.max_matches = args.max_matches
    if getattr(args, "days", None):
        s.horizon_days = max(1, min(14, args.days))
    setup_logging(s, args.verbose)
    old = None
    if refresh:
        try:
            old = json.loads((s.data_dir / "latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        s.cache_ttl_odds_s = 120
        s.cache_ttl_fixtures_s = 120
    ctx = build_context(s, offline=args.offline)
    from .orchestrator import persist, run_analysis

    report, reports = run_analysis(ctx)
    report["run_type"] = "refresh" if refresh else "morning"
    report["changes_since_previous"] = diff_reports(old, report) if refresh else []
    path = persist(ctx, report, reports)
    print_summary(report)
    if report["changes_since_previous"]:
        print("\nVariazioni rispetto all'analisi precedente:\n  - " + "\n  - ".join(report["changes_since_previous"]))
    print(f"\nReport: {path}")
    return 0


def cmd_verify(args):
    s = get_settings()
    setup_logging(s, args.verbose)
    ctx = build_context(s)
    from .history import verify

    h = verify(ctx, args.days)
    print(json.dumps(h["stats"], ensure_ascii=False, indent=1))
    return 0


def cmd_serve(args):
    s = get_settings()
    setup_logging(s, args.verbose)
    from .server import serve

    serve(s, args.host or s.dashboard_host, args.port or s.dashboard_port)
    return 0


def cmd_daemon(args):
    """Ciclo: analisi alle HH:MM locali, refresh ogni N minuti fino all'ultima partita, verifica serale e del mattino."""
    s = get_settings()
    setup_logging(s, args.verbose)
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(s.timezone)
    hh, mm = map(int, args.at.split(":"))
    last_run_day = last_refresh = last_verify = None
    while True:
        now = datetime.now(tz)
        try:
            if now.date() != last_run_day and (now.hour, now.minute) >= (hh, mm):
                ns = argparse.Namespace(max_matches=None, verbose=args.verbose, offline=False)
                verify_ns = argparse.Namespace(days=None, verbose=args.verbose)
                cmd_verify(verify_ns)  # chiude la giornata precedente
                cmd_run(ns)
                last_run_day, last_refresh = now.date(), now
            elif last_run_day == now.date() and last_refresh and now - last_refresh >= timedelta(minutes=args.refresh_every) and now.hour < 23:
                cmd_run(argparse.Namespace(max_matches=None, verbose=args.verbose, offline=False), refresh=True)
                last_refresh = now
            if now.hour == 23 and now.minute >= 45 and last_verify != now.date():
                cmd_verify(argparse.Namespace(days=None, verbose=args.verbose))
                last_verify = now.date()
        except Exception as ex:  # noqa: BLE001 - il demone non deve morire per un errore di rete
            logging.getLogger("fbi.daemon").exception("ciclo fallito: %s", ex)
        time.sleep(60)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fbi", description="Football Betting Intelligence – analisi multi-agent (solo ricerca e simulazione)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "refresh"):
        p = sub.add_parser(name)
        p.add_argument("--max-matches", type=int, default=None, help="limita il numero di partite (test rapidi)")
        p.add_argument("--offline", action="store_true", help="usa solo la cache locale (nessuna richiesta di rete)")
        p.add_argument("--days", type=int, default=None, help="giorni da analizzare a partire da oggi (es. 7 = settimana)")
    p = sub.add_parser("verify")
    p.add_argument("--days", nargs="*", default=None)
    p = sub.add_parser("serve")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p = sub.add_parser("daemon")
    p.add_argument("--at", default="08:00", help="ora locale dell'analisi mattutina (HH:MM)")
    p.add_argument("--refresh-every", type=int, default=60, help="minuti tra un refresh e l'altro")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "refresh":
        return cmd_run(args, refresh=True)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "daemon":
        return cmd_daemon(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
