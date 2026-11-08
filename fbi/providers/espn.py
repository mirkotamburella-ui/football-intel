"""Provider ESPN (endpoint JSON pubblici, nessuna chiave richiesta).

Copertura verificata: calendario mondiale giornaliero (~50 competizioni), stato
partita, classifiche complete (GF/GA, zone), calendario/risultati della stagione per
squadra, ultime 5 partite in tutte le competizioni, ultimi 5 scontri diretti,
formazioni quando pubblicate e quote DraftKings (USA) e Bet365 (1X2, doppia chance,
linea gol) esposte da ESPN.

Limiti noti (dichiarati nei report): niente xG/xGA, niente tiri per partita nello
storico, infortuni raramente popolati, solo 1–2 bookmaker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..http_client import Fetched, HttpClient

log = logging.getLogger("fbi.espn")

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer"
CORE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues"

SOURCE_NAME = "ESPN public API"


def parse_dt(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if len(s) == 22 and s[16] == "+":  # "2026-11-08T14:00+00:00"
        s = s[:16] + ":00" + s[16:]
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def frac_to_decimal(v: Any) -> float | None:
    """'10/11' -> 1.909; 'EVS' -> 2.0; numeri già decimali restituiti come float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 1 else None
    s = str(v).strip().upper()
    if s in ("EVS", "EVENS"):
        return 2.0
    if "/" in s:
        try:
            n, d = s.split("/")
            return round(1.0 + float(n) / float(d), 4)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        f = float(s)
        return f if f > 1 else None
    except ValueError:
        return None


def american_to_decimal(v: Any) -> float | None:
    try:
        a = float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return round(1 + (a / 100 if a > 0 else 100 / -a), 4)


class EspnProvider:
    def __init__(self, http: HttpClient, ttl_fixtures: int = 600, ttl_odds: int = 600, ttl_stats: int = 21600):
        self.http = http
        self.ttl_fixtures, self.ttl_odds, self.ttl_stats = ttl_fixtures, ttl_odds, ttl_stats

    # ------------------------------------------------------------- raw calls
    def scoreboard(self, yyyymmdd: str) -> Fetched:
        return self.http.get_json(f"{SITE}/all/scoreboard", self.ttl_fixtures, params={"dates": yyyymmdd, "limit": 1000})

    def summary(self, event_id: str, ttl: int | None = None) -> Fetched:
        return self.http.get_json(f"{SITE}/all/summary", ttl or self.ttl_fixtures, params={"event": event_id})

    def standings(self, slug: str) -> Fetched:
        return self.http.get_json(f"{STANDINGS}/{slug}/standings", self.ttl_stats)

    def team_schedule(self, slug: str, team_id: str, season: int | None = None) -> Fetched:
        params = {"season": season} if season else None
        return self.http.get_json(f"{SITE}/{slug}/teams/{team_id}/schedule", self.ttl_stats, params=params)

    def core_odds(self, slug: str, event_id: str) -> Fetched:
        return self.http.get_json(f"{CORE}/{slug}/events/{event_id}/competitions/{event_id}/odds", self.ttl_odds)


# ---------------------------------------------------------------- parsers
def parse_scoreboard(f: Fetched) -> list[dict]:
    out = []
    for e in f.data.get("events", []):
        try:
            comp = e["competitions"][0]
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            st = comp["status"]["type"]
            odds0 = (comp.get("odds") or [None])[0] or {}
            tags = {}
            for side in ("home", "away"):
                link = ((odds0.get("moneyline") or {}).get(side) or {}).get("close", {}).get("link", {})
                tags = link.get("tracking", {}).get("tags", {}) or tags
            out.append(
                {
                    "event_id": str(e["id"]),
                    "league_id": e["uid"].split("~")[1].replace("l:", ""),
                    "league_slug": tags.get("league"),
                    "competition": comp.get("altGameNote") or e.get("season", {}).get("slug", ""),
                    "season_slug": e.get("season", {}).get("slug", ""),
                    "kickoff_utc": comp.get("startDate") or e["date"],
                    "time_valid": comp.get("timeValid", True),
                    "state": st.get("state"),
                    "status": st.get("name"),
                    "completed": st.get("completed", False),
                    "home": {
                        "id": str(teams["home"]["team"]["id"]),
                        "name": teams["home"]["team"]["displayName"],
                        "form": teams["home"].get("form"),
                        "score": teams["home"].get("score"),
                    },
                    "away": {
                        "id": str(teams["away"]["team"]["id"]),
                        "name": teams["away"]["team"]["displayName"],
                        "form": teams["away"].get("form"),
                        "score": teams["away"].get("score"),
                    },
                    "venue_country": (comp.get("venue") or {}).get("address", {}).get("country"),
                    "neutral_site": comp.get("neutralSite", False),
                    "has_odds": bool(odds0),
                    "source": f.source,
                    "fetched_at": f.fetched_at,
                }
            )
        except (KeyError, IndexError, TypeError) as ex:
            log.warning("evento scoreboard non parsabile %s: %s", e.get("id"), ex)
    return out


def summary_league(f: Fetched) -> dict:
    lg = (f.data.get("header") or {}).get("league") or {}
    return {"slug": lg.get("slug"), "name": lg.get("name"), "is_tournament": lg.get("isTournament", False)}


def summary_status(f: Fetched) -> dict:
    comp = ((f.data.get("header") or {}).get("competitions") or [{}])[0]
    st = (comp.get("status") or {}).get("type") or {}
    scores = {}
    for c in comp.get("competitors", []):
        try:
            scores[c["homeAway"]] = int(float(c.get("score")))
        except (TypeError, ValueError):
            pass
    return {"state": st.get("state"), "completed": st.get("completed", False), "name": st.get("name"), "score": scores}


def parse_last_five(f: Fetched) -> dict[str, list[dict]]:
    """Ultime 5 partite (tutte le competizioni) per team_id."""
    out: dict[str, list[dict]] = {}
    for block in f.data.get("lastFiveGames") or []:
        tid = str(block.get("team", {}).get("id"))
        games = []
        for ev in block.get("events", []):
            try:
                hg, ag = int(ev["homeTeamScore"]), int(ev["awayTeamScore"])
            except (KeyError, TypeError, ValueError):
                continue
            is_home = str(ev.get("homeTeamId")) == tid
            games.append(
                {
                    "event_id": str(ev.get("id")),
                    "date": ev.get("gameDate"),
                    "competition": ev.get("competitionName") or ev.get("leagueName"),
                    "home": is_home,
                    "opponent_id": str((ev.get("opponent") or {}).get("id")),
                    "opponent": (ev.get("opponent") or {}).get("displayName"),
                    "gf": hg if is_home else ag,
                    "ga": ag if is_home else hg,
                    "result": ev.get("gameResult"),
                }
            )
        out[tid] = games
    return out


def parse_h2h(f: Fetched) -> list[dict]:
    out = []
    for series in f.data.get("seasonseries") or []:
        if series.get("type") != "head-to-head":
            continue
        for ev in series.get("events", []):
            if not (ev.get("statusType") or {}).get("completed"):
                continue
            comps = {c["homeAway"]: c for c in ev.get("competitors", [])}
            try:
                out.append(
                    {
                        "event_id": str(ev["id"]),
                        "date": ev["date"],
                        "home_id": str(comps["home"]["team"]["id"]),
                        "away_id": str(comps["away"]["team"]["id"]),
                        "home_goals": int(comps["home"]["score"]),
                        "away_goals": int(comps["away"]["score"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    seen, uniq = set(), []
    for g in out:
        if g["event_id"] not in seen:
            seen.add(g["event_id"])
            uniq.append(g)
    return sorted(uniq, key=lambda g: g["date"], reverse=True)


def parse_rosters(f: Fetched) -> dict[str, dict]:
    """Formazioni: presenti solo quando ESPN pubblica le distinte ufficiali."""
    out = {}
    for r in f.data.get("rosters") or []:
        tid = str((r.get("team") or {}).get("id"))
        roster = r.get("roster") or []
        starters = [p for p in roster if p.get("starter")]
        out[tid] = {
            "published": bool(starters),
            "formation": r.get("formation"),
            "starters": [((p.get("athlete") or {}).get("displayName"), (p.get("position") or {}).get("abbreviation")) for p in starters],
            "bench_count": len(roster) - len(starters),
        }
    return out


def parse_injuries(f: Fetched) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for block in f.data.get("injuries") or []:
        tid = str((block.get("team") or {}).get("id"))
        items = []
        for inj in block.get("injuries", []):
            items.append(
                {
                    "player": (inj.get("athlete") or {}).get("displayName"),
                    "status": inj.get("status"),
                    "type": (inj.get("type") or {}).get("description") if isinstance(inj.get("type"), dict) else inj.get("type"),
                    "date": inj.get("date"),
                }
            )
        out[tid] = items
    return out


def parse_standings(f: Fetched) -> dict[str, Any]:
    """Classifica completa. Ritorna {"groups": [{name, table:{team_id: row}}]}."""
    groups = []
    children = f.data.get("children") or []
    for ch in children:
        table = {}
        for e in (ch.get("standings") or {}).get("entries", []):
            stats = {s.get("name"): s.get("value") for s in e.get("stats", [])}
            tid = str(e["team"]["id"])
            table[tid] = {
                "team": e["team"].get("displayName"),
                "rank": int(stats.get("rank") or 0),
                "gp": int(stats.get("gamesPlayed") or 0),
                "w": int(stats.get("wins") or 0),
                "d": int(stats.get("ties") or 0),
                "l": int(stats.get("losses") or 0),
                "gf": int(stats.get("pointsFor") or 0),
                "ga": int(stats.get("pointsAgainst") or 0),
                "pts": int(stats.get("points") or 0),
                "deductions": stats.get("deductions") or 0,
                "note": (e.get("note") or {}).get("description"),
            }
        groups.append({"name": ch.get("name"), "table": table})
    return {"league": f.data.get("name"), "season": (f.data.get("season") or {}).get("displayName"), "groups": groups}


def parse_schedule(f: Fetched, team_id: str) -> list[dict]:
    """Risultati completati della stagione (competizione dello slug richiesto)."""
    out = []
    for ev in f.data.get("events", []):
        try:
            comp = ev["competitions"][0]
            if not comp["status"]["type"].get("completed"):
                continue
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            hs = (teams["home"].get("score") or {}).get("value")
            as_ = (teams["away"].get("score") or {}).get("value")
            if hs is None or as_ is None:
                continue
            out.append(
                {
                    "event_id": str(ev["id"]),
                    "date": ev["date"],
                    "home_id": str(teams["home"]["team"]["id"]),
                    "home_name": teams["home"]["team"]["displayName"],
                    "away_id": str(teams["away"]["team"]["id"]),
                    "away_name": teams["away"]["team"]["displayName"],
                    "home_goals": int(hs),
                    "away_goals": int(as_),
                    "neutral": comp.get("neutralSite", False),
                }
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    out.sort(key=lambda g: g["date"])
    return out


def parse_core_odds(f: Fetched, home_name: str = "", away_name: str = "") -> list[dict]:
    """Quote decimali per mercato. Ogni quota porta bookmaker, fonte e timestamp."""
    quotes: list[dict] = []

    def add(market, selection, price, book, line=None, open_price=None):
        if price is None or price <= 1.0:
            return
        quotes.append(
            {
                "market": market,
                "selection": selection,
                "line": line,
                "decimal": round(float(price), 3),
                "open_decimal": round(float(open_price), 3) if open_price else None,
                "bookmaker": book,
                "source": f.source,
                "fetched_at": f.fetched_at,
            }
        )

    for it in f.data.get("items", []):
        book = ((it.get("provider") or {}).get("name") or "?").lower().replace(" ", "")
        if "teamOdds" in (it.get("bettingOdds") or {}):  # Bet365 (quote frazionarie)
            to = it["bettingOdds"]["teamOdds"]
            g = lambda k: frac_to_decimal((to.get(k) or {}).get("value"))  # noqa: E731
            add("1X2", "HOME", g("preMatchFullTimeResultHome"), book)
            add("1X2", "DRAW", g("preMatchFullTimeResultDraw"), book)
            add("1X2", "AWAY", g("preMatchFullTimeResultAway"), book)
            add("DC", "1X", g("preMatchDoubleChanceHomeOrDraw"), book)
            add("DC", "X2", g("preMatchDoubleChanceDrawOrAway"), book)
            add("DC", "12", g("preMatchDoubleChanceHomeOrAway"), book)
            # linea gol: usata solo se ".5" (nessun rimborso parziale → equivale a Over/Under classico)
            try:
                line = float((to.get("preMatchOverUnderHandicap") or {}).get("value"))
            except (TypeError, ValueError):
                line = None
            if line is not None and abs(line * 2 % 2 - 1) < 1e-9:
                add("OU", "OVER", g("preMatchGoalLineOver"), book, line)
                add("OU", "UNDER", g("preMatchGoalLineUnder"), book, line)
            continue
        h, a = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}
        cur, op = it.get("current") or {}, it.get("open") or {}

        def dec(x):
            if isinstance(x, dict):
                return x.get("decimal") or x.get("value")
            return x

        add("1X2", "HOME", dec((h.get("current") or {}).get("moneyLine")) or american_to_decimal(h.get("moneyLine")), book,
            open_price=dec((h.get("open") or {}).get("moneyLine")))
        add("1X2", "AWAY", dec((a.get("current") or {}).get("moneyLine")) or american_to_decimal(a.get("moneyLine")), book,
            open_price=dec((a.get("open") or {}).get("moneyLine")))
        add("1X2", "DRAW", dec(cur.get("draw")) or american_to_decimal((it.get("drawOdds") or {}).get("moneyLine")), book,
            open_price=dec(op.get("draw")))
        line = it.get("overUnder")
        if line is not None and abs(float(line) * 2 % 2 - 1) < 1e-9:
            add("OU", "OVER", dec(cur.get("over")) or american_to_decimal(it.get("overOdds")), book, float(line), dec(op.get("over")))
            add("OU", "UNDER", dec(cur.get("under")) or american_to_decimal(it.get("underOdds")), book, float(line), dec(op.get("under")))
    return quotes
