"""Contesto di esecuzione condiviso (sola lettura per gli agenti) e accesso ai dati.

Gli agenti NON si scambiano stato mutabile: ognuno riceve il contesto e i report JSON
degli agenti da cui dipende, scarica in proprio i dati che gli servono attraverso il
client HTTP condiviso (che deduplica e mette in cache le richieste identiche) e
restituisce un report JSON.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .http_client import HttpClient, HttpError
from .providers import espn as E
from .providers.api_football import ApiFootballProvider
from .providers.odds_api import OddsApiProvider

log = logging.getLogger("fbi.context")


@dataclass
class RunContext:
    settings: Settings
    http: HttpClient
    espn: E.EspnProvider
    odds_api: OddsApiProvider
    api_football: ApiFootballProvider
    run_id: str
    now_utc: datetime
    tz: ZoneInfo
    _locks: dict = field(default_factory=dict)
    _memo: dict = field(default_factory=dict)
    _memo_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def today(self) -> date:
        return self.now_utc.astimezone(self.tz).date()

    def to_local(self, iso_utc: str) -> datetime:
        return E.parse_dt(iso_utc).astimezone(self.tz)

    # memo per-chiave con lock: calcoli costosi condivisi (es. xG con budget API) eseguiti una volta sola
    def memo(self, key: str, fn):
        with self._memo_lock:
            if key in self._memo:
                return self._memo[key]
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._memo:
                try:
                    self._memo[key] = fn()
                except Exception as ex:  # noqa: BLE001 - il fallimento viene memorizzato e dichiarato
                    log.warning("memo %s fallito: %s", key, ex)
                    self._memo[key] = None
            return self._memo[key]


def build_context(settings: Settings, offline: bool = False, now_utc: datetime | None = None) -> RunContext:
    http = HttpClient(
        settings.cache_dir,
        concurrency=settings.http_concurrency,
        min_interval_s=settings.http_min_interval_s,
        timeout_s=settings.http_timeout_s,
        retries=settings.http_retries,
        offline=offline,
    )
    now = now_utc or datetime.now(timezone.utc)
    return RunContext(
        settings=settings,
        http=http,
        espn=E.EspnProvider(http, settings.cache_ttl_fixtures_s, settings.cache_ttl_odds_s, settings.cache_ttl_stats_s),
        odds_api=OddsApiProvider(http, settings.odds_api_key, settings.odds_api_regions, settings.odds_api_max_credits),
        api_football=ApiFootballProvider(http, settings.api_football_key, settings.api_football_max_requests, settings.timezone),
        run_id=now.strftime("%Y%m%dT%H%M%SZ"),
        now_utc=now,
        tz=ZoneInfo(settings.timezone),
    )


# --------------------------------------------------------------------------- data
def src(f) -> dict:
    return {"source": f.source, "fetched_at": f.fetched_at}


def get_summary(ctx: RunContext, event_id: str):
    try:
        return ctx.espn.summary(event_id)
    except HttpError as e:
        log.warning("summary %s non disponibile: %s", event_id, e)
        return None


def get_standings(ctx: RunContext, slug: str | None):
    if not slug:
        return None, None
    def load():
        try:
            f = ctx.espn.standings(slug)
            st = E.parse_standings(f)
            return (st, f) if st["groups"] and any(g["table"] for g in st["groups"]) else (None, f)
        except HttpError as e:
            log.info("classifica %s non disponibile: %s", slug, e)
            return (None, None)
    return ctx.memo(f"standings:{slug}", load) or (None, None)


def standings_row(st: dict | None, team_id: str) -> tuple[dict | None, dict | None]:
    """Riga della squadra e tabella del suo girone."""
    if not st:
        return None, None
    for g in st["groups"]:
        if team_id in g["table"]:
            return g["table"][team_id], g["table"]
    return None, None


PREV_SEASON_BELOW = 20  # sotto questa soglia di partite si aggiunge la stagione precedente


def get_team_results(ctx: RunContext, slug: str | None, team_id: str, min_games: int = PREV_SEASON_BELOW) -> tuple[list[dict], list[dict]]:
    """Risultati di campionato della squadra (stagione corrente, + precedente se il campione è piccolo).

    Ritorna (risultati, fonti). I risultati della stagione precedente sono marcati `prev_season`.
    """
    if not slug:
        return [], []

    def load():
        sources, res = [], []
        try:
            f = ctx.espn.team_schedule(slug, team_id)
            res = E.parse_schedule(f, team_id)
            sources.append(src(f))
            season = (f.data.get("requestedSeason") or {}).get("year")
        except HttpError as e:
            log.info("calendario %s/%s non disponibile: %s", slug, team_id, e)
            return [], []
        if len(res) < min_games and season:
            try:
                f2 = ctx.espn.team_schedule(slug, team_id, season - 1)
                prev = [dict(m, prev_season=True) for m in E.parse_schedule(f2, team_id)]
                res = prev + res
                sources.append(src(f2))
            except HttpError:
                pass
        return res, sources

    return ctx.memo(f"results:{slug}:{team_id}", load) or ([], [])


def team_view(results: list[dict], team_id: str, current_only: bool = True) -> list[dict]:
    """Risultati dal punto di vista della squadra: gf, ga, home, points, opponent_id."""
    out = []
    for m in results:
        if current_only and m.get("prev_season"):
            continue
        home = m["home_id"] == team_id
        gf, ga = (m["home_goals"], m["away_goals"]) if home else (m["away_goals"], m["home_goals"])
        out.append({
            "event_id": m["event_id"], "date": m["date"], "home": home, "gf": gf, "ga": ga,
            "points": 3 if gf > ga else 1 if gf == ga else 0, "result": "W" if gf > ga else "D" if gf == ga else "L",
            "opponent_id": m["away_id"] if home else m["home_id"],
            "opponent": m["away_name"] if home else m["home_name"],
        })
    return out


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
