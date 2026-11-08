"""Storico delle previsioni, verifica dei risultati a fine giornata e statistiche di accuratezza.

Regola anti-ritocco: una volta iniziata una partita della multipla del giorno, la
previsione salvata è "bloccata" e gli aggiornamenti successivi non la sovrascrivono.
Il P/L è SIMULATO (nessuna scommessa reale).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import Settings
from .http_client import HttpError
from .providers import espn as E

log = logging.getLogger("fbi.history")


def _path(s: Settings):
    return s.data_dir / "history.json"


def load(s: Settings) -> dict:
    try:
        return json.loads(_path(s).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": {}, "stats": {}}


def save(s: Settings, h: dict) -> None:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(s).with_suffix(".tmp")
    tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_path(s))


LEG_FIELDS = ("event_id", "match", "competition", "kickoff_utc", "kickoff_local", "key", "label", "odds", "bookmaker", "p_est", "ev")


def _slim(acc: dict | None) -> dict | None:
    if not acc:
        return None
    out = {k: acc.get(k) for k in ("status", "reason", "total_odds", "stake_eur", "potential_gross_return_eur", "joint_probability", "expected_value")}
    out["legs"] = [{k: l.get(k) for k in LEG_FIELDS} | {"score": l["score"]["final"]} for l in acc.get("legs", [])]
    return out


def _started(entry: dict, now: datetime) -> bool:
    for acc in ("main", "prudent"):
        for l in (entry.get(acc) or {}).get("legs", []):
            if E.parse_dt(l["kickoff_utc"]) <= now:
                return True
    return False


def record_predictions(s: Settings, report: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    h = load(s)
    day = report["analysis_date"] + ("" if report.get("horizon_days", 1) == 1 else "_settimana")
    prev = h["days"].get(day)
    if prev and (prev.get("verified") or _started(prev, now)):
        log.info("previsione del %s già bloccata (partite iniziate): non sovrascritta", day)
        return
    h["days"][day] = {
        "run_id": report["run_id"], "generated_at_local": report["generated_at_local"],
        "matches_analyzed": report["matches_analyzed"],
        "main": _slim(report.get("main_accumulator")), "prudent": _slim(report.get("prudent_accumulator")),
        "singles": [{k: l.get(k) for k in LEG_FIELDS} | {"score": l["score"]["final"]} for l in report.get("top_singles", [])],
        "verified": False,
    }
    save(s, h)


def settle(key: str, hg: int, ag: int) -> str:
    """Esito di una selezione: 'won' | 'lost' | 'push'."""
    parts = key.split("|")
    m, sel = parts[0], parts[1]
    if m == "1X2":
        ok = {"HOME": hg > ag, "DRAW": hg == ag, "AWAY": hg < ag}[sel]
    elif m == "DC":
        ok = {"1X": hg >= ag, "X2": ag >= hg, "12": hg != ag}[sel]
    elif m == "OU":
        line = float(parts[2])
        ok = (hg + ag > line) if sel == "OVER" else (hg + ag < line)
    elif m == "BTTS":
        ok = (hg > 0 and ag > 0) == (sel == "YES")
    elif m == "DNB":
        if hg == ag:
            return "push"
        ok = (hg > ag) if sel == "HOME" else (ag > hg)
    else:
        raise ValueError(f"mercato sconosciuto {key}")
    return "won" if ok else "lost"


def settle_accumulator(acc: dict) -> None:
    legs = acc.get("legs", [])
    if not legs or acc.get("status") != "ok":
        return
    outcomes = [l.get("outcome") for l in legs]
    if "lost" in outcomes:
        acc["outcome"], acc["simulated_return_eur"] = "lost", 0.0
    elif all(o in ("won", "push", "void") for o in outcomes):
        odds = 1.0
        for l in legs:
            odds *= l["odds"] if l["outcome"] == "won" else 1.0
        acc["outcome"], acc["simulated_return_eur"] = "won", round(acc["stake_eur"] * odds, 2)
    else:
        acc["outcome"] = "pending"


def verify(ctx, days: list[str] | None = None) -> dict:
    s = ctx.settings
    h = load(s)
    for day, entry in sorted(h["days"].items()):
        if days and day not in days:
            continue
        if entry.get("verified"):
            continue
        all_done = True
        legs = [l for acc in ("main", "prudent") for l in (entry.get(acc) or {}).get("legs", [])] + entry.get("singles", [])
        for l in legs:
            if l.get("outcome") in ("won", "lost", "push", "void"):
                continue
            try:
                f = ctx.espn.summary(l["event_id"], ttl=300)
            except HttpError as e:
                log.warning("verifica %s fallita: %s", l["event_id"], e)
                all_done = False
                continue
            st = E.summary_status(f)
            name = st["name"] or ""
            if any(x in name for x in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED")):
                l["outcome"] = "void"  # rinviata/annullata: gamba nulla (quota 1.00)
                l["result_source"], l["result_fetched_at"] = f.source, f.fetched_at
            elif st["completed"] and "home" in st["score"] and "away" in st["score"]:
                l["final_score"] = f"{st['score']['home']}-{st['score']['away']}"
                l["outcome"] = settle(l["key"], st["score"]["home"], st["score"]["away"])
                l["result_source"], l["result_fetched_at"] = f.source, f.fetched_at
            else:
                all_done = False
        for acc in ("main", "prudent"):
            if entry.get(acc):
                settle_accumulator(entry[acc])
        entry["verified"] = all_done
    h["stats"] = accuracy_stats(h)
    save(s, h)
    return h


def accuracy_stats(h: dict) -> dict:
    legs = []
    for entry in h["days"].values():
        seen = set()
        for acc in ("main", "prudent"):
            for l in (entry.get(acc) or {}).get("legs", []):
                seen.add((l["event_id"], l["key"]))
                legs.append(l)
        for l in entry.get("singles", []):
            if (l["event_id"], l["key"]) not in seen:
                legs.append(l)
    settled = [l for l in legs if l.get("outcome") in ("won", "lost")]
    n = len(settled)
    stats = {"legs_settled": n, "legs_won": sum(l["outcome"] == "won" for l in settled),
             "legs_push": sum(l.get("outcome") == "push" for l in legs)}
    if n:
        stats["hit_rate"] = round(stats["legs_won"] / n, 3)
        stats["avg_predicted_prob"] = round(sum(l["p_est"] for l in settled) / n, 3)
        stats["brier_score"] = round(sum((l["p_est"] - (l["outcome"] == "won")) ** 2 for l in settled) / n, 4)
        buckets = {}
        for l in settled:
            b = min(9, int(l["p_est"] * 10))
            k = f"{b * 10}-{b * 10 + 10}%"
            x = buckets.setdefault(k, {"n": 0, "won": 0, "avg_p": 0.0})
            x["n"] += 1
            x["won"] += l["outcome"] == "won"
            x["avg_p"] += l["p_est"]
        for x in buckets.values():
            x["hit_rate"] = round(x["won"] / x["n"], 3)
            x["avg_p"] = round(x["avg_p"] / x["n"], 3)
        stats["calibration"] = dict(sorted(buckets.items()))
    for acc in ("main", "prudent"):
        accs = [e[acc] for e in h["days"].values() if e.get(acc) and e[acc].get("outcome") in ("won", "lost")]
        staked = sum(a["stake_eur"] for a in accs)
        ret = sum(a.get("simulated_return_eur", 0) for a in accs)
        stats[acc] = {"settled": len(accs), "won": sum(a["outcome"] == "won" for a in accs),
                      "simulated_staked_eur": round(staked, 2), "simulated_returned_eur": round(ret, 2),
                      "simulated_roi": round((ret - staked) / staked, 3) if staked else None}
    stats["note"] = "P/L simulato: nessuna scommessa reale. Servono centinaia di selezioni prima che le statistiche siano significative."
    return stats
