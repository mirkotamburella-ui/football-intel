"""Provider The Odds API v4 (opzionale, richiede ODDS_API_KEY).

Costi (documentazione ufficiale): ogni chiamata /odds costa
`n_mercati × n_regioni` crediti; /sports e /events sono gratuiti. Il piano free
offre 500 crediti/mese, quindi il provider applica un tetto per run
(ODDS_API_MAX_CREDITS_PER_RUN) e legge gli header x-requests-remaining/used.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from ..http_client import HttpClient, HttpError
from ..names import MATCH_THRESHOLD, match_any
from .espn import parse_dt

log = logging.getLogger("fbi.oddsapi")
BASE = "https://api.the-odds-api.com/v4"

# Mappatura slug ESPN → sport key The Odds API (modificabile; solo competizioni note).
SPORT_KEYS = {
    "eng.1": "soccer_epl", "eng.2": "soccer_efl_champ", "eng.3": "soccer_england_league1",
    "eng.4": "soccer_england_league2", "eng.fa": "soccer_fa_cup", "eng.league_cup": "soccer_england_efl_cup",
    "ita.1": "soccer_italy_serie_a", "ita.2": "soccer_italy_serie_b",
    "esp.1": "soccer_spain_la_liga", "esp.2": "soccer_spain_segunda_division",
    "ger.1": "soccer_germany_bundesliga", "ger.2": "soccer_germany_bundesliga2",
    "fra.1": "soccer_france_ligue_one", "fra.2": "soccer_france_ligue_two",
    "ned.1": "soccer_netherlands_eredivisie", "por.1": "soccer_portugal_primeira_liga",
    "bel.1": "soccer_belgium_first_div", "tur.1": "soccer_turkey_super_league", "sco.1": "soccer_spl",
    "aut.1": "soccer_austria_bundesliga", "den.1": "soccer_denmark_superliga", "nor.1": "soccer_norway_eliteserien",
    "swe.1": "soccer_sweden_allsvenskan", "gre.1": "soccer_greece_super_league", "sui.1": "soccer_switzerland_superleague",
    "pol.1": "soccer_poland_ekstraklasa", "arg.1": "soccer_argentina_primera_division", "bra.1": "soccer_brazil_campeonato",
    "bra.2": "soccer_brazil_serie_b", "chi.1": "soccer_chile_campeonato", "mex.1": "soccer_mexico_ligamx",
    "usa.1": "soccer_usa_mls", "jpn.1": "soccer_japan_j_league", "chn.1": "soccer_china_superleague",
    "aus.1": "soccer_australia_aleague", "kor.1": "soccer_korea_kleague1", "uefa.champions": "soccer_uefa_champs_league",
    "uefa.europa": "soccer_uefa_europa_league", "uefa.europa.conf": "soccer_uefa_europa_conference_league",
    "conmebol.libertadores": "soccer_conmebol_copa_libertadores", "conmebol.sudamericana": "soccer_conmebol_copa_sudamericana",
}


class OddsApiProvider:
    def __init__(self, http: HttpClient, api_key: str, regions: str = "eu", max_credits: int = 40, ttl: int = 600):
        self.http, self.key, self.regions, self.max_credits, self.ttl = http, api_key, regions, max_credits, ttl
        self.credits_used = 0
        self.remaining: str | None = None
        self._lock = threading.Lock()
        self._active: set[str] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _reserve(self, cost: int) -> bool:
        with self._lock:
            if self.credits_used + cost > self.max_credits:
                return False
            self.credits_used += cost
            return True

    def active_sports(self) -> set[str]:
        if self._active is None:
            try:
                f = self.http.get_json(f"{BASE}/sports", 6 * 3600, params={"apiKey": self.key})  # gratuito
                self._active = {s["key"] for s in f.data if s.get("active")}
            except HttpError as e:
                log.warning("The Odds API /sports non disponibile: %s", e)
                self._active = set()
        return self._active

    def _parse_event(self, ev: dict, fetched) -> list[dict]:
        quotes = []
        home, away = ev["home_team"], ev["away_team"]
        for bm in ev.get("bookmakers", []):
            book = bm.get("key", "?").lower()
            for mk in bm.get("markets", []):
                key = mk.get("key")
                for o in mk.get("outcomes", []):
                    name, price, point = o.get("name", ""), o.get("price"), o.get("point")
                    sel = market = None
                    if key == "h2h":
                        market = "1X2"
                        sel = "HOME" if name == home else "AWAY" if name == away else "DRAW" if name.lower() == "draw" else None
                    elif key in ("totals", "alternate_totals") and point is not None and abs(point * 2 % 2 - 1) < 1e-9:
                        market, sel = "OU", name.upper() if name.lower() in ("over", "under") else None
                    elif key == "btts":
                        market, sel = "BTTS", {"yes": "YES", "no": "NO"}.get(name.lower())
                    elif key == "draw_no_bet":
                        market = "DNB"
                        sel = "HOME" if name == home else "AWAY" if name == away else None
                    elif key == "double_chance":
                        market = "DC"
                        n = name.lower()
                        hd, ad, dr = home.lower() in n, away.lower() in n, "draw" in n
                        sel = "1X" if hd and dr else "X2" if ad and dr else "12" if hd and ad else None
                    if market and sel and price and price > 1:
                        quotes.append(
                            {
                                "market": market, "selection": sel, "line": point if market == "OU" else None,
                                "decimal": round(float(price), 3), "open_decimal": None, "bookmaker": book,
                                "source": fetched.source, "fetched_at": mk.get("last_update") or fetched.fetched_at,
                            }
                        )
        return quotes

    def league_odds(self, slug: str, day_start: datetime, day_end: datetime) -> list[dict]:
        """Eventi + quote h2h/totals per competizione (costo: 2 × regioni crediti)."""
        sport = SPORT_KEYS.get(slug or "")
        if not self.enabled or not sport or sport not in self.active_sports():
            return []
        cost = 2 * len(self.regions.split(","))
        if not self._reserve(cost):
            log.info("The Odds API: tetto crediti per run raggiunto, salto %s", sport)
            return []
        try:
            f = self.http.get_json(
                f"{BASE}/sports/{sport}/odds",
                self.ttl,
                params={
                    "apiKey": self.key, "regions": self.regions, "markets": "h2h,totals", "oddsFormat": "decimal",
                    "dateFormat": "iso", "commenceTimeFrom": day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "commenceTimeTo": day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        except HttpError as e:
            log.warning("The Odds API %s: %s", sport, e)
            return []
        self.remaining = f.headers.get("x-requests-remaining", self.remaining)
        events = []
        for ev in f.data:
            events.append({"odds_event_id": ev["id"], "sport": sport, "home": ev["home_team"], "away": ev["away_team"],
                           "commence": ev["commence_time"], "quotes": self._parse_event(ev, f)})
        return events

    def event_extra_markets(self, sport: str, odds_event_id: str) -> list[dict]:
        """Goal/No Goal, Draw No Bet, Doppia chance, linee Over/Under alternative (costo: 4 × regioni)."""
        markets = "btts,draw_no_bet,double_chance,alternate_totals"
        cost = 4 * len(self.regions.split(","))
        if not self.enabled or not self._reserve(cost):
            return []
        try:
            f = self.http.get_json(
                f"{BASE}/sports/{sport}/events/{odds_event_id}/odds", self.ttl,
                params={"apiKey": self.key, "regions": self.regions, "markets": markets, "oddsFormat": "decimal", "dateFormat": "iso"},
            )
        except HttpError as e:
            log.warning("The Odds API extra %s: %s", odds_event_id, e)
            return []
        self.remaining = f.headers.get("x-requests-remaining", self.remaining)
        return self._parse_event(f.data, f)


def find_event(events: list[dict], home: str, away: str, kickoff_utc: str) -> dict | None:
    ko = parse_dt(kickoff_utc)
    best, best_s = None, 0.0
    for ev in events:
        if abs(parse_dt(ev["commence"]) - ko) > timedelta(hours=2):
            continue
        s = match_any(home, away, ev["home"], ev["away"])
        if s > best_s:
            best, best_s = ev, s
    return best if best_s >= MATCH_THRESHOLD else None
