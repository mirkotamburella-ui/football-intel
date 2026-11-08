"""Esporta la dashboard come UNICA pagina HTML statica con i dati incorporati.

Serve per consultarla dal telefono senza server (es. pubblicandola come pagina privata).
È un'istantanea: per dati aggiornati va rigenerata dopo `python -m fbi run` / `refresh`.

Uso: python scripts/export_mobile.py [percorso_output.html]
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, DATA = ROOT / "web", ROOT / "data"


def safe_json(obj) -> str:
    # impedisce che "</script>" dentro i dati chiuda il tag
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main(out: Path, report_path: Path, history_path: Path | None, nav: str = "") -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path else None
    except (OSError, ValueError):
        history = None
    html = (WEB / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    body = re.sub(r'<script src="app.js"></script>', "", body)
    css = (WEB / "style.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    links = {"oggi": ("index.html", "Oggi"), "settimana": ("settimana.html", "Settimana")}
    navhtml = ""
    if nav:
        navhtml = '<nav class="pages">' + "".join(
            f'<a href="{h}" class="{"on" if k == nav else ""}">{t}</a>' for k, (h, t) in links.items()) + "</nav>"
    body = body.replace('<div class="rg" role="note">', navhtml + '<div class="rg" role="note">', 1)
    page = f"""<title>Football Intelligence</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta charset="utf-8">
<style>{css}</style>
{body}
<script>document.body.classList.add("embedded");window.FBI_EMBED={{"report":{safe_json(report)},"history":{safe_json(history)}}};</script>
<script>{js}</script>
"""
    out.write_text(page, encoding="utf-8")
    print(f"scritto {out} ({out.stat().st_size / 1e6:.1f} MB) – analisi del {report['generated_at_local']}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(DATA / "football-intel-mobile.html"))
    ap.add_argument("--report", default=str(DATA / "latest.json"))
    ap.add_argument("--history", default=str(DATA / "history.json"))
    ap.add_argument("--nav", default="", help="'oggi' o 'settimana': aggiunge i link tra le due pagine")
    a = ap.parse_args()
    main(Path(a.out), Path(a.report), Path(a.history) if a.history else None, a.nav)
