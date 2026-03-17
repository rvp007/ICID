import sqlite3
import pandas as pd
import uuid
import os
from typing import Optional
import json
import re
import os
import google.generativeai as genai
from collections import defaultdict
from typing import Optional
import re


#-------SQL SAFETY-------#

# SQL keywords that aren't allowed
BLOCKED_KEYWORDS = [
    r"\bDROP\b",
    r"\bDELETE\b",
    r"\bTRUNCATE\b",
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bREPLACE\b",
    r"\bATTACH\b",
    r"\bDETACH\b",
    r"\bPRAGMA\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
]

_BLOCKED_RE = re.compile("|".join(BLOCKED_KEYWORDS), re.IGNORECASE)
__COMMENT_RE = re.compile(r"--.*?$|/\*.*?\*/", re.DOTALL | re.MULTILINE) #regex pattern to strip SQL comments from query before safet-check


class UnsafeSQLError(Exception):
    pass


def validate_sql(sql: str) -> str:
    #Raises UnsafeSQLError if the SQL contains any destructive operation. Returns the cleaned SQL string on success.
    sql = sql.strip().rstrip(";")
    clean = __COMMENT_RE.sub("", sql).strip()
    match = _BLOCKED_RE.search(clean)
    if match:
        raise UnsafeSQLError(
            f"Query contains a blocked operation: '{match.group()}'. "
            "Only SELECT statements are permitted."
        )

    # Must start with SELECT (after stripping comments/whitespace)
    if not clean.upper().startswith("SELECT") and not clean.upper().startswith("WITH"):
        raise UnsafeSQLError(
            "Only SELECT (and WITH ... SELECT) queries are allowed."
        )

    return sql


#-------SESSION MANAGER-------#

class SessionManager:
    """
    Maintains per-session chat history so the LLM can handle follow-up questions that refine or filter previous results.
    Each history entry is a dict:
        { role: "user" | "model", content: str }
    """

    MAX_HISTORY = 20  # keep last N turns to avoid token bloat

    def __init__(self):
        self._history: dict[str, list[dict]] = defaultdict(list)

    def add_user_message(self, session_id: str, message: str):
        self._history[session_id].append({"role": "user", "parts": [message]})
        self._trim(session_id)

    def add_model_message(self, session_id: str, message: str):
        self._history[session_id].append({"role": "model", "parts": [message]})
        self._trim(session_id)

    def get_history(self, session_id: str) -> list[dict]:
        return list(self._history[session_id])

    def clear(self, session_id: str):
        self._history[session_id] = []

    def history_length(self, session_id: str) -> int:
        return len(self._history[session_id])

    def _trim(self, session_id: str):
        history = self._history[session_id]
        if len(history) > self.MAX_HISTORY:
            trimmed = history[-self.MAX_HISTORY:]
            while trimmed and trimmed[0]["role"] != "user": #AI requires first turn to be from user
                trimmed = trimmed[1:]
            self._history[session_id] = trimmed


# Singleton
session_manager = SessionManager()


#-------DATABASE MANAGER-------#

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False


class DBManager:
    """
    Manages database connections and schema extraction.
    CSVs are loaded into an in-memory SQLite instance so the same
    SQL execution path works for all three source types.
    """

    def __init__(self):
        # session_id -> { db_type, conn_or_path, schema, in_memory_conn }
        self._sessions: dict[str, dict] = {}


    # Public API
    def connect_sqlite(self, path: str, session_id: Optional[str] = None) -> str:
        """Register a SQLite file and return a session_id."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"SQLite file not found: {path}")
        sid = session_id or str(uuid.uuid4())
        schema = self._extract_sqlite_schema(path)
        self._sessions[sid] = {
            "db_type": "sqlite",
            "path": path,
            "schema": schema,
            "in_memory_conn": None,
        }
        return sid

    def connect_postgresql(self, dsn: str, session_id: Optional[str] = None) -> str:
        #Register a PostgreSQL DSN and return a session_id.
        if not POSTGRES_AVAILABLE:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
        conn = psycopg2.connect(dsn)
        try:
            schema = self._extract_postgres_schema(conn)
        finally:
            conn.close() #closes the PostgreSQL connection after schema extraction
        sid = session_id or str(uuid.uuid4())
        self._sessions[sid] = {
            "db_type": "postgresql",
            "dsn": dsn,
            "schema": schema,
            "in_memory_conn": None,
        }
        return sid

    def ingest_csv(self, file_path: str, session_id: Optional[str] = None) -> tuple[str, int]:
        #Load a CSV into an in-memory SQLite DB. Returns (session_id, row_count).Try common encodings in order — handles Excel/Windows CSVs
        for encoding in ("utf-8", "utf-8-sig", "windows-1252", "latin-1"):
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            raise ValueError("Could not decode CSV with any supported encoding (utf-8, windows-1252, latin-1)")
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        table_name = os.path.splitext(os.path.basename(file_path))[0].lower().replace(" ", "_")

        mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
        df.to_sql(table_name, mem_conn, index=False, if_exists="replace")

        sid = session_id or str(uuid.uuid4())
        schema = self._extract_sqlite_schema_from_conn(mem_conn)
        self._sessions[sid] = {
            "db_type": "csv",
            "source_file": file_path,
            "schema": schema,
            "in_memory_conn": mem_conn,
        }
        return sid, len(df)

    def get_schema(self, session_id: str) -> dict:
        self._require_session(session_id)
        return self._sessions[session_id]["schema"]

    def get_db_type(self, session_id: str) -> str:
        self._require_session(session_id)
        return self._sessions[session_id]["db_type"]

    def execute_query(self, session_id: str, sql: str) -> list[dict]:
        """Execute a SELECT query and return rows as list of dicts."""
        self._require_session(session_id)
        session = self._sessions[session_id]
        db_type = session["db_type"]

        if db_type in ("sqlite", "csv"):
            return self._exec_sqlite(session, sql)
        elif db_type == "postgresql":
            return self._exec_postgres(session, sql)
        else:
            raise ValueError(f"Unknown db_type: {db_type}")

    def close_session(self, session_id: str):
        if session_id in self._sessions:
            session = self._sessions[session_id]
            conn = session.get("in_memory_conn")
            if conn:
                conn.close()
                source = session.get("source_file")
                if source and os.path.exists(source):
                    os.unlink(source)
            del self._sessions[session_id]

    # Schema Extraction
    def _extract_sqlite_schema(self, path: str) -> dict:
        conn = sqlite3.connect(path)
        schema = self._extract_sqlite_schema_from_conn(conn)
        conn.close()
        return schema

    def _extract_sqlite_schema_from_conn(self, conn: sqlite3.Connection) -> dict:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        schema = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info('{table}');")
            cols = [{"name": row[1], "type": row[2]} for row in cursor.fetchall()]
            schema[table] = cols
        return schema

    def _extract_postgres_schema(self, conn) -> dict:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
        """)
        tables = [row["table_name"] for row in cursor.fetchall()]
        schema = {}
        for table in tables:
            cursor.execute("""
                SELECT column_name AS name, data_type AS type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position;
            """, (table,))
            schema[table] = [dict(row) for row in cursor.fetchall()]
        return schema

    # Query Executors
    def _exec_sqlite(self, session: dict, sql: str) -> list[dict]:
        if session["db_type"] == "csv":
            conn = session["in_memory_conn"]
        else:
            conn = sqlite3.connect(session["path"])
            conn.row_factory = sqlite3.Row

        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                return []
            if isinstance(rows[0], sqlite3.Row):
                return [dict(row) for row in rows]
            # For in-memory CSV connections, get column names from description
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]
        finally:
            if session["db_type"] != "csv":
                conn.close()

    def _exec_postgres(self, session: dict, sql: str) -> list[dict]:
        conn = psycopg2.connect(session["dsn"])
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # Helpers
    def _require_session(self, session_id: str):
        if session_id not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found. Connect a database first.")


# Singleton instance shared across the app
db_manager = DBManager()


#-------LLM MANAGER-------#

# Configure Gemini
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# System prompt template
SYSTEM_PROMPT = """IMPORTANT: You must respond with ONLY a valid JSON object. No markdown. No explanation. No code blocks. No natural language. The first character of your response must be {{ and the last must be }}.

You are a business data analyst assistant. Your job is to convert natural language questions into SQL queries and produce visual representation configurations.

You will be given:
1. A database schema (table names, columns, data types)
2. A user question
3. Conversation history for context

You MUST respond with ONLY a valid JSON object — no markdown, no explanation, no backticks.

The JSON must have exactly this structure:
{{
  "can_answer": true | false,
  "clarification_needed": false | true,
  "clarification_message": null | "string explaining what is unclear",
  "sql": "SELECT ... (null if can_answer is false or clarification_needed is true)",
  "chart_config": {{
    "type": "bar" | "line" | "pie" | "scatter" | "area" | "table",
    "title": "Human readable chart title",
    "x_key": "column_name_for_x_axis",
    "y_keys": ["column_name_for_y_axis"],
    "colors": ["#6366f1"],
    "description": "one line description of what this chart shows"
    }}
}}

RULES:
- ONLY generate SELECT statements. Never write INSERT, UPDATE, DELETE, DROP, or any DDL.
- If the question cannot be answered with the available schema, set can_answer=false and sql=null.
- If the question is ambiguous and you need clarification, set clarification_needed=true.
- Do NOT invent columns or tables that are not in the schema.
- Use table aliases for clarity. Limit results to 500 rows unless the user specifies otherwise.
- For aggregations, always alias the result columns clearly (e.g., COUNT(*) AS total_orders).
- Choose the most appropriate chart type:
    * bar: comparisons across categories. The optimal choice when you have long category labels or more than 10 items.
    * column (vertical): for a small number of discrete categories
    * line:  for tracking changes over continuous time, immediately highlight trends, peaks, and troughs.
    * pie: proportions/percentages (use only when ≤8 slices)
    * scatter:  to determine if one continuous variable affects another, or if there are clusters and outliers within a complex dataset.
    * bubble: Use as an extension of the scatter plot that introduces a third continuous variable, represented by the varying area (size) of the data points.
    * area: similar to line graphs, but the area below the line is filled. Use this when the cumulative trends magnitude or volume over time is just as important as the trend itself.
    * table: when raw data is more informative than a chart
    * histograms : for visualizing the frequency distribution of continuous data by grouping it into "bins.", which instantly reveals if data is normally distributed, skewed, or bimodal.
    * network graphs:  to map complex relationships between discrete entities (nodes)
    * treemaps :  space-efficient method for displaying highly complex, hierarchical, part-to-whole relationships using nested rectangles.

Database Schema:
{schema}
"""


# Public interface

def query_to_sql_and_chart(
    session_id: str,
    question: str,
    schema: dict,
) -> dict:
    """
    Sends the question + schema + history to Gemini.
    Returns parsed dict with keys: can_answer, clarification_needed,
    clarification_message, sql, chart_config.
    """
    schema_text = _format_schema(schema)
    system = SYSTEM_PROMPT.format(schema=schema_text)

    model = genai.GenerativeModel("gemini-3.1-flash-lite-preview", system_instruction=system) #passes system instructions into model


    # Build history for multi-turn context
    history = session_manager.get_history(session_id)

    # Add user message to history BEFORE calling model
    session_manager.add_user_message(session_id, question)

    # Build the chat with history
    chat = model.start_chat(history=history)

    # First message carries the system context + question
    response = chat.send_message(f"User question:{question} \n\nRemember: respond with ONLY a valid JSON object, no markdown, no explanation.")
    raw = response.text.strip()

    # Save model response to history
    session_manager.add_model_message(session_id, raw)

    return _parse_llm_response(raw)


# Helpers
def _format_schema(schema: dict) -> str:
    lines = []
    for table, cols in schema.items():
        col_defs = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
        lines.append(f"  Table '{table}': {col_defs}")
    return "\n".join(lines)


def _parse_llm_response(raw: str) -> dict:
    """
    Robustly parse the LLM JSON response.
    Handles accidental markdown code fences.
    """
    # Strip markdown fences if present
    clean = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().rstrip("`").strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        # Attempt to extract JSON object from a larger blob
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except Exception:
                raise ValueError(f"Gemini returned unparseable JSON: {clean[:300]}") from e
        else:
            raise ValueError(f"No JSON found in Gemini response: {clean[:300]}") from e

    # Provide defaults for safety
    data.setdefault("can_answer", True)
    data.setdefault("clarification_needed", False)
    data.setdefault("clarification_message", None)
    data.setdefault("sql", None)
    data.setdefault("chart_config", {
        "type": "table",
        "title": "Query Results",
        "x_key": None,
        "y_keys": [],
        "colors": ["#6366f1"],
        "description": "Raw query results"
    })

    return data
