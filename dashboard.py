# dashboard.py
#
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

st.title("Sistema di Sorveglianza Intelligente")
st.caption("Dashboard eventi + analisi anomalie + sintesi LLM locale")

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
        r = requests.post(f"{API_BASE_URL}/summaries", json=payload, timeout=120)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore LLM: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione LLM: {e}")
    return None


#SIDEBAR - FILTRI
st.sidebar.header(" Filtri")

today = date.today()
start_date = st.sidebar.date_input("Data iniziale", value=today)
end_date = st.sidebar.date_input("Data finale", value=today)
start_time = st.sidebar.time_input("Ora iniziale", value=time(0, 0))
end_time = st.sidebar.time_input("Ora finale", value=time(23, 59))

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

camera_options = ["tutte"]
cameras = get_cameras()
for c in cameras:
    camera_options.append(c["camera_id"])

selected_camera = st.sidebar.selectbox("Camera", camera_options)

event_type = st.sidebar.selectbox(
    "Tipo evento",
    ["tutti", "movement", "idle", "crowd", "intrusion", "loitering", "anomaly"],
)

keyword = st.sidebar.text_input("Keyword descrizione", placeholder="es. porta, gruppo, caduta")
limit = st.sidebar.slider("Numero massimo eventi", min_value=10, max_value=1000, value=100, step=10)

load_button = st.sidebar.button("Carica eventi")

#COMPOSIZIONE QUERY PARAMS
params = {
    "start": start_dt.isoformat(),
    "end": end_dt.isoformat(),
    "limit": limit,
}
if selected_camera != "tutte":
    params["camera_id"] = selected_camera
if event_type != "tutti":
    params["event_type"] = event_type
if keyword:
    params["keyword"] = keyword

#CREAZIONE DELLE SCHEDE NEL MAIN PANEL
tab_dashboard, tab_intelligenza_art = st.tabs(["Monitoraggio Eventi", "Analisi e Sintesi AI"])


#SCHEDA 1: VISUALIZZAZIONE DATI E GRAFICI

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
                    stats_params = {
                        "start": start_dt.isoformat(),
                        "end": end_dt.isoformat(),
                    }
                    if selected_camera != "tutte":
                        stats_params["camera_id"] = selected_camera

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
                        breakdown_df = pd.DataFrame({
                            "Tipo": list(stats["by_event_type"].keys()),
                            "Count": list(stats["by_event_type"].values()),
                        })
                        st.bar_chart(breakdown_df.set_index("Tipo"))

                    st.subheader("Lista eventi")
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.warning("Nessun evento trovato")
    else:
        st.info("💡 Imposta i filtri nella barra laterale a sinistra e clicca su 'Carica eventi'.")

#SCHEDA 2: LOGICA DI SINTESI GENERATIVA (OLLAMA)

with tab_intelligenza_art:
    st.subheader("Analisi Semantica con LLM Locale")
    st.write("Genera un report narrativo basato sulle fasce orarie e sui criteri di sicurezza stabiliti.")

    if st.button("Avvia Elaborazione Sintesi"):
        payload = {
            "start": start_dt.isoformat(),
        }
        #gestione coerenza con ollama.py
        payload["end"] = end_dt.isoformat()
        if selected_camera != "tutte":
            payload["camera_id"] = selected_camera

        with st.spinner("Il server locale Ollama sta analizzando i dati... Attendi..."):
            summary_data = generate_summary(payload)

            if summary_data:
                st.success("Sintesi completata con successo!")

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

                # sezione Risposta Testuale Generata da Llama3
                st.subheader("Report Generato da Ollama")
                st.markdown(summary_data["summary"])

#FOOTER
st.divider()
st.caption(
    "Sistema di archiviazione, rilevazione e sintesi eventi — FastAPI + MongoDB Atlas + Ollama Locale + Streamlit")