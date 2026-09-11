# LinkedIn Post Finder

Web app per: caricare un elenco di soci/partner (.docx, .xlsx o .xls — ogni
voce può essere un nome azienda/ente oppure un indirizzo email), risolvere
ciascuna voce alla relativa pagina/profilo LinkedIn tramite
[Crustdata](https://crustdata.com), recuperare i post pubblicati nel periodo
scelto, e scaricare i risultati in due file Excel (vista settimanale e vista
dettagliata).

Architettura volutamente minimale, come richiesto:
- **Nessun database.** I risultati vivono solo in memoria, per la durata del
  processo backend, e vengono persi al riavvio del server.
- **Nessuna autenticazione, nessuna registrazione utente, nessun login.**
- **Frontend statico** (HTML/CSS/JS puro, nessun framework, nessuna build).
- **Backend** (Python/FastAPI) che parla con l'API reale di Crustdata usando
  una API key tenuta esclusivamente lato server.

## Sicurezza — dove sta la API key

La `CRUSTDATA_API_KEY` viene letta **solo** da variabile d'ambiente sul
server (`backend/crustdata_client.py`, funzione `_get_api_key`). Non compare
mai:
- nel codice del frontend (`frontend/*.html`, `*.js`, `*.css`);
- nelle risposte JSON/SSE che il backend invia al browser;
- in nessun file da committare nel repository (`.env` è escluso, vedi
  `.gitignore`; usa `.env.example` come modello).

Su un hosting a runtime persistente (Render, Railway, Fly.io, un VPS) imposta
`CRUSTDATA_API_KEY` nel pannello "Environment Variables" / "Secrets" del
servizio, mai in un file versionato.

## Provider per il recupero post (Crustdata o Apify)

Il matching nome azienda/email -> pagina LinkedIn resta sempre su Crustdata
(l'endpoint di matching è gratuito). Il recupero dei **post** (la parte che
consuma più crediti Crustdata: 1 credito per post restituito) può invece
essere fatto tramite [Apify](https://apify.com), impostando la variabile
d'ambiente opzionale `APIFY_API_TOKEN`. Se non impostata, l'app usa
Crustdata anche per i post (comportamento precedente, invariato).

## Accesso — password condivisa (opzionale)

L'app non ha login utente/registrazione, ma supporta una **password unica
condivisa** per impedire l'uso a chiunque trovi l'URL. Si attiva impostando
la variabile d'ambiente `APP_PASSWORD` sul backend (stesso posto di
`CRUSTDATA_API_KEY`): se non impostata, l'app resta aperta a chiunque abbia
il link (comportamento precedente).

Quando è attiva:
- il frontend mostra una schermata di accesso con un campo password prima di
  poter usare l'app;
- la password viene verificata dal backend (`/api/login`) e poi inviata
  dal browser a ogni chiamata API (header `X-App-Password`, o come query
  param solo per lo stream SSE che non supporta header custom);
- non è per-utente: tutti i colleghi condividono la stessa password, non
  c'è tracciamento di chi ha fatto cosa.

## Ricerca Soci/Partner (Cluster MINIT) — generale o per tematica

L'app ha una sola sezione, pensata per l'elenco soci/partner del Cluster,
caricabile come file **.docx**, **.xlsx** o **.xls**. Ogni voce del file può
essere un **nome azienda/ente** oppure un **indirizzo email**: il backend
rileva il tipo voce per voce (contiene "@" → email) e usa la risoluzione
Crustdata corrispondente — pagina LinkedIn aziendale (`/company/identify`)
per i nomi azienda, profilo LinkedIn personale (`/person/enrich`) per le
email — senza bisogno di due file o due sezioni separate.

- **Ricerca generale**: per ciascuna voce trova la pagina/il profilo
  LinkedIn corrispondente e recupera tutti i post pubblicati nel periodo
  scelto, senza alcun filtro.
- **Ricerca per tematica**: fa lo stesso, e in più richiede il **piano
  editoriale** (.xlsx, colonna "Area Tematica"). Ogni post recuperato viene
  classificato tramite l'API gratuita di **Google Gemini** sulle tematiche
  presenti nel piano editoriale caricato: nessuna tematica viene mai
  inventata, solo quelle effettivamente elencate nel file (o "Nessuna
  tematica" se il post non rientra in nessuna). Il risultato mostra sempre
  **tutti** i post trovati, con la tematica assegnata in una colonna
  dedicata; un filtro a tendina nella vista dettagliata permette di
  visualizzare solo una tematica alla volta, senza scartare gli altri dati.

Una voce che matcha più pagine/profili candidati (es. un acronimo che
corrisponde sia all'ente specifico sia a un'entità più generica) viene
segnalata come **"Match non verificato"**, mai scelta a caso.

Questa modalità richiede in più la variabile d'ambiente `GEMINI_API_KEY`
(vedi `.env.example`), usata **solo** per la classificazione dei post nella
ricerca per tematica — non serve per la ricerca generale. È una chiave
**gratuita** (nessuna carta di credito richiesta), ottenibile su
https://aistudio.google.com/apikey.

## Struttura del progetto

```
linkedin-scraper-app/
├── render.yaml               # Blueprint di deploy automatico per Render
├── backend/
│   ├── main.py                # API FastAPI + serve anche il frontend statico
│   ├── crustdata_client.py    # chiamate reali all'API Crustdata
│   ├── excel_utils.py         # lettura input / generazione output .xlsx
│   ├── docx_utils.py          # lettura elenco soci/partner da .docx
│   ├── gemini_classifier.py   # classificazione post per tematica (Gemini)
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── app.js
    ├── styles.css
    └── config.js               # nessun segreto qui
```

Il backend serve anche i file del frontend (montati da `main.py`): un solo
processo, un solo URL pubblico, nessuna configurazione CORS/dominio incrociato
da gestire in produzione.

## Avvio in locale

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# apri .env e incolla la tua CRUSTDATA_API_KEY
export $(grep -v '^#' .env | xargs)   # oppure usa python-dotenv/il tuo hosting
uvicorn main:app --reload --port 8000
```

Apri **http://localhost:8000** nel browser: è già l'app completa (frontend +
backend sullo stesso URL). Verifica anche che la chiave sia stata letta:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","crustdata_api_key_configured":true}
```

## Deploy in produzione su Render (Opzione B — hosting proprio)

Render offre un piano gratuito per servizi web Python, senza carta di
credito, con un pannello semplice per i secret. Il servizio gratuito "si
addormenta" dopo ~15 minuti di inattività e si riattiva in pochi secondi al
primo utilizzo successivo — comportamento adatto a uno strumento interno
usato da poche persone.

### 1. Metti il codice su GitHub

Sul tuo computer, dentro la cartella `linkedin-scraper-app/` (quella
scompattata dallo zip):

```bash
git init
git add .
git commit -m "Prima versione LinkedIn Post Finder"
git branch -M main
```

Su [github.com](https://github.com) crea un nuovo repository **vuoto**
(senza README/licenza, per evitare conflitti), es. `linkedin-post-finder`.
GitHub ti mostrerà un URL tipo `https://github.com/<tuo-utente>/linkedin-post-finder.git`:

```bash
git remote add origin https://github.com/<tuo-utente>/linkedin-post-finder.git
git push -u origin main
```

(`.env` non verrà mai caricato: è escluso da `.gitignore`.)

### 2. Collega il repository a Render

1. Vai su [dashboard.render.com](https://dashboard.render.com) → **New +** →
   **Blueprint**.
2. Collega il repository GitHub appena creato. Render leggerà
   automaticamente `render.yaml` e proporrà un servizio già configurato
   (root directory `backend`, build/start command corretti, piano gratuito).
3. Quando richiesto, incolla il valore della tua `CRUSTDATA_API_KEY` nel
   campo dedicato — Render la salva come secret cifrato, non nel codice.
4. Conferma e avvia il deploy ("Apply"/"Create").

Se preferisci non usare il Blueprint, puoi configurare manualmente un
singolo **Web Service**: New + → Web Service → collega il repo → Root
Directory `backend` → Build Command `pip install -r requirements.txt` →
Start Command `uvicorn main:app --host 0.0.0.0 --port $PORT` → aggiungi la
variabile d'ambiente `CRUSTDATA_API_KEY` in "Environment".

### 3. Verifica e usa l'app

A fine deploy Render mostra un URL pubblico (tipo
`https://linkedin-post-finder.onrender.com`). Apri quell'URL:
- `/api/health` deve rispondere `{"status":"ok","crustdata_api_key_configured":true}`;
- la pagina principale è l'app stessa — nessun'altra configurazione serve.

Condividi quell'unico URL con i tuoi due colleghi: non serve installare
nulla, basta il browser.

### Aggiornare l'app in futuro

Basta fare `git push` sullo stesso repository: Render rilancia
automaticamente il deploy con l'ultima versione del codice (comportamento
di default, disattivabile dal pannello se preferisci deploy manuali).

## Cosa succede quando lanci una ricerca

1. Carichi un file `.docx`, `.xlsx` o `.xls` con l'elenco soci/partner (nomi
   azienda e/o indirizzi email).
2. Scegli il periodo (7 / 14 / 30 giorni, o intervallo personalizzato) ed
   eventualmente il piano editoriale per la ricerca per tematica.
3. Il backend, per ciascuna voce, in ordine:
   - rileva se la voce è un indirizzo email (contiene "@") o un nome
     azienda/ente, e usa la risoluzione Crustdata corrispondente
     (`/person/enrich` per le email, `/company/identify` per i nomi
     azienda — vedi nota tecnica sotto per il fallback v1 sulle email);
   - se trova **esattamente un** profilo/pagina con confidenza sufficiente,
     lo usa;
   - se non trova nulla → riga marcata **"Profilo non trovato"** /
     **"Pagina non trovata"**;
   - se trova **più candidati** e non c'è un criterio per scegliere in modo
     affidabile → riga marcata **"Match non verificato"** (non viene mai
     scelto un candidato a caso);
   - se risolto, recupera i post pubblicati nel periodo scelto (Crustdata o
     Apify, vedi sopra), **senza eliminare** eventuali post multipli nello
     stesso giorno;
   - se il profilo/pagina esiste ma non ha post nel periodo → **"Nessun
     post trovato"**.
4. Vedi l'avanzamento in tempo reale ("Elaborazione 12/56", "Ricerca
   profilo/pagina...", ecc.) e un riepilogo finale.
5. Scarichi i due file Excel dai pulsanti dedicati.

## Nota tecnica onesta su un dettaglio non verificabile al 100%

Durante lo sviluppo ho verificato sulla documentazione pubblica ufficiale di
Crustdata (e con chiamate reali, dati reali restituiti) che:
- l'endpoint v2 `POST /person/enrich` e l'endpoint v1
  `GET /screener/linkedin_posts` funzionano con autenticazione
  `Authorization: Bearer <API_KEY>` (v2 richiede anche l'header
  `x-api-version: 2025-11-01`).

Un solo endpoint — quello v1 di fallback per la risoluzione email→profilo
(`GET /screener/person/enrich`, usato solo se l'email non è un'email
aziendale riconosciuta da v2) — non ha un path confermabile al 100% dalla
documentazione pubblica, perché la pagina con il dettaglio di quell'endpoint
è protetta da login a cui non ho accesso. Il codice (`backend/crustdata_client.py`,
funzione `v1_person_enrich`) lo isola e, se quell'endpoint risponde 404,
lo segnala esplicitamente come "endpoint non trovato: verificare il path" —
non lo confonde mai con un "profilo non trovato". Se dopo il deploy vedi
questo errore specifico, verifica il path corretto sul pannello/documentazione
del tuo account Crustdata e aggiorna quella singola funzione: è isolata proprio
per rendere questa eventuale correzione rapida e localizzata.

## Costi Crustdata (indicativi, verifica sempre con `crustdata_credit_costs`)

- Risoluzione email→profilo via v2: economica (parte da 1 credito), copre
  email aziendali.
- Risoluzione email→profilo via v1 (fallback): più costosa (~3 crediti a
  profilo), copre anche email personali/istituzionali.
- Recupero post: fatturato per post effettivamente restituito, non per
  chiamata — per questo il backend interrompe la paginazione appena esce dal
  periodo richiesto, invece di scaricare l'intera cronologia dei post.

## Nessun dato inventato

In nessun punto del codice viene generato un profilo, un post o un risultato
non proveniente da una risposta reale dell'API Crustdata. Ogni stato mostrato
all'utente ("Profilo non trovato", "Match non verificato", "Nessun post
trovato", "Errore: ...") riflette esattamente cosa ha risposto l'API.
