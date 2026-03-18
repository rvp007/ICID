import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import uuid

API_BASE    = "http://localhost:8000"
BACKEND_DIR = Path(__file__).parent
POLL_EVERY  = 0.4   # seconds between health checks
TIMEOUT     = 30    # seconds before giving up

# -------------------- PAGE CONFIG --------------------
st.set_page_config(
    page_title="ICID",
    layout="wide"
)

# ─────────────────────────────────────────────────────
#  BACKEND PROCESS HANDLE  (module-level, survives reruns)
# ─────────────────────────────────────────────────────
_PROC: subprocess.Popen | None = None

def _kill():
    """Shut down the child process when Streamlit exits."""
    if _PROC and _PROC.poll() is None:
        _PROC.terminate()
        try:
            _PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _PROC.kill()

atexit.register(_kill)


def _backend_alive() -> bool:
    try:
        return requests.get(f"{API_BASE}/health", timeout=2).status_code == 200
    except Exception:
        return False


def _launch(api_key: str) -> subprocess.Popen:
    env = {**os.environ, "GEMINI_API_KEY": api_key}
    return subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ─────────────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────────────
if "backend_ready" not in st.session_state:
    st.session_state.backend_ready = False

if "backend_error" not in st.session_state:
    st.session_state.backend_error = None

if "session_id" not in st.session_state:
    st.session_state.session_id = None

if "connected" not in st.session_state:
    st.session_state.connected = False

if "schema" not in st.session_state:
    st.session_state.schema = None

if "messages" not in st.session_state:
    st.session_state.messages = []


# ─────────────────────────────────────────────────────
#  STARTUP GATE
#  Shows a key-entry screen until the backend is live.
#  Returns True once the backend is confirmed running.
# ─────────────────────────────────────────────────────
def startup_gate() -> bool:
    global _PROC

    # Already confirmed this session
    if st.session_state.backend_ready:
        # Check the process didn't die after we confirmed it
        if _PROC and _PROC.poll() is not None:
            st.session_state.backend_ready = False
            st.session_state.backend_error = "Backend exited unexpectedly. Please restart."
        else:
            return True

    # External backend already running (e.g. started manually)
    if _backend_alive():
        st.session_state.backend_ready = True
        return True

    # ── Render the key-entry screen ──────────────────
    st.markdown("## ICID")
    st.caption("Enter your Gemini API key to start the backend server.")
    st.divider()

    env_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if env_key:
        st.success("GEMINI_API_KEY detected in environment — ready to launch.")
        key_to_use = env_key
    else:
        key_to_use = st.text_input(
            "Gemini API Key",
            type="password",
            placeholder="AIza...",
            help="Passed only to the backend process. Never written to disk.",
        )
        st.caption("Get a key at https://aistudio.google.com/app/apikey")

    if st.session_state.backend_error:
        st.error(st.session_state.backend_error)

    if st.button("Start backend", type="primary"):
        key_to_use = (key_to_use or "").strip()

        if not key_to_use:
            st.warning("Paste your API key above first.")
            return False

        if not (BACKEND_DIR / "main.py").exists():
            st.error(
                f"main.py not found in `{BACKEND_DIR}`. "
                "Make sure app.py is in the same folder as main.py."
            )
            return False

        st.session_state.backend_error = None

        # Spawn the process
        try:
            _PROC = _launch(key_to_use)
        except Exception as e:
            st.session_state.backend_error = f"Failed to start process: {e}"
            st.rerun()
            return False

        # Animated progress while polling /health
        bar   = st.progress(0.0)
        label = st.empty()
        phases = [
            (0.20, "Starting server..."),
            (0.50, "Loading models..."),
            (0.80, "Waiting for health check..."),
        ]
        deadline  = time.time() + TIMEOUT
        phase_idx = 0
        ready     = False

        while time.time() < deadline:
            # Process crashed?
            if _PROC.poll() is not None:
                raw = ""
                try:
                    raw = _PROC.stderr.read().decode(errors="replace")[:400]
                except Exception:
                    pass
                st.session_state.backend_error = (
                    f"Process exited (code {_PROC.returncode})."
                    + (f"\n\nDetails: {raw}" if raw else "")
                )
                st.rerun()
                return False

            if _backend_alive():
                ready = True
                break

            elapsed  = time.time() - (deadline - TIMEOUT)
            progress = min(0.92, elapsed / TIMEOUT)
            bar.progress(progress)

            if phase_idx < len(phases) and progress >= phases[phase_idx][0]:
                label.caption(phases[phase_idx][1])
                phase_idx += 1

            time.sleep(POLL_EVERY)

        if ready:
            bar.progress(1.0)
            label.caption("Backend is ready.")
            st.session_state.backend_ready = True
            time.sleep(0.4)
            st.rerun()
        else:
            st.session_state.backend_error = (
                f"Backend did not respond within {TIMEOUT}s. "
                "Check that fastapi, uvicorn, and google-generativeai are installed."
            )
            st.rerun()

    return False


# ─────────────────────────────────────────────────────
#  GATE CHECK — stop here if backend isn't up yet
# ─────────────────────────────────────────────────────
if not startup_gate():
    st.stop()


# ================== YOUR ORIGINAL CODE BELOW (unchanged) ==================

# -------------------- STYLES --------------------
st.markdown("""
<style>
.block-container {
    padding-top: 1.5rem;
}
.chat-container {
    max-width: 900px;
    margin: auto;
}
</style>
""", unsafe_allow_html=True)

# -------------------- SIDEBAR --------------------
with st.sidebar:
    st.title("Database Setup")

    tab1, tab2 = st.tabs(["CSV Upload", "DB Connection"])

    # -------- CSV UPLOAD --------
    with tab1:
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

        if uploaded_file:
            if st.button("Ingest CSV"):
                with st.spinner("Uploading and processing CSV..."):
                    files = {"file": uploaded_file.getvalue()}
                    response = requests.post(f"{API_BASE}/api/upload", files={"file": uploaded_file})

                    if response.status_code == 200:
                        data = response.json()
                        st.session_state.session_id = data["session_id"]
                        st.session_state.schema = data["schema"]
                        st.session_state.connected = True
                        st.success(f"Loaded {data['filename']} ({data['row_count']} rows)")
                    else:
                        st.error(response.text)

    # -------- DB CONNECTION --------
    with tab2:
        db_type = st.selectbox("Database Type", ["sqlite", "postgresql"])
        conn_str = st.text_input("Connection String")

        if st.button("Connect"):
            payload = {
                "db_type": db_type,
                "connection_string": conn_str
            }

            with st.spinner("Connecting to database..."):
                response = requests.post(f"{API_BASE}/api/connect", json=payload)

                if response.status_code == 200:
                    data = response.json()
                    st.session_state.session_id = data["session_id"]
                    st.session_state.schema = data["schema"]
                    st.session_state.connected = True
                    st.success("Connected successfully")
                else:
                    st.error(response.text)

    st.divider()

    # -------- SCHEMA DISPLAY --------
    st.subheader("Schema")

    if st.session_state.connected and st.session_state.schema:
        for table, cols in st.session_state.schema.items():
            with st.expander(table):
                for col in cols:
                    st.text(f"{col['name']} ({col['type']})")
    else:
        st.caption("No active database")

# -------------------- CHART RENDERER --------------------
def render_chart(df: pd.DataFrame, config: dict):
    if df.empty:
        st.warning("No data returned")
        return

    chart_type = config.get("type", "table")
    x = config.get("x_key")
    y_keys = config.get("y_keys", [])
    title = config.get("title", "")

    try:
        if chart_type == "bar":
            fig = px.bar(df, x=x, y=y_keys, title=title)

        elif chart_type == "line":
            fig = px.line(df, x=x, y=y_keys, title=title)

        elif chart_type == "area":
            fig = px.area(df, x=x, y=y_keys, title=title)

        elif chart_type == "scatter":
            fig = px.scatter(df, x=x, y=y_keys[0] if y_keys else None, title=title)

        elif chart_type == "pie":
            fig = px.pie(df, names=x, values=y_keys[0] if y_keys else None, title=title)

        elif chart_type == "table":
            st.dataframe(df, use_container_width=True)
            return

        else:
            st.dataframe(df, use_container_width=True)
            return

        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Chart rendering failed: {e}")
        st.dataframe(df, use_container_width=True)

# -------------------- MAIN UI --------------------
st.title("Interactive Conversational Intelligence Dashboard")

st.divider()

# -------------------- CHAT HISTORY --------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["type"] == "text":
            st.markdown(msg["content"])

        elif msg["type"] == "chart":
            render_chart(msg["df"], msg["chart_config"])

            with st.expander("View Raw Data and SQL"):
                st.code(msg["sql"], language="sql")
                st.dataframe(msg["df"], use_container_width=True)

# -------------------- USER INPUT --------------------
if prompt := st.chat_input("Ask a question about your data..."):
    if not st.session_state.connected:
        st.error("Connect a database first")
    else:
        # Add user message
        st.session_state.messages.append({
            "role": "user",
            "type": "text",
            "content": prompt
        })

        with st.chat_message("user"):
            st.markdown(prompt)

        # API CALL
        with st.chat_message("assistant"):
            with st.spinner("Analyzing data and generating dashboard..."):
                payload = {
                    "session_id": st.session_state.session_id,
                    "question": prompt
                }

                response = requests.post(f"{API_BASE}/api/query", json=payload)

                if response.status_code != 200:
                    st.error(response.text)
                else:
                    result = response.json()

                    # Clarification case
                    if result.get("clarification_needed"):
                        msg = result.get("clarification_message", "Clarification required")

                        st.markdown(msg)

                        st.session_state.messages.append({
                            "role": "assistant",
                            "type": "text",
                            "content": msg
                        })

                    # Error case
                    elif result.get("error"):
                        st.error(result["error"])

                        st.session_state.messages.append({
                            "role": "assistant",
                            "type": "text",
                            "content": result["error"]
                        })

                    else:
                        df = pd.DataFrame(result.get("data", []))
                        chart_config = result.get("chart_config", {})
                        sql = result.get("sql", "")

                        render_chart(df, chart_config)

                        with st.expander("View Raw Data and SQL"):
                            st.code(sql, language="sql")
                            st.dataframe(df, use_container_width=True)

                        st.session_state.messages.append({
                            "role": "assistant",
                            "type": "chart",
                            "df": df,
                            "chart_config": chart_config,
                            "sql": sql
                        })