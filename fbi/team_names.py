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

# Nazionali: nome italiano (le chiavi sono i displayName ESPN esatti)
NATIONAL = {
    "Italy": "Italia", "Belgium": "Belgio", "Spain": "Spagna", "England": "Inghilterra", "France": "Francia",
    "Türkiye": "Turchia", "Turkey": "Turchia", "Croatia": "Croazia", "Czechia": "Repubblica Ceca", "Germany": "Germania",
    "Netherlands": "Paesi Bassi", "Switzerland": "Svizzera", "Austria": "Austria", "Denmark": "Danimarca", "Sweden": "Svezia",
    "Norway": "Norvegia", "Finland": "Finlandia", "Poland": "Polonia", "Hungary": "Ungheria", "Romania": "Romania",
    "Greece": "Grecia", "Serbia": "Serbia", "Scotland": "Scozia", "Wales": "Galles", "Northern Ireland": "Irlanda del Nord",
    "Republic of Ireland": "Irlanda", "Iceland": "Islanda", "Slovakia": "Slovacchia", "Slovenia": "Slovenia",
    "Ukraine": "Ucraina", "Georgia": "Georgia", "Albania": "Albania", "Belarus": "Bielorussia", "Cyprus": "Cipro",
    "Montenegro": "Montenegro", "Latvia": "Lettonia", "Lithuania": "Lituania", "Estonia": "Estonia", "Armenia": "Armenia",
    "San Marino": "San Marino", "North Macedonia": "Macedonia del Nord", "Bosnia-Herzegovina": "Bosnia ed Erzegovina",
    "Bulgaria": "Bulgaria", "Israel": "Israele", "Kosovo": "Kosovo", "Luxembourg": "Lussemburgo", "Moldova": "Moldavia",
    "Azerbaijan": "Azerbaigian", "Kazakhstan": "Kazakistan", "Faroe Islands": "Isole Faroe", "Malta": "Malta",
    "Andorra": "Andorra", "Gibraltar": "Gibilterra", "Liechtenstein": "Liechtenstein", "Portugal": "Portogallo",
    "Brazil": "Brasile", "Argentina": "Argentina", "Mexico": "Messico", "United States": "Stati Uniti", "USA": "Stati Uniti",
    "Morocco": "Marocco", "Japan": "Giappone", "South Korea": "Corea del Sud",
}


def is_national_competition(competition: str | None) -> bool:
    c = competition or ""
    return any(w in c for w in ("Nations League", "International Friendly", "World Cup Qualifying", "Euro Qualifying"))


def display(name: str | None) -> str | None:
    return DISPLAY.get(name, name) if name else name
