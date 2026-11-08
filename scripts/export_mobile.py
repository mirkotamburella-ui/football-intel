"""Esporta la dashboard come UNICA pagina HTML statica con i dati incorporati.

Serve per consultarla dal telefono senza server (es. pubblicandola come pagina privata).
È un'istantanea: per dati aggiornati va rigenerata dopo `python -m fbi run` / `refresh`.

Uso: python scripts/export_mobile.py [percorso_output.html]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, DATA = ROOT / "web", ROOT / "data"


def safe_json(obj) -> str:
    # impedisce che "</script>" dentro i dati chiuda il tag
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main(out: Path) -> None:
    report = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    try:
        history = json.loads((DATA / "history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = None
    html = (WEB / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    body = re.sub(r'<script src="app.js"></script>', "", body)
    css = (WEB / "style.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    page = f"""<title>Football Intelligence</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{css}</style>
{body}
<script>document.body.classList.add("embedded");window.FBI_EMBED={{"report":{safe_json(report)},"history":{safe_json(history)}}};</script>
<script>{js}</script>
"""
    out.write_text(page, encoding="utf-8")
    print(f"scritto {out} ({out.stat().st_size / 1e6:.1f} MB) – analisi del {report['generated_at_local']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "football-intel-mobile.html")
