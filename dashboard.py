import streamlit as st
from streamlit import container
import requests
import pandas as pd
import yaml
import time as pytime
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")

st.set_page_config(
    page_title="Sistema Sorveglianza Intelligente",
    layout="wide",
    initial_sidebar_state="expanded",
)

FUSO_ROMA = ZoneInfo("Europe/Rome")

BADGE = {
    "Critico": "🔴 Critico",
    "Alto": "🟠 Alto",
    "Medio": "🟡 Medio",
    "Basso": "🟢 Basso",
    "N/D": "⚪ N/D",
}

# ──────────────────────────────────────────────────────────────────────────
# CSS PERSONALIZZATO (Responsive layout & pulizia pulsanti)
# ──────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
div[data-testid="stMetric"] {
    background-color: rgba(120,120,120,0.06);
    border: 1px solid rgba(120,120,120,0.18);
    border-radius: 10px;
    padding: 14px 16px 10px 16px;
}
.critical-box {
    border: 1px solid rgba(220,50,50,0.5);
    background-color: rgba(220,50,50,0.06);
    border-radius: 10px;
    padding: 14px 18px;
}

/* Permette al testo nelle celle di andare a capo invece di sovrapporsi
   quando lo spazio orizzontale è stretto (fix collasso testo su mobile) */
div[data-testid="stColumn"] {
    white-space: normal !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
}

/* Riduzione padding interno per layout responsive */
div[data-testid="stColumn"] > div {
    padding-left: 4px !important;
    padding-right: 4px !important;
}

/* Le etichette delle colonne nell'intestazione restano su una riga sola */
div[class*="st-key-table_header"] .col-label {
    white-space: nowrap;
    font-weight: 600;
}

/* Frecce di ordinamento compatte, impilate accanto al nome colonna */
div[class*="st-key-sort_"] {
    line-height: 1 !important;
}
div[class*="st-key-sort_"] button {
    padding: 0px 3px !important;
    min-height: 15px !important;
    height: 15px !important;
    width: 100% !important;
    font-size: 8px !important;
    line-height: 1 !important;
    margin: 0px 0px 2px 0px !important;
    border-radius: 3px !important;
}
div[class*="st-key-sort_"]:last-child button {
    margin-bottom: 0px !important;
}

/* Pulsante escludi/Includi compatto e senza a capo */
.btn-action button {
    padding: 2px 6px !important;
    font-size: 12px !important;
    min-height: 28px !important;
    height: auto !important;
    white-space: nowrap !important;
}

/* Riga di "schede" di analisi: scorrimento orizzontale forzato */
div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-scheda_"]) {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    overflow-x: auto !important;
    gap: 8px !important;
    padding-bottom: 10px !important;
}

/* Disabilita la riduzione e la crescita forzata delle colonne all'interno della riga schede */
div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-scheda_"]) > div[data-testid="stColumn"] {
    flex: 0 0 auto !important;
    min-width: 140px !important;
    max-width: 220px !important;
    width: auto !important;
}

/* Stile pulsanti schede */
div[class*="st-key-scheda_"] button {
    white-space: nowrap !important;
    border-radius: 8px !important;
    font-size: 13px !important;
}

/* Pulsante "x": rimuove la scheda solo dalla pagina, non dallo storico */
div[class*="st-key-scheda_x_"] button {
    padding: 0px !important;
    min-height: 22px !important;
    height: 22px !important;
    width: 22px !important;
    min-width: 22px !important;
    border-radius: 50% !important;
    font-size: 11px !important;
    line-height: 1 !important;
}

/* In corso: bordo/sfondo ambra */
div[class*="st-key-scheda_in_corso_"] button {
    border-color: rgba(245,158,11,0.55) !important;
    background: rgba(245,158,11,0.10) !important;
}
/* Errore: bordo/sfondo rosso */
div[class*="st-key-scheda_errore_"] button {
    border-color: rgba(220,50,50,0.5) !important;
    background: rgba(220,50,50,0.08) !important;
}
/* Completata: bordo blu leggero */
div[class*="st-key-scheda_completato_"] button {
    border-color: rgba(37,99,235,0.35) !important;
}

/* Sidebar più stretta di default (resta comunque ridimensionabile a mano) */
section[data-testid="stSidebar"] {
    width: 250px !important;
}
section[data-testid="stSidebar"] > div:first-child {
    width: 250px !important;
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE DI DOMINIO
# ──────────────────────────────────────────────────────────────────────────
@st.cache_resource
def load_domain_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


DOMAIN_CONFIG = load_domain_config()
EMPLOYEE_TAG = DOMAIN_CONFIG.get("actor_tags", {}).get("employee", "employee")
RESTRICTED_CAMERAS = {
    r["camera_id"] for r in DOMAIN_CONFIG.get("security_rules", {}).get("restricted_cameras", [])
}


def classifica_criticita(evento: dict) -> tuple[str, str]:
    if not DOMAIN_CONFIG:
        return "N/D", "Configurazione di dominio non disponibile"

    dt_roma = converti_a_roma(evento.get("timestamp", ""))
    if dt_roma is None:
        return "N/D", "Timestamp non valido"

    hour = dt_roma.hour
    tags = evento.get("metadata", {}).get("tags", [])
    is_employee = EMPLOYEE_TAG in tags
    cam = evento.get("camera_id")

    if cam in RESTRICTED_CAMERAS and not is_employee:
        return "Critico", "Presenza non autorizzata nella camera blindata"

    if 19 <= hour < 22:
        if not is_employee:
            return "Critico", "Soggetto non riconosciuto come dipendente fuori orario"
        return "Basso", "Dipendente presente fuori orario"

    if hour >= 22 or hour < 6:
        if not is_employee:
            return "Critico", "Presenza notturna non identificata come dipendente"
        return "Medio", "Dipendente presente in orario notturno"

    if 6 <= hour < 8:
        if not is_employee:
            return "Alto", "Soggetto non dipendente durante la pre-apertura"
        return "Basso", "Dipendente in pre-apertura"

    if "loitering" in tags:
        return "Medio", "Stazionamento prolungato rilevato"
    return "Basso", "Comportamento ordinario per orario e zona"


# ──────────────────────────────────────────────────────────────────────────
# HELPER GENERICI
# ──────────────────────────────────────────────────────────────────────────
def converti_a_roma(dt_str):
    try:
        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt_utc.astimezone(FUSO_ROMA)
    except Exception:
        return None


def format_periodo(data_inizio, data_fine, ora_inizio, ora_fine):
    st.markdown(f"**Intervallo date:** {data_inizio} → {data_fine}")
    st.markdown(f"**Intervallo orario:** {ora_inizio} → {ora_fine}")


# ──────────────────────────────────────────────────────────────────────────
# CHIAMATE API
# ──────────────────────────────────────────────────────────────────────────
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


def richiedi_analisi_async(payload):
    """Crea subito la 'scheda' di analisi: il backend risponde all'istante con
    un id e lancia la generazione LLM in background."""
    try:
        r = requests.post(f"{API_BASE_URL}/summaries/richiedi", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore nella richiesta di analisi: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione: {e}")
    return None


def get_scheda_analisi(scheda_id: str, tipo: str):
    """Interroga lo stato/risultato di una scheda di analisi (in_corso / completato / errore)."""
    try:
        r = requests.get(f"{API_BASE_URL}/summaries/{scheda_id}", params={"tipo": tipo}, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore nel recupero della scheda: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione: {e}")
    return None


def nascondi_scheda_analisi(scheda_id: str, tipo: str):
    """Nasconde la scheda dalla Dashboard in modo permanente (persistito lato
    server): il record resta comunque nello Storico risposte."""
    try:
        r = requests.post(f"{API_BASE_URL}/summaries/{scheda_id}/nascondi", params={"tipo": tipo}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        st.error(f"Errore durante la rimozione della scheda: {e}")
        return False


def create_automation_job(payload):
    try:
        r = requests.post(f"{API_BASE_URL}/automation_jobs", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore creazione job periodico: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione: {e}")
    return None


def get_automation_jobs():
    try:
        r = requests.get(f"{API_BASE_URL}/automation_jobs", timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"Errore recupero job periodici: {e.response.text}")
    except Exception as e:
        st.error(f"Errore connessione: {e}")
    return None


def pause_automation_job(job_id):
    try:
        r = requests.post(f"{API_BASE_URL}/automation_jobs/{job_id}/pause", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Errore durante la pausa del job: {e}")
    return None


def resume_automation_job(job_id):
    try:
        r = requests.post(f"{API_BASE_URL}/automation_jobs/{job_id}/resume", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Errore durante la ripresa del job: {e}")
    return None


def get_history(kind: str):
    endpoint = "analysis_history" if kind == "analysis" else "prompt_history"
    try:
        r = requests.get(f"{API_BASE_URL}/{endpoint}", timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Errore recupero storico: {e}")
        return None


def delete_history_item(kind: str, item_id: str):
    endpoint = "analysis_history" if kind == "analysis" else "prompt_history"
    try:
        r = requests.delete(f"{API_BASE_URL}/{endpoint}/{item_id}", timeout=15)
        return r.status_code == 200
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────────────────────
if "page" not in st.session_state:
    st.session_state.page = "Dashboard"

if "camera_status" not in st.session_state:
    cameras_list = get_cameras()
    st.session_state.camera_status = {c["camera_id"]: True for c in cameras_list}

if "table_page" not in st.session_state:
    st.session_state.table_page = 1
if "excluded_events" not in st.session_state:
    st.session_state.excluded_events = set()
if "sort_column" not in st.session_state:
    st.session_state.sort_column = "Data e ora"
if "sort_ascending" not in st.session_state:
    st.session_state.sort_ascending = False
if "scheda_attiva" not in st.session_state:
    # (id, tipo) della scheda di analisi attualmente aperta; None = vista eventi
    st.session_state.scheda_attiva = None

# ──────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────────────────────────────
st.sidebar.markdown("## Sistema di sorveglianza intelligente")
st.sidebar.caption("Monitoraggio e analisi degli eventi")
st.sidebar.divider()

nav_items = ["Dashboard", "Impostazioni", "Storico risposte"]
for item in nav_items:
    tipo = "primary" if st.session_state.page == item else "secondary"
    if st.sidebar.button(item, key=f"nav_{item}", use_container_width=True, type=tipo):
        st.session_state.page = item
        st.rerun()

st.sidebar.divider()

if st.session_state.page == "Dashboard":
    st.sidebar.markdown("### Filtri globali")
    st.sidebar.caption("I filtri si aggiornano automaticamente ad ogni modifica.")

    today = date.today()
    start_date = st.sidebar.date_input("Data iniziale", value=today - timedelta(days=1))
    end_date = st.sidebar.date_input("Data finale", value=today)

    start_time = st.sidebar.time_input("Ora iniziale", value=time(0, 1))
    end_time = st.sidebar.time_input("Ora finale", value=time(23, 59))

    start_dt = datetime.combine(start_date, start_time)
    end_dt = datetime.combine(end_date, end_time)

    tipi_evento_sel = st.sidebar.multiselect(
        "Tipo evento",
        ["movement", "idle", "crowd"],
        default=[],
        placeholder="Tutti i tipi",
    )

    ricerca_libera = st.sidebar.text_input(
        "Ricerca libera",
        placeholder="Cerca in descrizione, posizione, camera...",
    )

    limit = 0

    if st.sidebar.button("Reimposta filtri", use_container_width=True):
        st.session_state.table_page = 1
        st.session_state.excluded_events = set()
        st.session_state.sort_column = "Data e ora"
        st.session_state.sort_ascending = False
        st.rerun()
else:
    start_dt = datetime.combine(date.today() - timedelta(days=1), time(0, 1))
    end_dt = datetime.combine(date.today(), time(23, 59))
    tipi_evento_sel, ricerca_libera, limit = [], "", 0

st.sidebar.divider()
st.sidebar.caption(f"Backend: `{API_BASE_URL}`")


ICONA_STATO = {"in_corso": "⏳", "errore": "⚠️", "completato": "✅"}
ETICHETTA_STATO = {"in_corso": "In corso", "errore": "Errore", "completato": "Pronta"}

def render_schede_analisi(storico_analisi, storico_prompt):
    """Mostra le richieste di analisi AI come schede."""

    schede = [{**it, "tipo": "standard"} for it in storico_analisi]
    schede += [{**it, "tipo": "prompt"} for it in storico_prompt]

    if not schede:
        return

    def _ts(it):
        return converti_a_roma(it.get("request_date", "")) or datetime.min.replace(
            tzinfo=FUSO_ROMA
        )

    schede.sort(key=_ts, reverse=True)
    schede = [s for s in schede if not s.get("nascosta")]

    if not schede:
        return

    st.markdown("")
    st.markdown("##### 🗂️ Le tue analisi")

    # Riga di colonne
    cols = st.columns(len(schede), gap="small")

    for col, it in zip(cols, schede):

        scheda_id = it["_id"]
        tipo = it["tipo"]
        stato = it.get(
            "stato",
            "completato" if it.get("risposta") else "in_corso"
        )

        testo_data = _ts(it).strftime("%d/%m/%Y %H:%M")

        with col:

            if st.button(
                f"{ICONA_STATO.get(stato,'✅')} {testo_data}",
                key=f"scheda_{stato}_{scheda_id}",
                use_container_width=True,
            ):
                st.session_state.scheda_attiva = (scheda_id, tipo)
                st.rerun()

            if st.button(
                "✕",
                key=f"scheda_x_{scheda_id}",
                use_container_width=True,
                help="Rimuovi dalla pagina (resta nello storico)",
            ):
                if nascondi_scheda_analisi(scheda_id, tipo):
                    st.rerun()
def render_vista_risposta_analisi():
    """Vista a schermo intero per una scheda: se l'analisi è ancora in corso
    mostra uno spinner e ricontrolla periodicamente, altrimenti la risposta."""
    scheda_id, tipo = st.session_state.scheda_attiva

    if st.button("← Torna agli eventi"):
        st.session_state.scheda_attiva = None
        st.rerun()

    scheda = get_scheda_analisi(scheda_id, tipo)
    if not scheda:
        st.warning("Impossibile recuperare questa scheda di analisi.")
        return

    stato = scheda.get("stato", "completato" if scheda.get("risposta") else "in_corso")
    dt_roma = converti_a_roma(scheda.get("request_date", ""))
    testo_data = dt_roma.strftime("%d/%m/%Y %H:%M:%S") if dt_roma else "-"

    st.markdown(f"#### Risultato analisi AI — {testo_data}")
    dettagli = f"{scheda.get('numero_eventi', '?')} eventi analizzati"
    if scheda.get("LLM"):
        dettagli += f" · {scheda['LLM']}"
    st.caption(dettagli)
    if scheda.get("prompt"):
        st.caption(f"Richiesta personalizzata: _{scheda['prompt']}_")

    if stato == "in_corso":
        with st.spinner("🤖 L'AI sta ultimando la tua richiesta..."):
            pytime.sleep(3)
        st.rerun()
    elif stato == "errore":
        st.error(f"Si è verificato un errore durante l'analisi: {scheda.get('errore', 'errore sconosciuto')}")
    else:
        st.success("Elaborazione completata!")
        st.markdown(scheda.get("risposta") or "_Nessuna risposta disponibile._")


# ──────────────────────────────────────────────────────────────────────────
# PAGINA: DASHBOARD (HOME)
# ──────────────────────────────────────────────────────────────────────────
def pagina_dashboard():
    st.title("Dashboard")
    st.caption("Monitoraggio e analisi degli eventi")

    camere_attive = [cam_id for cam_id, active in st.session_state.camera_status.items() if active]
    cameras_data = get_cameras()

    params = {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "limit": limit}
    if len(tipi_evento_sel) == 1:
        params["event_type"] = tipi_evento_sel[0]

    with st.spinner("Caricamento eventi..."):
        data = get_events(params)
        eventi_periodo = data["events"] if data else []

    if len(tipi_evento_sel) > 1:
        eventi_periodo = [e for e in eventi_periodo if e["event_type"] in tipi_evento_sel]

    now = datetime.now()
    params_24h = {"start": (now - timedelta(hours=24)).isoformat(), "end": now.isoformat(), "limit": 0}
    dati_24h = get_events(params_24h)

    eventi_24h = dati_24h["events"] if dati_24h else []
    classificati_24h = [(e, *classifica_criticita(e)) for e in eventi_24h]
    critici_24h = [e for e, liv, _ in classificati_24h if liv == "Critico"]

    storico_analisi = get_history("analysis") or []
    storico_prompt = get_history("prompt") or []
    analisi_24h = [
        it for it in (storico_analisi + storico_prompt)
        if converti_a_roma(it.get("request_date", "")) and
           converti_a_roma(it["request_date"]) >= datetime.now(FUSO_ROMA) - timedelta(hours=24)
    ]

    n_camere_attive = sum(1 for v in st.session_state.camera_status.values() if v)
    n_camere_totali = len(st.session_state.camera_status) or 1

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("📅 Eventi ultime 24 ore", len(eventi_24h))
    with k2:
        st.metric("⚠️ Eventi critici", len(critici_24h))
    with k3:
        st.metric("🎥 Telecamere attive", f"{n_camere_attive} / {n_camere_totali}")
    with k4:
        st.metric("🧠 Analisi AI completate", len(analisi_24h))

    st.markdown("")
    with st.container():
        st.markdown('<div class="critical-box">', unsafe_allow_html=True)
        st.markdown("#### 🔴 Eventi critici · ultime 24 ore")
        if not critici_24h:
            st.caption("Nessun evento critico rilevato nelle ultime 24 ore.")
        else:
            for e in critici_24h[:5]:
                _, liv, motivo = next(c for c in classificati_24h if c[0] is e)
                dt_roma = converti_a_roma(e["timestamp"])
                testo_data = dt_roma.strftime("%d/%m %H:%M") if dt_roma else e["timestamp"]
                c1, c2, c3 = st.columns([2, 5, 2])
                c1.markdown(f"**{testo_data}**")
                c2.markdown(f"**{e['description']}** — {e['location']}  \n_{motivo}_")
                c3.markdown(f"{BADGE.get(liv, liv)}")
                st.divider()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("")
    st.markdown(f"#### Telecamere ")

    if cameras_data:
        cols = st.columns(min(len(cameras_data), 6))
        for idx, c in enumerate(cameras_data):
            cam_id = c["camera_id"]
            cam_loc = c["location"]
            is_active = st.session_state.camera_status.get(cam_id, True)
            with cols[idx % len(cols)]:
                label = f"{cam_loc}  \n{'Attiva' if is_active else 'Offline'}"
                btn_type = "primary" if is_active else "secondary"
                if st.button(label, key=f"btn_{cam_id}", use_container_width=True, type=btn_type):
                    st.session_state.camera_status[cam_id] = not is_active
                    st.rerun()

    eventi_filtrati = [e for e in eventi_periodo if e["camera_id"] in camere_attive]

    if ricerca_libera.strip():
        chiave = ricerca_libera.strip().lower()
        eventi_filtrati = [
            e for e in eventi_filtrati
            if chiave in e["description"].lower()
               or chiave in e["location"].lower()
               or chiave in e["camera_id"].lower()
               or chiave in e["event_type"].lower()
        ]

    eventi_inclusi_analisi = [e for e in eventi_filtrati if e["_id"] not in st.session_state.excluded_events]
    n_esclusi = len(st.session_state.excluded_events)

    render_schede_analisi(storico_analisi, storico_prompt)

    if st.session_state.scheda_attiva:
        render_vista_risposta_analisi()
        return

    st.markdown("")
    with st.form("form_analisi_ai", clear_on_submit=False):
        col_prompt, col_submit = st.columns([5, 1.4])
        with col_prompt:
            ai_prompt_input = st.text_input(
                "Analizza con AI",
                placeholder="✨ Analizza con AI oppure scrivi qui una tua richiesta",
                label_visibility="collapsed",
            )
        with col_submit:
            analisi_avviata = st.form_submit_button("Analizza con AI", use_container_width=True)

    ai_prompt = ai_prompt_input.strip() if ai_prompt_input and ai_prompt_input.strip() else None

    if n_esclusi > 0:
        st.caption(f"L'analisi verrà eseguita su **{len(eventi_inclusi_analisi)}** eventi ({n_esclusi} esclusi).")

    if analisi_avviata:
        if not eventi_inclusi_analisi:
            st.warning("Nessun evento disponibile per l'analisi.")
        else:
            payload = {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "custom_prompt": ai_prompt if ai_prompt else None,
                "selected_events": eventi_inclusi_analisi,
                "camera_ids": camere_attive,
            }
            # la richiesta torna subito con l'id della scheda: l'LLM lavora
            # in background sul server, l'utente non resta bloccato in attesa
            esito = richiedi_analisi_async(payload)
            if esito:
                st.session_state.scheda_attiva = (esito["id"], esito["tipo"])
                st.rerun()

    st.markdown("")
    st.markdown("#### Tutti gli eventi")

    col_head, col_refresh, col_export = st.columns([4, 1, 1])
    with col_head:
        st.caption(f"{len(eventi_filtrati)} risultati per i filtri correnti")
    with col_refresh:
        if st.button("🔄 Aggiorna", use_container_width=True):
            st.rerun()

    if not eventi_filtrati:
        st.info("Nessun evento trovato per i filtri correnti.")
        return

    rows = []
    for e in eventi_filtrati:
        dt_roma = converti_a_roma(e["timestamp"])
        rows.append({
            "_id": e["_id"],
            "Data e ora": dt_roma if dt_roma else e["timestamp"],
            "Telecamera": e["camera_id"],
            "Posizione": e["location"],
            "Tipo": e["event_type"],
            "Descrizione": e["description"],
            "Confidenza": e.get("metadata", {}).get("confidence"),
        })
    df_full = pd.DataFrame(rows)

    with col_export:
        st.download_button(
            "Esporta CSV",
            data=df_full.drop(columns=["_id"]).to_csv(index=False).encode("utf-8"),
            file_name="eventi_filtrati.csv",
            mime="text/csv",
            use_container_width=True,
        )

    col_pp_label, col_pp = st.columns([3, 1])
    per_pagina = col_pp.selectbox("Per pagina", [10, 25, 50, 100], index=0, label_visibility="collapsed")

    df_sorted = df_full.sort_values(
        by=st.session_state.sort_column,
        ascending=st.session_state.sort_ascending,
        na_position="last"
    )

    n_eventi = len(df_sorted)
    n_pagine = max(1, -(-n_eventi // per_pagina))
    st.session_state.table_page = min(st.session_state.table_page, n_pagine)

    start_idx = (st.session_state.table_page - 1) * per_pagina
    end_idx = start_idx + per_pagina
    df_page = df_sorted.iloc[start_idx:end_idx].reset_index(drop=True)

    # ── Intestazione tabella Responsive ──
    # Tutti i campi dati sono ordinabili; "Azione" resta escluso perché non è un dato
    # ma la colonna dei pulsanti Escludi/Includi.
    COLONNE_ORDINABILI = {"Data e ora", "Telecamera", "Posizione", "Tipo", "Descrizione", "Confidenza"}

    # Proporzioni ricalibrate per garantire respiro a ogni colonna
    header_widths = [2.2, 1.8, 2.2, 1.5, 3.8, 1.5, 1.2]
    header_labels = ["Data e ora", "Telecamera", "Posizione", "Tipo", "Descrizione", "Confidenza", "Azione"]

    tabella_container = st.container(key="events_table")
    with tabella_container:
        header_container = st.container(key="table_header")
        with header_container:
            header_cols = st.columns(header_widths)

            for col, label in zip(header_cols, header_labels):
                with col:
                    if label in COLONNE_ORDINABILI:
                        is_curr_col = (st.session_state.sort_column == label)
                        up_active = is_curr_col and st.session_state.sort_ascending
                        down_active = is_curr_col and not st.session_state.sort_ascending

                        # Nome colonna + due freccette compatte impilate (▲ crescente / ▼ decrescente)
                        c_head1, c_head2 = st.columns([0.8, 0.2])
                        with c_head1:
                            st.markdown(f'<span class="col-label">{label}</span>', unsafe_allow_html=True)
                        with c_head2:
                            if st.button("▲", key=f"sort_up_{label}",
                                         type="primary" if up_active else "secondary",
                                         help=f"Ordina per {label} (crescente)"):
                                st.session_state.sort_column = label
                                st.session_state.sort_ascending = True
                                st.session_state.table_page = 1
                                st.rerun()
                            if st.button("▼", key=f"sort_down_{label}",
                                         type="primary" if down_active else "secondary",
                                         help=f"Ordina per {label} (decrescente)"):
                                st.session_state.sort_column = label
                                st.session_state.sort_ascending = False
                                st.session_state.table_page = 1
                                st.rerun()
                    else:
                        st.markdown(f'<span class="col-label">{label}</span>', unsafe_allow_html=True)

        st.divider()

        # Righe dati
        for _, row in df_page.iterrows():
            event_id = row["_id"]
            escluso = event_id in st.session_state.excluded_events

            cols = st.columns(header_widths)

            def cell(col, testo, escluso=escluso):
                if escluso:
                    col.markdown(f"<span style='color:#cc3333; text-decoration:line-through;'>{testo}</span>",
                                 unsafe_allow_html=True)
                else:
                    col.markdown(str(testo))

            data_val = row["Data e ora"]
            testo_data = data_val.strftime("%d/%m/%Y %H:%M:%S") if isinstance(data_val, datetime) else str(data_val)

            cell(cols[0], testo_data)
            cell(cols[1], row["Telecamera"])
            cell(cols[2], row["Posizione"])
            cell(cols[3], row["Tipo"])
            cell(cols[4], row["Descrizione"])
            cell(cols[5], row["Confidenza"])

            label_btn = "✓ Includi" if escluso else "✕ Escludi"
            with cols[6]:
                st.markdown('<div class="btn-action">', unsafe_allow_html=True)
                if st.button(label_btn, key=f"exc_{event_id}", use_container_width=True):
                    if escluso:
                        st.session_state.excluded_events.discard(event_id)
                    else:
                        st.session_state.excluded_events.add(event_id)
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.divider()

    # Paginazione
    col_prev, col_info, col_next = st.columns([1, 3, 1])
    with col_prev:
        if st.button("← Precedente", disabled=st.session_state.table_page <= 1, use_container_width=True):
            st.session_state.table_page -= 1
            st.rerun()
    with col_info:
        st.markdown(
            f"<div style='text-align:center'>Pagina {st.session_state.table_page} di {n_pagine} "
            f"— eventi {start_idx + 1}-{min(end_idx, n_eventi)} di {n_eventi}</div>",
            unsafe_allow_html=True,
        )
    with col_next:
        if st.button("Successiva →", disabled=st.session_state.table_page >= n_pagine, use_container_width=True):
            st.session_state.table_page += 1
            st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# PAGINA: IMPOSTAZIONI
# ──────────────────────────────────────────────────────────────────────────
def pagina_impostazioni():
    st.title("Impostazioni")
    st.caption("Configura ed esegui l'analisi automatica periodica degli eventi")

    cameras_data = get_cameras()
    camera_options = [c["camera_id"] for c in cameras_data] if cameras_data else []

    with st.expander("➕ Crea nuovo job di analisi automatica", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            job_camere = st.multiselect("Telecamere incluse (vuoto = tutte)", camera_options)
            job_tipi = st.multiselect("Tipi evento inclusi (vuoto = tutti)", ["movement", "idle", "crowd"])
            job_titolo = st.text_input("Titolo job (opzionale)", placeholder="Es. Monitoraggio ingresso notturno")
        with col2:
            job_start_date = st.date_input("Data inizio periodo", value=date.today(), key="job_start_date")
            job_start_time = st.time_input("Ora inizio periodo", value=time(0, 0), key="job_start_time")
            job_end_date = st.date_input("Data fine periodo", value=date.today(), key="job_end_date")
            job_end_time = st.time_input("Ora fine periodo", value=time(23, 59), key="job_end_time")

        st.markdown("**Frequenza di esecuzione**")
        col_20, col_40, col_60, col_custom = st.columns(4)
        if "job_interval" not in st.session_state:
            st.session_state.job_interval = 30
        if col_20.button("Ogni 20 min", use_container_width=True):
            st.session_state.job_interval = 20
        if col_40.button("Ogni 40 min", use_container_width=True):
            st.session_state.job_interval = 40
        if col_60.button("Ogni 60 min", use_container_width=True):
            st.session_state.job_interval = 60
        with col_custom:
            custom_interval = st.number_input(
                "Personalizzata (min)", min_value=1, step=1,
                value=st.session_state.job_interval, label_visibility="collapsed",
            )
            st.session_state.job_interval = int(custom_interval)

        st.caption(f"Frequenza selezionata: ogni **{st.session_state.job_interval}** minuti")

        job_prompt = st.text_area(
            "Prompt personalizzato (lascia vuoto per la sintesi standard)",
            placeholder="Es. Segnala solo eventi relativi alla camera blindata",
        )
        job_max_events = 0

        if st.button("Crea job periodico", type="primary"):
            job_start_dt = datetime.combine(job_start_date, job_start_time)
            job_end_dt = datetime.combine(job_end_date, job_end_time)
            payload = {
                "camera_ids": job_camere,
                "tipi_evento": job_tipi,
                "custom_prompt": job_prompt if job_prompt else None,
                "titolo": job_titolo.strip() if job_titolo.strip() else None,
                "max_events": job_max_events,
                "interval_minutes": st.session_state.job_interval,
                "excluded_ids": [],
                "start": job_start_dt.isoformat(),
                "end": job_end_dt.isoformat(),
            }
            with st.spinner("Creazione job in corso..."):
                result = create_automation_job(payload)
            if result:
                st.success("Job creato correttamente.")
                st.rerun()

    st.divider()
    st.markdown("### Job configurati")

    jobs = get_automation_jobs()
    if jobs is None:
        return
    if not jobs:
        st.info("Non sono presenti job periodici configurati.")
        return

    for job in jobs:
        job_id = job["_id"]

        ultima_modifica_raw = job.get("ultima_modifica")
        dt_mod = converti_a_roma(str(ultima_modifica_raw)) if ultima_modifica_raw else None
        data_titolo = dt_mod.strftime("%d/%m/%Y %H:%M") if dt_mod else "(data non disponibile)"

        titolo_personalizzato = (job.get("titolo") or "").strip()
        titolo = titolo_personalizzato if titolo_personalizzato else data_titolo
        job_enabled = job.get("enabled", True)
        titolo += "" if job_enabled else "  ⏸ in pausa"

        with st.expander(titolo):
            _, col_pause, col_delete = st.columns([8, 2, 2])
            with col_pause:
                if job_enabled:
                    if st.button("⏸ Pausa", key=f"pause_job_{job_id}", use_container_width=True):
                        if pause_automation_job(job_id):
                            st.rerun()
                else:
                    if st.button("▶ Avvia", key=f"resume_job_{job_id}", use_container_width=True):
                        if resume_automation_job(job_id):
                            st.rerun()
            with col_delete:
                if st.button("🗑 Elimina", key=f"delete_job_{job_id}", use_container_width=True):
                    response = requests.delete(f"{API_BASE_URL}/automation_jobs/{job_id}")
                    if response.status_code == 200:
                        st.rerun()

            if not job_enabled:
                st.warning("Questo job è in pausa.")

            job_start_raw, job_end_raw = job.get("start"), job.get("end")
            if job_start_raw and job_end_raw:
                try:
                    parti_start = str(job_start_raw).split("T")
                    parti_end = str(job_end_raw).split("T")
                    format_periodo(parti_start[0], parti_end[0], parti_start[1][:8], parti_end[1][:8])
                except Exception:
                    format_periodo(job_start_raw, job_end_raw, "", "")

            camere = ", ".join(job.get("camera_ids", [])) if job.get("camera_ids") else "Tutte"
            st.markdown(f"**Telecamere incluse:** {camere}")
            tipi_eventi = ", ".join(job.get("tipi_evento", [])) if job.get("tipi_evento") else "Tutti"
            st.markdown(f"**Tipi evento:** {tipi_eventi}")


# ──────────────────────────────────────────────────────────────────────────
# PAGINA: STORICO RISPOSTE
# ──────────────────────────────────────────────────────────────────────────
def pagina_storico():
    st.title("Storico risposte")
    st.caption("Sintesi e risposte AI generate in passato")

    if "history_view" not in st.session_state:
        st.session_state.history_view = "analysis"

    c1, c2 = st.columns(2)
    with c1:
        tipo = "primary" if st.session_state.history_view == "analysis" else "secondary"
        if st.button("Sintesi passate", use_container_width=True, type=tipo):
            st.session_state.history_view = "analysis"
            st.rerun()
    with c2:
        tipo = "primary" if st.session_state.history_view == "prompt" else "secondary"
        if st.button("Risposte ai prompt", use_container_width=True, type=tipo):
            st.session_state.history_view = "prompt"
            st.rerun()

    st.divider()

    kind = st.session_state.history_view
    storico = get_history(kind)

    if storico is None or not storico:
        st.info("Non sono presenti elementi salvati.")
        return

    for item in storico:
        dt_roma = converti_a_roma(item["request_date"])
        titolo = dt_roma.strftime("%d/%m/%Y  %H:%M") if dt_roma else "Data N/D"

        with st.expander(titolo):
            _, col_delete = st.columns([10, 2])
            with col_delete:
                if st.button("🗑 Elimina", key=f"delete_{kind}_{item['_id']}", use_container_width=True):
                    if delete_history_item(kind, item["_id"]):
                        st.rerun()

            st.write(item["risposta"])


# ──────────────────────────────────────────────────────────────────────────
# ROUTING
# ──────────────────────────────────────────────────────────────────────────
if st.session_state.page == "Dashboard":
    pagina_dashboard()
elif st.session_state.page == "Impostazioni":
    pagina_impostazioni()
elif st.session_state.page == "Storico risposte":
    pagina_storico()

st.divider()
st.caption("Sistema di archiviazione, rilevazione e sintesi eventi — FastAPI + Streamlit")


#TODO SCHERMATA PRINCIPALE JOB PERIODICO EVENTI CRITICI ULTIME 24H E RIPULIRE