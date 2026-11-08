"""Nomi squadra mostrati all'utente.

ESPN usa spesso nomi inglesi o senza diacritici ("FC Cologne", "Bodo/Glimt"). Per la
visualizzazione si usa il nome ufficiale locale; il nome ESPN originale resta nei dati
(`espn_name`) e viene usato anche per abbinare le partite tra provider diversi.
Le chiavi sono il `displayName` ESPN esatto (non sottostringhe), quindi "Athletic"
(Brasile) non tocca "Oldham Athletic".
"""
DISPLAY = {
    # Italia
    "Internazionale": "Inter", "AC Milan": "Milan", "AS Roma": "Roma",
    # Germania / Austria / Svizzera
    "FC Cologne": "1. FC Köln", "Bayern Munich": "Bayern München", "Borussia Monchengladbach": "Borussia Mönchengladbach",
    "1860 Munich": "1860 München", "Fortuna Dusseldorf": "Fortuna Düsseldorf", "SpVgg Greuther Furth": "SpVgg Greuther Fürth",
    "Nurnberg": "1. FC Nürnberg", "Austria Vienna": "Austria Wien", "Rapid Vienna": "Rapid Wien", "FC Zurich": "FC Zürich",
    # Portogallo / Spagna
    "Vitória de Guimaraes": "Vitória de Guimarães", "Estrela": "Estrela da Amadora", "C.D. Nacional": "Nacional",
    "Celta Vigo": "Celta", "Alavés": "Deportivo Alavés",
    # Paesi Bassi / Belgio
    "Ajax Amsterdam": "Ajax", "Union St.-Gilloise": "Union Saint-Gilloise",
    # Grecia
    "AEK Athens": "AEK Atene", "OFI Crete": "OFI Creta", "Asteras Tripoli": "Asteras Tripolis",
    # Scandinavia
    "Bodo/Glimt": "Bodø/Glimt", "Tromso": "Tromsø", "Lillestrom": "Lillestrøm", "Stromsgodset": "Strømsgodset",
    "Malmo FF": "Malmö FF", "IFK Goteborg": "IFK Göteborg", "BK Hacken": "BK Häcken", "Djurgarden": "Djurgården",
    # Turchia
    "Besiktas": "Beşiktaş", "Fenerbahce": "Fenerbahçe", "Genclerbirligi": "Gençlerbirliği", "Basaksehir": "Başakşehir",
    "Kasimpasa": "Kasımpaşa", "Goztepe": "Göztepe", "Eyupspor": "Eyüpspor", "Caykur Rizespor": "Çaykur Rizespor",
    # Est Europa
    "Red Star Belgrade": "Crvena zvezda", "Dynamo Kyiv": "Dinamo Kiev", "Dinamo Zagreb": "Dinamo Zagabria",
    # Brasile
    "Athletic": "Athletic Club (MG)",
}


def display(name: str | None) -> str | None:
    return DISPLAY.get(name, name) if name else name
