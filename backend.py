from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timedelta, time
from typing import Optional
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import httpx
import os
import yaml
from dotenv import load_dotenv

load_dotenv()

MONGO_DETAILS = os.getenv("MONGO_DETAILS", "mongodb://mongodb:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sistema_eventi")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "eventi_osservati")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")


#dominio applicatifo da config.yaml
def load_domain_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise RuntimeError(
            f"File di configurazione '{path}' non trovato. "
            "Verifica CONFIG_PATH nel .env e che config.yaml sia presente "
            "(e copiato nell'immagine Docker)."
        )
    return cfg


DOMAIN_CONFIG = load_domain_config(CONFIG_PATH)

ALLOWED_EVENT_TYPES = set(DOMAIN_CONFIG.get("event_types", ["movement", "crowd", "idle"]))
CAMERAS_REGISTRY = DOMAIN_CONFIG.get("cameras", {})
EMPLOYEE_TAG = DOMAIN_CONFIG.get("actor_tags", {}).get("employee", "employee")
TIME_WINDOWS = DOMAIN_CONFIG.get("time_windows", [])
SECURITY_RULES = DOMAIN_CONFIG.get("security_rules", {})
LIMITS_CONFIG = DOMAIN_CONFIG.get("limits", {})
MAX_EVENTS_LIMIT = LIMITS_CONFIG.get("max_events", 1000)
DEFAULT_EVENTS_LIMIT = LIMITS_CONFIG.get("default_events", 100)
MONGO_INDEXES = DOMAIN_CONFIG.get("mongo_indexes", ["timestamp", "camera_id", "event_type"])
LLM_CONFIG = DOMAIN_CONFIG.get("llm", {})
LLM_CATEGORIES = LLM_CONFIG.get("categories", [])
LLM_LANGUAGE = LLM_CONFIG.get("language", "it")
LLM_TEMPERATURE = LLM_CONFIG.get("temperature", 0.1)
LLM_MIN_NUM_CTX = LLM_CONFIG.get("min_num_ctx", 2048)
LLM_MIN_OUTPUT_TOKENS = LLM_CONFIG.get("min_output_tokens", 2048)
LLM_TOKENS_PER_EVENT = LLM_CONFIG.get("tokens_per_event", 80)
LLM_RESPONSE_LANGUAGE = LLM_CONFIG.get("response_language_name", "italiano")
LLM_DOMAIN_DESCRIPTION = LLM_CONFIG.get("domain_description", "una struttura")
LLM_PROMPTS = LLM_CONFIG.get("prompts", {})

# Schema del dataset eventi (nomi dei campi), definito in config.yaml
EVENT_SCHEMA = DOMAIN_CONFIG.get("event_schema", {})
FIELD_TIMESTAMP   = EVENT_SCHEMA.get("timestamp_field", "timestamp")
FIELD_CAMERA      = EVENT_SCHEMA.get("camera_field", "camera_id")
FIELD_LOCATION    = EVENT_SCHEMA.get("location_field", "location")
FIELD_DESCRIPTION = EVENT_SCHEMA.get("description_field", "description")
FIELD_TYPE        = EVENT_SCHEMA.get("type_field", "event_type")
FIELD_TAGS        = EVENT_SCHEMA.get("tags_field", "metadata.tags")


def get_nested_field(doc: dict, dotted_field: str, default=None):
    """Legge un campo anche annidato, es. 'metadata.tags'."""
    value = doc
    for part in dotted_field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
    return value if value is not None else default


PROMPT_CUSTOM_INTRO = LLM_PROMPTS.get(
    "custom_intro",
    "Sei un sistema di intelligenza artificiale per la sicurezza."
)
PROMPT_SUMMARY_INTRO = LLM_PROMPTS.get(
    "summary_intro",
    "Sei un sistema di analisi della sicurezza per {domain_description}."
).format(domain_description=LLM_DOMAIN_DESCRIPTION)

mongo_client = AsyncIOMotorClient(MONGO_DETAILS)
database     = mongo_client[MONGO_DB_NAME]
collection   = database.get_collection(MONGO_COLLECTION_NAME)

# LIFECYCLE
@asynccontextmanager
async def lifespan(app: FastAPI):
    for field in MONGO_INDEXES:
        await collection.create_index(field)
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

def _build_contesto_struttura() -> str:
    mappa_telecamere_txt = "\n".join(f"  - {k}: {v}" for k, v in CAMERAS_REGISTRY.items())

    fasce_orarie_txt = "\n".join(
        f"  {tw['start']}–{tw['end']}  {tw['label']}"
        for tw in TIME_WINDOWS
    )

    criteri_txt_righe = [
        f"  - {tw['label']} ({tw['start']}-{tw['end']}): {tw['note']}"
        for tw in TIME_WINDOWS
    ]
    for regola in SECURITY_RULES.get("restricted_cameras", []):
        criteri_txt_righe.append(f"  - {regola['rule']}")
    criteri_txt = "\n".join(criteri_txt_righe)

    return f"""CONTESTO DELLA STRUTTURA:

TELECAMERE E MAPPA DELLE ZONE:
{mappa_telecamere_txt}

FASCE ORARIE DELLA STRUTTURA:
{fasce_orarie_txt}

CRITERI DI SICUREZZA E NORMALITÀ:
{criteri_txt}"""


CONTESTO_STRUTTURA = _build_contesto_struttura()

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
    excluded_ids: list[str] = Field(
        default_factory=list,
        description="Lista di _id MongoDB da escludere dall'analisi LLM"
    )
    selected_events: list[dict] = Field(
        default_factory=list,
        description="Eventi già filtrati dal frontend"
    )

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
    start_time = start.time()
    end_time = end.time()

    current_day = start.date()
    last_day = end.date()

    clauses = []

    while current_day <= last_day:

        window_start = datetime.combine(current_day, start_time)

        if end_time >= start_time:
            # stessa giornata
            window_end = datetime.combine(current_day, end_time)

        elif current_day < last_day:
            # attraversa la mezzanotte
            window_end = datetime.combine(
                current_day + timedelta(days=1),
                end_time
            )
        else:

            window_end = datetime.combine(
                current_day,
                time(23, 59, 59, 999999)
            )

        clauses.append({
            FIELD_TIMESTAMP: {
                "$gte": window_start.isoformat(),
                "$lte": window_end.isoformat()
            }
        })

        current_day += timedelta(days=1)

    return {"$or": clauses}

# MODULO LLM — OLLAMA LOCALE
async def call_llm(prompt: str, n_events: int = 0) -> str:

    output_tokens = max(LLM_MIN_OUTPUT_TOKENS, n_events * LLM_TOKENS_PER_EVENT + 1024)
    prompt_tokens_estimate = max(LLM_MIN_NUM_CTX, len(prompt) // 4)
    num_ctx = int((prompt_tokens_estimate + output_tokens) * 1.2)
    ctx_power2 = 1
    while ctx_power2 < num_ctx:
        ctx_power2 *= 2
    num_ctx = min(ctx_power2, 65536)

    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": output_tokens,
            "num_ctx": num_ctx,
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
    start: datetime,
    end: datetime,
    camera_ids: list[str],
) -> str:
    righe = []
    for e in events:
        tags = get_nested_field(e, FIELD_TAGS, [])
        soggetto = "dipendente autorizzato" if EMPLOYEE_TAG in tags else "persona esterna non autorizzata"
        ts = e.get(FIELD_TIMESTAMP)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        data_ora = ts.strftime("%d/%m/%Y %H:%M")
        camera_id   = e.get(FIELD_CAMERA)
        location    = e.get(FIELD_LOCATION)
        description = e.get(FIELD_DESCRIPTION)
        righe.append(
            f"[{data_ora}] {camera_id} ({location}) | {description} | soggetto:{soggetto}"
        )
    eventi_txt = "\n".join(righe)
    n = len(events)

    guida_categorie_txt = "\n".join(
        f"  {c['name']:<24}→ {c['guida']}" for c in LLM_CATEGORIES
    )
    struttura_sezioni_txt = "\n\n".join(
        f"### {c['name']}\n"
        f"- [GG/MM/AAAA HH:MM] <camera> (<location>) | <descrizione> | Motivo: <{c['esempio_motivo']}>"
        for c in LLM_CATEGORIES
    )

    return f"""{PROMPT_SUMMARY_INTRO}
{CONTESTO_STRUTTURA}

GUIDA ALLE CATEGORIE (usala per assegnare ogni evento):
{guida_categorie_txt}

REGOLE DI RISPOSTA:
 - HAI RICEVUTO ESATTAMENTE {n} EVENTI. DEVI CLASSIFICARNE ESATTAMENTE {n}. Né di più, né di meno.
 - OGNI EVENTO COMPARE UNA SOLA VOLTA nella risposta. Duplicare un evento è un errore CRITICO.
 - Assegna ogni evento alla sezione più appropriata in base a orario, zona e soggetto.
 - NON RISCRIVERE la lista di eventi in blocco.
 - Formato OBBLIGATORIO di ogni riga classificata:
   [GG/MM/AAAA HH:MM] <camera> (<location>) | <descrizione> | Motivo: <la tua spiegazione>
 - Non aggiungere il tipo di soggetto nelle righe di output.
 - Se una sezione non ha eventi, OMETTILA completamente.

Periodo analizzato: {start.strftime('%d/%m/%Y %H:%M')} → {end.strftime('%d/%m/%Y %H:%M')}

HAI QUESTI EVENTI DA CLASSIFICARE :
{eventi_txt}

Rispondi in {LLM_RESPONSE_LANGUAGE} con questa ESATTA struttura.
{struttura_sezioni_txt}

Riepilogo:
"""

@app.get("/", tags=["Sistema"])
def home():
    return {
        "message": "Sistema di archiviazione eventi attivo (LLM: Ollama locale)",
        "version": "2.0.0",
        "ollama_url": OLLAMA_BASE_URL,
        "ollama_model": OLLAMA_MODEL,
        "llm_provider": LLM_PROVIDER,
        "mongo_db": MONGO_DB_NAME,
        "mongo_collection": MONGO_COLLECTION_NAME,
        "config_path": CONFIG_PATH,
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
    limit: int = Query(DEFAULT_EVENTS_LIMIT, ge=0, le=MAX_EVENTS_LIMIT),
):
    if end <= start:
        raise HTTPException(status_code=400, detail="'end' deve essere successivo a 'start'")

    conditions=[_build_time_query(start,end)]

    if camera_ids:
        conditions.append({
        FIELD_CAMERA: {"$in": camera_ids}
        })
    if event_type:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise HTTPException(status_code=400, detail=f"event_type non valido: {ALLOWED_EVENT_TYPES}")

        conditions.append({
            FIELD_TYPE: event_type
        })

    if location:
        conditions.append({
            FIELD_LOCATION: {
                "$regex": location,
                "$options": "i"
            }
        })
    query = {"$and": conditions}
    cursor = collection.find(query).sort(FIELD_TIMESTAMP, 1)

    if limit > 0:
        cursor = cursor.limit(limit)

    events = [doc_to_event(doc) async for doc in cursor]

    return {
        "count": len(events),
        "filters": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "camera_ids": camera_ids,
            "event_type": event_type,
            "location": location,
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
        query[FIELD_CAMERA] = {"$in": camera_ids}

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
        query[FIELD_CAMERA] = {"$in": camera_ids}

    cursor = collection.find(query)
    events = [doc_to_event(doc) async for doc in cursor]

    by_type: dict[str, int] = {}
    by_camera: dict[str, int] = {}
    for e in events:
        tipo = get_nested_field(e, FIELD_TYPE)
        cam = get_nested_field(e, FIELD_CAMERA)
        by_type[tipo] = by_type.get(tipo, 0) + 1
        by_camera[cam] = by_camera.get(cam, 0) + 1

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "total_events": len(events),
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
        query[FIELD_CAMERA] = {"$in": req.camera_ids}

    if req.selected_events:
        events = req.selected_events
    else:
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

    if req.custom_prompt and req.custom_prompt.strip():
        righe_lista = []
        for e in events:
            tags = get_nested_field(e, FIELD_TAGS, [])
            soggetto = "dipendente autorizzato" if EMPLOYEE_TAG in tags else "persona non autorizzata"
            ts = e.get(FIELD_TIMESTAMP)
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ora = ts.strftime("%H:%M")
            camera_id   = e.get(FIELD_CAMERA)
            location    = e.get(FIELD_LOCATION)
            event_type_ = e.get(FIELD_TYPE)
            description = e.get(FIELD_DESCRIPTION)
            righe_lista.append(
                f"- [{ora}] Camera: {camera_id} ({location}) | Tipo: {event_type_} | Soggetto: {soggetto} | Descrizione: {description}"
            )
        righe_eventi = "\n".join(righe_lista)
        prompt = (
            f"{PROMPT_CUSTOM_INTRO}\n\n"
            f"{CONTESTO_STRUTTURA}\n\n"
            f"ISTRUZIONI ADDIZIONALI:\n"
            f"- Se la richiesta implica un conteggio (es. 'quanti...'), analizza i testi, conta gli elementi corrispondenti e fornisci il risultato numerico.\n"
            f"- Rispondi in {LLM_RESPONSE_LANGUAGE} e non ripetere l'intera lista degli eventi nella risposta.\n\n"
            f"RICHIESTA OPERATORE: {req.custom_prompt}\n\n"
            f"LISTA DEGLI EVENTI DA ANALIZZARE ({len(events)} totali):\n{righe_eventi}\n\n"
        )
    else:
        prompt = _build_summary_prompt(events, req.start, end, req.camera_ids)

    sintesi = await call_llm(prompt, n_events=len(events))

    if req.custom_prompt and req.custom_prompt.strip():
        return {"summary": sintesi}

    return {
        "period": {"start": req.start.isoformat(), "end": end.isoformat()},
        "end_auto": req.end is None,
        "camera_ids": req.camera_ids,
        "llm_backend": f"Ollama ({OLLAMA_MODEL}) @ {OLLAMA_BASE_URL}",
        "total_events": len(events),
        "summary": sintesi,
    }