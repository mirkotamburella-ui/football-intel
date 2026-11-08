"""AGENT 5 – LINEUP & INJURY SCOUT: formazioni, indisponibili, squalificati, rotazioni.

Classificazione dell'affidabilità di ogni informazione:
  - "ufficiale"      → distinta pubblicata (ESPN roster con titolari)
  - "provider"       → assenza riportata da un provider dati (API-Football 'Missing Fixture', ESPN injuries)
  - "indiscrezione"  → giocatore in dubbio (API-Football 'Questionable'): non confermato
Il sistema NON raccoglie voci giornalistiche: se un dato manca, viene dichiarato mancante.
"""
from __future__ import annotations

from ..context import get_summary, src
from ..providers import espn as E
from .a01_fixture_scout import fixtures_from
from .base import Agent


class LineupInjuryScout(Agent):
    id = "A05"
    name = "Lineup & Injury Scout"
    depends_on = ("A01",)
    description = "Probabili/ufficiali formazioni, indisponibili, squalificati e dubbi; distingue dati ufficiali, del provider e non confermati."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)
        af = ctx.api_football

        def one(fx):
            out = {"home": {"team": fx["home"]["name"]}, "away": {"team": fx["away"]["name"]}}
            sm = get_summary(ctx, fx["event_id"])
            rosters, inj = {}, {}
            if sm:
                self.add_source(src(sm))
                rosters, inj = E.parse_rosters(sm), E.parse_injuries(sm)
            for side in ("home", "away"):
                tid = fx[side]["id"]
                r = rosters.get(tid)
                block = out[side]
                if r and r["published"]:
                    block["lineup"] = {"status": "ufficiale", "formation": r["formation"], "starters": r["starters"],
                                       "source": sm.source, "fetched_at": sm.fetched_at}
                else:
                    block["lineup"] = {"status": "non pubblicata", "note": "formazione ufficiale non ancora disponibile"}
                block["absences"], block["doubtful"] = [], []
                for i in inj.get(tid, []):
                    item = {"player": i["player"], "detail": i.get("type") or i.get("status"), "reliability": "provider", "source": "ESPN injuries"}
                    (block["doubtful"] if str(i.get("status", "")).lower() in ("questionable", "doubtful", "day-to-day") else block["absences"]).append(item)
                block["injury_data"] = "ESPN" if tid in inj else None
            # API-Football: infortuni/squalifiche per fixture
            if af.enabled:
                fxa = ctx.memo(f"af_fixture:{fx['event_id']}", lambda: af.find_fixture(ctx.today.isoformat(), fx["home"]["name"], fx["away"]["name"], fx["kickoff_utc"]))
                if fxa:
                    res = ctx.memo(f"af_inj:{fxa['fixture']['id']}", lambda: af.injuries(fxa["fixture"]["id"]))
                    items, f = res if res else (None, None)
                    if items is not None:
                        self.add_source(src(f))
                        for side in ("home", "away"):
                            af_tid = fxa["teams"][side]["id"]
                            out[side]["injury_data"] = "API-Football"
                            for i in items:
                                if i["team_id"] != af_tid:
                                    continue
                                missing = (i["type"] or "").lower().startswith("missing")
                                item = {"player": i["player"], "detail": i["reason"],
                                        "reliability": "provider" if missing else "indiscrezione",
                                        "source": "API-Football /injuries", "fetched_at": f.fetched_at}
                                (out[side]["absences"] if missing else out[side]["doubtful"]).append(item)
            for side in ("home", "away"):
                b = out[side]
                b["absences_count"], b["doubtful_count"] = len(b["absences"]), len(b["doubtful"])
                if not b["injury_data"]:
                    b["injury_note"] = "dati su infortuni/squalifiche non disponibili" + ("" if af.enabled else " (configura API_FOOTBALL_KEY)")
            out["coach_change"] = None
            out["coach_note"] = "cambi di allenatore non rilevabili dalle fonti configurate"
            out["key_players_note"] = "importanza dei giocatori chiave non stimabile senza dati individuali (minuti/xG per giocatore)"
            return out

        matches = self.per_match(ctx, fixtures, one)
        return {
            "matches_analyzed": len(matches),
            "official_lineups": sum(1 for m in matches.values() if m.get("home", {}).get("lineup", {}).get("status") == "ufficiale"),
            "with_injury_data": sum(1 for m in matches.values() if m.get("home", {}).get("injury_data")),
        }, matches
