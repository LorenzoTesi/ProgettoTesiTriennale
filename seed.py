# seed.py — Seeding automatico dell'ultimo mese di eventi simulati
#
# Sostituisce i 30 eventi scritti a mano in mongo-seed/init.js: all'avvio
# dello stack calcola dinamicamente l'intervallo [ora - SEED_DAYS giorni, ora]
# e lo riempie con eventi generati dalla stessa logica di Simulator.py,
# inviandoli al backend via POST /events (stessa cosa che fa
# `python Simulator.py --stream`, ma con date calcolate automaticamente).
#
# Passando dal backend invece di scrivere direttamente su Mongo, gli eventi
# generati vengono normalizzati esattamente come un evento reale (timezone,
# validazione event_type, indici) e non c'è nessuna logica duplicata.
#
# Idempotente: se la collezione contiene già eventi non fa nulla, così
# `docker-compose up` ripetuti non accumulano mesi su mesi di dati.
# Per rigenerare comunque: FORCE_RESEED=true docker-compose up seeder
#
# Variabili d'ambiente:
#   SEED_BACKEND_URL     default http://api:8000
#   SEED_DAYS            default 30
#   SEED_FREQ_PER_HOUR   default 1
#   SEED_INTERVAL        secondi tra un invio e l'altro, default 0 (nessuna attesa)
#   FORCE_RESEED         "true" per forzare il reseeding anche se ci sono già eventi

import os
import sys
import time
from datetime import datetime, timedelta

from pymongo import MongoClient

from Simulator import generate_events, send_event, check_backend

MONGO_DETAILS = os.getenv("MONGO_DETAILS", "mongodb://mongodb:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "sistema_eventi")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "eventi_osservati")

BACKEND_URL = os.getenv("SEED_BACKEND_URL", "http://api:8000")
SEED_DAYS = int(os.getenv("SEED_DAYS", "30"))
SEED_FREQ_PER_HOUR = float(os.getenv("SEED_FREQ_PER_HOUR", "2"))
SEED_INTERVAL = float(os.getenv("SEED_INTERVAL", "0"))
FORCE_RESEED = os.getenv("FORCE_RESEED", "false").strip().lower() == "true"


def wait_for_backend(url: str, retries: int = 30, delay: float = 2.0) -> bool:
    for attempt in range(1, retries + 1):
        if check_backend(url):
            return True
        print(f" Backend non pronto (tentativo {attempt}/{retries}), riprovo tra {delay}s...")
        time.sleep(delay)
    return False


def already_seeded() -> bool:
    client = MongoClient(MONGO_DETAILS, serverSelectionTimeoutMS=5000)
    try:
        count = client[MONGO_DB_NAME][MONGO_COLLECTION_NAME].count_documents({})
        return count > 0
    finally:
        client.close()


def main():
    print("\n Seeder eventi storici")

    if not FORCE_RESEED and already_seeded():
        print(" La collezione contiene già eventi: seeding saltato.")
        print("   (imposta FORCE_RESEED=true per rigenerare comunque)")
        return

    if not wait_for_backend(BACKEND_URL):
        print(f" Backend non raggiungibile su {BACKEND_URL}, seeding annullato.")
        sys.exit(1)

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=SEED_DAYS)

    print(f"   Periodo     : {start_dt}  →  {end_dt}  ({SEED_DAYS} giorni)")
    print(f"   Frequenza   : ~{SEED_FREQ_PER_HOUR} eventi/ora")

    events = generate_events(start_dt, end_dt, SEED_FREQ_PER_HOUR)

    if not events:
        print(" Nessun evento generato: controlla SEED_DAYS/SEED_FREQ_PER_HOUR.")
        return

    print(f"\n Generati {len(events)} eventi, invio a {BACKEND_URL} ...\n")

    ok = fail = 0
    for ev in events:
        if send_event(ev, BACKEND_URL):
            ok += 1
        else:
            fail += 1
        if SEED_INTERVAL > 0:
            time.sleep(SEED_INTERVAL)

    print(f"\n{'─' * 50}")
    print(f" Seeding completato:  {ok} inviati   {fail} errori")


if __name__ == "__main__":
    main()