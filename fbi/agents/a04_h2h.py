"""AGENT 4 – HEAD TO HEAD ANALYST: ultimi 5 confronti con peso decrescente nel tempo."""
from __future__ import annotations

from ..context import get_summary, src
from ..providers import espn as E
from .a01_fixture_scout import fixtures_from
from .base import Agent

HALF_LIFE_YEARS = 1.5  # un confronto di 1,5 anni fa pesa la metà; dopo 6 anni ~6%


class HeadToHeadAnalyst(Agent):
    id = "A04"
    name = "Head to Head Analyst"
    depends_on = ("A01",)
    description = "Ultimi 5 scontri diretti: frequenza Over/Under, BTTS, risultati ricorrenti e anomalie; peso decrescente per i confronti datati."

    def analyze(self, ctx, inputs):
        fixtures = fixtures_from(inputs)

        def one(fx):
            sm = get_summary(ctx, fx["event_id"])
            if not sm:
                return {"available": False, "note": "dati H2H non disponibili"}
            self.add_source(src(sm))
            games = E.parse_h2h(sm)[:5]
            if not games:
                return {"available": False, "note": "nessuno scontro diretto recente nelle fonti"}
            hid = fx["home"]["id"]
            rows, W = [], 0.0
            agg = {"home_team_win": 0.0, "draw": 0.0, "away_team_win": 0.0, "over15": 0.0, "over25": 0.0, "under35": 0.0, "btts": 0.0, "goals": 0.0}
            for g in games:
                years = (ctx.now_utc - E.parse_dt(g["date"])).days / 365.25
                w = 0.5 ** (years / HALF_LIFE_YEARS)
                gf = g["home_goals"] if g["home_id"] == hid else g["away_goals"]
                ga = g["away_goals"] if g["home_id"] == hid else g["home_goals"]
                tot = gf + ga
                agg["home_team_win"] += w * (gf > ga)
                agg["draw"] += w * (gf == ga)
                agg["away_team_win"] += w * (gf < ga)
                agg["over15"] += w * (tot > 1.5)
                agg["over25"] += w * (tot > 2.5)
                agg["under35"] += w * (tot < 3.5)
                agg["btts"] += w * (gf > 0 and ga > 0)
                agg["goals"] += w * tot
                W += w
                rows.append({"date": g["date"][:10], "score_from_today_home_view": f"{gf}-{ga}",
                             "venue": "casa" if g["home_id"] == hid else "trasferta", "weight": round(w, 2)})
            weighted = {k: round(v / W, 2) for k, v in agg.items()}
            # dimensione effettiva del campione (Kish): con confronti vecchi è < numero di partite
            n_eff = round(W**2 / sum(r["weight"] ** 2 for r in rows), 2) if W else 0
            results = [r["score_from_today_home_view"] for r in rows]
            recurrent = max(set(results), key=results.count)
            anomalies = []
            if results.count(recurrent) >= 3:
                anomalies.append(f"risultato ricorrente {recurrent} ({results.count(recurrent)} volte)")
            if n_eff < 2:
                anomalies.append("confronti prevalentemente datati: peso statistico molto basso")
            return {"available": True, "games": rows, "weighted": weighted, "effective_sample": n_eff,
                    "recurrent_result": recurrent, "anomalies": anomalies,
                    "usage_note": "usato solo come segnale secondario (Contrarian), mai come base della scelta"}

        matches = self.per_match(ctx, fixtures, one)
        return {"matches_analyzed": len(matches), "with_h2h": sum(1 for m in matches.values() if m.get("available"))}, matches
