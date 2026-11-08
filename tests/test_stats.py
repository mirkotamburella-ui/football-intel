import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from fbi.stats.elo import Elo
from fbi.stats.glm import PoissonGLM
from fbi.stats.montecarlo import accumulator_joint, match_intervals, simulate_scorelines
from fbi.stats.odds_math import devig_power, devig_proportional, ev, ev_dnb, fair_from_books, overround
from fbi.stats.poisson import market_probs, score_matrix


class OddsMath(unittest.TestCase):
    def test_overround_and_devig(self):
        prices = [1.909, 3.75, 3.75]  # Bet365 Brighton–Brentford (reale)
        self.assertAlmostEqual(overround(prices), 1 / 1.909 + 2 / 3.75 - 1, places=9)
        for fn in (devig_proportional, devig_power):
            p = fn(prices)
            self.assertAlmostEqual(sum(p), 1.0, places=9)
            self.assertTrue(all(0 < x < 1 for x in p))
        # il metodo power toglie proporzionalmente più margine alle quote lunghe
        self.assertGreater(devig_power(prices)[0], devig_proportional(prices)[0])

    def test_ev(self):
        self.assertAlmostEqual(ev(0.5, 2.2), 0.1)
        self.assertAlmostEqual(ev(0.5, 2.0), 0.0)
        # DNB: pareggio rimborsato
        self.assertAlmostEqual(ev_dnb(0.5, 0.25, 1.5), 0.5 * 1.5 + 0.25 - 1)

    def test_fair_from_books(self):
        cons, ovr = fair_from_books({"a": [2.0, 3.5, 4.0], "b": [2.1, 3.4, 3.8]})
        self.assertAlmostEqual(sum(cons), 1.0, places=9)
        self.assertEqual(set(ovr), {"a", "b"})


class Poisson(unittest.TestCase):
    def test_matrix_and_markets(self):
        m = score_matrix(1.6, 1.1)
        self.assertAlmostEqual(m.sum(), 1.0, places=9)
        p = market_probs(m)
        self.assertAlmostEqual(p["1X2|HOME"] + p["1X2|DRAW"] + p["1X2|AWAY"], 1.0, places=9)
        self.assertAlmostEqual(p["DC|1X"], p["1X2|HOME"] + p["1X2|DRAW"], places=9)
        self.assertAlmostEqual(p["OU|OVER|2.5"] + p["OU|UNDER|2.5"], 1.0, places=9)
        self.assertGreater(p["OU|OVER|1.5"], p["OU|OVER|2.5"])
        self.assertGreater(p["1X2|HOME"], p["1X2|AWAY"])

    def test_independent_case(self):
        m = score_matrix(1.3, 0.9, rho=0.0)
        self.assertAlmostEqual(m[0, 0], math.exp(-2.2), places=6)

    def test_simulation_matches_analytic(self):
        sim = simulate_scorelines(1.5, 1.2, n=200000, seed=1)
        ana = market_probs(score_matrix(1.5, 1.2, rho=0.0))
        for k in ("1X2|HOME", "OU|OVER|2.5", "BTTS|YES"):
            self.assertAlmostEqual(sim[k], ana[k], delta=0.006)

    def test_intervals_contain_point(self):
        iv = match_intervals(1.5, 1.0, cv=0.2, draws=200, seed=3)
        p = market_probs(score_matrix(1.5, 1.0))["1X2|HOME"]
        lo, hi = iv["1X2|HOME"]
        self.assertLess(lo, p)
        self.assertGreater(hi, p)


def synthetic_league(seed=0, rounds=4):
    """Lega sintetica con forze note: la squadra T0 è la più forte, T5 la più debole."""
    rng = np.random.default_rng(seed)
    att = {f"T{i}": 0.4 - 0.15 * i for i in range(6)}
    dfn = {f"T{i}": 0.3 - 0.12 * i for i in range(6)}
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    out, k = [], 0
    for r in range(rounds):
        for h in att:
            for a in att:
                if h == a:
                    continue
                lh = math.exp(0.1 + 0.25 + att[h] - dfn[a])
                la = math.exp(0.1 + att[a] - dfn[h])
                out.append({"event_id": str(k), "date": (t0 + timedelta(days=k)).isoformat(), "home_id": h, "away_id": a,
                            "home_goals": int(rng.poisson(lh)), "away_goals": int(rng.poisson(la))})
                k += 1
    return out


class Models(unittest.TestCase):
    def test_glm_recovers_ordering(self):
        data = synthetic_league()
        g = PoissonGLM(ridge=1.0, half_life_days=10000).fit(data, datetime(2027, 1, 1, tzinfo=timezone.utc))
        lh, la = g.predict("T0", "T5")
        self.assertGreater(lh, la * 2)
        self.assertGreater(g.home_advantage(), 1.0)

    def test_elo(self):
        e = Elo().fit(synthetic_league())
        self.assertGreater(e.rating("T0"), e.rating("T5"))
        p = e.probs_1x2("T0", "T5")
        self.assertAlmostEqual(sum(p.values()), 1.0, places=9)
        self.assertGreater(p["1X2|HOME"], 0.5)

    def test_accumulator_independent_vs_correlated(self):
        probs, sds = [0.7] * 5, [0.01] * 5
        ind = accumulator_joint(probs, sds, None, outer=60, inner=4000, seed=1)
        self.assertAlmostEqual(ind["joint_prob"], 0.7**5, delta=0.01)
        C = np.full((5, 5), 0.5)
        np.fill_diagonal(C, 1.0)
        cor = accumulator_joint(probs, sds, C, outer=60, inner=4000, seed=1)
        # correlazione positiva → probabilità congiunta maggiore del prodotto semplice
        self.assertGreater(cor["joint_prob"], ind["joint_prob"] + 0.03)
        self.assertLessEqual(ind["joint_prob_p5"], ind["joint_prob"] + 1e-12)


if __name__ == "__main__":
    unittest.main()
