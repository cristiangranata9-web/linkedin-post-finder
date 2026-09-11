"""
Classificazione dei post LinkedIn recuperati rispetto alle "Aree Tematiche"
del piano editoriale, tramite l'API gratuita di Google Gemini (Gemini
Developer API / Google AI Studio) — scelta al posto di Claude per non avere
alcun costo (la Gemini Developer API ha un piano gratuito senza carta di
credito, a differenza dell'API di Anthropic).

Nessuna tematica viene mai inventata: al modello viene fornito l'elenco
esatto delle tematiche presenti nel piano editoriale caricato dall'utente, e
il prompt richiede esplicitamente di scegliere solo tra quelle (o restituire
null se nessuna tematica si applica). La risposta viene richiesta in JSON
puro (response_mime_type="application/json") e comunque parsata in modo
difensivo: se un post non produce un'assegnazione valida (tematica non tra
quelle fornite, JSON malformato, indice fuori range, ecc.) viene trattato
come "nessuna tematica assegnata", mai forzato su una tematica a caso.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from google import genai
from google.genai.types import GenerateContentConfig

CLASSIFIER_MODEL = "gemini-3.6-flash"
BATCH_SIZE = 20


class ClassifierConfigError(RuntimeError):
    pass


class ClassifierAPIError(RuntimeError):
    pass


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ClassifierConfigError(
            "GEMINI_API_KEY non impostata come variabile d'ambiente lato server. "
            "La ricerca per tematica richiede questa chiave (gratuita, da Google "
            "AI Studio) per classificare i post tramite Gemini. Vedi README.md "
            "per come configurarla."
        )
    return genai.Client(api_key=api_key)


def _build_prompt(posts: list[dict], topics: list[str]) -> str:
    topics_list = "\n".join(f"- {t}" for t in topics)
    posts_block = "\n".join(
        f'{i}. """{(p.get("text") or "")[:1500]}"""' for i, p in enumerate(posts)
    )
    return f"""Classifica ciascuno dei seguenti post LinkedIn assegnando ESATTAMENTE una
delle tematiche elencate qui sotto, oppure null se nessuna tematica si applica
in modo ragionevole. Non inventare mai tematiche diverse da quelle elencate.

Tematiche disponibili:
{topics_list}

Post da classificare (indice, testo):
{posts_block}

Rispondi SOLO con un array JSON di oggetti, uno per post, nello stesso ordine,
nel formato esatto: [{{"index": 0, "topic": "..."}}, ...]. Usa null per
"topic" se nessuna tematica si applica. Nessun testo prima o dopo il JSON."""


async def classify_posts(posts: list[dict], topics: list[str]) -> list[Optional[str]]:
    """
    Restituisce, per ciascun post in `posts` (nello stesso ordine), la
    tematica assegnata (una tra `topics`) oppure None.
    """
    if not posts:
        return []
    if not topics:
        return [None] * len(posts)

    client = _get_client()
    results: list[Optional[str]] = [None] * len(posts)

    for batch_start in range(0, len(posts), BATCH_SIZE):
        batch = posts[batch_start : batch_start + BATCH_SIZE]
        prompt = _build_prompt(batch, topics)
        try:
            resp = await client.aio.models.generate_content(
                model=CLASSIFIER_MODEL,
                contents=prompt,
                config=GenerateContentConfig(response_mime_type="application/json"),
            )
        except Exception as exc:  # noqa: BLE001
            raise ClassifierAPIError(
                f"Errore chiamando l'API Gemini per la classificazione: {exc}"
            ) from exc

        raw_text = (resp.text or "").strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            # Output non JSON valido: nessuna assegnazione forzata per questo batch.
            continue

        if not isinstance(parsed, list):
            continue

        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            topic = item.get("topic")
            if not isinstance(idx, int) or idx < 0 or idx >= len(batch):
                continue
            if topic is not None and topic not in topics:
                # Il modello ha restituito una tematica fuori dall'elenco fornito:
                # non la accettiamo mai (nessun dato inventato).
                continue
            results[batch_start + idx] = topic

    return results
