"""Regole di business: budget, settlement, controllo qualità, costruzione multiple, parser."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fbi.accumulator import build_main, build_prudent
from fbi.config import Settings, get_settings
from fbi.history import settle, settle_accumulator
from fbi.names import match_pair
from fbi.providers.espn import american_to_decimal, frac_to_decimal, parse_core_odds
from fbi.quality import qc_report, qc_selection

NOW = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
POLICY = {"same_league": 0.05, "same_league_same_kickoff": 0.08}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sel(i, score=70.0, p=0.7, odds=1.5, ev=0.02, league="ita.1"):
    return {"event_id": f"E{i}", "key": "1X2|HOME", "match": f"M{i}", "label": "1", "odds": odds, "p_est": p,
            "p_est_range_90": [p - 0.04, p + 0.04], "ev": ev, "score": {"final": score},
            "odds_source": "https://example/odds", "odds_fetched_at": iso(NOW),
            "correlation_tags": {"event_id": f"E{i}", "league": league, "kickoff_slot": "2026110815"}}


class Budget(unittest.TestCase):
    def test_stake_cannot_exceed_10(self):
        os.environ["STAKE_EUR"] = "20"
        try:
            with self.assertRaises(ValueError):
                get_settings(load_env_file=False)
        finally:
            del os.environ["STAKE_EUR"]

    def test_default_stake(self):
        self.assertLessEqual(Settings().stake_eur, 10)


class Settlement(unittest.TestCase):
    def test_markets(self):
        self.assertEqual(settle("1X2|HOME", 2, 1), "won")
        self.assertEqual(settle("1X2|DRAW", 1, 1), "won")
        self.assertEqual(settle("1X2|AWAY", 2, 1), "lost")
        self.assertEqual(settle("DC|1X", 1, 1), "won")
        self.assertEqual(settle("DC|X2", 2, 0), "lost")
        self.assertEqual(settle("DC|12", 0, 0), "lost")
        self.assertEqual(settle("OU|OVER|2.5", 2, 1), "won")
        self.assertEqual(settle("OU|UNDER|3.5", 2, 2), "lost")
        self.assertEqual(settle("OU|OVER|1.5", 1, 0), "lost")
        self.assertEqual(settle("BTTS|YES", 1, 1), "won")
        self.assertEqual(settle("BTTS|NO", 1, 0), "won")
        self.assertEqual(settle("DNB|HOME", 1, 1), "push")
        self.assertEqual(settle("DNB|AWAY", 0, 1), "won")

    def test_accumulator(self):
        acc = {"status": "ok", "stake_eur": 10, "legs": [{"odds": 2.0, "outcome": "won"}, {"odds": 1.5, "outcome": "push"}]}
        settle_accumulator(acc)
        self.assertEqual((acc["outcome"], acc["simulated_return_eur"]), ("won", 20.0))
        acc["legs"].append({"odds": 3.0, "outcome": "void"})  # partita rinviata: quota 1.00
        settle_accumulator(acc)
        self.assertEqual((acc["outcome"], acc["simulated_return_eur"]), ("won", 20.0))
        acc["legs"].append({"odds": 1.3, "outcome": None})
        settle_accumulator(acc)
        self.assertEqual(acc["outcome"], "pending")
        acc["legs"][-1]["outcome"] = "lost"
        settle_accumulator(acc)
        self.assertEqual(acc["outcome"], "lost")


class Accumulators(unittest.TestCase):
    def setUp(self):
        self.s = Settings()

    def test_not_possible_with_fewer_than_5(self):
        r = build_main([sel(i) for i in range(4)], self.s, POLICY)
        self.assertEqual(r["status"], "not_possible")
        self.assertIn("non vengono abbassati", r["reason"])

    def test_main_between_5_and_10_one_leg_per_match(self):
        pool = [sel(i, score=80 - i, p=0.8, odds=1.3) for i in range(14)]
        pool.append(dict(sel(0, score=99), key="DC|1X"))  # seconda selezione sulla stessa partita
        r = build_main(pool, self.s, POLICY)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(5 <= r["legs_count"] <= 10)
        ids = [l["event_id"] for l in r["legs"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(r["stake_eur"], 10)
        self.assertAlmostEqual(r["potential_gross_return_eur"], round(r["stake_eur"] * r["total_odds"], 2), places=1)
        self.assertEqual(qc_report({"main_accumulator": r}), [])

    def test_extra_legs_need_higher_score(self):
        pool = [sel(i, score=65) for i in range(9)]
        r = build_main(pool, self.s, POLICY)
        self.assertEqual(r["legs_count"], 5)  # le gambe oltre la 5ª richiedono score ≥ 68

    def test_prudent(self):
        pool = [sel(i, p=0.6 + 0.02 * i) for i in range(8)]
        r = build_prudent(pool, self.s, POLICY)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(all(l["p_est"] >= self.s.prudent_min_leg_prob for l in r["legs"]))

    def test_correlation_increases_joint_prob(self):
        same = build_main([sel(i, p=0.6, score=70) for i in range(5)], self.s, {"same_league": 0.5, "same_league_same_kickoff": 0.5})
        diff = build_main([sel(i, p=0.6, score=70, league=f"L{i}") for i in range(5)], self.s, POLICY)
        self.assertGreater(same["joint_probability"], diff["joint_probability"])

    def test_qc_report_detects_duplicates(self):
        acc = {"status": "ok", "stake_eur": 10, "legs": [sel(1), sel(1), sel(2), sel(3), sel(4)]}
        self.assertTrue(any("duplicate" in p for p in qc_report({"main_accumulator": acc})))


class QualityControl(unittest.TestCase):
    def ctx(self):
        return SimpleNamespace(settings=Settings(), now_utc=NOW)

    def fixture(self, ko=NOW + timedelta(hours=3)):
        return {"state": "pre", "kickoff_utc": iso(ko), "league_slug": "ita.1"}

    def candidate(self, **kw):
        c = {"odds_source": "https://x", "odds_fetched_at": iso(NOW - timedelta(minutes=5)), "p_model": 0.7, "ev": 0.03,
             "odds": 1.6, "preferred_bookmaker": True, "bookmaker": "bet365"}
        c.update(kw)
        return c

    def test_passes(self):
        f = qc_selection(self.ctx(), self.candidate(), {"final": 70}, {}, {}, self.fixture(), {"available": True})
        self.assertEqual(f, [])

    def test_failures(self):
        ctx, model = self.ctx(), {"available": True}
        stale = qc_selection(ctx, self.candidate(odds_fetched_at=iso(NOW - timedelta(hours=5))), {"final": 70}, {}, {}, self.fixture(), model)
        self.assertTrue(any("non aggiornata" in x for x in stale))
        started = qc_selection(ctx, self.candidate(), {"final": 70}, {}, {}, self.fixture(NOW - timedelta(minutes=5)), model)
        self.assertTrue(any("iniziata" in x for x in started))
        intuition = qc_selection(ctx, self.candidate(), {"final": 70}, {}, {}, self.fixture(), {"available": False})
        self.assertTrue(any("modello" in x for x in intuition))
        low = qc_selection(ctx, self.candidate(ev=-0.01), {"final": 50}, {}, {}, self.fixture(), model)
        self.assertTrue(any("punteggio" in x for x in low) and any("EV" in x for x in low))
        nosrc = qc_selection(ctx, self.candidate(odds_source=None), {"final": 70}, {}, {}, self.fixture(), model)
        self.assertTrue(any("fonte" in x for x in nosrc))
        foreign = qc_selection(ctx, self.candidate(preferred_bookmaker=False, bookmaker="draftkings"), {"final": 70}, {}, {}, self.fixture(), model)
        self.assertTrue(any("non preferito" in x for x in foreign))
        risk = qc_selection(ctx, self.candidate(), {"final": 70}, {}, {"exclusion_reasons": ["campionato poco affidabile"]}, self.fixture(), model)
        self.assertIn("campionato poco affidabile", risk)


class Parsers(unittest.TestCase):
    def test_odds_formats(self):
        self.assertAlmostEqual(frac_to_decimal("10/11"), 1.9091, places=3)
        self.assertEqual(frac_to_decimal("EVS"), 2.0)
        self.assertIsNone(frac_to_decimal("abc"))
        self.assertAlmostEqual(american_to_decimal("-110"), 1.9091, places=3)
        self.assertAlmostEqual(american_to_decimal("+280"), 3.8)

    def test_core_odds_real_payload(self):
        # estratto reale ESPN core odds (Brighton–Brentford, 8/11/2026)
        f = SimpleNamespace(source="https://sports.core.api.espn.com/...", fetched_at="2026-11-08T10:05:00Z", data={"items": [
            {"provider": {"name": "DraftKings"}, "overUnder": 2.5, "overOdds": -165.0, "underOdds": 135.0,
             "homeTeamOdds": {"moneyLine": -110, "current": {"moneyLine": {"decimal": 1.9}}, "open": {"moneyLine": {"decimal": 2.0}}},
             "awayTeamOdds": {"moneyLine": 280, "current": {"moneyLine": {"decimal": 3.8}}, "open": {"moneyLine": {"decimal": 3.4}}},
             "drawOdds": {"moneyLine": 290.0},
             "current": {"over": {"decimal": 1.6}, "under": {"decimal": 2.35}, "draw": {"decimal": 3.9}},
             "open": {"over": {"decimal": 1.64}, "under": {"decimal": 2.15}, "draw": {"decimal": 3.6}}},
            {"provider": {"name": "Bet 365"}, "bettingOdds": {"teamOdds": {
                "preMatchFullTimeResultHome": {"value": "10/11"}, "preMatchFullTimeResultDraw": {"value": "11/4"},
                "preMatchFullTimeResultAway": {"value": "11/4"}, "preMatchDoubleChanceHomeOrDraw": {"value": "1/4"},
                "preMatchDoubleChanceDrawOrAway": {"value": "5/6"}, "preMatchDoubleChanceHomeOrAway": {"value": "1/4"},
                "preMatchOverUnderHandicap": {"value": "3"}, "preMatchGoalLineOver": {"value": "39/40"}, "preMatchGoalLineUnder": {"value": "7/8"}}}},
        ]})
        q = {(x["bookmaker"], x["market"], x["selection"], x["line"]): x for x in parse_core_odds(f)}
        self.assertEqual(q[("draftkings", "1X2", "HOME", None)]["decimal"], 1.9)
        self.assertEqual(q[("draftkings", "1X2", "HOME", None)]["open_decimal"], 2.0)
        self.assertEqual(q[("draftkings", "OU", "OVER", 2.5)]["decimal"], 1.6)
        self.assertAlmostEqual(q[("bet365", "DC", "X2", None)]["decimal"], 1.8333, places=3)
        # linea asiatica 3.0 (con rimborso) NON viene trattata come Over/Under classico
        self.assertFalse(any(k[0] == "bet365" and k[1] == "OU" for k in q))
        self.assertTrue(all(x["source"] and x["fetched_at"] for x in q.values()))

    def test_name_matching(self):
        self.assertGreaterEqual(match_pair("Brighton & Hove Albion", "Brentford", "Brighton and Hove Albion", "Brentford"), 0.8)
        self.assertLess(match_pair("Inter Milan", "AC Milan", "AC Milan", "Inter Milan"), 0.8)


if __name__ == "__main__":
    unittest.main()
