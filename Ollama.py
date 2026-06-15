from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import httpx
import os


# CONFIGURAZIONE
MONGO_DETAILS = os.getenv(
    "MONGO_DETAILS",
    "mongodb+srv://lorenzotesi:Zekrom03!@progettotesi.eyjcybv.mongodb.net/?appName=ProgettoTesi",
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = "llama3.2"

mongo_client = AsyncIOMotorClient(MONGO_DETAILS)
database     = mongo_client.sistema_eventi
collection   = database.get_collection("eventi_osservati")


# LIFECYCLE
@asynccontextmanager
async def lifespan(app: FastAPI):
    await collection.create_index("timestamp")
    await collection.create_index("camera_id")
    await collection.create_index("event_type")
    yield

app = FastAPI(
    title="Sistema Sorveglianza Intelligente (Ollama)",
    version="1.0.0",
    description="Backend con LLM locale Ollama per sintesi eventi di sorveglianza.",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EVENT_TYPES = {"movement", "intrusion", "loitering", "crowd", "anomaly", "idle"}

CAMERAS_REGISTRY = {
    "corridor_1": "corridoio principale",
    "corridor_2": "corridoio secondario",
    "entrance_clients": "ingresso principale per i clienti",
    "reception_hall": "sportello",
    "vault": "camera blindata",
    "exit": "uscita principale",
}

# SCHEMI PYDANTIC
class EventMetadata(BaseModel):
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    tags: list[str]   = Field(default_factory=list)


class Event(BaseModel):
    timestamp:   datetime     = Field(description="Data e ora dell'evento (Formato ISO 8601)")
    camera_id:   str          = Field(description="Identificativo della camera")
    location:    str          = Field(description="Descrizione testuale del luogo")
    description: str          = Field(description="Descrizione dell'evento osservato")
    event_type:  str          = Field(description=f"Tipo evento: {ALLOWED_EVENT_TYPES}")
    metadata:    EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"event_type deve essere uno di: {ALLOWED_EVENT_TYPES}")
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        if v > datetime.now():
            raise ValueError("Il timestamp non può essere nel futuro")
        return v


class SummaryRequest(BaseModel):
    start: datetime = Field(
        default_factory=datetime.now,
        description="Inizio periodo. Se omesso, il sistema usa automaticamente la data e l'ora corrente."
    )
    end: Optional[datetime] = Field(
        default=None,
        description="Fine periodo. Se omesso, viene usata la fine della giornata corrente (23:59:59 di oggi)."
    )
    camera_ids: list[str] = Field(default_factory=list, description="Lista di camere su cui filtrare")
    custom_prompt: Optional[str] = Field(default=None, description="Prompt personalizzato opzionale dell'utente")

    def resolved_end(self) -> datetime:
        if self.end is not None:
            return self.end
        return datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)


# FUNZIONI DI UTILITY
def event_to_doc(event: Event) -> dict:
    doc = event.model_dump()
    doc["timestamp"] = event.timestamp.isoformat()
    return doc


def doc_to_event(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


def _build_time_query(start: datetime, end: datetime) -> dict:
    return {"timestamp": {"$gte": start.isoformat(), "$lte": end.isoformat()}}

def _detect_anomalies(events: list[dict]) -> list[dict]:
    anomalies = []
    for e in events:
        ts_str = e.get("timestamp", "")
        try:
            hour = int(ts_str[11:13])
        except (ValueError, IndexError):
            hour = 12

        is_night   = hour >= 22 or hour < 6
        is_evening = 19 <= hour < 22
        is_day     = 8  <= hour < 19
        etype      = e.get("event_type", "")

        if etype in ("intrusion", "anomaly"):
            label    = "ANOMALIA ESPLICITA"
            severity = _severity(etype, is_night)
            reason   = "Evento pericoloso indipendentemente dall'orario"

        elif etype == "loitering":
            if is_night:
                label    = "ANOMALIA ESPLICITA"
                severity = "ALTA"
                reason   = "Stazionamento prolungato in orario notturno"
            elif is_evening:
                label    = "POTENZIALMENTE ANOMALO"
                severity = "MEDIA"
                reason   = "Stazionamento prolungato in orario serale"
            else:
                label    = "FORSE ROUTINE"
                severity = "BASSA"
                reason   = "Stazionamento prolungato ma in orario diurno"

        elif etype == "crowd":
            if is_night:
                label    = "ANOMALIA CONTESTUALE"
                severity = "MEDIA"
                reason   = "Gruppo di persone in orario notturno"
            elif is_evening:
                label    = "POTENZIALMENTE ANOMALO"
                severity = "BASSA"
                reason   = "Gruppo di persone in orario serale"
            elif is_day:
                label    = "FORSE ROUTINE"
                severity = "BASSA"
                reason   = "Gruppo di persone in orario diurno — monitoraggio leggero"
            else:
                continue

        elif etype in ("movement", "idle") and is_night:
            label    = "ANOMALIA CONTESTUALE"
            severity = "BASSA"
            reason   = "Movimento/presenza in orario notturno inatteso"

        else:
            continue

        anomalies.append({
            "timestamp":   ts_str,
            "camera_id":   e.get("camera_id"),
            "location":    e.get("location"),
            "type":        etype,
            "label":       label,
            "description": e.get("description"),
            "reason":      reason,
            "severity":    severity,
        })
    return anomalies


def _severity(event_type: str, is_night: bool) -> str:
    if event_type == "intrusion":
        return "ALTA"
    if event_type == "loitering":
        return "MEDIA" if not is_night else "ALTA"
    if event_type == "anomaly":
        return "MEDIA"
    if is_night:
        return "BASSA"
    return "BASSA"


# MODULO LLM — OLLAMA LOCALE
async def call_llm(prompt: str) -> str:
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "[Ollama: risposta vuota]")
    except httpx.ConnectError:
        return f"[Errore Ollama] Impossibile connettersi a {OLLAMA_BASE_URL}."
    except httpx.TimeoutException:
        return f"[Errore Ollama] Timeout '{OLLAMA_MODEL}'."
    except Exception as e:
        return f"[Errore Ollama] {type(e).__name__}: {e}"


async def check_ollama_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            model_available = any(OLLAMA_MODEL in m for m in models)
            return {
                "ollama_reachable": True,
                "models_available": models,
                "requested_model": OLLAMA_MODEL,
                "model_ready": model_available,
                "warning": None if model_available else f"Modello '{OLLAMA_MODEL}' non trovato.",
            }
    except Exception as e:
        return {"ollama_reachable": False, "error": str(e)}


def _build_summary_prompt(
    events: list[dict],
    anomalies: list[dict],
    start: datetime,
    end: datetime,
    camera_ids: list[str],
) -> str:

    righe = "\n".join(
        f"- [{e['timestamp'][11:16]}] {e['camera_id']} ({e['location']}): "
        f"{e['description']} [tipo={e['event_type']}, confidence={e.get('metadata', {}).get('confidence', 'N/A')}]"
        for e in events
    )

    camere_formattate = ", ".join(camera_ids) if camera_ids else "tutte"

    # pre-calcola conteggi per non far contare il modello
    from collections import Counter
    by_type     = Counter(e["event_type"] for e in events)
    by_camera   = Counter(e["camera_id"]  for e in events)
    stats_block = "\n".join(
        f"  - {k}: {v} eventi" for k, v in sorted(by_type.items(), key=lambda x: -x[1])
    )
    camera_block = "\n".join(
        f"  - {k} ({CAMERAS_REGISTRY.get(k, k)}): {v} eventi"
        for k, v in sorted(by_camera.items(), key=lambda x: -x[1])
    )

    return f"""Sei un sistema adibito all'analisi della sicurezza.
Il tuo compito è analizzare gli eventi osservati e classificarli distinguendo
routine, anomalie contestuali e anomalie esplicite.

Contesto operativo:

Periodo analizzato : {start.strftime('%d/%m/%Y %H:%M')} → {end.strftime('%d/%m/%Y %H:%M')}
Telecamere incluse : {camere_formattate}
Totale eventi      : {len(events)}
Anomalie rilevate  : {len(anomalies)}

Criteri di normalità:

Il sistema monitora un ambiente con il seguente comportamento atteso:

Fasce orarie:
  • 08:00–10:00  Alta affluenza (apertura) — movimento e crowd sono ROUTINE
  • 10:00–12:00  Attività normale         — movement/idle/crowd sono ROUTINE
  • 12:00–14:00  Pausa pranzo             — attività ridotta, idle frequente è ROUTINE
  • 14:00–17:00  Attività normale         — movement/idle/crowd sono ROUTINE
  • 17:00–19:00  Alta affluenza (chiusura)— movement e crowd sono ROUTINE
  • 19:00–22:00  Fuori orario             — qualsiasi presenza è FORSE ANOMALA
  • 22:00–06:00  Orario notturno          — qualsiasi presenza è ANOMALIA CONTESTUALE o ESPLICITA

Comportamenti SEMPRE ANOMALI (indipendentemente dall'orario):
  • intrusion    : accesso non autorizzato, porta forzata, finestra rotta
  • anomaly      : cadute, oggetti sospetti, emergenze mediche
  • loitering    : stazionamento prolungato davanti a porte/accessi

Comportamenti ambigui (valutare in base all'orario):
  • crowd        : gruppo di persone — routine di giorno, sospetto di notte
  • movement     : singola persona in movimento — routine di giorno, anomalia di notte
  • idle         : persona ferma — routine di giorno (attesa sportello), anomala di notte

Statistiche precalcolate:
Per tipo evento:
{stats_block}

Per telecamera:
{camera_block}

Classificazione da usare:

Usa ESCLUSIVAMENTE queste 5 etichette:
  ROUTINE                — evento atteso per orario e contesto
  ANOMALIA CONTESTUALE   — evento innocuo ma sospetto per l'orario
  ANOMALIA ESPLICITA     — evento pericoloso indipendentemente dall'orario
  FORSE ROUTINE          — evento ambiguo diurno, monitoraggio leggero
  POTENZIALMENTE ANOMALO — evento ambiguo serale/notturno, richiede verifica

Struttura della risposta:

1. Riepilogo Generale:
   Sintesi in 3-4 righe del periodo osservato.

2. Classificazione eventi:
   [HH:MM] <camera> (<location>) — <descrizione>
   → Etichetta | Gravità: ALTA/MEDIA/BASSA | Motivo: <spiegazione>

3. Pattern rilevati:
   Tendenze ricorrenti, orari critici, camere più attive.

4. Raccomandazioni operative:
   Azioni suggerite basate sulle anomalie rilevate.

Eventi da classificare ({len(events)} totali)
{righe}

Rispondi in italiano. Sii preciso e conciso."""


@app.get("/", tags=["Sistema"])
def home():
    return {
        "message": "Sistema di archiviazione eventi attivo (LLM: Ollama locale)",
        "version": "2.0.0",
        "ollama_url": OLLAMA_BASE_URL,
        "ollama_model": OLLAMA_MODEL,
        "docs": "/docs",
    }


@app.get("/llm/status", tags=["Sistema"])
async def llm_status():
    return await check_ollama_status()


# ENDPOINT — CAMERAS
@app.get("/cameras", tags=["Cameras"])
def get_cameras():
    return {
        "count": len(CAMERAS_REGISTRY),
        "cameras": [{"camera_id": k, "location": v} for k, v in CAMERAS_REGISTRY.items()],
    }


@app.post("/events", status_code=201, tags=["Eventi"])
async def create_event(event: Event):
    doc    = event_to_doc(event)
    result = await collection.insert_one(doc)
    return {
        "status":     "success",
        "id":         str(result.inserted_id),
        "event_type": event.event_type,
        "timestamp":  event.timestamp.isoformat(),
    }


@app.get("/events", tags=["Eventi"])
async def get_events(
    start: datetime = Query(..., description="Inizio intervallo"),
    end: datetime = Query(..., description="Fine intervallo"),
    camera_ids: Optional[list[str]] = Query(None, description="Filtra per una o più camere"),
    event_type: Optional[str] = Query(None, description="Filtra per tipo di evento"),
    location: Optional[str] = Query(None, description="Parola chiave nella location"),
    keyword: Optional[str] = Query(None, description="Parola chiave nella descrizione"),
    limit: int = Query(100, ge=1, le=1000),
):
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query: dict = _build_time_query(start, end)

    if camera_ids:
        query["camera_id"] = {"$in": camera_ids}

    if event_type:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"event_type non valido: {ALLOWED_EVENT_TYPES}")
        query["event_type"] = event_type
    if location:
        query["location"] = {"$regex": location, "$options": "i"}
    if keyword:
        query["description"] = {"$regex": keyword, "$options": "i"}

    cursor = collection.find(query).sort("timestamp", 1).limit(limit)
    events = [doc_to_event(doc) async for doc in cursor]

    return {
        "count": len(events),
        "filters": {
            "start": start.isoformat(), "end": end.isoformat(),
            "camera_ids": camera_ids, "event_type": event_type,
            "location": location, "keyword": keyword,
        },
        "events": events,
    }


@app.get("/events/{event_id}", tags=["Eventi"])
async def get_event_by_id(event_id: str):
    try:
        oid = ObjectId(event_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    doc = await collection.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    return doc_to_event(doc)


@app.delete("/events/{event_id}", tags=["Eventi"])
async def delete_event(event_id: str):
    try:
        oid = ObjectId(event_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    result = await collection.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    return {"status": "deleted", "id": event_id}


@app.delete("/events", tags=["Eventi"])
async def delete_events_in_range(
    start:      datetime            = Query(...),
    end:        datetime            = Query(...),
    camera_ids: Optional[list[str]] = Query(None),
):
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(start, end)
    if camera_ids:
        query["camera_id"] = {"$in": camera_ids}

    result = await collection.delete_many(query)
    return {"status": "deleted", "deleted_count": result.deleted_count}


# ENDPOINT — STATISTICHE
@app.get("/stats", tags=["Statistiche"])
async def get_stats(
    start: datetime = Query(...),
    end: datetime = Query(...),
    camera_ids: Optional[list[str]] = Query(None),
):
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(start, end)

    if camera_ids:
        query["camera_id"] = {"$in": camera_ids}

    cursor = collection.find(query)
    events = [doc_to_event(doc) async for doc in cursor]

    by_type: dict[str, int] = {}
    by_camera: dict[str, int] = {}
    for e in events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
        by_camera[e["camera_id"]] = by_camera.get(e["camera_id"], 0) + 1

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_events": len(events),
        "anomaly_count": len(_detect_anomalies(events)),
        "by_event_type": by_type,
        "by_camera": by_camera,
    }


# ENDPOINT — SINTESI
@app.post("/summaries", tags=["Sintesi LLM"])
async def generate_summary(req: SummaryRequest):
    end = req.resolved_end()

    if end <= req.start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(req.start, end)

    if req.camera_ids:
        query["camera_id"] = {"$in": req.camera_ids}

    cursor = collection.find(query).sort("timestamp", 1)
    events = [doc_to_event(doc) async for doc in cursor]

    if not events:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Nessun evento trovato tra {req.start.isoformat()} e {end.isoformat()}. "
                "Controlla che il simulatore abbia inviato eventi in questo intervallo."
            ),
        )

    anomalies = _detect_anomalies(events)

    if req.custom_prompt and req.custom_prompt.strip():
        righe_eventi = "\n".join(
            f"- [{e['timestamp'][11:16]}] Camera: {e['camera_id']} ({e['location']}) | Tipo: {e['event_type']} | Descrizione: {e['description']}"
            for e in events
        )
        prompt = (
            f"Sei un sistema di intelligenza artificiale per la sicurezza.\n"
            f"Il tuo compito è leggere attentamente la lista degli eventi grezzi riportata sotto, "
            f"comprenderne il significato e rispondere alla richiesta dell'operatore in modo diretto, preciso e conciso.\n"
            f"Se la richiesta implica un conteggio (es. 'quanti...'), analizza i testi, conta gli elementi corrispondenti e fornisci il risultato numerico spiegando brevemente il perché.\n"
            f"Rispondi in italiano e non ripetere l'intera lista degli eventi nella risposta.\n\n"
            f"RICHIESTA OPERATORE: {req.custom_prompt}\n\n"
            f"LISTA DEGLI EVENTI DA ANALIZZARE ({len(events)} totali):\n{righe_eventi}\n\n"
            f"RISPOSTA DELL'ASSISTENTE AI:"
        )
    else:
        # Se non c'è un prompt personalizzato, usa il report standard
        prompt = _build_summary_prompt(events, anomalies, req.start, end, req.camera_ids)
    sintesi = await call_llm(prompt)
#TODO STA ROBA CI METE UNA MAREA E RISPONDE CON LA SINTESI STANDARD
    if req.custom_prompt and req.custom_prompt.strip():
        return {"summary": sintesi}

    return {
        "period": {"start": req.start.isoformat(), "end": end.isoformat()},
        "end_auto": req.end is None,
        "camera_ids": req.camera_ids,
        "llm_backend": f"Ollama ({OLLAMA_MODEL}) @ {OLLAMA_BASE_URL}",
        "total_events": len(events),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "summary": sintesi,
    }