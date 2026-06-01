from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import httpx
import os


#CONFIGURAZIONE

MONGO_DETAILS = os.getenv(
    "MONGO_DETAILS",
    "mongodb+srv://lorenzotesi:Zekrom03!@progettotesi.eyjcybv.mongodb.net/?appName=ProgettoTesi",
)

#URL del server Ollama locale.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

mongo_client = AsyncIOMotorClient(MONGO_DETAILS)
database     = mongo_client.sistema_eventi
collection   = database.get_collection("eventi_osservati")

app = FastAPI(
    title="Sistema Sorveglianza Intelligente (Ollama)",
    version="1.0.0",
    description=(
        "Backend con LLM locale Ollama per sintesi eventi di sorveglianza. "
    ),
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

#SCHEMI PYDANTIC
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
    camera_id: Optional[str] = Field(default=None, description="Filtra su una sola camera")

    def resolved_end(self) -> datetime:
        if self.end is not None:
            return self.end
        return datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)

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

        is_night = hour >= 22 or hour < 6
        etype    = e.get("event_type", "")

        explicit   = etype in ("intrusion", "loitering", "anomaly")
        contextual = is_night and etype in ("crowd", "movement")

        if explicit or contextual:
            anomalies.append({
                "timestamp":   ts_str,
                "camera_id":   e.get("camera_id"),
                "location":    e.get("location"),
                "type":        etype,
                "description": e.get("description"),
                "reason":      "anomalia esplicita" if explicit else "anomalia contestuale (orario notturno)",
                "severity":    _severity(etype, is_night),
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



#MODULO LLM — OLLAMA LOCALE

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
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            # /api/generate restituisce {"response": "testo generato", ...}
            return data.get("response", "[Ollama: risposta vuota]")

    except httpx.ConnectError:
        return (
            f"[Errore Ollama] Impossibile connettersi a {OLLAMA_BASE_URL}. "
            "Assicurati che Ollama sia avviato con 'ollama serve'."
        )
    except httpx.TimeoutException:
        return (
            f"[Errore Ollama] Timeout dopo 120s. "
            f"Il modello '{OLLAMA_MODEL}' potrebbe essere troppo pesante per questa macchina. "
            "Prova con un modello più leggero (phi3:mini, gemma3:4b)."
        )
    except Exception as e:
        return f"[Errore Ollama] {type(e).__name__}: {e}"


async def check_ollama_status() -> dict:
    """Verifica che Ollama sia raggiungibile e che il modello richiesto sia disponibile."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # GET /api/tags restituisce i modelli scaricati
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            model_available = any(OLLAMA_MODEL in m for m in models)
            return {
                "ollama_reachable": True,
                "models_available": models,
                "requested_model": OLLAMA_MODEL,
                "model_ready": model_available,
                "warning": None if model_available else (
                    f"Modello '{OLLAMA_MODEL}' non trovato. "
                    f"Esegui: ollama pull {OLLAMA_MODEL}"
                ),
            }
    except Exception as e:
        return {
            "ollama_reachable": False,
            "error": str(e),
            "hint": f"Avvia Ollama con: ollama serve  (oppure controlla {OLLAMA_BASE_URL})",
        }


def _build_summary_prompt(
    events: list[dict],
    anomalies: list[dict],
    start: datetime,
    end: datetime,
    camera_id: Optional[str],
) -> str:

    righe = "\n".join(
        f"- [{e['timestamp'][11:16]}] {e['camera_id']} ({e['location']}): "
        f"{e['description']} "
        f"[tipo_sensore={e['event_type']}, confidence={e.get('metadata', {}).get('confidence', 'N/A')}]"
        for e in events
    )

    return f"""Sei un sistema esperto di analisi della sicurezza fisica.
Devi analizzare gli eventi riportati sotto e classificare OGNI evento usando
ESCLUSIVAMENTE una delle seguenti 5 etichette (scritte esattamente così):

  ROUTINE
  ANOMALIA CONTESTUALE
  ANOMALIA ESPLICITA
  FORSE ROUTINE
  POTENZIALMENTE ANOMALO

DEFINIZIONI DELLE 5 ETICHETTE:

ROUTINE
  Evento atteso per orario e contesto. Nessuna azione richiesta.
  Esempio: persona attraversa il corridoio alle 10:00.

ANOMALIA CONTESTUALE
  Evento di per sé innocuo, ma sospetto perché avviene in orario inatteso
  (tipicamente tra 22:00 e 06:00, o fuori dall'orario lavorativo).
  Esempio: persona attraversa il corridoio alle 03:00.

ANOMALIA ESPLICITA
  Evento pericoloso o chiaramente fuori norma INDIPENDENTEMENTE dall'orario.
  Comprende: cadute, intrusioni, accessi non autorizzati, oggetti abbandonati,
  comportamenti aggressivi o sospetti dichiarati dal sensore.
  Esempio: persona cade a terra alle 10:00.

FORSE ROUTINE
  Evento ambiguo che potrebbe essere normale in orario diurno (06:00–21:00),
  ma merita monitoraggio. Non scattare allarme, solo annotare.
  Esempio: gruppo fermo davanti alla porta alle 11:00.

POTENZIALMENTE ANOMALO
  Evento ambiguo che in orario serale/notturno (21:00–06:00) richiede
  verifica attiva da parte del personale.
  Esempio: gruppo fermo davanti alla porta alle 23:30.

FASCE ORARIE:

06:00–08:00  Apertura, attività in crescita
08:00–12:00  Orario lavorativo pieno
12:00–14:00  Pausa pranzo, attività ridotta
14:00–18:00  Orario lavorativo normale
18:00–21:00  Sera, soglia attenzione più alta
21:00–22:00  Serale tardo, attività molto ridotta
22:00–06:00  Notte, qualsiasi attività richiede attenzione

COMPITO:

Periodo analizzato: {start.strftime('%d/%m/%Y %H:%M')} → {end.strftime('%d/%m/%Y %H:%M')}
Camera: {camera_id or "tutte"}
Totale eventi: {len(events)}

Struttura la risposta esattamente così:

1. RIEPILOGO GENERALE
   Descrizione sintetica dell'attività nel periodo.

2. CLASSIFICAZIONE EVENTI
   Per ogni evento elenca:
   [HH:MM] <camera> - <descrizione>
   Etichetta: <una delle 5 etichette>
   Gravità: BASSA / MEDIA / ALTA  (solo se etichetta != ROUTINE)
   Motivo: <una riga di spiegazione>

3. PATTERN RILEVATI
   Ripetizioni, concentrazioni orarie o spaziali degne di nota.

4. RACCOMANDAZIONI OPERATIVE
   Azioni concrete per il personale di sicurezza.

EVENTI DA CLASSIFICARE ({len(events)} totali):
{righe}

Rispondi in italiano. Sii preciso e conciso. Non usare etichette diverse dalle 5 indicate."""


# LIFECYCLE
@app.on_event("startup")
async def startup_event():
    await collection.create_index("timestamp")
    await collection.create_index("camera_id")
    await collection.create_index("event_type")

# ENDPOINT — HEALTHCHECK
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
    """
    Verifica che Ollama sia raggiungibile e che il modello richiesto sia scaricato.
    Chiama questo endpoint prima di usare /summaries.
    """
    return await check_ollama_status()

# ENDPOINT — CAMERAS
@app.get("/cameras", tags=["Cameras"])
def get_cameras():
    return {
        "count": len(CAMERAS_REGISTRY),
        "cameras": [
            {"camera_id": k, "location": v}
            for k, v in CAMERAS_REGISTRY.items()
        ],
    }



# ENDPOINT — EVENTI (CRUD)
@app.post("/events", status_code=201, tags=["Eventi"])
async def create_event(event: Event):
    """Valida e salva un nuovo evento su MongoDB."""
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
    start:      datetime       = Query(..., description="Inizio intervallo es. 2026-04-30T00:00:00"),
    end:        datetime       = Query(..., description="Fine intervallo es. 2026-04-30T23:59:59"),
    camera_id:  Optional[str]  = Query(None, description="Filtra per camera"),
    event_type: Optional[str]  = Query(None, description="Filtra per tipo di evento"),
    location:   Optional[str]  = Query(None, description="Parola chiave nella location"),
    keyword:    Optional[str]  = Query(None, description="Parola chiave nella descrizione"),
    limit:      int            = Query(100, ge=1, le=1000),
):
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query: dict = _build_time_query(start, end)
    if camera_id:
        query["camera_id"] = camera_id
    if event_type:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"event_type non valido: {ALLOWED_EVENT_TYPES}")
        query["event_type"] = event_type
    if location:
        query["location"]    = {"$regex": location, "$options": "i"}
    if keyword:
        query["description"] = {"$regex": keyword, "$options": "i"}

    cursor = collection.find(query).sort("timestamp", 1).limit(limit)
    events = [doc_to_event(doc) async for doc in cursor]

    return {
        "count": len(events),
        "filters": {
            "start": start.isoformat(), "end": end.isoformat(),
            "camera_id": camera_id, "event_type": event_type,
            "location": location,   "keyword": keyword,
        },
        "events": events,
    }


@app.get("/events/{event_id}", tags=["Eventi"])
async def get_event_by_id(event_id: str):
    """Recupera un singolo evento per ID."""
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
    """Elimina un singolo evento per ID."""
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
    start:     datetime      = Query(...),
    end:       datetime      = Query(...),
    camera_id: Optional[str] = Query(None),
):
    """Elimina tutti gli eventi in un intervallo temporale (utile per test)."""
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(start, end)
    if camera_id:
        query["camera_id"] = camera_id

    result = await collection.delete_many(query)
    return {"status": "deleted", "deleted_count": result.deleted_count}



# ENDPOINT — STATISTICHE

@app.get("/stats", tags=["Statistiche"])
async def get_stats(
    start:     datetime      = Query(...),
    end:       datetime      = Query(...),
    camera_id: Optional[str] = Query(None),
):
    """Statistiche aggregate: totale, breakdown per tipo e camera, conteggio anomalie."""
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(start, end)
    if camera_id:
        query["camera_id"] = camera_id

    cursor = collection.find(query)
    events = [doc_to_event(doc) async for doc in cursor]

    by_type:   dict[str, int] = {}
    by_camera: dict[str, int] = {}
    for e in events:
        by_type[e["event_type"]]   = by_type.get(e["event_type"], 0) + 1
        by_camera[e["camera_id"]]  = by_camera.get(e["camera_id"], 0) + 1

    return {
        "period":         {"start": start.isoformat(), "end": end.isoformat()},
        "total_events":   len(events),
        "anomaly_count":  len(_detect_anomalies(events)),
        "by_event_type":  by_type,
        "by_camera":      by_camera,
    }



# ENDPOINT — SINTESI
@app.post("/summaries", tags=["Sintesi LLM"])
async def generate_summary(req: SummaryRequest):
    """
    Genera una sintesi degli eventi usando Ollama locale.

    Flusso:
    1. Recupera eventi da MongoDB nel periodo richiesto
    2. Rileva anomalie (esplicite + contestuali per orario)
    3. Costruisce il prompt
    4. Chiama Ollama su /api/generate (blocking, non-streaming)
    5. Restituisce sintesi + lista anomalie con severità

    Se Ollama non risponde il campo 'summary' conterrà il messaggio di errore.
    Verifica lo stato LLM con GET /llm/status prima di chiamare questo endpoint.
    """
    end = req.resolved_end()

    if end <= req.start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    query = _build_time_query(req.start, end)
    if req.camera_id:
        query["camera_id"] = req.camera_id

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
    prompt    = _build_summary_prompt(events, anomalies, req.start, end, req.camera_id)
    sintesi   = await call_llm(prompt)

    return {
        "period":          {"start": req.start.isoformat(), "end": end.isoformat()},
        "end_auto":        req.end is None,   # True se end è stato calcolato automaticamente
        "camera_id":       req.camera_id or "tutte",
        "llm_backend":     f"Ollama ({OLLAMA_MODEL}) @ {OLLAMA_BASE_URL}",
        "total_events":    len(events),
        "anomaly_count":   len(anomalies),
        "anomalies":       anomalies,
        "summary":         sintesi,
    }