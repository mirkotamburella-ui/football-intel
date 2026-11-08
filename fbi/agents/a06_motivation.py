"""AGENT 6 – MOTIVATION ANALYST: classifica, obiettivi, zone (titolo/Europa/retrocessione), turnover."""
from __future__ import annotations

import math

from ..context import get_standings, get_summary, src, standings_row
from ..providers import espn as E
from .a01_fixture_scout import fixtures_from
from .base import Agent

POSITIVE = ("champions", "europa", "conference", "promotion", "promoted", "playoff", "play-off", "libertadores", "sudamericana", "title", "qualif", "final", "championship round")
NEGATIVE = ("relegation", "relegated", "descenso", "playout", "play-out")


def zones(table: dict) -> list[dict]:
    """Confini di zona ricavati dalle note di classifica ESPN (rank → descrizione)."""
    out = []
    for r in table.values():
        n = (r.get("note") or "").lower()
        if n:
            kind = "neg" if any(k in n for k in NEGATIVE) else "pos" if any(k in n for k in POSITIVE) else "other"
            out.append({"rank": r["rank"], "kind": kind, "label": r["note"]})
    return sorted(out, key=lambda z: z["rank"])


def team_motivation(row, table) -> dict:
    n = len(table)
    gp = row["gp"]
    total = 2 * (n - 1)
    left = max(total - gp, 0)
    by_rank = sorted(table.values(), key=lambda r: r["rank"])
    pts_at = {r["rank"]: r["pts"] for r in by_rank}
    zs = zones(table)
    scale = max(3.0, left * 0.6)  # punti "recuperabili" realisticamente
    stakes = []
    leader_gap = pts_at.get(1, row["pts"]) - row["pts"]
    if row["rank"] <= 3 and leader_gap <= max(6, left):
        stakes.append(("lotta per il titolo", math.exp(-leader_gap / scale)))
    pos_ranks = [z["rank"] for z in zs if z["kind"] == "pos"]
    neg_ranks = [z["rank"] for z in zs if z["kind"] == "neg"]
    if pos_ranks:
        cut = max(pos_ranks)
        if row["rank"] > cut:
            gap = pts_at.get(cut, 0) - row["pts"]
            stakes.append(("rincorsa zona qualificazione/promozione", math.exp(-max(gap, 0) / scale)))
        else:
            gap = row["pts"] - pts_at.get(cut + 1, 0)
            stakes.append(("difesa zona qualificazione/promozione", math.exp(-max(gap, 0) / scale)))
    if neg_ranks:
        cut = min(neg_ranks)
        if row["rank"] >= cut:
            gap = pts_at.get(cut - 1, 0) - row["pts"]
            stakes.append(("lotta salvezza (in zona retrocessione)", min(1.0, 0.6 + math.exp(-max(gap, 0) / scale))))
        else:
            gap = row["pts"] - pts_at.get(cut, 0)
            stakes.append(("lotta salvezza (sopra la zona)", math.exp(-max(gap, 0) / scale)))
    stake = max([s for _, s in stakes], default=0.3)
    if left == 0:
        stake = 0.1
    return {
        "rank": row["rank"], "points": row["pts"], "games_played": gp, "teams": n,
        "games_left_estimate": left, "games_left_assumption": "girone doppio andata/ritorno",
        "leader_gap": leader_gap, "zones": [z["label"] + f" (fino al {z['rank']}°)" for z in zs][:6],
        "objectives": [name for name, _ in stakes], "stake_index": round(min(stake, 1.0), 2),
        "note": row.get("note"),
    }


class MotivationAnalyst(Agent):
    id = "A06"
    name = "Motivation Analyst"
    depends_on = ("A01",)
    description = "Posizione in classifica, obiettivi stagionali (titolo, Europa, salvezza), indice di motivazione e rischio turnover da calendario ravvicinato."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)

        def one(fx):
            st, f_st = get_standings(ctx, fx.get("league_slug"))
            if f_st:
                self.add_source(src(f_st))
            sm = get_summary(ctx, fx["event_id"])
            l5 = E.parse_last_five(sm) if sm else {}
            if sm:
                self.add_source(src(sm))
            cup = bool(fx.get("is_tournament")) or not st
            out = {"competition_type": "coppa/torneo" if cup else "campionato"}
            for side in ("home", "away"):
                tid = fx[side]["id"]
                row, table = standings_row(st, tid)
                if row and table and len(table) >= 6:
                    m = team_motivation(row, table)
                else:
                    m = {"stake_index": 0.75 if cup else None,
                         "objectives": ["partita di coppa/torneo"] if cup else [],
                         "note": "classifica non disponibile" if not cup else "competizione a eliminazione o fase a gironi"}
                # turnover / stanchezza: giorni dall'ultima partita e impegni di altre competizioni
                games = sorted(l5.get(tid, []), key=lambda g: g["date"] or "")
                if games and games[-1]["date"]:
                    days = (ctx.now_utc - E.parse_dt(games[-1]["date"])).total_seconds() / 86400
                    m["days_since_last_match"] = round(days, 1)
                    comps = {g["competition"] for g in games[-3:] if g["competition"]}
                    m["recent_competitions"] = sorted(comps)
                    m["turnover_risk"] = "alto" if days < 3.5 else "medio" if days < 5 else "basso"
                else:
                    m["turnover_risk"] = "non valutabile"
                m["team"] = fx[side]["name"]
                out[side] = m
            hs, as_ = out["home"].get("stake_index"), out["away"].get("stake_index")
            out["stake_diff_home"] = round(hs - as_, 2) if hs is not None and as_ is not None else None
            return out

        matches = self.per_match(ctx, fixtures, one)
        return {"matches_analyzed": len(matches)}, matches
