# simulator.py — Generatore di eventi di sorveglianza simulati

#verifica che il backend FastAPI sia acceso:
#    uvicorn backend:app --reload
#Personalizza il periodo e la frequenza (espressa in  un evento all'ora):
#    python Simulator.py --stream --start 2026-04-30T06:00:00 --end 2026-04-30T23:00:00 --freq 3

# Parametri
# --start         datetime inizio simulazione  (default: oggi 06:00)
# --end           datetime fine simulazione    (default: oggi 23:00)
# --freq          eventi medi per ora          (default: 3)
# --stream        invia al backend invece di salvare su file
# --interval      secondi tra un invio e l'altro in modalità stream (default: 0.2)
# --url           URL del backend              (default: http://127.0.0.1:8000)
#  --check         solo verifica connessione al backend, non genera eventi


import argparse
import random
import time
import sys
from datetime import datetime, timedelta
import httpx


# VALIDATORI ARGPARSE
def positive_float(value: str) -> int:
    try:
        fvalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' non è un numero valido")
    if fvalue <= 0:
        raise argparse.ArgumentTypeError(
            f"--freq deve essere un intero maggiore di 0 (valore ricevuto: {fvalue})"
        )
    return fvalue

# Dati con cui si compilano i campi degli eventi
CAMERAS = {
    "corridor_1": "corridoio principale",
    "corridor_2": "corridoio secondario",
    "entrance_clients": "ingresso principale per i clienti",
    "reception_hall": "sportello",
    "vault": "camera blindata",
    "exit": "uscita principale",
}

EVENT_DESCRIPTIONS = {
    "una persona cammina lentamente per la stanza": {
        "type": "movement",
        "tags": ["person", "walking"]
    },

    "una persona si dirige verso la porta": {
        "type": "movement",
        "tags": ["person", "door"]
    },

    "una dipendente entra nella stanza": {
        "type": "movement",
        "tags": ["employee", "transit"]
    },

    "persona ferma al distributore automatico": {
        "type": "idle",
        "tags": ["person", "vending_machine"]
    },

    "dipendente in pausa": {
        "type": "idle",
        "tags": ["employee", "break"]
    },

    "persona ferma al cellulare": {
        "type": "idle",
        "tags": ["person", "phone"]
    },

    "piccolo gruppo di persone discute": {
        "type": "crowd",
        "tags": ["group", "discussion"]
    },

    "gruppo di dipendenti a lavoro": {
        "type": "crowd",
        "tags": ["group", "employee"]
    },

    "gruppo di persone stazionano davanti alla porta da oltre 10 minuti": {
        "type": "crowd",
        "tags": ["group", "door", "loitering"]
    }
}


# LOGICA DI GENERAZIONE

def generate_single_event(ts: datetime) -> dict:
    hour = ts.hour
    is_night = hour >= 22 or hour < 6

    if is_night:
        scelte_evento = ["movement", "idle", "crowd"]
        pesi_evento = [0.25, 0.70, 0.05]
    else:
        scelte_evento = ["movement", "idle", "crowd"]
        pesi_evento = [0.70, 0.20, 0.10]

    event_type = random.choices(
        scelte_evento,
        weights=pesi_evento
    )[0]

    descrizioni_possibili = [
        desc
        for desc, info in EVENT_DESCRIPTIONS.items()
        if info["type"] == event_type
    ]

    description = random.choice(descrizioni_possibili)

    event_info = EVENT_DESCRIPTIONS[description]

    camera_id = random.choice(list(CAMERAS.keys()))

    base_tags = event_info["tags"]

    confidence = (
        round(random.uniform(0.55, 0.75), 2)
        if is_night
        else round(random.uniform(0.82, 0.99), 2)
    )

    return {
        "timestamp": ts.isoformat(timespec="seconds"),
        "camera_id": camera_id,
        "location": CAMERAS[camera_id],
        "description": description,
        "event_type": event_type,
        "metadata": {
            "confidence": confidence,
            "tags": base_tags,
        },
    }


def generate_events(
        start: datetime,
        end: datetime,
        freq_per_hour: float,
) -> list[dict]:
    events = []
    current = start

    while current < end:
        base_interval = 3600 / max(0.05, freq_per_hour)

        jitter = random.uniform(0.95, 1.05)
        interval = base_interval * jitter

        current += timedelta(seconds=interval)

        if current >= end:
            break

        events.append(generate_single_event(current))

    return events

# INVIO AL BACKEND

# verifica che il backend FastAPI sia raggiungibile
def check_backend(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/", timeout=5)
        r.raise_for_status()
        data = r.json()
        print(f" Backend raggiungibile: {data.get('message', 'OK')}")
        return True
    except httpx.ConnectError:
        print(f" Backend non raggiungibile su {url}")
        print(f" Avvia il server con:  uvicorn Ollama:app --reload")
        return False
    except Exception as e:
        print(f" Errore connessione: {e}")
        return False


# Invia un singolo evento via POST /events. Restituisce True se ok
def send_event(event: dict, url: str) -> bool:
    try:
        r = httpx.post(f"{url}/events", json=event, timeout=5)
        r.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        print(f" Rifiutato dal backend: {e.response.status_code} — {e.response.text[:120]}")
        return False
    except Exception as e:
        print(f" Errore di rete: {e}")
        return False


# ENTRY POINT
def main():
    # default: oggi dalle 06:00 alle 23:00
    today = datetime.now().strftime("%Y-%m-%d")
    default_start = f"{today}T06:00:00"
    default_end = f"{today}T23:00:00"

    parser = argparse.ArgumentParser(
        description="Generatore di eventi sorveglianza",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start", default=default_start, help="Inizio simulazione (ISO 8601)")
    parser.add_argument("--end", default=default_end, help="Fine simulazione (ISO 8601)")
    parser.add_argument("--freq", type=positive_float, default=3.0, help="Eventi medi per ora (deve essere > 0)")
    parser.add_argument("--stream", action="store_true", help="Invia eventi al backend")
    parser.add_argument("--interval", type=float, default=0.2, help="Secondi tra invii (stream)")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="URL backend FastAPI")
    parser.add_argument("--check", action="store_true", help="Solo verifica connessione backend")
    args = parser.parse_args()

    print("\n Generatore eventi")

    # modalità --check
    if args.check:
        print(f"Verifica connessione a {args.url} ...")
        check_backend(args.url)
        return

    # parse date
    try:
        start_dt = datetime.fromisoformat(args.start)
        end_dt = datetime.fromisoformat(args.end)
    except ValueError as e:
        print(f" Formato data non valido: {e}")
        print("    Usa il formato ISO 8601, es: 2026-04-30T06:00:00")
        sys.exit(1)

    if end_dt <= start_dt:
        print(" --end deve essere successivo a --start")
        sys.exit(1)

    print(f"   Periodo     : {start_dt}  →  {end_dt}")
    print(f"   Frequenza   : ~{args.freq} eventi/ora")

    # generazione
    events = generate_events(start_dt, end_dt, args.freq)

    if not events:
        print("\n Nessun evento generato nel periodo selezionato (controlla gli orari).")
        return

    print(f"\n Generati {len(events)} eventi")

    # invio al backend
    if args.stream:
        print(f"\n Verifica connessione a {args.url} ...")
        if not check_backend(args.url):
            sys.exit(1)

        print(f"\n Invio {len(events)} eventi {args.url}  (intervallo: {args.interval}s)\n")

        ok = fail = 0
        for i, ev in enumerate(events, 1):
            print(
                f"{ev['timestamp'][11:16]}  "
                f"{ev['camera_id']:<14}  "
                f"{ev['event_type']:<12}  "
                f"{ev['description'][:55]}"
            )

            if send_event(ev, args.url):
                ok += 1
            else:
                fail += 1

            if args.interval > 0:
                time.sleep(args.interval)

        print(f"\n{'─' * 50}")
        print(f" Invio completato:  {ok} inviati   {fail} errori")


if __name__ == "__main__":
    main()