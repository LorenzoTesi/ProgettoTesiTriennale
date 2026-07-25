from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timedelta, time, timezone, date
from zoneinfo import ZoneInfo
from typing import Optional
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import asyncio
import httpx
import os
import re
import json
import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

load_dotenv()

MONGO_DETAILS = os.getenv("MONGO_DETAILS", "mongodb://mongodb:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sistema_eventi")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "eventi_osservati")
MONGO_ANALYSIS_COLLECTION = os.getenv("MONGO_ANALYSIS_COLLECTION","risposte_analisi")
MONGO_PROMPT_COLLECTION = os.getenv("MONGO_PROMPT_COLLECTION","risposte_prompt")
MONGO_SCHEDULER_COLLECTION = os.getenv("MONGO_SCHEDULER_COLLECTION", "risposte_job_periodico")
MONGO_CRITICAL_COLLECTION = os.getenv("MONGO_CRITICAL_COLLECTION", "eventi_critici_cache")


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")

LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Rome"))

VALID_LLM_PROVIDERS = {"ollama", "openai"}
if LLM_PROVIDER not in VALID_LLM_PROVIDERS:
    raise RuntimeError(
        f"LLM_PROVIDER='{LLM_PROVIDER}' non valido. Valori ammessi: {sorted(VALID_LLM_PROVIDERS)}. "
        "Controlla la variabile LLM_PROVIDER nel file .env."
    )
if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
    raise RuntimeError(
        "LLM_PROVIDER='openai' ma OPENAI_API_KEY non è impostata nel file .env."
    )

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

#dominio applicativo da config.yaml
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
LLM_MIN_NUM_CTX = LLM_CONFIG.get("min_num_ctx", 4096)
LLM_MIN_OUTPUT_TOKENS = LLM_CONFIG.get("min_output_tokens", 4096)
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
    value = doc
    for part in dotted_field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
    return value if value is not None else default


PROMPT_CUSTOM_INTRO = LLM_PROMPTS.get(
    "custom_intro",
    "Sei un sistema di analisi per la sicurezza."
)
PROMPT_SUMMARY_INTRO = LLM_PROMPTS.get(
    "summary_intro",
    "Sei un sistema di analisi della sicurezza."
).format(domain_description=LLM_DOMAIN_DESCRIPTION)

mongo_client = AsyncIOMotorClient(MONGO_DETAILS, tz_aware=True)
database     = mongo_client[MONGO_DB_NAME]
collection   = database.get_collection(MONGO_COLLECTION_NAME)
analysis_collection = database.get_collection(MONGO_ANALYSIS_COLLECTION)
prompt_collection = database.get_collection(MONGO_PROMPT_COLLECTION)
scheduler_collection = database.get_collection(MONGO_SCHEDULER_COLLECTION)
critical_collection = database.get_collection(MONGO_CRITICAL_COLLECTION)

# LIFECYCLE
@asynccontextmanager
async def lifespan(app: FastAPI):
    for field in MONGO_INDEXES:
        await collection.create_index(field)
    yield

app = FastAPI(
    title="Sistema Sorveglianza Intelligente",
    version="2.0.0",
    description="Backend con LLM configurabile (Ollama locale o OpenAI cloud) per sintesi eventi di sorveglianza.",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _find_time_window(t) -> Optional[dict]:
    """Trova la fascia oraria (da TIME_WINDOWS) in cui cade l'orario `t` (datetime.time).
    Calcolato deterministicamente in Python invece di lasciarlo indovinare all'LLM,
    che su modelli piccoli tende a sbagliare il confronto numerico tra orari,
    specialmente su fasce che attraversano la mezzanotte (es. 22:00–06:00)."""
    for tw in TIME_WINDOWS:
        start = time.fromisoformat(tw["start"])
        end = time.fromisoformat(tw["end"])
        if start <= end:
            if start <= t < end:
                return tw
        else:
            # Fascia che attraversa la mezzanotte (es. 22:00-06:00)
            if t >= start or t < end:
                return tw
    return None


_RESTRICTED_CAMERA_IDS = {
    r.get("camera_id") for r in SECURITY_RULES.get("restricted_cameras", [])
}


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

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, v: datetime) -> datetime:
        # Se il timestamp arriva senza timezone (es. da una sorgente locale),
        # assumiamo sia orario locale (Europe/Rome) e lo convertiamo esplicitamente
        # in UTC, in modo che venga salvato in Mongo come BSON Date coerente e
        # confrontabile con le query del worker/backend, che lavorano sempre in UTC.
        if v.tzinfo is None:
            v = v.replace(tzinfo=LOCAL_TZ)
        return v.astimezone(timezone.utc)

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


# --- SCHEMA PYDANTIC JOB PERIODICI ---
class AutomationJobCreate(BaseModel):
    titolo: str = Field(..., description="Titolo identificativo del job periodico")
    camera_ids: list[str] = Field(default_factory=list, description="Telecamere abilitate; vuoto = tutte")
    tipi_evento: list[str] = Field(default_factory=list, description="Tipi evento da includere; vuoto = tutti")
    interval_minutes: int = Field(..., gt=0, description="Frequenza di esecuzione in minuti (es. 30, 60, 1440)")
    custom_prompt: Optional[str] = Field(default=None, description="Istruzioni o prompt personalizzato per l'LLM")

    @field_validator("tipi_evento")
    @classmethod
    def validate_tipi_evento(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ALLOWED_EVENT_TYPES
        if invalid:
            raise ValueError(f"tipi_evento non validi: {sorted(invalid)}. Ammessi: {sorted(ALLOWED_EVENT_TYPES)}")
        return v


# FUNZIONI DI UTILITY
def event_to_doc(event: Event) -> dict:
    doc = event.model_dump()
    doc["timestamp"] = event.timestamp
    return doc


def doc_to_event(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc

def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)

def _build_time_query(start: datetime, end: datetime) -> dict:
    start_utc = _to_utc(start)
    end_utc = _to_utc(end)

    start_time = start_utc.time()
    end_time = end_utc.time()

    current_day = start_utc.date()
    last_day = end_utc.date()

    clauses = []

    while current_day <= last_day:

        window_start = datetime.combine(current_day, start_time, tzinfo=timezone.utc)

        if end_time >= start_time:
            # stessa giornata
            window_end = datetime.combine(current_day, end_time, tzinfo=timezone.utc)

        elif current_day < last_day:
            # attraversa la mezzanotte
            window_end = datetime.combine(
                current_day + timedelta(days=1),
                end_time,
                tzinfo=timezone.utc
            )
        else:

            window_end = datetime.combine(
                current_day,
                time(23, 59, 59, 999999),
                tzinfo=timezone.utc
            )

        # Finestra locale corrispondente, usata per il fallback su stringhe naive
        window_start_local = window_start.astimezone(LOCAL_TZ)
        window_end_local = window_end.astimezone(LOCAL_TZ)

        clauses.extend([
            # 1. Stringhe ISO con offset UTC esplicito (es. '...+00:00')
            {FIELD_TIMESTAMP: {
                "$gte": window_start.isoformat(),
                "$lte": window_end.isoformat()
            }},
            # 2. Stringhe naive in orario locale (es. dati storici tipo init.js)
            {FIELD_TIMESTAMP: {
                "$gte": window_start_local.strftime("%Y-%m-%dT%H:%M:%S"),
                "$lte": window_end_local.strftime("%Y-%m-%dT%H:%M:%S")
            }},
            # 3. Veri oggetti Date BSON (comportamento attuale dopo la normalizzazione)
            {FIELD_TIMESTAMP: {
                "$gte": window_start,
                "$lte": window_end
            }},
        ])

        current_day += timedelta(days=1)

    return {"$or": clauses}

#calcola intervallo temporale per job periodici
def _build_last_Nminutes_query(reference: datetime, minutes: int) -> dict:
    ref_utc = _to_utc(reference)
    start_utc = ref_utc - timedelta(minutes=minutes)

    # Orari in UTC
    start_iso_utc = start_utc.isoformat()
    ref_iso_utc = ref_utc.isoformat()

    # Orari in Timezone Locale (es. Europe/Rome) senza tzinfo per matchare stringhe naive
    ref_local = reference.astimezone(LOCAL_TZ) if reference.tzinfo else reference
    start_local = ref_local - timedelta(minutes=minutes)

    start_str_naive = start_local.strftime("%Y-%m-%dT%H:%M:%S")
    end_str_naive = ref_local.strftime("%Y-%m-%dT%H:%M:%S")

    return {
        "$or": [
            # 1. Matching per stringhe con offset ISO (es: '2026-07-23T15:25:00+00:00')
            {FIELD_TIMESTAMP: {"$gte": start_iso_utc, "$lte": ref_iso_utc}},

            # 2. Matching per stringhe naive locali (es: '2026-07-23T17:25:00')
            {FIELD_TIMESTAMP: {"$gte": start_str_naive, "$lte": end_str_naive}},

            # 3. Matching per veri oggetti Date BSON in Mongo
            {FIELD_TIMESTAMP: {"$gte": start_utc, "$lte": ref_utc}}
        ]
    }

def _local_midnight(reference: datetime) -> datetime:
    """Restituisce la mezzanotte (00:00:00) locale del giorno di 'reference'."""
    ref_local = reference.astimezone(LOCAL_TZ) if reference.tzinfo else reference.replace(tzinfo=LOCAL_TZ)
    return ref_local.replace(hour=0, minute=0, second=0, microsecond=0)


async def count_events_until(reference: datetime) -> int:
    """
    Dato un datetime, restituisce il numero di eventi avvenuti in quella
    giornata (dalla mezzanotte locale) fino a quell'ora e minuto compresi.
    """
    inizio_giorno = _local_midnight(reference)
    ref = reference if reference.tzinfo else reference.replace(tzinfo=LOCAL_TZ)
    query = _build_time_query(inizio_giorno, ref)
    return await collection.count_documents(query)


async def get_daily_counts(reference: Optional[datetime] = None) -> dict:
    """Numero di eventi di oggi (dalla mezzanotte a 'reference')."""
    now = reference.astimezone(LOCAL_TZ) if reference else datetime.now(LOCAL_TZ)
    eventi_oggi = await count_events_until(now)

    return {
        "riferimento": now.isoformat(),
        "eventi_oggi": eventi_oggi,
    }

#prompt che indidua solo eventi critici e li restiuisce con JSON
# prompt che individua solo eventi critici richiedendo una decisione esplicita (critico: true/false)
def _build_critical_events_prompt(events: list[dict]) -> str:
    righe = []
    for e in events:
        tags = get_nested_field(e, FIELD_TAGS, [])
        soggetto = "dipendente" if EMPLOYEE_TAG in tags else "visitatore/cliente (non dipendente)"
        ts = e.get(FIELD_TIMESTAMP)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(LOCAL_TZ)
        data_ora = ts.strftime("%d/%m/%Y %H:%M")

        fascia = _find_time_window(ts.time())
        fascia_label = fascia["label"] if fascia else "Sconosciuta"

        camera_id = e.get(FIELD_CAMERA)
        zona = "RISERVATA" if camera_id in _RESTRICTED_CAMERA_IDS else "pubblica/standard"

        righe.append(
            f'- id:"{e.get("_id")}" | [{data_ora}] {camera_id} ({e.get(FIELD_LOCATION)}) '
            f'| {e.get(FIELD_DESCRIPTION)} | tipo:{e.get(FIELD_TYPE)} | soggetto:{soggetto} '
            f'| fascia_oraria:{fascia_label} | zona:{zona}'
        )
    eventi_txt = "\n".join(righe)
    n = len(events)

    return f"""{PROMPT_SUMMARY_INTRO}
{CONTESTO_STRUTTURA}

COMPITO:
Analizza i seguenti {n} eventi uno per uno.
Per ogni evento sono già indicati "fascia_oraria" e "zona": NON ricalcolare tu l'orario, usa
direttamente questi due valori e confrontali con le note delle FASCE ORARIE e con i CRITERI DI
SICUREZZA definiti sopra nel CONTESTO DELLA STRUTTURA per decidere se l'evento è critico.
Per OGNI evento inserito nella lista, esprimi un giudizio esplicito indicando se è CRITICO oppure NO.
Un evento è critico (critico: true) solo se, incrociando fascia_oraria, zona e soggetto,
viola uno dei criteri sopra descritti.
Un evento è normale (critico: false) in tutti gli altri casi.
IMPORTANTE:
- La sola presenza di un visitatore/cliente (non dipendente) NON è di per sé un motivo di
  criticità — lo è solo se il CONTESTO DELLA STRUTTURA per quella fascia_oraria/zona lo qualifica
  esplicitamente come tale (es. orario notturno, zona RISERVATA, fuori orario).
- Non saltare o riassumere eventi: devi restituire una valutazione per TUTTI e {n} gli eventi elencati,
  senza eccezioni, anche se molti si assomigliano tra loro.

REGOLE DI RISPOSTA (OBBLIGATORIE):
 - Rispondi SOLO con un array JSON valido contenente un oggetto per ciascun evento analizzato ({n} oggetti totali). Nessun testo prima o dopo, nessun commento, nessun Markdown.
 - Ogni elemento dell'array deve avere ESATTAMENTE questi campi:
   "id" (l'id dell'evento fornito),
   "critico" (boolean: true se critico, false se normale),
   "motivo" (spiegazione breve in {LLM_RESPONSE_LANGUAGE} della tua valutazione)
 - Non inventare id che non sono nella lista fornita.

EVENTI DA ANALIZZARE:
{eventi_txt}

JSON:"""


# Numero massimo di eventi per chiamata LLM: batch più corti riducono il rischio
# che un modello piccolo "perda" eventi in mezzo a una lista lunga (effetto
# lost-in-the-middle), a costo di più chiamate quando gli eventi del giorno sono tanti.
CRITICAL_BATCH_SIZE = int(os.getenv("CRITICAL_BATCH_SIZE", "12"))


def _parse_json_array(raw: str) -> list[dict]:
    if not raw:
        return []
    testo = raw.strip()
    testo = re.sub(r"^```(?:json)?\s*", "", testo)
    testo = re.sub(r"\s*```$", "", testo)
    try:
        parsed = json.loads(testo)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        pass
    inizio = testo.find("[")
    fine = testo.rfind("]")
    if inizio != -1 and fine != -1 and fine > inizio:
        try:
            parsed = json.loads(testo[inizio:fine + 1])
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _formatta_evento_critico(eid: str, e: dict, motivo: str) -> dict:
    ts = e.get(FIELD_TIMESTAMP)
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if isinstance(ts, datetime) and ts.tzinfo is not None:
        ts = ts.astimezone(LOCAL_TZ)
    data_ora_str = ts.strftime("%d/%m/%Y %H:%M") if isinstance(ts, datetime) else str(ts)

    return {
        "id": eid,
        "timestamp": data_ora_str,
        "camera_id": e.get(FIELD_CAMERA),
        "location": e.get(FIELD_LOCATION),
        "description": e.get(FIELD_DESCRIPTION),
        "motivo": motivo,
    }


async def _analizza_batch_critico(events: list[dict]) -> list[dict]:
    prompt = _build_critical_events_prompt(events)
    raw = await call_llm(prompt, n_events=len(events))
    valutazioni = _parse_json_array(raw)

    events_by_id = {str(e.get("_id")): e for e in events}
    eventi_critici = []
    for item in valutazioni:
        eid = str(item.get("id", ""))
        if eid in events_by_id and item.get("critico") is True:
            eventi_critici.append(
                _formatta_evento_critico(eid, events_by_id[eid], item.get("motivo", "Nessuna motivazione fornita"))
            )
    return eventi_critici


# Chiede al provider di individuare gli eventi critici, elaborando a blocchi per
# ridurre il rischio che eventi vengano "persi" su liste lunghe.
async def analizza_eventi_critici(events: list[dict]) -> list[dict]:
    if not events:
        return []

    eventi_critici: list[dict] = []
    for i in range(0, len(events), CRITICAL_BATCH_SIZE):
        batch = events[i:i + CRITICAL_BATCH_SIZE]
        eventi_critici.extend(await _analizza_batch_critico(batch))

    return eventi_critici


_critical_recompute_lock = asyncio.Lock()


async def recompute_critical_cache() -> dict:
    """Ricalcola e salva in cache gli eventi critici della giornata odierna.

    Valutazione INCREMENTALE: rispetto alla cache già salvata per oggi, chiediamo
    all'LLM di classificare solo gli eventi NON ancora valutati, non l'intera
    giornata da capo. Questo tiene bassa la latenza (importante man mano che gli
    eventi di oggi si accumulano) ed evita di rifare lavoro identico ad ogni
    trigger del watcher.

    Il lock evita che due ricalcoli sovrapposti (es. uno partito dal worker e uno
    lanciato a mano da /docs mentre il primo è ancora in corso) possano finire in
    ordine invertito e sovrascriversi a vicenda, lasciando in cache un risultato
    più vecchio di quello reale.
    """
    async with _critical_recompute_lock:
        oggi = datetime.now(LOCAL_TZ).date()
        oggi_iso = oggi.isoformat()
        inizio_giorno = datetime.combine(oggi, time(0, 0, 0), tzinfo=LOCAL_TZ)
        riferimento = datetime.now(LOCAL_TZ)

        query = _build_time_query(inizio_giorno, riferimento)
        cursor = collection.find(query).sort(FIELD_TIMESTAMP, 1)
        events = [doc_to_event(doc) async for doc in cursor]

        cache_esistente = await critical_collection.find_one({"data": oggi_iso})
        # Se la cache esistente non è di oggi (giorno cambiato) la ignoriamo.
        if cache_esistente and cache_esistente.get("data") == oggi_iso:
            id_gia_valutati = set(cache_esistente.get("id_eventi_valutati", []))
            eventi_critici_precedenti = cache_esistente.get("eventi_critici", [])
        else:
            id_gia_valutati = set()
            eventi_critici_precedenti = []

        eventi_nuovi = [e for e in events if e.get("_id") not in id_gia_valutati]

        critici_nuovi = await analizza_eventi_critici(eventi_nuovi) if eventi_nuovi else []

        eventi_critici = eventi_critici_precedenti + critici_nuovi
        # Le date sono tutte di oggi ("dd/mm/yyyy HH:MM"): ordinamento lessicografico
        # coincide con quello cronologico.
        eventi_critici.sort(key=lambda ev: ev.get("timestamp", ""))

        id_valutati_aggiornati = list(id_gia_valutati | {e.get("_id") for e in eventi_nuovi})

        llm_label = (
            f"OpenAI ({OPENAI_MODEL})" if LLM_PROVIDER == "openai" else f"Ollama ({OLLAMA_MODEL})"
        )

        documento = {
            "data": oggi_iso,
            "aggiornato_il": datetime.now(LOCAL_TZ),
            "riferimento": riferimento,
            "numero_eventi_totali": len(events),
            "numero_critici": len(eventi_critici),
            "eventi_critici": eventi_critici,
            "id_eventi_valutati": id_valutati_aggiornati,
            "LLM": llm_label,
        }

        await critical_collection.update_one(
            {"data": oggi_iso},
            {"$set": documento},
            upsert=True,
        )
        return documento


async def get_critical_cache() -> Optional[dict]:
    """Restituisce la cache degli eventi critici odierni, se già calcolata."""
    oggi = datetime.now(LOCAL_TZ).date()
    doc = await critical_collection.find_one({"data": oggi.isoformat()})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _compute_generation_params(prompt: str, n_events: int) -> tuple[int, int]:
    #calcolo dinamico dei token
    output_tokens = max(LLM_MIN_OUTPUT_TOKENS, n_events * LLM_TOKENS_PER_EVENT + 1024)
    prompt_tokens_estimate = max(LLM_MIN_NUM_CTX, len(prompt) // 4)
    num_ctx = int((prompt_tokens_estimate + output_tokens) * 1.3)
    ctx_power2 = 1
    while ctx_power2 < num_ctx:
        ctx_power2 *= 2
    num_ctx = min(ctx_power2, 65536)
    return output_tokens, num_ctx


async def _call_ollama(prompt: str, n_events: int) -> str:
    output_tokens, num_ctx = _compute_generation_params(prompt, n_events)

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
    except httpx.ConnectError as e:
        raise RuntimeError(f"Impossibile connettersi a Ollama ({OLLAMA_BASE_URL}).") from e
    except httpx.TimeoutException as e:
        raise RuntimeError(f"Timeout chiamando il modello Ollama '{OLLAMA_MODEL}'.") from e
    except httpx.HTTPStatusError as e:
        # Include qui il caso più comune al primo avvio: modello non ancora scaricato.
        raise RuntimeError(
            f"Ollama ha risposto con errore ({e.response.status_code}) per il modello "
            f"'{OLLAMA_MODEL}': potrebbe non essere ancora stato scaricato."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Errore Ollama imprevisto: {type(e).__name__}: {e}") from e


async def _call_openai(prompt: str, n_events: int) -> str:
    if openai_client is None:
        return "[Errore OpenAI] OPENAI_API_KEY non configurata."

    output_tokens, _ = _compute_generation_params(prompt, n_events)

    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_TEMPERATURE,
            max_tokens=output_tokens,
        )
        content = response.choices[0].message.content
        return content if content else "[OpenAI: risposta vuota]"
    except APIConnectionError as e:
        raise RuntimeError("Impossibile connettersi all'API OpenAI.") from e
    except APITimeoutError as e:
        raise RuntimeError(f"Timeout chiamando il modello OpenAI '{OPENAI_MODEL}'.") from e
    except APIStatusError as e:
        raise RuntimeError(f"OpenAI ha risposto con errore {e.status_code}: {e.message}") from e
    except Exception as e:
        raise RuntimeError(f"Errore OpenAI imprevisto: {type(e).__name__}: {e}") from e

#dispatch tra Ollama locale e OpenAI cloud, in base a LLM_PROVIDER
async def call_llm(prompt: str, n_events: int = 0) -> str:
    if LLM_PROVIDER == "openai":
        return await _call_openai(prompt, n_events)
    return await _call_ollama(prompt, n_events)


async def check_ollama_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            model_available = any(OLLAMA_MODEL in m for m in models)
            return {
                "provider": "ollama",
                "ollama_reachable": True,
                "models_available": models,
                "requested_model": OLLAMA_MODEL,
                "model_ready": model_available,
                "warning": None if model_available else f"Modello '{OLLAMA_MODEL}' non trovato.",
            }
    except Exception as e:
        return {"provider": "ollama", "ollama_reachable": False, "error": str(e)}


async def check_openai_status() -> dict:
    if openai_client is None:
        return {
            "provider": "openai",
            "openai_reachable": False,
            "error": "OPENAI_API_KEY non configurata.",
        }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            )
            r.raise_for_status()
            models = [m["id"] for m in r.json().get("data", [])]
            model_available = any(OPENAI_MODEL in m for m in models) if models else True
            return {
                "provider": "openai",
                "openai_reachable": True,
                "requested_model": OPENAI_MODEL,
                "model_ready": model_available,
                "warning": None if model_available else f"Modello '{OPENAI_MODEL}' non trovato tra quelli disponibili.",
            }
    except Exception as e:
        return {"provider": "openai", "openai_reachable": False, "error": str(e)}


async def check_llm_status() -> dict:
    if LLM_PROVIDER == "openai":
        return await check_openai_status()
    return await check_ollama_status()

#metodo per costruire il prompt per analisi sistematica degli eventi
def _build_summary_prompt(
    events: list[dict],
) -> str:
    righe = []
    for e in events:
        tags = get_nested_field(e, FIELD_TAGS, [])
        soggetto = "dipendente" if EMPLOYEE_TAG in tags else "visitatore/cliente (non dipendente)"
        ts = e.get(FIELD_TIMESTAMP)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(LOCAL_TZ)
        data_ora = ts.strftime("%d/%m/%Y %H:%M")
        camera_id   = e.get(FIELD_CAMERA)
        location    = e.get(FIELD_LOCATION)
        description = e.get(FIELD_DESCRIPTION)
        righe.append(
            f'- id:"{e.get("_id")}" | [{data_ora}] {camera_id} ({location}) | {description} | soggetto:{soggetto}'
        )
    eventi_txt = "\n".join(righe)
    n = len(events)

    nomi_categorie = [c["name"] for c in LLM_CATEGORIES]
    guida_categorie_txt = "\n".join(
        f"  {c['name']:<24}→ {c['guida']}" for c in LLM_CATEGORIES
    )

    return f"""{PROMPT_SUMMARY_INTRO}
{CONTESTO_STRUTTURA}

GUIDA ALLE CATEGORIE (usala per assegnare ogni evento):
{guida_categorie_txt}

HAI RICEVUTO ESATTAMENTE {n} EVENTI, ognuno con un "id" univoco:
{eventi_txt}

COMPITO: per OGNI id qui sopra, assegna ESATTAMENTE UNA categoria tra: {", ".join(nomi_categorie)}.
Scrivi anche un breve motivo (in {LLM_RESPONSE_LANGUAGE}) per la classificazione.

REGOLE DI RISPOSTA (OBBLIGATORIE):
 - Rispondi SOLO con un array JSON valido, nessun testo prima o dopo, nessun commento, nessun Markdown.
 - L'array deve contenere ESATTAMENTE {n} elementi, uno per ciascun id ricevuto, ognuno una sola volta.
 - Ogni elemento deve avere ESATTAMENTE questi campi: "id" (uguale a uno di quelli forniti sopra), "categoria" (una delle categorie elencate, testo identico), "motivo" (spiegazione breve).
 - Non inventare id che non sono nella lista fornita.

JSON:"""


def _render_summary_sections(events: list[dict], classificazione: list[dict]) -> str:
    """
    Assembla il testo finale a sezioni (### Categoria) a partire dagli eventi
    originali e dalla classificazione id->categoria restituita dall'LLM.
    L'assemblaggio è fatto qui, in modo deterministico: ogni evento viene
    inserito al massimo una volta, indipendentemente da eventuali id
    duplicati o ripetuti nella risposta del modello.
    """
    eventi_by_id = {str(e.get("_id")): e for e in events}
    nomi_categorie = [c["name"] for c in LLM_CATEGORIES]

    # id -> (categoria, motivo); la prima assegnazione valida per ogni id vince,
    # eventuali duplicati restituiti dal modello vengono scartati qui.
    assegnazione: dict[str, tuple[str, str]] = {}
    for item in classificazione:
        eid = str(item.get("id", ""))
        categoria = item.get("categoria")
        if eid in eventi_by_id and eid not in assegnazione and categoria in nomi_categorie:
            assegnazione[eid] = (categoria, item.get("motivo", ""))

    def _riga(e: dict, motivo: str) -> str:
        ts = e.get(FIELD_TIMESTAMP)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(LOCAL_TZ)
        data_ora = ts.strftime("%d/%m/%Y %H:%M")
        riga = f"- [{data_ora}] {e.get(FIELD_CAMERA)} ({e.get(FIELD_LOCATION)}) | {e.get(FIELD_DESCRIPTION)}"
        if motivo:
            riga += f" | Motivo: {motivo}"
        return riga

    sezioni: dict[str, list[str]] = {nome: [] for nome in nomi_categorie}
    non_classificati = []

    for e in events:
        eid = str(e.get("_id"))
        if eid in assegnazione:
            categoria, motivo = assegnazione[eid]
            sezioni[categoria].append(_riga(e, motivo))
        else:
            # Il modello non ha classificato questo evento (o l'ha assegnato
            # a una categoria inesistente): lo segnaliamo comunque, non lo
            # perdiamo silenziosamente.
            non_classificati.append(_riga(e, ""))

    blocchi = []
    for nome in nomi_categorie:
        if sezioni[nome]:
            blocchi.append(f"### {nome}\n" + "\n".join(sezioni[nome]))

    if non_classificati:
        blocchi.append("### Non classificato dal modello\n" + "\n".join(non_classificati))

    return "\n\n".join(blocchi) if blocchi else "Nessun evento da segnalare."

#metodo per costruire il prompt per una richiesta personalizata
def _build_custom_prompt(events: list[dict], custom_prompt: str) -> str:
    righe_lista = []
    for e in events:
        tags = get_nested_field(e, FIELD_TAGS, [])
        soggetto = "dipendente" if EMPLOYEE_TAG in tags else "visitatore/cliente (non dipendente)"
        ts = e.get(FIELD_TIMESTAMP)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(LOCAL_TZ)
        ora = ts.strftime("%H:%M")
        camera_id = e.get(FIELD_CAMERA)
        location = e.get(FIELD_LOCATION)
        event_type_ = e.get(FIELD_TYPE)
        description = e.get(FIELD_DESCRIPTION)
        righe_lista.append(
            f"- [{ora}] Camera: {camera_id} ({location}) | Tipo: {event_type_} | "
            f"Soggetto: {soggetto} | Descrizione: {description}"
        )
    righe_eventi = "\n".join(righe_lista)

    return (
        f"{PROMPT_CUSTOM_INTRO}\n\n"
        f"{CONTESTO_STRUTTURA}\n\n"
        f"ISTRUZIONI ADDIZIONALI:\n"
        f"- Se la richiesta implica un conteggio (es. 'quanti...'), analizza i testi, conta gli elementi corrispondenti e fornisci il risultato numerico.\n"
        f"- Rispondi in {LLM_RESPONSE_LANGUAGE} e non ripetere l'intera lista degli eventi nella risposta.\n\n"
        f"RICHIESTA OPERATORE: {custom_prompt}\n\n"
        f"LISTA DEGLI EVENTI DA ANALIZZARE ({len(events)} totali):\n{righe_eventi}\n\n"
    )

#dispatcher per i due tipi di richieste all'LLM: sintesi "standard" a sezioni
#(classificazione JSON + rendering deterministico in Python, per evitare
#eventi duplicati o omessi) oppure risposta libera a un prompt personalizzato.
async def genera_sintesi(
    events: list[dict],
    custom_prompt: Optional[str] = None,
) -> str:
    if custom_prompt and custom_prompt.strip():
        prompt = _build_custom_prompt(events, custom_prompt.strip())
        return await call_llm(prompt, n_events=len(events))

    prompt = _build_summary_prompt(events)
    raw = await call_llm(prompt, n_events=len(events))
    classificazione = _parse_json_array(raw)
    return _render_summary_sections(events, classificazione)



@app.get("/", tags=["Sistema"])
def home():
    return {
        "message": f"Sistema di archiviazione eventi attivo (LLM: {LLM_PROVIDER})",
        "version": "2.0.0",
        "llm_provider": LLM_PROVIDER,
        "ollama_url": OLLAMA_BASE_URL,
        "ollama_model": OLLAMA_MODEL,
        "openai_model": OPENAI_MODEL if LLM_PROVIDER == "openai" else None,
        "mongo_db": MONGO_DB_NAME,
        "mongo_collection": MONGO_COLLECTION_NAME,
        "config_path": CONFIG_PATH,
        "docs": "/docs",
    }

@app.get("/llm/status", tags=["Sistema"])
async def llm_status():
    return await check_llm_status()


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


# ENDPOINT — CONTATORI GIORNALIERI (per le box in alto della dashboard)
@app.get("/stats/daily_counts", tags=["Statistiche"])
async def daily_counts():
    """Numero di eventi di oggi (dalla mezzanotte a ora:minuto correnti)
    confrontato con lo stesso intervallo del giorno precedente."""
    return await get_daily_counts()


# ENDPOINT — EVENTI CRITICI (individuati dall'LLM_PROVIDER configurato)
@app.get("/stats/critical_events", tags=["Statistiche"])
async def critical_events():
    """
    Restituisce, dalla cache calcolata in background dal worker (o dall'ultimo
    ricalcolo manuale), il numero e la lista degli eventi critici individuati
    dall'LLM per la giornata odierna (dalla mezzanotte a ora).
    """
    oggi = datetime.now(LOCAL_TZ).date()
    doc = await get_critical_cache()
    if doc is None:
        return {
            "data": oggi.isoformat(),
            "calcolato": False,
            "numero_critici": 0,
            "numero_eventi_totali": 0,
            "eventi_critici": [],
        }

    doc["calcolato"] = True
    doc.pop("id_eventi_valutati", None)
    return doc


@app.post("/stats/critical_events/recompute", tags=["Statistiche"])
async def critical_events_recompute():
    return await recompute_critical_cache()


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
    sintesi = await genera_sintesi(events, req.custom_prompt)

    llm_backend_label = (
        f"OpenAI ({OPENAI_MODEL})"
        if LLM_PROVIDER == "openai"
        else f"Ollama ({OLLAMA_MODEL})"
    )

    event_types = sorted({
        e.get(FIELD_TYPE)
        for e in events
        if e.get(FIELD_TYPE)
    })

    if req.custom_prompt and req.custom_prompt.strip():
        await salva_risposta_prompt(
            req,
            end,
            len(events),
            sintesi,
            llm_backend_label,
            event_types,
        )
        return {"summary": sintesi}

    await salva_risposta_analisi(
        req,
        end,
        len(events),
        sintesi,
        llm_backend_label,
        event_types,
    )
    return {
        "period": {"start": req.start.isoformat(), "end": end.isoformat()},
        "end_auto": req.end is None,
        "camera_ids": req.camera_ids,
        "llm_backend": llm_backend_label,
        "total_events": len(events),
        "summary": sintesi,
    }




# ENDPOINT — SINTESI ASINCRONA
@app.post("/summaries/richiedi", tags=["Sintesi LLM"])
async def richiedi_summary(req: SummaryRequest, background_tasks: BackgroundTasks):
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

    is_prompt = bool(req.custom_prompt and req.custom_prompt.strip())
    target_collection = prompt_collection if is_prompt else analysis_collection

    event_types = sorted({
        e.get(FIELD_TYPE)
        for e in events
        if e.get(FIELD_TYPE)
    })

    start_local = req.start if req.start.tzinfo else req.start.replace(tzinfo=LOCAL_TZ)
    end_local = end if end.tzinfo else end.replace(tzinfo=LOCAL_TZ)

    documento = {
        "request_date": datetime.now(LOCAL_TZ),
        "camera_ids": req.camera_ids,
        "numero_eventi": len(events),
        "tipi_eventi": event_types,
        "data_inizio": start_local.date().isoformat(),
        "data_fine": end_local.date().isoformat(),
        "ora_inizio": start_local.time().isoformat(timespec="seconds"),
        "ora_fine": end_local.time().isoformat(timespec="seconds"),
        "LLM": None,
        "risposta": None,
        "stato": "in_corso",
    }
    if is_prompt:
        documento["prompt"] = req.custom_prompt

    result = await target_collection.insert_one(documento)
    scheda_id = str(result.inserted_id)

    background_tasks.add_task(
        _esegui_analisi_in_background, scheda_id, is_prompt, events, req, end,
    )

    return {"id": scheda_id, "tipo": "prompt" if is_prompt else "standard", "stato": "in_corso"}


async def _esegui_analisi_in_background(scheda_id: str, is_prompt: bool, events, req, end):
    target_collection = prompt_collection if is_prompt else analysis_collection
    try:
        sintesi = await genera_sintesi(events, req.custom_prompt)
        llm_backend_label = (
            f"OpenAI ({OPENAI_MODEL})" if LLM_PROVIDER == "openai"
            else f"Ollama ({OLLAMA_MODEL})"
        )
        await target_collection.update_one(
            {"_id": ObjectId(scheda_id)},
            {"$set": {
                "risposta": sintesi,
                "LLM": llm_backend_label,
                "stato": "completato",
                "completato_il": datetime.now(LOCAL_TZ),
            }}
        )
    except Exception as ex:
        await target_collection.update_one(
            {"_id": ObjectId(scheda_id)},
            {"$set": {"stato": "errore", "errore": str(ex)}}
        )


@app.get("/summaries/{scheda_id}", tags=["Sintesi LLM"])
async def get_summary_scheda(scheda_id: str, tipo: str = "standard"):
    try:
        oid = ObjectId(scheda_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    target_collection = prompt_collection if tipo == "prompt" else analysis_collection
    doc = await target_collection.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Scheda non trovata")

    doc["_id"] = str(doc["_id"])
    doc.setdefault("stato", "completato" if doc.get("risposta") else "in_corso")
    return doc


@app.post("/summaries/{scheda_id}/nascondi", tags=["Sintesi LLM"])
async def nascondi_summary_scheda(scheda_id: str, tipo: str = "standard"):
    try:
        oid = ObjectId(scheda_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    target_collection = prompt_collection if tipo == "prompt" else analysis_collection
    result = await target_collection.update_one({"_id": oid}, {"$set": {"nascosta": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    return {"status": "nascosta", "id": scheda_id}


#metodi per salvare le risposte in mongodb
async def salva_risposta_analisi(
    req,
    end,
    total_events,
    risposta,
    llm_backend,
    event_types
):
    start_local = req.start if req.start.tzinfo else req.start.replace(tzinfo=LOCAL_TZ)
    end_local = end if end.tzinfo else end.replace(tzinfo=LOCAL_TZ)

    now = datetime.now(LOCAL_TZ)
    documento = {
        "request_date": now,
        "camera_ids": req.camera_ids,
        "numero_eventi": total_events,
        "tipi_eventi": event_types,
        "data_inizio": start_local.date().isoformat(),
        "data_fine": end_local.date().isoformat(),
        "ora_inizio": start_local.time().isoformat(timespec="seconds"),
        "ora_fine": end_local.time().isoformat(timespec="seconds"),
        "LLM": llm_backend,
        "risposta": risposta,
        "stato": "completato",
        "completato_il": now,
    }

    await analysis_collection.insert_one(documento)


async def salva_risposta_prompt(
    req,
    end,
    total_events,
    risposta,
    llm_backend,
    event_types
):
    start_local = req.start if req.start.tzinfo else req.start.replace(tzinfo=LOCAL_TZ)
    end_local = end if end.tzinfo else end.replace(tzinfo=LOCAL_TZ)

    now = datetime.now(LOCAL_TZ)
    documento = {
        "request_date": now,
        "camera_ids": req.camera_ids,
        "numero_eventi": total_events,
        "tipi_eventi": event_types,
        "data_inizio": start_local.date().isoformat(),
        "data_fine": end_local.date().isoformat(),
        "ora_inizio": start_local.time().isoformat(timespec="seconds"),
        "ora_fine": end_local.time().isoformat(timespec="seconds"),
        "prompt": req.custom_prompt,
        "LLM": llm_backend,
        "risposta": risposta,
        "stato": "completato",
        "completato_il": now,
    }
    await prompt_collection.insert_one(documento)

#recupero risposte passate
@app.get("/analysis_history")
async def analysis_history():
    risultati = []
    cursor = analysis_collection.find().sort("request_date", -1)

    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        doc.setdefault("stato", "completato" if doc.get("risposta") else "in_corso")
        if doc["stato"] == "completato":
            doc.setdefault("completato_il", doc.get("request_date"))
        doc.setdefault("nascosta", False)
        risultati.append(doc)

    return risultati

@app.get("/prompt_history")
async def prompt_history():
    risultati = []
    cursor = prompt_collection.find().sort("request_date", -1)

    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        doc.setdefault("stato", "completato" if doc.get("risposta") else "in_corso")
        if doc["stato"] == "completato":
            doc.setdefault("completato_il", doc.get("request_date"))
        doc.setdefault("nascosta", False)
        risultati.append(doc)

    return risultati

#metodi per cancellare una risposta dalla relativa collezione
@app.delete("/analysis_history/{analysis_id}")
async def delete_analysis(analysis_id: str):

    try:
        oid = ObjectId(analysis_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    result = await analysis_collection.delete_one({"_id": oid})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Analisi non trovata")

    return {"status": "deleted"}

@app.delete("/prompt_history/{prompt_id}")
async def delete_prompt(prompt_id: str):

    try:
        oid = ObjectId(prompt_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    result = await prompt_collection.delete_one({"_id": oid})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Prompt non trovato")

    return {"status": "deleted"}


# ENDPOINT — AUTOMAZIONE
@app.post("/automation_jobs", status_code=201, tags=["Automazione"])
async def create_automation_job(req: AutomationJobCreate):
    now = datetime.now(LOCAL_TZ)
    documento = {
        "titolo": req.titolo.strip(),
        "camera_ids": req.camera_ids,
        "tipi_evento": req.tipi_evento,
        "interval_minutes": req.interval_minutes,
        "custom_prompt": req.custom_prompt.strip() if req.custom_prompt else None,
        "enabled": True,
        "analisi": [],
        "ultima_esecuzione": None,
        "ultima_modifica": now,
        "creato_il": now,
    }
    result = await scheduler_collection.insert_one(documento)
    return {"status": "created", "id": str(result.inserted_id)}

@app.get("/automation_jobs", tags=["Automazione"])
async def list_automation_jobs():
    risultati = []
    cursor = scheduler_collection.find().sort("ultima_modifica", -1)
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        risultati.append(doc)
    return risultati


@app.get("/automation_jobs/{job_id}", tags=["Automazione"])
async def get_automation_job(job_id: str):
    try:
        oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    doc = await scheduler_collection.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Job non trovato")
    doc["_id"] = str(doc["_id"])
    return doc


@app.delete("/automation_jobs/{job_id}", tags=["Automazione"])
async def delete_automation_job(job_id: str):
    try:
        oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")

    result = await scheduler_collection.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Job non trovato")
    return {"status": "deleted"}

@app.post("/automation_jobs/{job_id}/pause", tags=["Automazione"])
async def pause_automation_job(job_id: str):
    try:
        oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID job non valido")

    result = await scheduler_collection.update_one(
        {"_id": oid},
        {"$set": {"enabled": False, "ultima_modifica": datetime.now(LOCAL_TZ)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Job non trovato")
    return {"status": "paused"}

@app.post("/automation_jobs/{job_id}/resume", tags=["Automazione"])
async def resume_automation_job(job_id: str):
    try:
        oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID job non valido")

    result = await scheduler_collection.update_one(
        {"_id": oid},
        {"$set": {"enabled": True, "ultima_modifica": datetime.now(LOCAL_TZ)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Job non trovato")
    return {"status": "resumed"}


@app.get("/analysis_status", tags=["Sintesi LLM"])
async def get_analysis_status():
    cursor = analysis_collection.find().sort("request_date", -1).limit(10)
    return [doc_to_event(doc) async for doc in cursor]