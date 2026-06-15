# Dashboard Streamlit per:
# - visualizzare eventi
# - filtrare per data/camera/tipo
# - vedere statistiche
# - generare sintesi LLM via Ollama locale
#
# AVVIO:
# streamlit run dashboard.py
#
# Assicurarsi che il backend FastAPI sia acceso:
# uvicorn ollama:app --reload

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date, time

API_BASE_URL = "http://127.0.0.1:8000"

st.set_page_config(
    page_title="Sistema Sorveglianza Intelligente",
    layout="wide",
)

#funzioni chiamata API
def get_cameras():
    try:
        r = requests.get(f"{API_BASE_URL}/cameras", timeout=10)
        r.raise_for_status()
        return r.json()["cameras"]
    except Exception as e:
        st.error(f"Errore recupero camere: {e}")
        return []

def get_events(params):
    try:
        r = requests.get(f"{API_BASE_URL}/events", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore API: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione: {e}")
    return None

def get_stats(params):
    try:
        r = requests.get(f"{API_BASE_URL}/stats", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Errore statistiche: {e}")
        return None

def generate_summary(payload):
    try:
        r = requests.post(f"{API_BASE_URL}/summaries", json=payload, timeout=None)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore LLM: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione LLM: {e}")
    return None

#Inizializza lo stato delle telecamere nel session_state se non esiste già
if "camera_status" not in st.session_state:
    cameras_list = get_cameras()
    # all'inizio tutte le telecamere sono attive
    st.session_state.camera_status = {c["camera_id"]: True for c in cameras_list}

#INTERFACCIA: titolo e pulsanti delle telecamere
st.title("Sistema di Sorveglianza Intelligente")
st.caption("Dashboard eventi + analisi anomalie + sintesi LLM locale")
st.subheader("Stato telecamere")
st.write("Clicca su una telecamera per escluderla/includerla dalla ricerca:")

cameras_data = get_cameras()
if cameras_data:
    cols = st.columns(len(cameras_data))

    for idx, c in enumerate(cameras_data):
        cam_id = c["camera_id"]
        cam_loc = c["location"]
        is_active = st.session_state.camera_status.get(cam_id, True)
        label = f"{cam_loc}" if is_active else f" {cam_loc}\n(DISATTIVATA)"
        btn_type = "primary" if is_active else "secondary"

        if cols[idx].button(label, key=f"btn_{cam_id}", type=btn_type, use_container_width=True):
            # cliccare inverte lo stato
            st.session_state.camera_status[cam_id] = not is_active
            st.rerun()

#SIDEBAR
st.sidebar.header(" Filtri")

today = date.today()
start_date = st.sidebar.date_input("Data iniziale", value=today)
end_date = st.sidebar.date_input("Data finale", value=today)

#default alle 00:01 del giorno corrente
start_time = st.sidebar.time_input("Ora iniziale", value=time(0, 1))
end_time = st.sidebar.time_input("Ora finale", value=time(23, 59))

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

event_type = st.sidebar.selectbox(
    "Tipo evento",
    ["tutti", "movement", "idle", "crowd", "intrusion", "loitering", "anomaly"],
)

keyword = st.sidebar.text_input("Keywords", placeholder="es. porta, gruppo, caduta")
limit = st.sidebar.slider("Numero massimo eventi", min_value=10, max_value=1000, value=100, step=10)

load_button = st.sidebar.button("Carica eventi")

#Prende la lista di tutte le camere che sono ATTIVE (True) nei pulsanti in alto
camere_attive = [cam_id for cam_id, active in st.session_state.camera_status.items() if active]
all_cameras = list(st.session_state.camera_status.keys())

# COMPOSIZIONE QUERY PARAMS
params = {
    "start": start_dt.isoformat(),
    "end": end_dt.isoformat(),
    "limit": limit,
}

if event_type != "tutti":
    params["event_type"] = event_type

if keyword:
    params["keyword"] = keyword

if camere_attive:
    params["camera_ids"] = camere_attive

# CREAZIONE DELLE TABS
tab_dashboard, tab_intelligenza_art = st.tabs(["Monitoraggio Eventi", "Analisi e Sintesi AI"])


# SCHEDA 1: VISUALIZZAZIONE DATI E GRAFICI
with tab_dashboard:
    if load_button:
        with st.spinner("Recupero eventi dal backend..."):
            data = get_events(params)

            if data is not None:
                events = data["events"]
                st.success(f"Recuperati {len(events)} eventi")

                if events:
                    rows = []
                    for e in events:
                        rows.append({
                            "timestamp": e["timestamp"],
                            "camera": e["camera_id"],
                            "location": e["location"],
                            "event_type": e["event_type"],
                            "description": e["description"],
                            "confidence": e.get("metadata", {}).get("confidence"),
                        })
                    df = pd.DataFrame(rows)

                    # Generazione parametri per le statistiche
                    stats_params = {
                        "start": start_dt.isoformat(),
                        "end": end_dt.isoformat(),
                    }
                    if camere_attive:
                        stats_params["camera_ids"] = camere_attive

                    stats = get_stats(stats_params)

                    if stats:
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Totale eventi", stats["total_events"])
                        with col2:
                            st.metric("Anomalie", stats["anomaly_count"])
                        with col3:
                            st.metric("Camere attive", len(stats["by_camera"]))

                    st.subheader("Breakdown per tipo evento")

                    breakdown_df = pd.DataFrame(
                        {"Numero Eventi": list(stats["by_event_type"].values())},
                        index=list(stats["by_event_type"].keys())
                    )
                    st.bar_chart(breakdown_df, horizontal=True)
                    st.subheader("Lista eventi")
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.warning("Nessun evento trovato per i criteri o per le telecamere selezionate.")
    else:
        st.info("💡 Imposta i filtri nella barra laterale a sinistra e clicca su 'Carica eventi'.")

# SCHEDA 2: LOGICA GENERATIVA (OLLAMA)
with tab_intelligenza_art:
    st.subheader("Analisi Semantica con LLM Locale")
    st.write(
        "Fai una richiesta personalizzata oppure clicca il tasto per una sintesi.")

    tasto_standard = st.button("Avvia Elaborazione Sintesi Standard")
    user_custom_prompt = st.chat_input(
        "Scrivi qui cosa vuoi chiedere a Ollama riguardo agli eventi...")

    if tasto_standard or user_custom_prompt:
        payload = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "custom_prompt": user_custom_prompt if user_custom_prompt else None
        }

        if camere_attive:
            payload["camera_ids"] = camere_attive

        msg_caricamento = "Ollama sta generando la sintesi standard..." if not user_custom_prompt else f"Ollama sta elaborando la tua richiesta: '{user_custom_prompt}'..."

        with st.spinner(msg_caricamento):
            summary_data = generate_summary(payload)

            if summary_data:
                st.success("Elaborazione completata!")

                # Mostra le anomalie cronologiche solo se stiamo facendo il report standard
                if not user_custom_prompt:
                    anomalies = summary_data.get("anomalies", [])
                    st.subheader("Anomalie Rilevate (Analisi Cronologica)")
                    if anomalies:
                        for a in anomalies:
                            severity = a.get("severity", "MEDIA")
                            testo_box = f"**[{a['timestamp']}] {a['camera_id']} ({a['location']})** — {a['description']}\n\n*Motivo:* {a['reason']}"
                            if severity == "ALTA":
                                st.error(testo_box)
                            elif severity == "MEDIA":
                                st.warning(testo_box)
                            else:
                                st.info(testo_box)
                    else:
                        st.success("Nessuna anomalia critica rilevata dall'algoritmo orario.")

                st.subheader("Risposta di Ollama")
                st.markdown(summary_data["summary"])

# FOOTER
st.divider()
st.caption(
    "Sistema di archiviazione, rilevazione e sintesi eventi — FastAPI + MongoDB Atlas + Ollama Locale + Streamlit"
)