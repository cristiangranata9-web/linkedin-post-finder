"""
Client HTTP per le API REST di Crustdata.

IMPORTANTE (trasparenza tecnica):
- Gli endpoint v2 (`/person/enrich`) sono stati verificati sulla documentazione
  pubblica ufficiale di Crustdata: POST https://api.crustdata.com/person/enrich,
  autenticazione `Authorization: Bearer <API_KEY>` + header `x-api-version: 2025-11-01`.
- L'endpoint v1 per i post LinkedIn (`/screener/linkedin_posts`) è stato verificato
  con dati reali (post effettivamente restituiti durante lo sviluppo) e usa lo
  stesso schema di autenticazione Bearer.
- L'endpoint v1 per la risoluzione email -> profilo (`/screener/person/enrich`)
  segue la stessa convenzione dei path "/screener/..." documentata da Crustdata
  per l'API v1, ma il suo path esatto NON è stato confermato riga per riga sulla
  documentazione pubblica (la pagina con il dettaglio di autenticazione v1 è protetta
  da login). Il codice qui sotto isola questa chiamata (`v1_person_enrich`) e
  distingue esplicitamente un 404 ("endpoint non trovato: verificare il path")
  da un "nessun match" o da un errore generico, così un eventuale problema non
  viene mai mascherato da un risultato silenzioso o inventato.

Nessuna chiamata qui restituisce mai dati simulati: se una richiesta fallisce,
viene propagata un'eccezione con un messaggio esplicito.
"""
from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

import httpx

CRUSTDATA_BASE_URL = "https://api.crustdata.com"

# Suffissi/forme societarie generiche che su LinkedIn di norma NON compaiono
# nel nome della pagina aziendale (es. "OpenEconomics" e non "OpenEconomics
# Srl"). Se lasciati nella query, l'endpoint fuzzy /company/identify li
# tratta come parole di ricerca a sé stanti e restituisce centinaia di
# aziende nel mondo che contengono lo stesso acronimo (es. "Srl" compare
# anche in ragioni sociali sudamericane, indiane, ecc.), annegando il match
# corretto in mezzo a rumore. Vengono rimossi solo come TOKEN separati (word
# boundary), mai come sottostringa, per non alterare nomi propri.
_LEGAL_SUFFIX_TOKENS = [
    r"s\.?\s*r\.?\s*l\.?\s*s\.?",  # Srls / S.r.l.s.
    r"s\.?\s*r\.?\s*l\.?",  # Srl / S.r.l.
    r"s\.?\s*p\.?\s*a\.?",  # SpA / S.p.A.
    r"s\.?\s*a\.?\s*s\.?",  # Sas / S.a.s.
    r"s\.?\s*n\.?\s*c\.?",  # Snc / S.n.c.
    r"s\.?\s*c\.?\s*a\.?\s*r\.?\s*l\.?",  # Scarl
    r"soc\.?\s*coop\.?(\s*a\s*r\s*l\.?)?",
    r"cooperativa",
    r"soc(iet[aà])?\.?",
    r"aps",
    r"ets",
    r"onlus",
    r"aisbl",
    r"ltd",
    r"inc",
    r"llc",
    r"gmbh",
]
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(_LEGAL_SUFFIX_TOKENS) + r")\b\.?", re.IGNORECASE
)


def _strip_legal_suffixes(name: str) -> str:
    """Rimuove forme societarie generiche (Srl, SPA, Soc. Coop., ecc.) da un
    nome azienda, per aumentare le probabilità di match esatto col nome
    usato sulla pagina LinkedIn. Non inventa né modifica il resto del nome."""
    cleaned = _LEGAL_SUFFIX_RE.sub(" ", name)
    cleaned = re.sub(r"[.,]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or name


def _normalize_for_compare(name: str) -> str:
    """Normalizza un nome azienda per confronti case/accenti/punteggiatura
    -insensitive (usato solo per verificare che un candidato fuzzy
    corrisponda davvero al nome cercato, mai per indovinare tra ambiguità
    reali)."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_only = re.sub(r"[^a-z0-9 ]", " ", ascii_only.lower())
    return re.sub(r"\s+", " ", ascii_only).strip()


class CrustdataConfigError(RuntimeError):
    """Sollevato quando manca la configurazione server-side (es. API key)."""


class CrustdataAPIError(RuntimeError):
    """Sollevato quando una chiamata reale a Crustdata fallisce o è inconcludente."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _get_api_key() -> str:
    api_key = os.environ.get("CRUSTDATA_API_KEY")
    if not api_key:
        raise CrustdataConfigError(
            "CRUSTDATA_API_KEY non impostata come variabile d'ambiente lato server. "
            "L'app non può contattare Crustdata senza questa chiave. "
            "Vedi README.md per come configurarla in modo sicuro."
        )
    return api_key


def _headers(v2: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }
    if v2:
        headers["x-api-version"] = "2025-11-01"
    return headers


@dataclass
class ProfileMatch:
    linkedin_url: str
    name: Optional[str]
    confidence: Optional[float]


@dataclass
class ResolutionResult:
    status: str  # "resolved" | "not_found" | "ambiguous" | "error"
    matches: list[ProfileMatch]
    source: Optional[str] = None  # quale endpoint ha prodotto il match
    error_message: Optional[str] = None


# Soglia sotto la quale un singolo match v2 non è considerato affidabile
# (evita di accettare un profilo "quasi corrispondente" senza criterio).
MIN_CONFIDENCE = 0.75


async def v2_person_enrich(client: httpx.AsyncClient, email: str) -> ResolutionResult:
    """POST /person/enrich (v2, 2025-11-01) - business_emails reverse lookup."""
    url = f"{CRUSTDATA_BASE_URL}/person/enrich"
    body = {
        "business_emails": [email],
        "fields": ["basic_profile", "social_handles"],
    }
    try:
        resp = await client.post(url, headers=_headers(v2=True), json=body, timeout=30.0)
    except httpx.RequestError as exc:
        raise CrustdataAPIError(f"Errore di rete chiamando v2 /person/enrich: {exc}") from exc

    if resp.status_code == 404:
        raise CrustdataAPIError("v2 /person/enrich: endpoint non trovato (404).", status_code=404)
    if resp.status_code == 401 or resp.status_code == 403:
        raise CrustdataAPIError(
            f"v2 /person/enrich: autenticazione rifiutata ({resp.status_code}). "
            "Verificare CRUSTDATA_API_KEY.",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        raise CrustdataAPIError(
            f"v2 /person/enrich: errore HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    entries = data if isinstance(data, list) else data.get("results", [])
    matches: list[ProfileMatch] = []
    for entry in entries:
        for m in entry.get("matches", []):
            person = m.get("person_data", {}) or {}
            social = person.get("social_handles", {}) or {}
            prof_net = social.get("professional_network_identifier", {}) or {}
            profile_url = prof_net.get("profile_url") or person.get("linkedin_profile_url")
            name = (person.get("basic_profile", {}) or {}).get("name") or person.get("name")
            if profile_url:
                matches.append(
                    ProfileMatch(
                        linkedin_url=profile_url,
                        name=name,
                        confidence=m.get("confidence_score"),
                    )
                )

    if not matches:
        return ResolutionResult(status="not_found", matches=[], source="v2_person_enrich")

    if len(matches) == 1:
        m = matches[0]
        if m.confidence is not None and m.confidence < MIN_CONFIDENCE:
            return ResolutionResult(status="ambiguous", matches=matches, source="v2_person_enrich")
        return ResolutionResult(status="resolved", matches=matches, source="v2_person_enrich")

    # Più match: non scegliamo arbitrariamente.
    return ResolutionResult(status="ambiguous", matches=matches, source="v2_person_enrich")


async def v1_person_enrich(client: httpx.AsyncClient, email: str) -> ResolutionResult:
    """
    GET /screener/person/enrich (v1) - fallback per email personali/istituzionali
    non coperte dal lookup v2 (che copre solo business_emails).

    Path non confermato al 100% sulla documentazione pubblica (vedi nota di
    modulo). In caso di 404 il chiamante deve trattarlo come "funzione non
    disponibile", NON come "profilo non trovato".
    """
    url = f"{CRUSTDATA_BASE_URL}/screener/person/enrich"
    for param_name in ("personal_email", "business_email"):
        try:
            resp = await client.get(
                url,
                headers=_headers(v2=False),
                params={param_name: email},
                timeout=30.0,
            )
        except httpx.RequestError as exc:
            raise CrustdataAPIError(f"Errore di rete chiamando v1 /screener/person/enrich: {exc}") from exc

        if resp.status_code == 404:
            raise CrustdataAPIError(
                "v1 /screener/person/enrich: endpoint non trovato (404). "
                "Il path di questo endpoint non è stato confermato sulla documentazione "
                "pubblica di Crustdata (pagina protetta da login) e potrebbe essere diverso. "
                "Verificare con il supporto/documentazione Crustdata dell'account.",
                status_code=404,
            )
        if resp.status_code in (401, 403):
            raise CrustdataAPIError(
                f"v1 /screener/person/enrich: autenticazione rifiutata ({resp.status_code}).",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            # Prova il parametro successivo prima di arrendersi.
            continue

        data = resp.json()
        records = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if isinstance(records, dict):
            records = [records]
        matches = []
        for person in records or []:
            profile_url = person.get("linkedin_profile_url") or person.get("linkedin_url")
            name = person.get("name") or person.get("full_name")
            if profile_url:
                matches.append(ProfileMatch(linkedin_url=profile_url, name=name, confidence=None))
        if matches:
            if len(matches) > 1:
                return ResolutionResult(status="ambiguous", matches=matches, source="v1_person_enrich")
            return ResolutionResult(status="resolved", matches=matches, source="v1_person_enrich")

    return ResolutionResult(status="not_found", matches=[], source="v1_person_enrich")


async def resolve_email_to_profile(client: httpx.AsyncClient, email: str) -> ResolutionResult:
    """
    Strategia di risoluzione email -> profilo LinkedIn, in ordine di costo/copertura:
      1. v2 /person/enrich (business_emails) - economico, copre email aziendali.
      2. v1 /screener/person/enrich (personal_email poi business_email) - fallback
         per email personali/istituzionali non risolte da v2.
    Nessun indovinamento: se entrambe le vie non producono un match affidabile,
    il risultato è "not_found" o "ambiguous", mai un profilo inventato.
    """
    try:
        result = await v2_person_enrich(client, email)
    except CrustdataAPIError as exc:
        result = ResolutionResult(
            status="error", matches=[], source="v2_person_enrich", error_message=str(exc)
        )

    if result.status == "resolved":
        return result

    # Se v2 non trova nulla (o va in errore per motivi diversi da auth), prova v1.
    if result.status in ("not_found", "error"):
        try:
            v1_result = await v1_person_enrich(client, email)
        except CrustdataAPIError as exc:
            v1_result = ResolutionResult(
                status="error", matches=[], source="v1_person_enrich", error_message=str(exc)
            )
        if v1_result.status == "resolved":
            return v1_result
        if v1_result.status == "ambiguous":
            return v1_result
        if v1_result.status == "error" and result.status == "error":
            # Entrambe le vie hanno fallito tecnicamente: non è "non trovato",
            # è un errore da segnalare esplicitamente.
            return ResolutionResult(
                status="error",
                matches=[],
                error_message=f"v2: {result.error_message} | v1: {v1_result.error_message}",
            )
        if v1_result.status == "not_found":
            return ResolutionResult(status="not_found", matches=[])
        return v1_result

    # result.status == "ambiguous"
    return result


async def _company_identify_call(
    client: httpx.AsyncClient, company_name: str, exact_match: bool
) -> list[ProfileMatch]:
    """Singola chiamata POST /company/identify (v2), parsing incluso.

    Verificato con una chiamata reale: corpo `{"names": [...]}`, risposta
    `[{"matched_on": ..., "match_type": ..., "matches": [{"confidence_score":
    ..., "company_data": {"basic_info": {"professional_network_url": ...}}}]}]`.
    Il campo con l'URL LinkedIn è `professional_network_url` dentro
    `basic_info` (non `linkedin_url`/`linkedin_profile_url` come in una
    versione precedente di questo codice, che causava falsi "non trovato").
    Come per v1_person_enrich, un eventuale 404 viene segnalato esplicitamente
    come "endpoint/schema da verificare", mai confuso con "azienda non trovata".
    """
    url = f"{CRUSTDATA_BASE_URL}/company/identify"
    body: dict[str, Any] = {"names": [company_name]}
    if exact_match:
        body["exact_match"] = True
    try:
        resp = await client.post(url, headers=_headers(v2=True), json=body, timeout=30.0)
    except httpx.RequestError as exc:
        raise CrustdataAPIError(f"Errore di rete chiamando v2 /company/identify: {exc}") from exc

    if resp.status_code == 404:
        raise CrustdataAPIError(
            "v2 /company/identify: endpoint non trovato (404). Verificare con il "
            "supporto/documentazione Crustdata dell'account.",
            status_code=404,
        )
    if resp.status_code in (401, 403):
        raise CrustdataAPIError(
            f"v2 /company/identify: autenticazione rifiutata ({resp.status_code}). "
            "Verificare CRUSTDATA_API_KEY.",
            status_code=resp.status_code,
        )
    if resp.status_code == 429:
        raise CrustdataAPIError(
            "v2 /company/identify: rate limit superato. Riprovare più tardi o "
            "contattare Crustdata per aumentare il limite.",
            status_code=429,
        )
    if resp.status_code >= 400:
        raise CrustdataAPIError(
            f"v2 /company/identify: errore HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    entries = data if isinstance(data, list) else [data]
    matches: list[ProfileMatch] = []
    for entry in entries:
        for m in entry.get("matches", []):
            company = m.get("company_data", {}) or {}
            basic = company.get("basic_info", {}) or {}
            profile_url = basic.get("professional_network_url")
            name = basic.get("name") or basic.get("profile_name")
            if profile_url:
                matches.append(
                    ProfileMatch(
                        linkedin_url=profile_url,
                        name=name,
                        confidence=m.get("confidence_score"),
                    )
                )
    return matches


def _evaluate_matches(matches: list[ProfileMatch]) -> ResolutionResult:
    if not matches:
        return ResolutionResult(status="not_found", matches=[], source="v2_company_identify")
    if len(matches) == 1:
        m = matches[0]
        if m.confidence is not None and m.confidence < MIN_CONFIDENCE:
            return ResolutionResult(status="ambiguous", matches=matches, source="v2_company_identify")
        return ResolutionResult(status="resolved", matches=matches, source="v2_company_identify")
    # Più match: non scegliamo arbitrariamente (es. "Confindustria Nautica"
    # che matcha anche la generica "Confindustria", o "ENEA" che è il nome
    # esatto di più aziende diverse nel mondo).
    return ResolutionResult(status="ambiguous", matches=matches, source="v2_company_identify")


async def v2_company_identify(client: httpx.AsyncClient, company_name: str) -> ResolutionResult:
    """
    Risoluzione nome azienda -> pagina LinkedIn aziendale, in due passaggi:

    1. Si prova `exact_match=true` sul nome ripulito da forme societarie
       generiche (Srl, SPA, Soc. Coop., ecc. - vedi `_strip_legal_suffixes`),
       perché su LinkedIn le pagine aziendali di solito non includono la
       forma societaria nel nome (es. "OpenEconomics", non "OpenEconomics
       Srl"). Se produce match, si usano direttamente (nessuna azienda
       inventata: sono comunque risultati letterali dell'API).
    2. Se il passaggio esatto non trova nulla, si ricade sulla ricerca fuzzy
       di default sullo stesso nome ripulito, filtrando però i candidati per
       tenere solo quelli il cui nome (normalizzato: minuscolo, senza
       accenti/punteggiatura) coincide esattamente col nome cercato - questo
       perché la ricerca fuzzy tratta acronimi societari generici come "Srl"
       come parole di ricerca a sé stanti e restituisce centinaia di aziende
       nel mondo che li contengono, annegando il match corretto in mezzo a
       rumore con la stessa confidenza.

    In nessun caso viene scelto un profilo arbitrariamente tra più candidati
    realmente ambigui (es. "ENEA", nome esatto di aziende diverse nel mondo).
    """
    clean_name = _strip_legal_suffixes(company_name)

    exact_matches = await _company_identify_call(client, clean_name, exact_match=True)
    if exact_matches:
        return _evaluate_matches(exact_matches)

    await asyncio.sleep(1.0)
    fuzzy_matches = await _company_identify_call(client, clean_name, exact_match=False)
    target = _normalize_for_compare(clean_name)
    filtered = [m for m in fuzzy_matches if m.name and _normalize_for_compare(m.name) == target]
    return _evaluate_matches(filtered)


async def resolve_company_to_profile(client: httpx.AsyncClient, company_name: str) -> ResolutionResult:
    """
    Risoluzione nome azienda -> profilo LinkedIn aziendale. A differenza della
    risoluzione email->persona non esiste un fallback v1 noto/documentato:
    se v2 non produce un match affidabile, il risultato è "not_found" o
    "ambiguous", mai un profilo scelto arbitrariamente.
    """
    try:
        return await v2_company_identify(client, company_name)
    except CrustdataAPIError as exc:
        return ResolutionResult(
            status="error", matches=[], source="v2_company_identify", error_message=str(exc)
        )


async def get_linkedin_posts(
    client: httpx.AsyncClient,
    date_from,
    date_to,
    person_linkedin_url: Optional[str] = None,
    company_linkedin_url: Optional[str] = None,
    max_pages: int = 5,
) -> list[dict]:
    """
    GET /screener/linkedin_posts (v1) - recupera i post di un profilo persona
    o azienda (esattamente uno dei due URL va passato), più recenti per primi,
    paginando finché non si esce dal range di date richiesto (per non
    consumare crediti oltre il necessario: la fatturazione è per post
    restituito). Nessuna deduplicazione: tutti i post nel range vengono tenuti,
    anche più nello stesso giorno.

    max_pages=5 è un tetto di sicurezza sui crediti consumati per singolo
    profilo/azienda: nel normale utilizzo (periodi di 7-30 giorni) il ciclo
    si ferma molto prima grazie al filtro data, questo limite serve solo a
    evitare consumi anomali in casi limite.

    NOTA: l'API accetta "page" o "limit", mai entrambi insieme (risposta 400
    "Only one of 'page' or 'limit' can be provided, not both" se passati
    contemporaneamente) - qui si usa solo "page", con la dimensione pagina di
    default del server.
    """
    from datetime import datetime, timezone

    if bool(person_linkedin_url) == bool(company_linkedin_url):
        raise ValueError("Passare esattamente uno tra person_linkedin_url e company_linkedin_url.")

    base_params = (
        {"person_linkedin_url": person_linkedin_url}
        if person_linkedin_url
        else {"company_linkedin_url": company_linkedin_url}
    )

    url = f"{CRUSTDATA_BASE_URL}/screener/linkedin_posts"
    posts: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            resp = await client.get(
                url,
                headers=_headers(v2=False),
                params={
                    **base_params,
                    "page": page,
                    "response_format": "json",
                    "compact": "true",
                },
                timeout=30.0,
            )
        except httpx.RequestError as exc:
            raise CrustdataAPIError(f"Errore di rete chiamando /screener/linkedin_posts: {exc}") from exc

        if resp.status_code == 404:
            raise CrustdataAPIError(
                "/screener/linkedin_posts: endpoint non trovato (404).", status_code=404
            )
        if resp.status_code in (401, 403):
            raise CrustdataAPIError(
                f"/screener/linkedin_posts: autenticazione rifiutata ({resp.status_code}).",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise CrustdataAPIError(
                f"/screener/linkedin_posts: errore HTTP {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
            )

        data = resp.json()
        page_posts = data if isinstance(data, list) else data.get("posts", data.get("results", []))
        if not page_posts:
            break

        stop = False
        for p in page_posts:
            raw_date = p.get("date") or p.get("posted_at") or p.get("created_at")
            if not raw_date:
                continue
            try:
                post_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                if post_date.tzinfo is None:
                    post_date = post_date.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if post_date.date() < date_from:
                stop = True
                continue
            if post_date.date() > date_to:
                continue

            posts.append(
                {
                    "date": post_date,
                    "url": p.get("url") or p.get("post_url") or p.get("linkedin_post_url"),
                    "text": p.get("text") or p.get("content"),
                }
            )

        if stop or len(page_posts) < 100:
            break

    return posts
