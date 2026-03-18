# Interactive Conversational Intelligence Dashboard

Ask questions about your data in plain English. The app translates them into SQL using Google Gemini, runs the query, and renders a chart automatically.

---

## Project Structure

```
├── app.py       # Streamlit frontend
├── main.py      # FastAPI backend
├── engine.py    # DB manager, session manager, LLM interface
└── models.py    # Pydantic models
```

---

## Dependencies

```bash
pip install fastapi uvicorn streamlit requests pandas plotly google-generativeai python-multipart
```

PostgreSQL support (optional):
```bash
pip install psycopg2-binary
```

---

## How to Run

```bash
streamlit run app.py
```

On first launch, a startup screen asks for your **Gemini API key**. Enter it and click **Start backend** — the app spawns the FastAPI server automatically and loads once it is ready.

To skip the prompt, set the key as an environment variable beforehand:

```bash
export GEMINI_API_KEY=AIza...
```

Get a key at [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)

---

## How It Works

Every question goes through this pipeline:

```
User question
     │
     ▼
Validate session → Extract schema → Call Gemini (question + schema + history)
     │
     ▼
Gemini returns SQL + chart config (JSON)
     │
     ▼
Validate SQL safety → Execute query → Return data to frontend
     │
     ▼
Plotly renders the chart
```

**Key internals:**

- **CSV ingestion** — uploaded files are loaded into an in-memory SQLite database via pandas. The filename becomes the table name.
- **LLM call** — the database schema and last 20 turns of conversation are sent to `gemini-flash` with each request, enabling follow-up questions.
- **SQL safety** — before execution, all SQL is checked against a blocklist (`DROP`, `DELETE`, `INSERT`, `UPDATE`, etc.). Only `SELECT` statements are allowed.
- **Chart selection** — Gemini picks the chart type based on the data shape (bar, line, pie, scatter, area, or table).
- **Backend auto-launch** — the Streamlit app spawns `main.py` as a child process with the API key injected into its environment. The process is terminated automatically when Streamlit exits.

---

## Supported Data Sources

| Source | How to connect |
|--------|---------------|
| CSV | Upload via the sidebar — ingested into in-memory SQLite |
| SQLite | Provide the file path |
| PostgreSQL | Provide a DSN: `postgresql://user:pass@host:5432/db` |

---

## Known Limitations

- Dates stored as strings (e.g. `13-04-2022`) can cause incorrect results for date range queries.
- Complex multi-part questions often fail — break them into smaller focused questions.
- CSV sessions are lost if the backend restarts and the file must be re-uploaded.
