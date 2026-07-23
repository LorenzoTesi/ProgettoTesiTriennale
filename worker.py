import os
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from backend     import (
    call_llm,
    build_llm_prompt,
    _build_last_Nminutes_query,
    doc_to_event,
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

mongo_client = AsyncIOMotorClient(MONGO_DETAILS)
database = mongo_client[MONGO_DB_NAME]
events_collection = database.get_collection(MONGO_COLLECTION_NAME)
scheduler_collection = database.get_collection(MONGO_SCHEDULER_COLLECTION)


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
        prompt = build_llm_prompt(events, custom_prompt)
        sintesi = await call_llm(prompt, n_events=len(events))

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

    while True:
        try:
            await sync_jobs(scheduler)
        except Exception as e:
            print(f"[Worker Error] Sincronizzazione fallita: {e}")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
#TODO FARE CHE JOB PERIODICO NON HA INIZIO E FINE MA SE GLI DICI 20 MINUTI JOB GIORNALIERO POI INCREMENTA QUINDI
#VA DA 00:00 A 23:59 POI PROSSIMO ITER DA 00:20 A 00:20 DEL GIORNO DOPO ETC