"""Affidabilità dei dati per competizione (usata da Odds Intelligence e Risk Manager).

Tier 1: campionati principali con mercati liquidi e dati completi.
Tier 2: campionati professionistici con buona copertura.
Tier 3: competizioni con dati/quote meno affidabili (giovanili, universitari, leghe minori).
Le competizioni non elencate sono Tier 3 per prudenza.
"""
TIER1 = {"eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.champions", "uefa.europa", "ned.1", "por.1", "eng.2"}
TIER2 = {
    "ita.2", "esp.2", "ger.2", "fra.2", "bel.1", "tur.1", "sco.1", "aut.1", "den.1", "nor.1", "swe.1", "sui.1", "gre.1",
    "rus.1", "pol.1", "cze.1", "eng.3", "ned.2", "uefa.europa.conf", "bra.1", "arg.1", "mex.1", "usa.1", "jpn.1",
    "kor.1", "chn.1", "aus.1", "chi.1", "col.1", "conmebol.libertadores", "conmebol.sudamericana", "bra.2", "ksa.1",
    "eng.fa", "ita.coppa_italia", "esp.copa_del_rey", "ger.dfb_pokal", "fra.coupe_de_france", "eng.league_cup",
    "eng.w.1", "usa.nwsl",
}
LOW_RELIABILITY_MARKERS = ("ncaa", "u19", "u20", "u21", "u23", "youth", "friendly", "reserve", "w.2")


def tier(slug: str | None) -> int:
    s = (slug or "").lower()
    if any(m in s for m in LOW_RELIABILITY_MARKERS):
        return 3
    if s in TIER1:
        return 1
    if s in TIER2:
        return 2
    return 3


TIER_RELIABILITY = {1: 1.0, 2: 0.8, 3: 0.5}
