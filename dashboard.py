# Dashboard Streamlit per:
# - visualizzare eventi
# - filtrare per data/camera/tipo
# - generare sintesi LLM via Ollama locale o fare richieste personalizzate
#
# AVVIO:
# streamlit run dashboard.py
#
# Assicurarsi che il backend FastAPI sia acceso:
# uvicorn backend:app --reload

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date, time
import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
st.set_page_config(
    page_title="Sistema Sorveglianza Intelligente",
    layout="wide",
)


# funzioni chiamata API
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


# Inizializza lo stato delle telecamere nel session_state se non esiste già
if "camera_status" not in st.session_state:
    cameras_list = get_cameras()
    # all'inizio tutte le telecamere sono attive
    st.session_state.camera_status = {c["camera_id"]: True for c in cameras_list}

if "loaded_events" not in st.session_state:
    st.session_state.loaded_events = None
if "loaded_stats" not in st.session_state:
    st.session_state.loaded_stats = None
if "excluded_events" not in st.session_state:
    st.session_state.excluded_events = set()

# INTERFACCIA: titolo e pulsanti delle telecamere
st.title("Sistema di Sorveglianza Intelligente")
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

# SIDEBAR
st.sidebar.header(" Filtri")

today = date.today()
start_date = st.sidebar.date_input("Data iniziale", value=today)
end_date = st.sidebar.date_input("Data finale", value=today)

# default alle 00:01 del giorno corrente
start_time = st.sidebar.time_input("Ora iniziale", value=time(0, 1))
end_time = st.sidebar.time_input("Ora finale", value=time(23, 59))

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

event_type = st.sidebar.selectbox(
    "Tipo evento",
    ["tutti", "movement", "idle", "crowd"],
)

opzioni_limite = {
    "Tutti (Nessun limite)": 0,
    "10 eventi": 10,
    "25 eventi": 25,
    "50 eventi": 50,
    "100 eventi": 100,
    "250 eventi": 250,
    "500 eventi": 500,
    "1000 eventi": 1000
}

limite_selezionato = st.sidebar.selectbox(
    "Numero massimo eventi",
    options=list(opzioni_limite.keys()),
    index=3  # Imposta di default "100 eventi"
)
limit = opzioni_limite[limite_selezionato]

load_button = st.sidebar.button("Carica eventi")

# Prende la lista di tutte le camere che sono attive
camere_attive = [cam_id for cam_id, active in st.session_state.camera_status.items() if active]

params = {
    "start": start_dt.isoformat(),
    "end": end_dt.isoformat(),
    "limit": limit,
}

if event_type != "tutti":
    params["event_type"] = event_type

if camere_attive:
    params["camera_ids"] = camere_attive


if load_button:
    with st.spinner("Recupero eventi dal backend..."):
        data = get_events(params)
        if data is not None:
            st.session_state.loaded_events = data["events"]
            st.session_state.excluded_events = set()

            stats_params = {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            }
            if camere_attive:
                stats_params["camera_ids"] = camere_attive
            st.session_state.loaded_stats = get_stats(stats_params)

tab_dashboard, tab_intelligenza_art, tab_responses = st.tabs(["Monitoraggio Eventi ", " Analisi e Sintesi AI"," Visualizza risposte"])

# Tab 1: Visualizzazione dati
with tab_dashboard:
    if st.session_state.loaded_events is not None:
        events = st.session_state.loaded_events

        if events:
            rows = []
            for e in events:
                rows.append({
                    "_id": e["_id"],
                    "timestamp": e["timestamp"],
                    "camera": e["camera_id"],
                    "location": e["location"],
                    "event_type": e["event_type"],
                    "description": e["description"],
                    "confidence": e.get("metadata", {}).get("confidence"),
                })
            df = pd.DataFrame(rows)

            stats = st.session_state.loaded_stats
            if stats:
                col1, _ = st.columns(2)
                with col1:
                    st.metric("Totale eventi recuperati", stats["total_events"])

            n_esclusi = len(st.session_state.excluded_events)
            st.subheader("Lista eventi")
            st.caption(
                f"Clicca su un evento per escluderlo dall'analisi LLM. "
                f"Eventi esclusi: **{n_esclusi}** / {len(events)}"
            )

            if n_esclusi > 0:
                if st.button("Ripristina tutti gli eventi"):
                    st.session_state.excluded_events = set()
                    st.rerun()

            header = st.columns([2, 1.5, 2, 1.2, 3, 1, 1.2])
            headers_text = ["Timestamp", "Camera", "Location", "Tipo", "Descrizione", "Confidence", "Azione"]
            for col, label in zip(header, headers_text):
                col.markdown(f"**{label}**")
            st.divider()

            for _, row in df.iterrows():
                event_id = row["_id"]
                escluso = event_id in st.session_state.excluded_events

                cols = st.columns([2, 1.5, 2, 1.2, 3, 1, 1.2])


                #se l'evento è escluso viene sbarrato
                def cell(col, testo, escluso):
                    if escluso:
                        col.markdown(f"<span style='color:#cc3333; text-decoration:line-through;'>**{testo}**</span>",
                                     unsafe_allow_html=True)
                    else:
                        col.markdown(str(testo))


                cell(cols[0], row["timestamp"][11:16] + " " + row["timestamp"][:10], escluso)
                cell(cols[1], row["camera"], escluso)
                cell(cols[2], row["location"], escluso)
                cell(cols[3], row["event_type"], escluso)
                cell(cols[4], row["description"], escluso)
                cell(cols[5], row["confidence"], escluso)

                label_btn = "✓ includi" if escluso else "✕ escludi"
                if cols[6].button(label_btn, key=f"exc_{event_id}", use_container_width=True):
                    if escluso:
                        st.session_state.excluded_events.discard(event_id)
                    else:
                        st.session_state.excluded_events.add(event_id)
                    st.rerun()

                st.divider()
        else:
            st.warning("Nessun evento trovato per i criteri o per le telecamere selezionate.")
    else:
        st.info("Imposta i filtri nella barra laterale a sinistra e clicca su 'Carica eventi'.")

# Tab 2: Logica generativa (Ollama)
with tab_intelligenza_art:
    st.subheader("Analisi degli eventi con LLM locale")
    st.write("Fai una richiesta personalizzata oppure clicca il tasto per una sintesi.")

    tasto_standard = st.button("Avvia Elaborazione Sintesi Standard")
    user_custom_prompt = st.chat_input("Scrivi qui cosa vuoi chiedere a Ollama riguardo agli eventi...")

    if tasto_standard or user_custom_prompt:
        eventi_caricati = st.session_state.get("loaded_events", [])
        esclusi = st.session_state.get("excluded_events", set())

        # Filtra via gli eventi che l'utente ha escluso
        eventi_inclusi = [e for e in eventi_caricati if e["_id"] not in esclusi]

        if not eventi_caricati:
            st.warning("Carica prima gli eventi dal tab 'Monitoraggio Eventi' utilizzando i filtri laterali.")
        elif not eventi_inclusi:
            st.warning(
                "Tutti gli eventi caricati sono stati esclusi. Ripristina almeno un evento prima di avviare l'analisi.")
        else:
            n_esclusi = len(esclusi)
            if n_esclusi > 0:
                st.info(
                    f"L'analisi verrà eseguita su **{len(eventi_inclusi)}** eventi ({n_esclusi} esclusi manualmente e non inviati al modello).")

            payload = {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "custom_prompt": user_custom_prompt if user_custom_prompt else None,
                "selected_events": eventi_inclusi,
            }
            if camere_attive:
                payload["camera_ids"] = camere_attive

            msg_caricamento = (
                "Ollama sta generando la sintesi standard..."
                if not user_custom_prompt
                else f"Ollama sta elaborando: '{user_custom_prompt}'..."
            )

            with st.spinner(msg_caricamento):
                summary_data = generate_summary(payload)

                if summary_data:
                    st.success("Elaborazione completata!")
                    st.subheader("Analisi di Ollama")
                    st.markdown(summary_data["summary"])

#Tab 3 log delle risposte passate
    with tab_responses:

        st.subheader("Log delle risposte")

        if "history_view" not in st.session_state:
            st.session_state.history_view = None

        left, center, right = st.columns([1, 2, 1])

        with center:

            c1, c2 = st.columns(2)

            with c1:
                if st.button(
                        "Sintesi passate",
                        use_container_width=True,
                ):
                    st.session_state.history_view = "analysis"

            with c2:
                if st.button(
                        "Risposte ai prompt",
                        use_container_width=True,
                ):
                    st.session_state.history_view = "prompt"

        st.divider()

        if st.session_state.history_view == "analysis":

            response = requests.get(f"{API_BASE_URL}/analysis_history")

            if response.status_code == 200:

                storico = response.json()

                if not storico:
                    st.info("Non sono presenti analisi salvate.")
                else:

                    for item in storico:
                        dt = datetime.fromisoformat(item["request_date"].replace("Z", "+00:00"))
                        titolo = dt.strftime(" %d/%m/%Y  %H:%M")

                        with st.expander(titolo):
                            _, col_delete = st.columns([10, 1])

                            with col_delete:
                                if st.button(
                                        "Elimina",
                                        key=f"delete_analysis_{item['_id']}",
                                        use_container_width=True,
                                ):
                                    response = requests.delete(
                                        f"{API_BASE_URL}/analysis_history/{item['_id']}"
                                    )

                                    if response.status_code == 200:
                                        st.success("Analisi eliminata.")
                                        st.rerun()
                                    else:
                                        st.error("Errore durante l'eliminazione.")
                            st.markdown(f"**Data richiesta:** {item['request_date']}")

                            st.markdown(
                                f"**Periodo analizzato:** "
                                f"{item['data_inizio']} {item['ora_inizio']} → "
                                f"{item['data_fine']} {item['ora_fine']}"
                            )

                            camere = ", ".join(item["camera_ids"]) if item["camera_ids"] else "Tutte"

                            st.markdown(f"**Camere:** {camere}")

                            st.markdown(f"**Numero eventi:** {item['numero_eventi']}")

                            tipi_eventi = ", ".join(item.get("tipi_eventi", [])) if item.get("tipi_eventi") else "Tutti"

                            st.markdown(f"**Tipi evento:** {tipi_eventi}")

                            st.markdown(f"**LLM:** {item.get('LLM', 'N/D')}")

                            st.divider()

                            st.markdown("### Sintesi AI")

                            st.write(item["risposta"])

            else:
                st.error("Errore durante il recupero delle analisi.")

        elif st.session_state.history_view == "prompt":

            response = requests.get(f"{API_BASE_URL}/prompt_history")

            if response.status_code == 200:

                storico = response.json()

                if not storico:
                    st.info("Non sono presenti prompt salvati.")
                else:

                    for item in storico:
                        dt = datetime.fromisoformat(item["request_date"].replace("Z", "+00:00"))
                        titolo = dt.strftime(" %d/%m/%Y  %H:%M")

                        with st.expander(titolo):
                            _, col_delete = st.columns([10, 1])

                            with col_delete:
                                if st.button(
                                        "Elimina",
                                        key=f"delete_prompt_{item['_id']}",
                                        use_container_width=True,
                                ):
                                    response = requests.delete(
                                        f"{API_BASE_URL}/prompt_history/{item['_id']}"
                                    )

                                    if response.status_code == 200:
                                        st.success("Risposta eliminata.")
                                        st.rerun()
                                    else:
                                        st.error("Errore durante l'eliminazione.")
                            st.markdown(f"**Data richiesta:** {item['request_date']}")

                            st.markdown(
                                f"**Periodo analizzato:** "
                                f"{item['data_inizio']} {item['ora_inizio']} → "
                                f"{item['data_fine']} {item['ora_fine']}"
                            )

                            camere = ", ".join(item["camera_ids"]) if item["camera_ids"] else "Tutte"

                            st.markdown(f"**Camere:** {camere}")

                            st.markdown(f"**Numero eventi:** {item['numero_eventi']}")

                            tipi_eventi = ", ".join(item.get("tipi_eventi", [])) if item.get("tipi_eventi") else "Tutti"

                            st.markdown(f"**Tipi evento:** {tipi_eventi}")

                            st.markdown(f"**LLM:** {item.get('LLM', 'N/D')}")

                            st.markdown("### Prompt dell'utente")

                            st.info(item["prompt"])

                            st.divider()

                            st.markdown("### Risposta AI")

                            st.write(item["risposta"])

            else:
                st.error("Errore durante il recupero dei prompt.")

    st.divider()

# FOOTER
st.divider()
st.caption(
    "Sistema di archiviazione, rilevazione e sintesi eventi — FastAPI + MongoDB Atlas + Ollama Locale + Streamlit"
)