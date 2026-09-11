"""
Backend FastAPI per lo scraper/interfaccia di ricerca profili e post LinkedIn.

- Nessun database, nessuna autenticazione utente, nessuna registrazione.
- Lo stato dei job vive SOLO in memoria per la durata del processo (dict Python),
  in linea con "i risultati possono essere elaborati temporaneamente durante la
  sessione". Un riavvio del server cancella i job in corso.
- La CRUSTDATA_API_KEY viene letta esclusivamente da variabile d'ambiente
  server-side (vedi crustdata_client.py) e non è MAI esposta al frontend: le
  risposte JSON/SSE inviate al browser non contengono mai la chiave.
- Il frontend statico (../frontend) viene servito direttamente da questo
  stesso processo, così l'intera app è UN SOLO servizio da distribuire
  (un solo URL, nessuna configurazione CORS cross-dominio da gestire).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from crustdata_client import (
    CrustdataAPIError,
    CrustdataConfigError,
    get_linkedin_posts,
    resolve_email_to_profile,
)
from excel_utils import (
    GIORNI_IT,
    InputFileError,
    build_detailed_xlsx,
    build_weekly_xlsx,
    read_emails_from_xlsx,
)

app = FastAPI(title="LinkedIn Post Finder - Backend")

_allowed_origins = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins.split(",")] if _allowed_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Password condivisa opzionale per limitare l'uso dell'app ai soli colleghi
# che la conoscono. Se APP_PASSWORD non è impostata, l'app resta aperta a
# chiunque abbia l'URL (comportamento precedente, invariato).
APP_PASSWORD = os.environ.get("APP_PASSWORD")


def _verify_password(request: Request) -> None:
    if not APP_PASSWORD:
        return
    supplied = request.headers.get("x-app-password") or request.query_params.get("password")
    if supplied != APP_PASSWORD:
        raise HTTPException(401, "Password mancante o errata.")


# Job store in memoria: {job_id: {...}}
JOBS: dict[str, dict] = {}
JOB_TTL = timedelta(hours=6)


def _cleanup_jobs():
    now = datetime.utcnow()
    stale = [jid for jid, j in JOBS.items() if now - j["created_at"] > JOB_TTL]
    for jid in stale:
        JOBS.pop(jid, None)


def _compute_date_range(period: str, custom_start: Optional[str], custom_end: Optional[str]) -> tuple[date, date]:
    today = date.today()
    if period == "custom":
        if not custom_start or not custom_end:
            raise HTTPException(400, "Periodo personalizzato richiede custom_start e custom_end (YYYY-MM-DD).")
        try:
            d_from = date.fromisoformat(custom_start)
            d_to = date.fromisoformat(custom_end)
        except ValueError as exc:
            raise HTTPException(400, f"Formato data non valido: {exc}") from exc
        if d_from > d_to:
            raise HTTPException(400, "custom_start deve precedere custom_end.")
        return d_from, d_to

    days_map = {"7": 7, "14": 14, "30": 30}
    if period not in days_map:
        raise HTTPException(400, "Periodo non valido. Usare 7, 14, 30 oppure custom.")
    d_from = today - timedelta(days=days_map[period])
    return d_from, today


@app.post("/api/login")
async def login(request: Request):
    _verify_password(request)
    return {"status": "ok"}


@app.post("/api/jobs", dependencies=[Depends(_verify_password)])
async def create_job(
    file: UploadFile = File(...),
    period: str = Form(...),
    custom_start: Optional[str] = Form(None),
    custom_end: Optional[str] = Form(None),
):
    _cleanup_jobs()

    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Il file deve essere in formato .xlsx.")

    content = await file.read()
    try:
        emails = read_emails_from_xlsx(content)
    except InputFileError as exc:
        raise HTTPException(400, str(exc)) from exc

    date_from, date_to = _compute_date_range(period, custom_start, custom_end)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "created_at": datetime.utcnow(),
        "emails": emails,
        "date_from": date_from,
        "date_to": date_to,
        "started": False,
        "status": "pending",
        "results": None,
    }
    return {"job_id": job_id, "total_emails": len(emails), "date_from": str(date_from), "date_to": str(date_to)}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _process_job(job_id: str):
    job = JOBS[job_id]
    emails = job["emails"]
    date_from, date_to = job["date_from"], job["date_to"]
    total = len(emails)

    weekly_rows = []
    detailed_rows = []
    summary = {"processed": 0, "profiles_found": 0, "profiles_not_found": 0, "unverified_matches": 0, "errors": 0, "total_posts": 0}

    config_error: Optional[str] = None

    async with httpx.AsyncClient() as client:
        for idx, email in enumerate(emails, start=1):
            yield _sse("progress", {"index": idx, "total": total, "email": email, "message": "Ricerca profilo LinkedIn..."})

            giorni: dict[str, list[str]] = {g: [] for g in GIORNI_IT}
            row_common = {"email": email}

            try:
                resolution = await resolve_email_to_profile(client, email)
            except CrustdataConfigError as exc:
                config_error = str(exc)
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Errore di configurazione: {exc}"})
                summary["errors"] += 1
                weekly_rows.append({**row_common, "profile_name": None, "profile_url": None, "status_label": "Errore configurazione backend", "giorni": giorni})
                summary["processed"] += 1
                break
            except CrustdataAPIError as exc:
                summary["errors"] += 1
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Errore Crustdata: {exc}"})
                weekly_rows.append({**row_common, "profile_name": None, "profile_url": None, "status_label": f"Errore: {exc}", "giorni": giorni})
                summary["processed"] += 1
                continue

            if resolution.status == "error":
                summary["errors"] += 1
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Errore: {resolution.error_message}"})
                weekly_rows.append({**row_common, "profile_name": None, "profile_url": None, "status_label": f"Errore: {resolution.error_message}", "giorni": giorni})
                summary["processed"] += 1
                continue

            if resolution.status == "not_found":
                summary["profiles_not_found"] += 1
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": "Profilo non trovato."})
                weekly_rows.append({**row_common, "profile_name": None, "profile_url": None, "status_label": "Profilo non trovato", "giorni": giorni})
                detailed_rows.append({**row_common, "profile_name": "", "profile_url": "", "post_date": "", "day_of_week": "", "post_link": "Profilo non trovato"})
                summary["processed"] += 1
                continue

            if resolution.status == "ambiguous":
                summary["unverified_matches"] += 1
                candidates = ", ".join(m.linkedin_url for m in resolution.matches[:5])
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Match non verificato ({len(resolution.matches)} profili candidati)."})
                weekly_rows.append({**row_common, "profile_name": None, "profile_url": None, "status_label": f"Match non verificato: {candidates}", "giorni": giorni})
                detailed_rows.append({**row_common, "profile_name": "", "profile_url": "", "post_date": "", "day_of_week": "", "post_link": f"Match non verificato ({len(resolution.matches)} candidati)"})
                summary["processed"] += 1
                continue

            # resolved
            match = resolution.matches[0]
            summary["profiles_found"] += 1
            yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Profilo trovato: {match.name or match.linkedin_url}. Ricerca post..."})

            try:
                posts = await get_linkedin_posts(client, match.linkedin_url, date_from, date_to)
            except CrustdataAPIError as exc:
                summary["errors"] += 1
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Errore nel recupero dei post: {exc}"})
                weekly_rows.append({**row_common, "profile_name": match.name, "profile_url": match.linkedin_url, "status_label": f"Errore post: {exc}", "giorni": giorni})
                summary["processed"] += 1
                continue

            if not posts:
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": "Nessun post trovato nel periodo selezionato."})
                weekly_rows.append({**row_common, "profile_name": match.name, "profile_url": match.linkedin_url, "status_label": "Nessun post trovato", "giorni": giorni})
                detailed_rows.append({**row_common, "profile_name": match.name or "", "profile_url": match.linkedin_url, "post_date": "", "day_of_week": "", "post_link": "Nessun post trovato"})
            else:
                for p in posts:
                    giorno_idx = p["date"].weekday()  # 0=Lunedì
                    giorno_nome = GIORNI_IT[giorno_idx]
                    if p["url"]:
                        giorni[giorno_nome].append(p["url"])
                    detailed_rows.append(
                        {
                            **row_common,
                            "profile_name": match.name or "",
                            "profile_url": match.linkedin_url,
                            "post_date": p["date"].strftime("%Y-%m-%d"),
                            "day_of_week": giorno_nome,
                            "post_link": p["url"] or "",
                        }
                    )
                summary["total_posts"] += len(posts)
                weekly_rows.append({**row_common, "profile_name": match.name, "profile_url": match.linkedin_url, "status_label": "", "giorni": giorni})
                yield _sse("progress", {"index": idx, "total": total, "email": email, "message": f"Ricerca completata: {len(posts)} post trovati."})

            summary["processed"] += 1

    result_payload = {"weekly_rows": weekly_rows, "detailed_rows": detailed_rows, "summary": summary, "config_error": config_error}
    job["results"] = result_payload
    job["status"] = "done"
    yield _sse("summary", summary)
    yield _sse("result", result_payload)


@app.get("/api/jobs/{job_id}/stream", dependencies=[Depends(_verify_password)])
async def stream_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job non trovato (scaduto o mai creato).")
    if job["started"]:
        raise HTTPException(409, "Questo job è già stato avviato.")
    job["started"] = True

    async def event_gen():
        async for chunk in _process_job(job_id):
            yield chunk

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/export", dependencies=[Depends(_verify_password)])
async def export_job(job_id: str, type: str = "weekly"):
    job = JOBS.get(job_id)
    if not job or not job.get("results"):
        raise HTTPException(404, "Risultati non disponibili per questo job.")

    results = job["results"]
    if type == "weekly":
        content = build_weekly_xlsx(results["weekly_rows"])
        filename = "linkedin_post_settimanale.xlsx"
    elif type == "detailed":
        content = build_detailed_xlsx(results["detailed_rows"])
        filename = "linkedin_post_dettagliato.xlsx"
    else:
        raise HTTPException(400, "type deve essere 'weekly' oppure 'detailed'.")

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/health")
async def health():
    has_key = bool(os.environ.get("CRUSTDATA_API_KEY"))
    return {
        "status": "ok",
        "crustdata_api_key_configured": has_key,
        "password_protected": bool(APP_PASSWORD),
    }


# Serve il frontend statico dallo stesso servizio (montato per ultimo, dopo
# tutte le route /api/..., così non entra mai in conflitto con esse).
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
