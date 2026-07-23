import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId
from backend import (
    scheduler_collection, collection,
    call_llm, build_llm_prompt, _build_time_query,
    FIELD_CAMERA, FIELD_TYPE, FIELD_TIMESTAMP,
    LLM_PROVIDER, OPENAI_MODEL, OLLAMA_MODEL,
    LOCAL_TZ
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutomationWorker")

risposte_auto_collection = scheduler_collection

scheduler = AsyncIOScheduler()
tracked_jobs = {}

def _parse_ts(ts):
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if isinstance(ts, datetime):
        return ts
    return None

def ObjectIdSafe(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except Exception:
        return value

#costruisce la query di select
def _build_incremental_query(config: dict) -> dict:

    camera_ids = config.get("camera_ids", []) or []
    tipi_evento = config.get("tipi_evento", []) or []
    start = config.get("start")
    end = config.get("end")

    #lista di eventi analizzati in iterazioni precedenti
    analizzati = config.get("id_eventi_analizzati", []) or []

    #lista di eventi esclusi manualmente dall'utente (restano esclusi finché
    # non vengono reinclusi dall'utente rimuovendoli da questo campo)
    esclusi = config.get("id_eventi_esclusi", []) or []

    black_list = [ObjectIdSafe(i) for i in (analizzati + esclusi) if i]

    filtri = []
    if start and end:
        filtri.append(_build_time_query(start, end))
    if camera_ids:
        filtri.append({FIELD_CAMERA: {"$in": camera_ids}})
    if tipi_evento:
        filtri.append({FIELD_TYPE: {"$in": tipi_evento}})
    if black_list:
        filtri.append({"_id": {"$nin": black_list}})

    return {"$and": filtri} if filtri else {}

# esegue la query di select ogni N minuti e chiama LLM per far aggiornare la risposta precedente
async def execute_automatic_analysis(job_id: str):

    logger.info(f"[Job {job_id}] Avvio ciclo di analisi automatica...")

    # recupera la configurazione e lo stato corrente dal documento unico
    config = await risposte_auto_collection.find_one({"_id": ObjectIdSafe(job_id)})
    if not config or not config.get("enabled", True):
        logger.warning(f"[Job {job_id}] Configurazione non trovata o disabilitata.")
        return

    limit = config.get("max_events", config.get("default_events", 100))
    custom_prompt = config.get("custom_prompt", "").strip()
    risposta_precedente = config.get("risposta", "").strip()
    analizzati_storico = config.get("id_eventi_analizzati", []) or []

    query = _build_incremental_query(config)
    cursor = collection.find(query).sort(FIELD_TIMESTAMP, 1)
    if limit > 0:
        cursor = cursor.limit(limit)

    new_events = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        new_events.append(doc)

    if not new_events:
        logger.info(f"[Job {job_id}] Nessun nuovo evento da analizzare in questo ciclo. Query: {query}")
        await risposte_auto_collection.update_one(
            {"_id": ObjectIdSafe(job_id)},
            {"$set": {
                "ultimo_esito": "Nessun nuovo evento trovato in questo ciclo.",
                "ultima_esecuzione": datetime.now(LOCAL_TZ),
            }}
        )
        return

    logger.info(f"[Job {job_id}] Trovati {len(new_events)} nuovi eventi. Generazione prompt incrementale...")

    prompt_base = build_llm_prompt(new_events, custom_prompt)

    if risposta_precedente:
        if custom_prompt:
            #caso prompt personalizzato si aggiorna la risposta precedente
            prompt_str = (
                f"Aggiorna la risposta data in precedenza su questo sistema.\n"
                f"RISPOSTA PRECEDENTE:\n{risposta_precedente}\n\n"
                f"Ora analizza e integra nella risposta i seguenti NUOVI eventi arrivati nel frattempo:\n"
                f"{prompt_base}\n"
                f"Fornisci come output la risposta complessiva aggiornata e armonizzata."
            )
        else:
            #caso sintesi standard si aggiungono i nuovi eventi alle classificazioni
            prompt_str = (
                f"{prompt_base}\n\n"
                f"NOTA IMPORTANTE PER L'AGGIORNAMENTO:\n"
                f"Il sistema ha già prodotto questa classificazione parziale in precedenza:\n"
                f"{risposta_precedente}\n"
                f"NON MODIFICARE come sono stati classificati gli eventi passati sopra citati, "
                f"ma aggiungi solo i nuovi eventi selezionati alle rispettive classificazioni/sezioni "
                f"mantenendo lo stesso identico formato."
            )
    else:
        # primo iter come per risposta standard
        prompt_str = prompt_base

    # chiama LLM
    try:
        nuova_risposta = await call_llm(prompt_str, n_events=len(new_events))
    except Exception as ex:
        logger.error(f"[Job {job_id}] Errore durante la chiamata all'LLM: {ex}")
        await risposte_auto_collection.update_one(
            {"_id": ObjectIdSafe(job_id)},
            {"$set": {
                "ultimo_esito": f"Errore LLM: {ex}",
                "ultima_esecuzione": datetime.now(LOCAL_TZ),
            }}
        )
        return

    llm_backend_label = (
        f"OpenAI ({OPENAI_MODEL})" if LLM_PROVIDER == "openai"
        else f"Ollama ({OLLAMA_MODEL})"
    )

    nuovi_id_analizzati = [e["_id"] for e in new_events]
    totale_analizzati = list(set(analizzati_storico + nuovi_id_analizzati))

    await risposte_auto_collection.update_one(
        {"_id": ObjectIdSafe(job_id)},
        {
            "$set": {
                "modello_LLM": llm_backend_label,
                "id_eventi_analizzati": totale_analizzati,
                "risposta": nuova_risposta,
                "ultima_esecuzione": datetime.now(LOCAL_TZ),
                "ultimo_esito": f"OK: {len(new_events)} eventi analizzati.",
            }
        }
    )
    logger.info(f"[Job {job_id}] Risposta aggiornata con successo nel DB.")


async def sync_scheduler_with_db():
    global tracked_jobs
    try:
        cursor = risposte_auto_collection.find({})
        db_configs = {}
        async for doc in cursor:
            db_configs[str(doc["_id"])] = doc
    except Exception as e:
        logger.error(f"Errore di lettura MongoDB nello sync: {e}")
        return

    # Rimozione Job spenti o eliminati
    for job_id in list(tracked_jobs.keys()):
        if job_id not in db_configs or not db_configs[job_id].get("enabled", False):
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            tracked_jobs.pop(job_id, None)
            logger.info(f"Job automatico {job_id} rimosso dallo scheduler.")

    # Aggiunta o Aggiornamento frequenza/filtri
    for job_id, config in db_configs.items():
        if not config.get("enabled", False):
            continue


        interval_minutes = int(config.get("interval_minutes", 30))
        ultima_modifica = config.get("ultima_modifica", "")

        if job_id not in tracked_jobs or tracked_jobs[job_id] != ultima_modifica:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)

            if interval_minutes > 0:
                scheduler.add_job(
                    execute_automatic_analysis,
                    "interval",
                    minutes=interval_minutes,
                    id=job_id,
                    args=[job_id],
                    next_run_time=datetime.now(LOCAL_TZ),
                )
                tracked_jobs[job_id] = ultima_modifica
                logger.info(f"Schedulato Job {job_id}: Esecuzione ogni {interval_minutes} minuti.")

async def main():
    logger.info("Avvio del Worker dei job automatici...")
    scheduler.start()
    scheduler.add_job(sync_scheduler_with_db, "interval", seconds=10, id="worker_db_sync_job")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Arresto del Worker...")
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())

#TODO FARE CHE JOB PERIODICO NON HA INIZIO E FINE MA SE GLI DICI 20 MINUTI JOB GIORNALIERO POI INCREMENTA QUINDI
#VA DA 00:00 A 23:59 POI PROSSIMO ITER DA 00:20 A 00:20 DEL GIORNO DOPO ETC