"""Abbinamento nomi squadra tra provider diversi (ESPN ↔ The Odds API ↔ API-Football).

Un abbinamento è accettato solo con somiglianza alta E orario di inizio coerente:
in caso di dubbio la partita resta senza dati del provider esterno (meglio un dato
mancante dichiarato che un dato attribuito alla partita sbagliata).
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_STOP = {
    "fc", "cf", "ac", "sc", "afc", "cd", "ud", "sd", "ss", "us", "as", "bk", "fk", "sk", "if", "cfc",
    "club", "de", "the", "calcio", "sporting", "athletic", "atletico", "real", "sv", "vfb", "vfl", "tsg",
    "1", "04", "05", "07", "1899", "1909", "1846", "1860", "hellas",
}
_ALIASES = {
    "inter": "internazionale", "man city": "manchester city", "man united": "manchester united",
    "man utd": "manchester united", "spurs": "tottenham hotspur", "wolves": "wolverhampton wanderers",
    "psg": "paris saint germain", "brighton": "brighton hove albion",
}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " ").replace("-", " ").replace(".", " ").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = _ALIASES.get(s, s)
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks) or s


def similarity(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    short, long_ = sorted((na, nb), key=len)
    # "contenimento" solo se il nome corto è abbastanza specifico (evita "milan" ⊂ "inter milan")
    contain = 0.9 if short in long_ and len(short) >= 6 and len(short) / len(long_) >= 0.5 else 0.0
    return max(jacc, seq, contain)


def match_any(home_a, away_a, home_b: str, away_b: str) -> float:
    """Come match_pair, ma home_a/away_a possono essere liste di nomi alternativi."""
    ha = [home_a] if isinstance(home_a, str) else [x for x in home_a if x]
    aa = [away_a] if isinstance(away_a, str) else [x for x in away_a if x]
    return max(match_pair(h, a, home_b, away_b) for h in ha for a in aa)


def match_pair(home_a: str, away_a: str, home_b: str, away_b: str) -> float:
    direct = min(similarity(home_a, home_b), similarity(away_a, away_b))
    crossed = min(similarity(home_a, away_b), similarity(away_a, home_b))
    # se l'abbinamento incrociato è altrettanto buono il caso è ambiguo: meglio nessun abbinamento
    return direct if direct > crossed else 0.0


MATCH_THRESHOLD = 0.80
