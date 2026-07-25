import os
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from backend     import (
    genera_sintesi,
    _build_last_Nminutes_query,
    doc_to_event,
    recompute_critical_cache,
    FIELD_CAMERA,
    FIELD_TYPE,
    LLM_PROVIDER,
    OPENAI_MODEL,
    OLLAMA_MODEL,
)

load_dotenv()

MONGO_DETAILS = os.getenv("MONGO_DETAILS", "mongodb://mongodb:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sistema_eventi")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "eventi_osservati")
MONGO_SCHEDULER_COLLECTION = os.getenv("MONGO_SCHEDULER_COLLECTION", "risposte_job_periodico")
LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Rome"))

# Ogni quanto (secondi) il worker controlla se sono arrivati nuovi eventi nel DB.
EVENTS_WATCH_POLL_SECONDS = int(os.getenv("EVENTS_WATCH_POLL_SECONDS", "10"))
# Intervallo minimo (secondi) tra due ricalcoli via LLM degli eventi critici:
# evita di interrogare l'LLM ad ogni singolo evento inserito (debounce).
CRITICAL_RECOMPUTE_MIN_INTERVAL = int(os.getenv("CRITICAL_RECOMPUTE_MIN_INTERVAL_SECONDS", "30"))

mongo_client = AsyncIOMotorClient(MONGO_DETAILS, tz_aware=True)
database = mongo_client[MONGO_DB_NAME]
events_collection = database.get_collection(MONGO_COLLECTION_NAME)
scheduler_collection = database.get_collection(MONGO_SCHEDULER_COLLECTION)

# Stato del watcher: numero di eventi al momento dell'ultimo ricalcolo riuscito
# (NON l'ultimo poll: il conteggio va confrontato con l'ultimo ricalcolo, altrimenti
# un burst di eventi che arriva tra due poll consecutivi mentre il debounce è attivo
# viene "assorbito" nel tracking e non genera mai un ricalcolo successivo).
_last_recomputed_event_count = None
_last_critical_recompute = 0.0
# Tiene traccia se per la data odierna esiste già una cache calcolata CON SUCCESSO.
# Se il primo tentativo fallisce (es. modello Ollama ancora in download al primo
# avvio) e nel frattempo il seeding finisce e smette di generare nuovi eventi,
# senza questo flag il ricalcolo non verrebbe più ritentato.
_critical_cache_ok_date = None


async def execute_job(job_id: str):
    from bson import ObjectId

    doc = await scheduler_collection.find_one({"_id": ObjectId(job_id)})
    if not doc or not doc.get("enabled", True):
        return

    now = datetime.now(timezone.utc)
    interval_minutes = doc.get("interval_minutes", 30)

    time_query = _build_last_Nminutes_query(now, interval_minutes)

    conditions = [time_query]

    camera_ids = doc.get("camera_ids", [])
    if camera_ids:
        conditions.append({FIELD_CAMERA: {"$in": camera_ids}})

    tipi_evento = doc.get("tipi_evento", [])
    if tipi_evento:
        conditions.append({FIELD_TYPE: {"$in": tipi_evento}})

    query = {"$and": conditions} if len(conditions) > 1 else conditions[0]

    cursor = events_collection.find(query).sort("timestamp", 1)
    events = [doc_to_event(e) async for e in cursor]

    if not events:
        sintesi = f"Nessun evento rilevato negli ultimi {interval_minutes} minuti."
    else:
        custom_prompt = doc.get("custom_prompt")
        try:
            sintesi = await genera_sintesi(events, custom_prompt)
        except Exception as e:
            print(f"[Worker Error] Job {job_id}: generazione sintesi fallita: {e}")
            sintesi = f"[Errore] Generazione sintesi non riuscita: {e}"

    llm_label = (
        f"OpenAI ({OPENAI_MODEL})"
        if LLM_PROVIDER == "openai"
        else f"Ollama ({OLLAMA_MODEL})"
    )

    esito = {
        "timestamp_esecuzione": now.astimezone(LOCAL_TZ),
        "numero_eventi": len(events),
        "risposta": sintesi,
        "modello_LLM": llm_label,
    }

    await scheduler_collection.update_one(
        {"_id": ObjectId(job_id)},
        {
            "$push": {"analisi": {"$each": [esito], "$slice": -50}},
            "$set": {"ultima_esecuzione": now.astimezone(LOCAL_TZ)},
        },
    )


async def watch_new_events():
    """
    Job in background eseguito ogni EVENTS_WATCH_POLL_SECONDS secondi.
    Rileva se sono stati inseriti nuovi eventi nel DB (confrontando il conteggio
    totale) e, in tal caso, richiede all'LLM_PROVIDER un ricalcolo della lista/
    numero di eventi critici di 'oggi'. Il ricalcolo è "debounced": se arrivano
    molti eventi ravvicinati, l'LLM viene interrogato al massimo una volta ogni
    CRITICAL_RECOMPUTE_MIN_INTERVAL secondi, non ad ogni singolo evento.
    """
    global _last_recomputed_event_count, _last_critical_recompute, _critical_cache_ok_date

    oggi = datetime.now(LOCAL_TZ).date()
    if _critical_cache_ok_date != oggi:
        # Nuovo giorno (o nessun successo ancora registrato): la cache di oggi
        # va considerata non affidabile finché non riusciamo davvero a calcolarla.
        _critical_cache_ok_date = None

    try:
        current_count = await events_collection.count_documents({})
    except Exception as e:
        print(f"[Worker Error] Conteggio eventi fallito: {e}")
        return

    is_first_run = _last_recomputed_event_count is None
    # NB: confrontiamo con il conteggio dell'ULTIMO RICALCOLO RIUSCITO, non
    # dell'ultimo poll. Se confrontassimo poll-su-poll, un burst di eventi che
    # arriva più in fretta del ciclo di debounce verrebbe "assorbito" senza
    # mai far scattare un ricalcolo (bug osservato: seeding che finisce in
    # pochi secondi, più veloce del debounce di 30s).
    events_changed = (not is_first_run) and current_count != _last_recomputed_event_count

    now_monotonic = asyncio.get_event_loop().time()
    debounce_elapsed = (now_monotonic - _last_critical_recompute) >= CRITICAL_RECOMPUTE_MIN_INTERVAL

    # Ricalcoliamo se: è la primissima esecuzione, oppure sono arrivati nuovi
    # eventi rispetto all'ultimo ricalcolo riuscito, oppure semplicemente non
    # abbiamo ANCORA una cache valida per oggi (es. il tentativo precedente ha
    # trovato 0 eventi solo perché il seeding era ancora in corso, o Ollama non
    # era pronto) — in quest'ultimo caso ritentiamo periodicamente finché non
    # otteniamo un risultato coerente con lo stato reale del DB.
    need_retry_no_success_yet = _critical_cache_ok_date != oggi

    should_recompute = is_first_run or (
        debounce_elapsed and (events_changed or need_retry_no_success_yet)
    )

    if not should_recompute:
        return

    _last_critical_recompute = now_monotonic

    async def _fai_calcolo():
        global _critical_cache_ok_date, _last_recomputed_event_count
        try:
            await recompute_critical_cache()
            _last_recomputed_event_count = current_count
            _critical_cache_ok_date = oggi
        except Exception as e:
            print(f"[Worker Error] Ricalcolo eventi critici fallito, verrà ritentato: {e}")

    asyncio.create_task(_fai_calcolo())


async def sync_jobs(scheduler: AsyncIOScheduler):
    active_jobs = set()
    cursor = scheduler_collection.find({"enabled": True})

    async for doc in cursor:
        job_id = str(doc["_id"])
        interval = doc.get("interval_minutes", 30)
        active_jobs.add(job_id)

        existing_job = scheduler.get_job(job_id)
        if not existing_job:
            scheduler.add_job(
                execute_job,
                "interval",
                minutes=interval,
                id=job_id,
                args=[job_id],
                replace_existing=True,
                # Senza next_run_time, APScheduler aspetta un intervallo pieno
                # prima della prima esecuzione: forziamo la partenza immediata.
                next_run_time=datetime.now(scheduler.timezone),
            )
        elif existing_job.trigger.interval.total_seconds() != interval * 60:
            scheduler.add_job(
                execute_job,
                "interval",
                minutes=interval,
                id=job_id,
                args=[job_id],
                replace_existing=True,
                next_run_time=datetime.now(scheduler.timezone),
            )

    for job in scheduler.get_jobs():
        if job.id not in active_jobs:
            scheduler.remove_job(job.id)


async def main():
    scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)
    scheduler.start()

    # Watcher che rileva nuovi eventi e aggiorna la cache degli eventi critici di oggi.
    scheduler.add_job(
        watch_new_events,
        "interval",
        seconds=EVENTS_WATCH_POLL_SECONDS,
        id="watch_new_events",
        next_run_time=datetime.now(scheduler.timezone),
    )

    while True:
        try:
            await sync_jobs(scheduler)
        except Exception as e:
            print(f"[Worker Error] Sincronizzazione fallita: {e}")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())