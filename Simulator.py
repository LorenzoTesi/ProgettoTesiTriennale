"""
simulator.py — Generatore di eventi di sorveglianza simulati

# 1. Prima verifica che il backend FastAPI sia acceso:
#    uvicorn main_ollama:app --reload
#
# 2. Salva gli eventi in un file JSON (senza inviare nulla):
#    python simulator.py --output preview.json
#
# 3. Invia gli eventi al backend in blocco (veloce, per test):
#    python simulator.py --stream --interval 0
#
# 4. Invia gli eventi lentamente, uno al secondo (simula real-time):
#    python simulator.py --stream --interval 1
#
# 5. Personalizza il periodo e la frequenza:
#    python simulator.py --stream --start 2026-04-30T06:00:00 --end 2026-04-30T23:00:00 --freq 15 --anomaly-rate 0.1
#
# 6. Solo eventi notturni (più anomalie):
#    python simulator.py --stream --start 2026-04-30T22:00:00 --end 2026-05-01T06:00:00 --anomaly-rate 0.3

PARAMETRI
  --start         datetime inizio simulazione  (default: oggi 06:00)
  --end           datetime fine simulazione    (default: oggi 23:00)
  --freq          eventi medi per ora          (default: 10)
  --anomaly-rate  probabilità anomalia 0-1     (default: 0.08)
  --stream        invia al backend invece di salvare su file
  --interval      secondi tra un invio e l'altro in modalità stream (default: 0.2)
  --url           URL del backend              (default: http://127.0.0.1:8000)
  --output        file JSON di output locale   (default: events_preview.json)
  --check         solo verifica connessione al backend, non genera eventi
"""

import argparse
import json
import random
import time
import sys
from datetime import datetime, timedelta

import httpx


#DATI DI RIFERIMENTO

CAMERAS = {
    "corridor_1": "corridoio principale",
    "corridor_2": "corridoio secondario",
    "entrance_clients": "ingresso principale per i clienti",
    "reception_hall": "sportello",
    "vault": "camera blindata",
    "exit": "uscita principale",
}

NORMAL_EVENTS = {
    "movement": [
        "una persona cammina per la stanza",
        "un gruppo di persone camminano per la stanza",
        "una persona si dirige verso la porta"
    ],
    "idle": [
        "persona in attesa vicino allo sportello",
        "persona ferma al distributore automatico",
        "dipendente in pausa",
    ],
    "crowd": [
        "fila allo sportello",
        "gruppo di dipendenti a lavoro",
    ],
}

ANOMALY_EVENTS = {
    "intrusion": [
        "sensore rileva finestra rotta",
        "sensore rileva la porta è stata forzata",
    ],
    "loitering": [
        "gruppo di persone stazionano davanti alla porta da oltre 10 minuti"
    ],
    "anomaly": [
        "movimento brusco rilevato, possibile caduta",
        "oggetto sospetto abbandonato nell'area",
        "emergenza medica"
    ],
}

TAGS_MAP = {
    "movement":  ["person", "transit"],
    "idle":      ["person", "standing", "waiting"],
    "crowd":     ["group", "door", "standing"],
    "intrusion": ["unauthorized", "door", "alert"],
    "loitering": ["group", "door", "standing", "alert"],
    "anomaly":   ["alert", "suspicious", "object"],
}


#LOGICA DI GENERAZIONE

def expected_activity(hour: int) -> float:
    """
    Moltiplicatore di attività per ora del giorno.
    1.5 = rush hour mattina/sera, 1.0 = normale, 0.05 = notte
    """
    if 8 <= hour < 10 or 17 <= hour < 19:
        return 1.5
    if 10 <= hour < 12 or 14 <= hour < 17:
        return 1.0
    if 12 <= hour < 14:
        return 0.8
    if 19 <= hour < 22:
        return 0.3
    return 0.05


def generate_event(ts: datetime, anomaly_rate: float) -> dict:
    hour     = ts.hour
    is_night = hour >= 21 or hour < 6

    # Di notte la probabilità di anomalia triplica
    effective_rate = min(1.0, anomaly_rate * (3.0 if is_night else 1.0))
    is_anomaly = random.random() < effective_rate

    if is_anomaly:
        event_type  = random.choice(list(ANOMALY_EVENTS.keys()))
        description = random.choice(ANOMALY_EVENTS[event_type])
        confidence  = round(random.uniform(0.55, 0.80), 2)
    else:
        activity = expected_activity(hour)
        if activity < 0.1:
            event_type = "idle"
        else:
            event_type = random.choices(
                ["movement", "idle", "crowd"], weights=[0.7, 0.2, 0.1]
            )[0]
        description = random.choice(NORMAL_EVENTS[event_type])
        confidence  = round(random.uniform(0.82, 0.99), 2)

    camera_id  = random.choice(list(CAMERAS.keys()))
    base_tags  = TAGS_MAP.get(event_type, [])
    extra_tags = random.sample(
        ["holiday", "weekend", "peak_hour"], k=random.randint(0, 1)
    )

    return {
        "timestamp":  ts.isoformat(timespec="seconds"),
        "camera_id":  camera_id,
        "location":   CAMERAS[camera_id],
        "description": description,
        "event_type": event_type,
        "metadata": {
            "confidence": confidence,
            "tags":       base_tags + extra_tags,
        },
    }


def generate_events(
    start: datetime,
    end: datetime,
    freq_per_hour: float,
    anomaly_rate: float,
) -> list[dict]:
    events  = []
    current = start
    now     = datetime.now()

    while current < end:
        hour     = current.hour
        activity = expected_activity(hour)
        avg_interval = 3600 / max(0.1, freq_per_hour * activity)
        interval = random.expovariate(1.0 / avg_interval)
        current += timedelta(seconds=interval)

        if current >= end:
            break

        #se il timestamp generato è nel futuro, viene saltato
        if current > now:
            break

        events.append(generate_event(current, anomaly_rate))

    return events

#INVIO AL BACKEND

def check_backend(url: str) -> bool:
    """Verifica che il backend FastAPI sia raggiungibile."""
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


def send_event(event: dict, url: str) -> bool:
    """Invia un singolo evento via POST /events. Restituisce True se ok."""
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



#ENTRY POINT
def main():
    #default: oggi dalle 06:00 alle 23:00
    today        = datetime.now().strftime("%Y-%m-%d")
    default_start = f"{today}T06:00:00"
    default_end   = f"{today}T23:00:00"

    parser = argparse.ArgumentParser(
        description="Generatore eventi sorveglianza",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start",        default=default_start, help="Inizio simulazione (ISO 8601)")
    parser.add_argument("--end",          default=default_end,   help="Fine simulazione (ISO 8601)")
    parser.add_argument("--freq",         type=float, default=10.0,  help="Eventi medi per ora")
    parser.add_argument("--anomaly-rate", type=float, default=0.08,  help="Probabilità anomalia 0-1")
    parser.add_argument("--stream",       action="store_true",        help="Invia eventi al backend")
    parser.add_argument("--interval",     type=float, default=0.2,   help="Secondi tra invii (stream)")
    parser.add_argument("--url",          default="http://127.0.0.1:8000", help="URL backend FastAPI")
    parser.add_argument("--output",       default="events_preview.json", help="File JSON di output locale")
    parser.add_argument("--check",        action="store_true",        help="Solo verifica connessione backend")
    args = parser.parse_args()

    print("\n Generatore eventi")
    print("─" * 50)

    #modalità --check
    if args.check:
        print(f"Verifica connessione a {args.url} ...")
        check_backend(args.url)
        return

    #parse date
    try:
        start_dt = datetime.fromisoformat(args.start)
        end_dt   = datetime.fromisoformat(args.end)
    except ValueError as e:
        print(f" Formato data non valido: {e}")
        print("    Usa il formato ISO 8601, es: 2026-04-30T06:00:00")
        sys.exit(1)

    if end_dt <= start_dt:
        print(" --end deve essere successivo a --start")
        sys.exit(1)

    print(f"   Periodo     : {start_dt}  →  {end_dt}")
    print(f"   Frequenza   : ~{args.freq} eventi/ora  (modulata per orario)")
    print(f"   Anomalie    : {args.anomaly_rate*100:.0f}% base  (×3 di notte)")

    #generazione
    events    = generate_events(start_dt, end_dt, args.freq, args.anomaly_rate)
    anomalies = [e for e in events if e["event_type"] in ("intrusion", "loitering", "anomaly")]

    print(f"\n Generati {len(events)} eventi  ({len(anomalies)} anomalie)")

    # Breakdown per tipo
    by_type: dict[str, int] = {}
    for e in events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
    for etype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        bar = "█" * (count * 20 // max(by_type.values()))
        print(f"   {etype:<12} {count:>4}  {bar}")

    #modalità STREAM (invia al backend)
    if args.stream:
        print(f"\n🔍  Verifica connessione a {args.url} ...")
        if not check_backend(args.url):
            sys.exit(1)

        print(f"\n Invio {len(events)} eventi → {args.url}  (intervallo: {args.interval}s)\n")

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

        print(f"\n{'─'*50}")
        print(f" Invio completato:  {ok} inviati   {fail} errori")
        if fail == 0:
            print(f"    Puoi verificare su MongoDB Atlas o chiamare:")
            print(f"    GET {args.url}/events?start={start_dt.date()}T00:00:00&end={end_dt.date()}T23:59:59")

    #modalità FILE (salva JSON locale)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

        print(f"\n  Salvati in '{args.output}'")
        print(f"\n Primo evento: ")
        print(json.dumps(events[0], ensure_ascii=False, indent=2))
        if anomalies:
            print(f"\n Primo evento anomalo: ")
            print(json.dumps(anomalies[0], ensure_ascii=False, indent=2))

        print(f"\n Per inviare al backend:")
        print(f"    python simulator.py --stream \\")
        print(f"        --start {args.start} --end {args.end} \\")
        print(f"        --freq {args.freq} --anomaly-rate {args.anomaly_rate} \\")
        print(f"        --interval 0")


if __name__ == "__main__":
    main()