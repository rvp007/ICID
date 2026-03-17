import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import (
    ConnectDBRequest,
    QueryRequest,
    QueryResponse,
    SchemaResponse,
    UploadResponse,
    SessionInfo,
)
from db_manager import db_manager
from session_manager import session_manager
from llm_engine import query_to_sql_and_chart
from sql_safety import validate_sql, UnsafeSQLError

app = FastAPI(title="NL2Dashboard API", version="1.0.0")

# Allow all origins for local dev — tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@app.get("/health") # Health check
def health():
    return {"status": "ok"}


# Connect to an existing DB (SQLite file path or PostgreSQL DSN)
@app.post("/api/connect", response_model=SchemaResponse)
def connect_db(req: ConnectDBRequest):
    try:
        if req.db_type == "sqlite":
            sid = db_manager.connect_sqlite(req.connection_string, req.session_id)
        elif req.db_type == "postgresql":
            sid = db_manager.connect_postgresql(req.connection_string, req.session_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported db_type: {req.db_type}")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection failed: {e}")

    schema = db_manager.get_schema(sid)
    return SchemaResponse(session_id=sid, schema=schema)


# Uploading CSV
@app.post("/api/upload", response_model=UploadResponse)
async def upload_csv(file: UploadFile = File(...)):
    """
    CSV file is ingested into an in-memory SQLite database
    so the same SQL pipeline handles it transparently.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported.")

    dest = UPLOAD_DIR / f"{uuid.uuid4()}_{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        sid, row_count = db_manager.ingest_csv(str(dest), session_id=None)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"CSV parse error: {e}")

    schema = db_manager.get_schema(sid)
    return UploadResponse(
        session_id=sid,
        filename=file.filename,
        schema=schema,
        row_count=row_count,
    )


# Get schema for a session
@app.get("/api/schema/{session_id}", response_model=SchemaResponse)
def get_schema(session_id: str):
    try:
        schema = db_manager.get_schema(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SchemaResponse(session_id=session_id, schema=schema)


# Natural language query (the core endpoint)
@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """
    Main pipeline:
      1. Validate session
      2. Get schema
      3. Call Gemini → SQL + chart config
      4. Validate SQL safety
      5. Execute SQL
      6. Return data + chart config
    """
    # 1. Validate session
    try:
        schema = db_manager.get_schema(req.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found. Connect a database first.")

    # 2. Call LLM
    try:
        llm_result = query_to_sql_and_chart(
            session_id=req.session_id,
            question=req.question,
            schema=schema,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # 3. Handle cases where LLM cannot answer
    if not llm_result.get("can_answer"):
        return QueryResponse(
            session_id=req.session_id,
            question=req.question,
            sql="",
            data=[],
            chart_config={},
            clarification_needed=True,
            clarification_message=(
                llm_result.get("clarification_message") or
                "This question cannot be answered with the available data."
            ),
        )

    # 4. Handle clarification requests
    if llm_result.get("clarification_needed"):
        return QueryResponse(
            session_id=req.session_id,
            question=req.question,
            sql="",
            data=[],
            chart_config={},
            clarification_needed=True,
            clarification_message=llm_result.get("clarification_message"),
        )

    sql = llm_result.get("sql", "")

    # 5. Safety validation
    try:
        sql = validate_sql(sql)
    except UnsafeSQLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 6. Execute SQL
    try:
        data = db_manager.execute_query(req.session_id, sql)
    except Exception as e:
        # Return error gracefully — don't crash the response
        return QueryResponse(
            session_id=req.session_id,
            question=req.question,
            sql=sql,
            data=[],
            chart_config=llm_result.get("chart_config", {}),
            error=f"SQL execution failed: {e}",
        )

    return QueryResponse(
        session_id=req.session_id,
        question=req.question,
        sql=sql,
        data=data,
        chart_config=llm_result.get("chart_config", {}),
    )


# Session management

@app.get("/api/session/{session_id}", response_model=SessionInfo)
def session_info(session_id: str):
    try:
        schema = db_manager.get_schema(session_id)
        db_type = db_manager.get_db_type(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")
    return SessionInfo(
        session_id=session_id,
        db_type=db_type,
        schema=schema,
        history_length=session_manager.history_length(session_id),
    )


@app.delete("/api/session/{session_id}")
def delete_session(session_id: str):
    db_manager.close_session(session_id)
    session_manager.clear(session_id)
    return {"deleted": session_id}


@app.delete("/api/session/{session_id}/history")
def clear_history(session_id: str):
    session_manager.clear(session_id)
    return {"cleared": session_id}


# Dev entry point

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)