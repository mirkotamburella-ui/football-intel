"""Provider API-Football v3 (opzionale, richiede API_FOOTBALL_KEY).

Usato per i dati che ESPN non fornisce: infortuni/squalifiche per partita e xG
(dalle statistiche delle ultime partite giocate). Piano free: 100 richieste/giorno,
quindi ogni run ha un tetto (API_FOOTBALL_MAX_REQUESTS_PER_RUN) e le partite vengono
servite in ordine di priorità finché il budget lo consente.
"""
from __future__ import annotations

import logging
import threading

from ..http_client import HttpClient, HttpError
from ..names import MATCH_THRESHOLD, match_pair
from .espn import parse_dt

log = logging.getLogger("fbi.apifootball")
BASE = "https://v3.football.api-sports.io"


class ApiFootballProvider:
    def __init__(self, http: HttpClient, api_key: str, max_requests: int = 60, timezone: str = "Europe/Rome"):
        self.http, self.key, self.max_requests, self.tz = http, api_key, max_requests, timezone
        self.used = 0
        self._lock = threading.Lock()
        self._fixtures: dict[str, list[dict]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def budget_left(self) -> int:
        return self.max_requests - self.used

    def _get(self, path: str, params: dict, ttl: int = 3600):
        with self._lock:
            if self.used >= self.max_requests:
                return None
            self.used += 1
        try:
            f = self.http.get_json(f"{BASE}{path}", ttl, headers={"x-apisports-key": self.key}, params=params)
        except HttpError as e:
            log.warning("API-Football %s: %s", path, e)
            return None
        if f.data.get("errors"):
            log.warning("API-Football %s errori: %s", path, f.data["errors"])
            return None
        return f

    def fixtures_on(self, date_iso: str) -> list[dict]:
        if date_iso not in self._fixtures:
            f = self._get("/fixtures", {"date": date_iso, "timezone": self.tz}, ttl=1800)
            self._fixtures[date_iso] = (f.data.get("response", []) if f else [])
        return self._fixtures[date_iso]

    def find_fixture(self, date_iso: str, home: str, away: str, kickoff_utc: str) -> dict | None:
        ko = parse_dt(kickoff_utc)
        best, best_s = None, 0.0
        for fx in self.fixtures_on(date_iso):
            try:
                t = parse_dt(fx["fixture"]["date"])
            except (KeyError, ValueError):
                continue
            if abs((t - ko).total_seconds()) > 7200:
                continue
            s = match_pair(home, away, fx["teams"]["home"]["name"], fx["teams"]["away"]["name"])
            if s > best_s:
                best, best_s = fx, s
        return best if best_s >= MATCH_THRESHOLD else None

    def injuries(self, fixture_id: int):
        """Ritorna (lista, Fetched) – type: 'Missing Fixture' (assente) o 'Questionable' (in dubbio)."""
        f = self._get("/injuries", {"fixture": fixture_id})
        if not f:
            return None, None
        out = []
        for it in f.data.get("response", []):
            out.append({
                "team_id": it.get("team", {}).get("id"),
                "team": it.get("team", {}).get("name"),
                "player": it.get("player", {}).get("name"),
                "type": it.get("player", {}).get("type"),
                "reason": it.get("player", {}).get("reason"),
            })
        return out, f

    def team_xg(self, team_id: int, last: int = 5):
        """Media xG prodotti/concessi nelle ultime `last` partite (1 + last richieste)."""
        if self.budget_left() < last + 1:
            return None
        f = self._get("/fixtures", {"team": team_id, "last": last, "status": "FT"})
        if not f:
            return None
        xg_for, xg_against, sources = [], [], [f.source]
        for fx in f.data.get("response", []):
            st = self._get("/fixtures/statistics", {"fixture": fx["fixture"]["id"]}, ttl=7 * 86400)
            if not st:
                break
            sources.append(st.source)
            vals = {}
            for block in st.data.get("response", []):
                for s in block.get("statistics", []):
                    if s.get("type") == "expected_goals" and s.get("value") not in (None, ""):
                        vals[block["team"]["id"]] = float(s["value"])
            if team_id in vals and len(vals) == 2:
                xg_for.append(vals[team_id])
                xg_against.append(next(v for k, v in vals.items() if k != team_id))
        if len(xg_for) < 3:
            return None
        return {"xg_for": round(sum(xg_for) / len(xg_for), 2), "xga": round(sum(xg_against) / len(xg_against), 2),
                "matches": len(xg_for), "sources": sources}
