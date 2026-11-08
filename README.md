# ⚽ Football Betting Intelligence — sistema multi-agent

Sistema di **analisi statistica** delle partite di calcio del giorno in tutto il mondo, composto da
**12 agenti indipendenti** che lavorano in parallelo, con dashboard web ottimizzata per smartphone.

> **Solo ricerca e simulazione.** Il sistema non piazza scommesse, non si collega a conti di gioco e non
> automatizza puntate. Le probabilità sono stime con incertezza: **si può perdere l'intera puntata**.
> Budget di riferimento massimo **10 €** (limite imposto anche nel codice: `STAKE_EUR > 10` blocca l'avvio).
> Niente recupero delle perdite, raddoppi o martingale. 18+ · Telefono Verde Nazionale 800 558822.

La priorità è la **qualità dell'analisi**: se meno di 5 selezioni superano tutti i controlli, il sistema
dichiara che oggi non è possibile costruire una multipla conforme e **non abbassa i criteri**.

---

## Avvio rapido

```bash
cd football-intel
pip install -r requirements.txt        # unica dipendenza: numpy (Python ≥ 3.10)
cp .env.example .env                   # opzionale: chiavi API e soglie
python -m fbi run                      # analisi completa della giornata (~20–40 s)
python -m fbi serve                    # dashboard su http://localhost:8365
```

Dallo smartphone: apri `http://<IP-del-computer>:8365` sulla stessa rete Wi-Fi (oppure pubblica la
porta dietro un reverse proxy con HTTPS). La pagina è installabile come app (manifest PWA).

| Comando | Cosa fa |
|---|---|
| `python -m fbi run` | procedura completa: ricerca partite → dati → 12 agenti → QC → multiple → report |
| `python -m fbi refresh` | aggiorna quote e formazioni prima dei calci d'inizio, esclude le partite iniziate e segnala le variazioni |
| `python -m fbi verify` | a fine giornata scarica i risultati, chiude le previsioni e aggiorna le statistiche di accuratezza |
| `python -m fbi serve` | dashboard web mobile + API JSON |
| `python -m fbi daemon --at 08:30` | automazione: analisi al mattino, refresh orari, verifica serale |
| `python -m fbi run --max-matches 10` | run rapido su poche partite |
| `python -m fbi run --offline` | usa solo la cache locale (nessuna richiesta di rete) |

---

## Architettura

```
                ┌──────────────────────────── Orchestratore (DAG, thread pool) ───────────────────────────┐
 Stadio 1       │  A01 Global Fixture Scout ── partite di oggi (ora italiana), dedup, esclusione iniziate │
                │        │                                                                                │
 Stadio 2       │  ┌─────┼──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐ (paralleli)  │
                │  A02 Forma  A03 Off/Def  A04 H2H  A05 Formazioni  A06 Motivazioni  A07 Quote  A08 Modelli │
                │        │                                          │          │                          │
 Stadio 3       │        └──────────────────────────────── A09 Value (EV) ◄────┘                          │
 Stadio 4       │                                  A10 Risk Manager  ‖  A11 Contrarian      (paralleli)   │
 Stadio 5       │                                  A12 Final Strategist → scoring, QC, multiple, report   │
                └────────────────────────────────────────────────────────────────────────────────────────┘
   Client HTTP condiviso: cache su disco con TTL · rate limit · retry con backoff · deduplica richieste concorrenti
```

**Gli agenti sono realmente indipendenti**: ogni agente è un modulo (`fbi/agents/aNN_*.py`) che riceve solo
il contesto in sola lettura e i report JSON degli agenti da cui dichiara di dipendere, scarica in proprio i
dati di cui ha bisogno e restituisce un report JSON con schema fisso (`agent_id`, `status`, `summary`,
`matches`, `sources`, `warnings`, `errors`, timestamp). Non condividono stato mutabile. Gli agenti A02–A08
girano contemporaneamente (verificato dal test `test_parallel_stage` e visibile nella timeline del report).

Sono agenti **statistici e deterministici**, non modelli linguistici: ogni affermazione nei report è
calcolata da dati con fonte e timestamp. Questo è voluto, per evitare dati o motivazioni "inventate".

| Agente | Compito | Dati usati |
|---|---|---|
| A01 Global Fixture Scout | tutte le partite di oggi (00:00–23:59 ora italiana), competizione, orario, stato; deduplica; esclude iniziate/concluse/rinviate/senza quote; copertura per area geografica | scoreboard ESPN (3 giornate per coprire i fusi) |
| A02 Team Form Analyst | ultime 5 e 10 (V/N/P, gol), casa/trasferta, continuità (dev. std punti), qualità avversari (ppg avversari) → indice di forma | calendari squadra, ultime 5 in tutte le competizioni, classifica |
| A03 Offensive & Defensive | gol fatti/subiti (totali e casa/trasferta), clean sheet, % senza segnare, frequenze Over/BTTS, xG/xGA (se API-Football), stima Poisson da tassi osservati | calendari, classifica, API-Football |
| A04 Head to Head | ultimi 5 confronti con peso decrescente (emivita 1,5 anni), Over/Under, BTTS, risultati ricorrenti, anomalie, campione effettivo | ESPN season series |
| A05 Lineup & Injury | formazioni (ufficiali solo se pubblicate), assenti e dubbi; ogni dato marcato `ufficiale` / `provider` / `indiscrezione`; dichiara ciò che manca (cambi allenatore, peso giocatori chiave) | ESPN rosters/injuries, API-Football injuries |
| A06 Motivation | posizione, obiettivi dalle zone di classifica (titolo, Europa, promozione, salvezza), indice di motivazione, rischio turnover dai giorni di riposo | classifica completa, ultime partite |
| A07 Odds Intelligence | quote 1X2, doppia chance, Over/Under (1.5, 2.5, 3.5…), Goal/No Goal, DNB; confronto bookmaker, movimenti apertura→attuale, probabilità implicite, margine e de-vig (metodo *power*) | ESPN (DraftKings, Bet365), The Odds API |
| A08 Mathematical Prediction | Poisson Dixon–Coles, regressione di Poisson (ridge, pesi temporali), modello xG, Elo, Monte Carlo (intervalli 90% e simulazione) | risultati di 2 stagioni per competizione |
| A09 Value Betting | probabilità stimata = mix modello/mercato pesato sulla qualità dei dati; EV = p × quota − 1 (DNB con rimborso) | A07 + A08 |
| A10 Risk Manager | qualità dati 0–1, tier del campionato, esclusioni (dati insufficienti, quote > 3.50, p < 35%, pareggio secco, modelli in disaccordo), livelli di rischio, politica di correlazione | tutti |
| A11 Contrarian | per ogni selezione cerca motivi per cui può perdere (forma avversaria, trend gol contrari, quote in salita, H2H, motivazioni, assenze, campione piccolo, disaccordo bookmaker, EV negativo) → penalità fino a 10 punti | tutti |
| A12 Final Strategist | punteggio 0–100, controllo qualità, classifica, multipla principale (5–10), alternativa prudente (5), 5 migliori singole, motivazioni e rischi per ogni scelta | tutti |

---

## Fonti dati, costi e limiti

Ogni quota, statistica e risultato nel report porta **URL della fonte e timestamp** di recupero
(le chiavi API sono oscurate). Quando un dato non è disponibile, viene dichiarato esplicitamente.

| Fonte | Chiave | Cosa fornisce al sistema | Costi / limiti | Stato |
|---|---|---|---|---|
| **ESPN** (endpoint JSON pubblici) | no | calendario mondiale (~50 competizioni/giorno), risultati per squadra, classifiche complete con zone, ultime 5, H2H, formazioni quando pubblicate, quote DraftKings e Bet365 | gratuito, non documentato ufficialmente (può cambiare senza preavviso); il client usa cache e rate limit | **attivo**, verificato |
| **The Odds API** | `ODDS_API_KEY` | quote di molti bookmaker EU: 1X2, Over/Under su più linee, Goal/No Goal, DNB, doppia chance | free 500 crediti/mese; costo = mercati × regioni per chiamata; tetto per run configurabile | implementato, testato con risposte simulate |
| **API-Football v3** | `API_FOOTBALL_KEY` | infortuni/squalifiche per partita, xG/xGA dalle statistiche delle ultime 5 partite | free 100 richieste/giorno; tetto per run; priorità ai campionati principali | implementato, testato con risposte simulate |
| Sportmonks | — | valutata: copertura ampia e xG, ma piano free limitato a pochi campionati e piani a pagamento | — | non integrata |
| football-data.org | — | valutata: free 10 richieste/min ma solo ~12 competizioni, niente quote/xG/infortuni | — | non integrata (ESPN copre di più) |

Prezzi e limiti dei piani vanno ricontrollati sui siti dei provider: cambiano nel tempo.

**Limiti dichiarati senza chiavi API**: niente xG/xGA, niente tiri/occasioni, infortuni quasi sempre non
disponibili, quote giocabili solo da Bet365 (DraftKings opera solo negli USA ed è usato solo per il consenso
di mercato). Con le sole quote Bet365, il cui margine sull'1X2 è spesso del 5–7%, è raro che una selezione
abbia EV positivo: **per un uso reale si consiglia `ODDS_API_KEY`**, che permette di confrontare più
bookmaker autorizzati.

**Bookmaker "giocabili"**: `PREFERRED_BOOKMAKERS` (default: bet365, williamhill, unibet_eu, betfair_ex_eu,
sport888, betsson, leovegas). La quota usata è la migliore tra questi; l'elenco va verificato sul sito ADM.

---

## Metodologia

### Modelli (A08)
- **Strength**: λ casa = media gol casa di lega × attacco casa × difesa ospite (tassi casa/trasferta delle
  ultime 20 partite, *shrinkage* di 6 pseudo-partite verso la media di lega).
- **Regressione di Poisson**: `log λ = μ + casa + attacco_i − difesa_j`, stimata con Newton–Raphson,
  penalità ridge e pesi temporali (emivita 180 giorni) su due stagioni di risultati.
- **xG**: Poisson su xG/xGA medi (solo con API-Football).
- **Elo**: formula World Football Elo (K=24, vantaggio campo 60, moltiplicatore per scarto reti); 1X2 con
  modello di pareggio dipendente dall'equilibrio e dal tasso di pareggi della lega.
- **Ensemble**: media geometrica pesata dei λ (GLM 0,55 · strength 0,20 · xG 0,25), matrice dei risultati
  Dixon–Coles; Elo mescolato sull'1X2 con peso ≤ 0,3 proporzionale al campione.
- **Monte Carlo**: incertezza sui λ (Gamma, CV ∝ 1/√partite) → intervallo 90% per ogni probabilità;
  simulazione diretta dei risultati come verifica del calcolo analitico.

Verifica sul campione reale dell'8/11/2026 (107 partite con quote): scarto medio assoluto dalla probabilità
equa di mercato sull'"1" = 6,2 pp per l'ensemble (GLM 6,1 · Elo 7,8 · strength 9,1).

### Probabilità stimata ed Expected Value (A09)
```
p_stimata = w · p_modello + (1 − w) · p_mercato_equa,   w = 0,40 × qualità_campione × accordo_modelli
EV        = p_stimata × quota − 1          (DNB: + p(pareggio) per il rimborso)
```
Il mercato senza margine è uno stimatore molto forte: il modello lo corregge solo in proporzione alla
qualità dei dati. Una quota alta senza vantaggio stimato non è "valore".

### Scoring 0–100 (trasparente, ogni componente con spiegazione nel report)
| Componente | Max | Calcolo |
|---|---|---|
| Forma recente (A02) | 15 | conferma rispetto al mercato: 7,5 se la forma concorda col mercato, 15 se vede +15 pp |
| Statistiche offensive e difensive (A03) | 20 | conferma dei tassi gol osservati: 10 se concordano col mercato, 20 a +10 pp |
| xG e modelli previsionali (A08) | 20 | conferma dei modelli: 10 → 20 a +10 pp, meno punti se i modelli sono in disaccordo |
| Formazioni e indisponibili (A05) | 10 | 5 se dati assenti (neutro, non positivo); 8 ± assenze; +2 formazioni ufficiali |
| Motivazioni (A06) | 10 | 5 + 5 × differenza indice motivazione; −2 se rischio turnover |
| Valore della quota (A09) | 15 | EV −5% → 0 punti … EV +10% → 15 punti |
| Affidabilità dei dati (A10) | 10 | 10 × qualità dati |

Le componenti statistiche non premiano la probabilità in sé (vincerebbero sempre le quote 1,10), ma quanto
ogni fonte indipendente conferma che la selezione è più probabile di quanto dica il mercato senza margine.
Ogni scheda riporta anche il profilo del campionato (gol a partita, pareggi, vittorie in casa, Over 2.5),
così la scelta è letta nel contesto della sua lega.

Punteggio finale = base − penalità Contrarian (max 10; divari modello/mercato ≥ 15 pp pesano 4 punti). **Un punteggio alto non è una garanzia di vincita.**

### Controllo qualità (una selezione entra in multipla solo se supera TUTTO)
quota con fonte e timestamp e aggiornata da ≤ 90 min · bookmaker preferito · partita verificata, pre-match e
a ≥ 10 min dal via · modello statistico disponibile (niente scelte "a intuito") · ≥ 5 partite di campionato
per squadra · qualità dati ≥ 0,55 · campionato non tier 3 · punteggio ≥ 60 · EV ≥ 0 · quota ≥ 1,20 · p ≥ 35% ·
quota ≤ 3,50 · niente pareggio secco · modelli non in forte disaccordo · una sola selezione per partita · massimo 2 selezioni per campionato (`MAX_PER_LEAGUE`).
Le probabilità sono mostrate al punto percentuale e sempre con intervallo (niente falsa precisione).

### Multiple
- **Principale**: migliore selezione per partita ordinata per punteggio; le prime 5 obbligatorie, dalla 6ª
  alla 10ª solo con punteggio ≥ 68 e probabilità congiunta ≥ 2%.
- **Alternativa prudente**: le 5 selezioni conformi con probabilità più alta (≥ 62%), confronto con la principale.
- **5 migliori singole** della giornata.
- **Probabilità congiunta**: Monte Carlo con **copula gaussiana** (correlazione 0,05 tra partite dello stesso
  campionato, 0,08 se anche alla stessa ora; stessa partita vietata) e incertezza Beta su ogni gamba →
  stima, intervallo 90% e confronto con il semplice prodotto. Il ritorno lordo è `10 € × quota totale`.

### Storico e accuratezza
`verify` chiude le previsioni con i risultati ESPN (fonte e timestamp salvati): esito per gamba
(vinta/persa/rimborso/nulla), esito multipla, P/L **simulato**, tasso di successo, Brier score e tabella di
calibrazione. Una previsione è bloccata appena inizia una sua partita (niente ritocchi a posteriori).

---

## Dashboard (smartphone)

- **Oggi**: data/ora dell'analisi, partite trovate e analizzate, competizioni, agenti attivi, migliori
  opportunità (le non conformi sono marcate "Non consigliata" con il motivo), copertura per area, partite escluse.
- **Multiple**: principale, alternativa, 5 singole, quote, probabilità con intervallo, ritorno su 10 €,
  probabilità di perdere la puntata, statistiche dei motivi di esclusione.
- **Partite**: ricerca e filtri; scheda con squadre, campionato, orario, mercato consigliato, quota con fonte,
  probabilità stimata, punteggio AI scomposto, affidabilità, motivazione, rischi e tutti i mercati valutati.
- **Agenti**: stato, durata, timeline di esecuzione parallela, sintesi, avvisi/errori, report JSON completo.
- **Storico**: accuratezza, calibrazione, P/L simulato, giornate passate.

Per avviare analisi dal telefono imposta `DASHBOARD_TOKEN` in `.env`: comparirà il pulsante ▶ (il token viene
inviato nell'header `X-Token`). Senza token gli endpoint di scrittura sono disabilitati.

API JSON: `/api/latest`, `/api/history`, `/api/reports`, `/api/reports/<run_id>`, `/api/agents/<data>/<A01..A12>`.

---

## Automazione quotidiana

`scripts/crontab.example` (con `scripts/daily.sh`) implementa la procedura richiesta:
08:30 analisi completa (passi 1–7) · ogni ora 11–22 refresh di quote e formazioni (passo 8) · 23:50 e 08:00
verifica dei risultati (passo 9). In alternativa `python -m fbi daemon --at 08:30 --refresh-every 60`.

## Aggiornamento automatico gratuito (GitHub Actions + GitHub Pages)

`.github/workflows/analisi.yml` esegue tutto sui server di GitHub, senza computer acceso e senza usare Claude:
alle 08:30 analisi completa, alle 12:30, 15:30, 18:30 e 21:00 aggiornamento di quote e formazioni, alle 23:45
verifica dei risultati (ora solare italiana; con l'ora legale un'ora dopo). Ogni giro verifica le partite
concluse, rifà l'analisi, salva lo storico nel branch `data` e pubblica la dashboard su
`https://<utente>.github.io/football-intel/` (pagina statica con i dati incorporati, `noindex`).

Attivazione (una volta sola):
1. Sul piano GitHub gratuito Pages richiede un repository **pubblico** (Settings → General → Change visibility).
   Le chiavi API non finiscono mai nel codice: vanno in Settings → Secrets and variables → Actions
   (`ODDS_API_KEY`, `API_FOOTBALL_KEY`, entrambe facoltative).
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Actions → "Analisi automatica" → **Run workflow** per il primo giro.

Ogni giro dura 1–2 minuti: circa 300 minuti al mese, nei limiti gratuiti di GitHub Actions.

## Output su disco
```
data/latest.json                      ultimo report (letto dalla dashboard)
data/reports/<data>/report_<run>.json report di ogni run
data/reports/<data>/agents/Axx.json   report JSON completo di ogni agente
data/history.json                     storico previsioni e accuratezza
data/cache/                           cache HTTP (TTL: quote 10 min, statistiche 6 h)
data/logs/fbi.log                     log con rotazione
```

## Test
```bash
python -m unittest discover -s tests -t .
```
35 test: matematica (de-vig, Poisson, GLM, Elo, Monte Carlo, copula), regole (budget ≤ 10 €, settlement di
tutti i mercati, QC, multiple 5–10 senza doppioni, correlazioni), parser su payload reali, provider a chiave
con risposte simulate, e un **test end-to-end offline** in cui i 12 agenti girano su risposte reali ESPN
catturate l'8/11/2026 (`tests/fixtures/e2e`, ridotte con `scripts/build_test_fixture.py`).

## Configurazione
Tutte le soglie sono in `.env` / `fbi/config.py` (vedi `.env.example`). Le chiavi API vanno **solo** in `.env`
(già escluso da git) o in variabili d'ambiente, mai nel codice.

## Limiti noti
- ESPN non è un'API ufficiale: la struttura delle risposte può cambiare (i parser falliscono in modo esplicito
  e il report lo dichiara).
- I modelli si basano sui gol (e sugli xG se disponibili): non vedono infortuni non riportati, notizie,
  meteo o arbitri. Per questo il mercato pesa sempre almeno il 60% della stima.
- I pesi dell'ensemble e le soglie sono ipotesi iniziali da ricalibrare con lo storico (`verify`): servono
  centinaia di selezioni prima che le statistiche di accuratezza siano significative.
