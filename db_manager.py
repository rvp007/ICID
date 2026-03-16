import sqlite3
import pandas as pd
import uuid
import os
from typing import Optional

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        """Register a PostgreSQL DSN and return a session_id."""
        if not POSTGRES_AVAILABLE:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
        conn = psycopg2.connect(dsn)
        sid = session_id or str(uuid.uuid4())
        schema = self._extract_postgres_schema(conn)
        self._sessions[sid] = {
            "db_type": "postgresql",
            "dsn": dsn,
            "schema": schema,
            "in_memory_conn": None,
        }
        return sid

    def ingest_csv(self, file_path: str, session_id: Optional[str] = None) -> tuple[str, int]:
        """
        Load a CSV into an in-memory SQLite DB.
        Returns (session_id, row_count).
        """
        # Try common encodings in order — handles Excel/Windows CSVs
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
            conn = self._sessions[session_id].get("in_memory_conn")
            if conn:
                conn.close()
            del self._sessions[session_id]

    # ------------------------------------------------------------------
    # Schema Extraction
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Query Executors
    # ------------------------------------------------------------------

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
            if isinstance(rows[0], sqlite3.Row) if rows else False:
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_session(self, session_id: str):
        if session_id not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found. Connect a database first.")


# Singleton instance shared across the app
db_manager = DBManager()
