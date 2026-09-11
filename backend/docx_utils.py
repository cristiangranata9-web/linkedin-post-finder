"""
Lettura dell'elenco soci/partner da un file .docx (formato "Elenco soci"
fornito dai colleghi), in alternativa al precedente file .xlsx con colonna
"Email".

Il documento non contiene email né URL LinkedIn: elenca solo nomi di
aziende/enti, organizzati in sezioni tramite paragrafi di intestazione (es.
"Soci", "Nuove richieste di adesione...", "RECESSI..."). Vengono incluse
tutte le sezioni tranne quelle che rappresentano soci usciti ("RECESSI"),
riconosciute tramite parole chiave nel testo del paragrafo di intestazione.

Nessun nome viene inventato o dedotto: l'elenco restituito è esattamente
l'elenco di paragrafi non vuoti trovati nelle sezioni incluse, nell'ordine in
cui compaiono nel documento. Se la struttura del documento cambia radicalmente
(nuove intestazioni di sezione non riconosciute), questa funzione può
includere per errore un'intestazione come se fosse un nome azienda: in tal
caso va aggiornato l'elenco di parole chiave qui sotto, esattamente come già
documentato per l'endpoint v1 non verificato in crustdata_client.py.
"""
from __future__ import annotations

import io
import re

from docx import Document

from excel_utils import InputFileError

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def read_emails_from_docx(file_bytes: bytes) -> list[str]:
    """Legge un elenco di indirizzi email da un file .docx, estraendo ogni
    stringa che corrisponde al pattern di un indirizzo email dai paragrafi e
    dalle tabelle del documento. Nessun indirizzo viene inventato: solo
    quelli effettivamente presenti nel testo del file caricato."""
    try:
        doc = Document(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        raise InputFileError(f"Impossibile leggere il file .docx: {exc}") from exc

    emails: list[str] = []
    texts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.append(cell.text)

    for text in texts:
        emails.extend(_EMAIL_RE.findall(text))

    if not emails:
        raise InputFileError(
            "Non ho trovato nessun indirizzo email nel file .docx caricato."
        )

    return emails

# Parole chiave (case-insensitive) che, se presenti nel testo di un paragrafo,
# lo identificano come intestazione di una sezione da ESCLUDERE (soci usciti).
EXCLUDE_SECTION_KEYWORDS = ["recessi", "recesso", "usciti", "cessati"]

# Testi di intestazione "positivi" noti: terminano un'eventuale esclusione in
# corso e non vengono mai aggiunti come nome azienda.
INCLUDE_SECTION_EXACT = {"soci", "elenco soci"}
INCLUDE_SECTION_SUBSTRINGS = ["richieste di adesione"]


def read_company_names_from_docx(file_bytes: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        raise InputFileError(f"Impossibile leggere il file .docx: {exc}") from exc

    names: list[str] = []
    excluding = False

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        lowered = text.lower()

        if any(kw in lowered for kw in EXCLUDE_SECTION_KEYWORDS):
            excluding = True
            continue

        if lowered in INCLUDE_SECTION_EXACT or any(s in lowered for s in INCLUDE_SECTION_SUBSTRINGS):
            excluding = False
            continue

        if excluding:
            continue

        names.append(text)

    if not names:
        raise InputFileError(
            "Non ho trovato nessun nome di azienda/ente nel file .docx caricato. "
            "Verifica che il documento contenga un elenco di soci/partner."
        )

    return names
