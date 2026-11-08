"""AGENT 1 – GLOBAL FIXTURE SCOUT: tutte le partite di OGGI (ora italiana) nel mondo."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from ..context import get_summary, src
from ..http_client import HttpError
from ..providers import espn as E
from .base import Agent

# Aree monitorate richieste dal progetto → prefissi slug ESPN
REGIONS = {
    "Italia (Serie A, Serie B, coppe)": ("ita.",),
    "Inghilterra (Premier League, Championship, coppe)": ("eng.",),
    "Spagna (La Liga)": ("esp.",),
    "Germania (Bundesliga)": ("ger.",),
    "Francia (Ligue 1)": ("fra.",),
    "Olanda (Eredivisie)": ("ned.",),
    "Portogallo (Primeira Liga)": ("por.",),
    "Altri campionati europei": ("bel.", "tur.", "sco.", "aut.", "den.", "nor.", "swe.", "gre.", "sui.", "rus.", "pol.", "cze.", "cro.", "srb.", "rou.", "ukr.", "irl.", "isl.", "fin.", "cyp.", "isr.", "hun.", "bul.", "svk.", "svn.", "wal.", "nir."),
    "Sudamerica": ("arg.", "bra.", "chi.", "col.", "uru.", "par.", "per.", "ecu.", "bol.", "ven.", "conmebol."),
    "Asia e Oceania": ("jpn.", "chn.", "kor.", "ksa.", "uae.", "qat.", "ind.", "tha.", "idn.", "mys.", "aus.", "afc.", "irn.", "uzb.", "vie.", "sgp.", "saff."),
    "Africa": ("rsa.", "egy.", "mar.", "tun.", "alg.", "nga.", "gha.", "caf.", "ken."),
    "Nord e Centro America": ("usa.", "mex.", "can.", "concacaf.", "crc.", "hon.", "gua.", "slv.", "pan.", "jam."),
    "Competizioni internazionali (UEFA, FIFA, nazionali)": ("uefa.", "fifa.", "club.friendly", "intl."),
}


def region_of(slug: str | None) -> str:
    s = (slug or "").lower()
    for name, prefixes in REGIONS.items():
        if s.startswith(prefixes):
            return name
    return "Altro / non classificato"


class FixtureScout(Agent):
    id = "A01"
    name = "Global Fixture Scout"
    description = "Raccoglie tutte le partite di oggi (ora italiana), verifica stato e competizione, elimina doppioni ed esclude le partite già iniziate."

    def analyze(self, ctx, inputs):
        s = ctx.settings
        today = ctx.today
        raw: list[dict] = []
        # ESPN usa giornate in fuso USA: per coprire 00:00–23:59 italiane servono ieri/oggi/domani
        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            try:
                f = ctx.espn.scoreboard(d.strftime("%Y%m%d"))
                self.add_source(src(f))
                raw.extend(E.parse_scoreboard(f))
            except HttpError as e:
                self.errors.append(f"scoreboard {d}: {e}")
        # filtra per data italiana e deduplica
        seen_ids, seen_keys, fixtures, dupes = set(), set(), [], 0
        for fx in raw:
            local = ctx.to_local(fx["kickoff_utc"])
            if local.date() != today:
                continue
            key = (fx["home"]["name"].lower(), fx["away"]["name"].lower(), local.strftime("%Y%m%d%H"))
            if fx["event_id"] in seen_ids or key in seen_keys:
                dupes += 1
                continue
            seen_ids.add(fx["event_id"])
            seen_keys.add(key)
            fx["kickoff_local"] = local.strftime("%Y-%m-%d %H:%M")
            fixtures.append(fx)

        # risolvi slug di competizione (necessario per classifiche/calendari) e verifica lo stato
        def resolve(fx):
            if fx["state"] == "pre" and not fx.get("league_slug"):
                sm = get_summary(ctx, fx["event_id"])
                if sm:
                    lg = E.summary_league(sm)
                    fx["league_slug"] = lg["slug"]
                    fx["league_name"] = lg["name"]
                    fx["is_tournament"] = lg["is_tournament"]
                    self.add_source(src(sm))
            return None

        self.per_match(ctx, fixtures, resolve)

        min_ko = ctx.now_utc + timedelta(minutes=s.min_minutes_before_kickoff)
        eligible, excluded = [], []
        odds_api_on = ctx.odds_api.enabled
        for fx in fixtures:
            fx["region"] = region_of(fx.get("league_slug"))
            reason = None
            if fx["state"] == "in":
                reason = "partita già iniziata"
            elif fx["state"] == "post" or fx["completed"]:
                reason = "partita conclusa"
            elif fx["state"] != "pre":
                reason = f"stato non pre-partita ({fx['status']})"
            elif "POSTPONED" in (fx["status"] or "") or "CANCELED" in (fx["status"] or ""):
                reason = "rinviata/annullata"
            elif not fx["time_valid"]:
                reason = "orario non confermato"
            elif E.parse_dt(fx["kickoff_utc"]) < min_ko:
                reason = f"calcio d'inizio entro {s.min_minutes_before_kickoff} minuti"
            elif not fx["has_odds"] and not odds_api_on:
                reason = "nessuna quota disponibile dalle fonti configurate"
            elif not fx.get("league_slug"):
                reason = "competizione non identificata (dati non verificabili)"
            if reason:
                excluded.append({"event_id": fx["event_id"], "match": f"{fx['home']['name']} - {fx['away']['name']}",
                                 "competition": fx["competition"], "kickoff_local": fx["kickoff_local"], "reason": reason})
            else:
                eligible.append(fx)
        eligible.sort(key=lambda x: x["kickoff_utc"])
        if s.max_matches and len(eligible) > s.max_matches:
            self.warn(f"MAX_MATCHES={s.max_matches}: analizzate solo le prime {s.max_matches} partite eleggibili su {len(eligible)}")
            for fx in eligible[s.max_matches:]:
                excluded.append({"event_id": fx["event_id"], "match": f"{fx['home']['name']} - {fx['away']['name']}",
                                 "competition": fx["competition"], "kickoff_local": fx["kickoff_local"], "reason": "limite MAX_MATCHES"})
            eligible = eligible[: s.max_matches]

        by_region = Counter(fx["region"] for fx in fixtures)
        regions = [{"region": r, "matches_today": by_region.get(r, 0),
                    "note": None if by_region.get(r) else "nessuna partita oggi coperta dalle fonti"} for r in list(REGIONS) + ["Altro / non classificato"]]
        competitions = Counter(fx["competition"] for fx in fixtures)
        summary = {
            "date": today.isoformat(),
            "timezone": s.timezone,
            "total_found": len(fixtures),
            "duplicates_removed": dupes,
            "eligible_prematch": len(eligible),
            "excluded": len(excluded),
            "competitions_count": len(competitions),
            "competitions": sorted(competitions.items(), key=lambda x: -x[1]),
            "regions": regions,
            "fixtures": eligible,
            "excluded_list": excluded,
        }
        matches = {fx["event_id"]: {"match": f"{fx['home']['name']} - {fx['away']['name']}", "competition": fx["competition"],
                                    "kickoff_local": fx["kickoff_local"], "league_slug": fx.get("league_slug"),
                                    "sources": [{"source": fx["source"], "fetched_at": fx["fetched_at"]}]} for fx in eligible}
        return summary, matches


def fixtures_from(inputs: dict) -> list[dict]:
    return inputs["A01"]["summary"].get("fixtures", [])
