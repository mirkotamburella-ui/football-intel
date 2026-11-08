"""Provider con chiave API (non chiamabili senza credenziali): test con risposte nel
formato documentato dalle rispettive API, iniettate al posto della rete."""
import unittest
from datetime import datetime, timezone

from fbi.agents.a07_odds import analyze_quotes
from fbi.http_client import Fetched
from fbi.providers.api_football import ApiFootballProvider
from fbi.providers.odds_api import OddsApiProvider, find_event


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_json(self, url, ttl=0, headers=None, params=None):
        self.calls.append((url, params, headers))
        for pattern, data in self.routes.items():
            if pattern in url:
                return Fetched(data, url, "2026-11-08T09:00:00Z", False, {"x-requests-remaining": "450"})
        raise AssertionError(f"URL inatteso {url}")


ODDS_EVENT = {
    "id": "abc123", "sport_key": "soccer_italy_serie_a", "commence_time": "2026-11-08T19:45:00Z",
    "home_team": "Napoli", "away_team": "Lazio",
    "bookmakers": [
        {"key": "williamhill", "title": "William Hill", "last_update": "2026-11-08T08:55:00Z", "markets": [
            {"key": "h2h", "last_update": "2026-11-08T08:55:00Z", "outcomes": [
                {"name": "Napoli", "price": 2.2}, {"name": "Lazio", "price": 3.3}, {"name": "Draw", "price": 3.4}]},
            {"key": "totals", "last_update": "2026-11-08T08:55:00Z", "outcomes": [
                {"name": "Over", "price": 1.9, "point": 2.5}, {"name": "Under", "price": 1.95, "point": 2.5},
                {"name": "Over", "price": 1.7, "point": 2.25}]}]},
        {"key": "unibet_eu", "title": "Unibet", "last_update": "2026-11-08T08:50:00Z", "markets": [
            {"key": "h2h", "last_update": "2026-11-08T08:50:00Z", "outcomes": [
                {"name": "Napoli", "price": 2.25}, {"name": "Lazio", "price": 3.2}, {"name": "Draw", "price": 3.35}]}]},
    ],
}
EXTRA = {**ODDS_EVENT, "bookmakers": [{"key": "williamhill", "last_update": "2026-11-08T08:56:00Z", "markets": [
    {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.8}, {"name": "No", "price": 2.0}]},
    {"key": "draw_no_bet", "outcomes": [{"name": "Napoli", "price": 1.6}, {"name": "Lazio", "price": 2.4}]},
    {"key": "double_chance", "outcomes": [{"name": "Napoli/Draw", "price": 1.3}, {"name": "Draw/Lazio", "price": 1.65}, {"name": "Napoli/Lazio", "price": 1.33}]},
]}]}


class OddsApi(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp({"/events/abc123/odds": EXTRA, "/sports/soccer_italy_serie_a/odds": [ODDS_EVENT],
                              "/v4/sports": [{"key": "soccer_italy_serie_a", "active": True}]})
        self.p = OddsApiProvider(self.http, "KEY", "eu", max_credits=8)

    def test_league_and_extra_markets(self):
        d = datetime(2026, 11, 8, tzinfo=timezone.utc)
        evs = self.p.league_odds("ita.1", d, d)
        self.assertEqual(len(evs), 1)
        ev = find_event(evs, "Napoli", "Lazio", "2026-11-08T19:45Z")
        self.assertIsNotNone(ev)
        keys = {(q["bookmaker"], q["market"], q["selection"], q["line"]) for q in ev["quotes"]}
        self.assertIn(("williamhill", "1X2", "HOME", None), keys)
        self.assertIn(("williamhill", "OU", "OVER", 2.5), keys)
        self.assertNotIn(("williamhill", "OU", "OVER", 2.25), keys)  # linee asiatiche escluse
        extra = self.p.event_extra_markets(ev["sport"], ev["odds_event_id"])
        k2 = {(q["market"], q["selection"]) for q in extra}
        self.assertTrue({("BTTS", "YES"), ("DNB", "HOME"), ("DC", "1X"), ("DC", "X2"), ("DC", "12")} <= k2)
        self.assertEqual(self.p.credits_used, 6)  # 2 (h2h,totals) + 4 (mercati extra), regione eu
        # tetto crediti: una seconda chiamata extra supererebbe 8
        self.assertEqual(self.p.event_extra_markets(ev["sport"], ev["odds_event_id"]), [])
        # la chiave API non compare mai nelle fonti salvate
        a = analyze_quotes(ev["quotes"] + extra, ["williamhill", "unibet_eu"])
        self.assertEqual(a["markets"]["1X2|HOME"]["playable"]["bookmaker"], "unibet_eu")  # migliore tra i preferiti
        self.assertAlmostEqual(sum(a["fair"][f"1X2|{s}"] for s in ("HOME", "DRAW", "AWAY")), 1.0, places=3)
        self.assertIn("BTTS|YES", a["fair"])

    def test_disabled_without_key(self):
        p = OddsApiProvider(self.http, "", "eu")
        self.assertFalse(p.enabled)
        self.assertEqual(p.league_odds("ita.1", datetime.now(timezone.utc), datetime.now(timezone.utc)), [])


class ExchangesAndAnomalies(unittest.TestCase):
    def q(self, book, d, sel="HOME"):
        return {"market": "1X2", "selection": sel, "line": None, "decimal": d, "open_decimal": None,
                "bookmaker": book, "source": "x", "fetched_at": "t"}

    def test_exchange_commission_and_not_playable(self):
        a = analyze_quotes([self.q("betfair_ex_eu", 2.64), self.q("unibet_it", 2.40)], ["betfair_ex_eu", "unibet_it"])
        m = a["markets"]["1X2|HOME"]
        self.assertEqual(m["books"]["betfair_ex_eu"], 2.558)  # 1 + 1.64 × 0.95
        self.assertEqual(m["playable"]["bookmaker"], "unibet_it")  # la borsa non è mai "giocabile"

    def test_anomalous_price_flagged(self):
        qs = [self.q("unibet_it", 2.90), self.q("williamhill", 2.40), self.q("betsson", 2.45), self.q("sport888", 2.42)]
        m = analyze_quotes(qs, ["unibet_it"])["markets"]["1X2|HOME"]
        self.assertEqual(m["market_median"], 2.42)
        self.assertTrue(m["anomalous"])
        ok = analyze_quotes(qs[1:], ["williamhill"])["markets"]["1X2|HOME"]
        self.assertFalse(ok["anomalous"])


class DrawNoBet(unittest.TestCase):
    def test_dnb_fair_is_absolute_win_probability(self):
        q = lambda m, s, d: {"market": m, "selection": s, "line": None, "decimal": d, "open_decimal": None,  # noqa: E731
                             "bookmaker": "williamhill", "source": "x", "fetched_at": "t"}
        a = analyze_quotes([q("1X2", "HOME", 2.0), q("1X2", "DRAW", 3.5), q("1X2", "AWAY", 4.0),
                            q("DNB", "HOME", 1.4), q("DNB", "AWAY", 2.9)], ["williamhill"])
        f = a["fair"]
        # p(vittoria) assoluta: DNB_home + DNB_away = 1 − p(pareggio)
        self.assertAlmostEqual(f["DNB|HOME"] + f["DNB|AWAY"], 1 - f["1X2|DRAW"], places=3)
        only_dnb = analyze_quotes([q("DNB", "HOME", 1.4), q("DNB", "AWAY", 2.9)], ["williamhill"])
        self.assertNotIn("DNB|HOME", only_dnb["fair"])  # senza 1X2 non è confrontabile col modello


class ApiFootball(unittest.TestCase):
    def test_injuries_and_xg(self):
        fixtures = {"response": [{"fixture": {"id": 99, "date": "2026-11-08T20:45:00+01:00"},
                                  "teams": {"home": {"id": 492, "name": "Napoli"}, "away": {"id": 487, "name": "Lazio"}}}]}
        injuries = {"response": [
            {"team": {"id": 492, "name": "Napoli"}, "player": {"name": "Player A", "type": "Missing Fixture", "reason": "Knee Injury"}},
            {"team": {"id": 487, "name": "Lazio"}, "player": {"name": "Player B", "type": "Questionable", "reason": "Muscle"}}]}
        last = {"response": [{"fixture": {"id": i}} for i in (1, 2, 3)]}
        stats = {"response": [{"team": {"id": 492}, "statistics": [{"type": "expected_goals", "value": "1.80"}]},
                              {"team": {"id": 1}, "statistics": [{"type": "expected_goals", "value": "0.90"}]}]}
        http = FakeHttp({"/fixtures/statistics": stats, "/injuries": injuries, "/fixtures": None})

        def get_json(url, ttl=0, headers=None, params=None):
            http.calls.append((url, params, headers))
            if url.endswith("/fixtures") and params and "date" in params:
                data = fixtures
            elif url.endswith("/fixtures"):
                data = last
            elif url.endswith("/injuries"):
                data = injuries
            else:
                data = stats
            return Fetched(data, url, "2026-11-08T09:00:00Z", False, {})
        http.get_json = get_json
        p = ApiFootballProvider(http, "KEY", max_requests=10)
        fx = p.find_fixture("2026-11-08", "Napoli", "Lazio", "2026-11-08T19:45Z")
        self.assertEqual(fx["fixture"]["id"], 99)
        items, _ = p.injuries(99)
        self.assertEqual([i["type"] for i in items], ["Missing Fixture", "Questionable"])
        xg = p.team_xg(492, last=3)
        self.assertEqual((xg["xg_for"], xg["xga"], xg["matches"]), (1.8, 0.9, 3))
        self.assertTrue(all(h["x-apisports-key"] == "KEY" for _, _, h in http.calls))
        self.assertEqual(p.used, 6)
        p.max_requests = 6
        self.assertIsNone(p.team_xg(492, last=3))  # budget esaurito → dato dichiarato mancante


if __name__ == "__main__":
    unittest.main()
