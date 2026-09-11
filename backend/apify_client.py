"""
Client HTTP per Apify, usato come alternativa a Crustdata per il recupero
dei post LinkedIn (la parte che consuma più crediti: 1 credito Crustdata per
post restituito). Attivo solo se APIFY_API_TOKEN è impostata lato server;
altrimenti main.py ricade su Crustdata (vedi crustdata_client.get_linkedin_posts).

Il matching nome azienda -> pagina LinkedIn resta su Crustdata
(/company/identify è gratuito, vedi crustdata_client.py): qui si sostituisce
solo la parte a pagamento.

Endpoint verificato sulla documentazione pubblica degli actor Apify:
POST https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token=...
(slug dell'actor con "/" sostituito da "~"), corpo JSON = input dell'actor,
risposta = lista di item del dataset già pronta (esecuzione sincrona).

Actor usati (harvestapi, "No Cookies", pay-per-risultato):
- harvestapi/linkedin-profile-posts  per profili persona
- harvestapi/linkedin-company-posts  per pagine azienda
Input principale: targetUrls (lista di URL LinkedIn), postedLimitDate
(limite INFERIORE sulla data: "scarica i post da oggi indietro fino a
questa data"). Non esiste un limite superiore lato API, quindi il filtro
su date_to viene applicato qui - stesso approccio già usato per Crustdata.
Campi di output per ogni post: linkedinUrl, content, postedAt.date (ISO 8601).

Nessuna chiamata qui restituisce mai dati simulati: se una richiesta fallisce,
viene propagata un'eccezione con un messaggio esplicito.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

import httpx

APIFY_BASE_URL = "https://api.apify.com/v2"
_ACTOR_PERSON = "harvestapi~linkedin-profile-posts"
_ACTOR_COMPANY = "harvestapi~linkedin-company-posts"

# Tetto di sicurezza sui post richiesti per singolo profilo/azienda, per non
# generare consumi anomali in caso di pagine molto attive (stesso scopo del
# max_pages=5 usato in crustdata_client.get_linkedin_posts).
_MAX_POSTS_SAFETY_CAP = 200


class ApifyConfigError(RuntimeError):
    """Sollevato quando manca la configurazione server-side (APIFY_API_TOKEN)."""


class ApifyAPIError(RuntimeError):
    """Sollevato quando una chiamata reale ad Apify fallisce o è inconcludente."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    """True se APIFY_API_TOKEN è impostata: usato da main.py per decidere se
    usare Apify (più economico) o ricadere su Crustdata per i post."""
    return bool(os.environ.get("APIFY_API_TOKEN"))


def _get_token() -> str:
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise ApifyConfigError(
            "APIFY_API_TOKEN non impostata come variabile d'ambiente lato server."
        )
    return token


async def get_linkedin_posts_apify(
    client: httpx.AsyncClient,
    date_from: date,
    date_to: date,
    person_linkedin_url: Optional[str] = None,
    company_linkedin_url: Optional[str] = None,
) -> list[dict]:
    """
    Recupera i post di un profilo persona o di una pagina azienda tramite
    Apify (esattamente uno dei due URL va passato), filtrati sul range
    [date_from, date_to]. Stessa forma di ritorno di
    crustdata_client.get_linkedin_posts: lista di dict con "date" (datetime),
    "url", "text".
    """
    if bool(person_linkedin_url) == bool(company_linkedin_url):
        raise ValueError("Passare esattamente uno tra person_linkedin_url e company_linkedin_url.")

    actor = _ACTOR_PERSON if person_linkedin_url else _ACTOR_COMPANY
    target_url = person_linkedin_url or company_linkedin_url

    url = f"{APIFY_BASE_URL}/acts/{actor}/run-sync-get-dataset-items"
    body = {
        "targetUrls": [target_url],
        "maxPosts": _MAX_POSTS_SAFETY_CAP,
        "postedLimitDate": date_from.isoformat(),
    }
    try:
        resp = await client.post(
            url,
            params={"token": _get_token()},
            json=body,
            timeout=180.0,
        )
    except httpx.RequestError as exc:
        raise ApifyAPIError(f"Errore di rete chiamando Apify ({actor}): {exc}") from exc

    if resp.status_code in (401, 403):
        raise ApifyAPIError(
            f"Apify ({actor}): autenticazione rifiutata ({resp.status_code}). "
            "Verificare APIFY_API_TOKEN.",
            status_code=resp.status_code,
        )
    if resp.status_code == 429:
        raise ApifyAPIError(f"Apify ({actor}): rate limit superato.", status_code=429)
    if resp.status_code >= 400:
        raise ApifyAPIError(
            f"Apify ({actor}): errore HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    items = resp.json()
    posts: list[dict] = []
    for item in items or []:
        posted_at = item.get("postedAt") or {}
        raw_date = posted_at.get("date")
        if not raw_date:
            continue
        try:
            post_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            if post_date.tzinfo is None:
                post_date = post_date.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if post_date.date() < date_from or post_date.date() > date_to:
            continue

        posts.append(
            {
                "date": post_date,
                "url": item.get("linkedinUrl") or item.get("url"),
                "text": item.get("content"),
            }
        )

    return posts
