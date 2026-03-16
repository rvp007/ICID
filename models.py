from pydantic import BaseModel
from typing import Optional, Any


class ConnectDBRequest(BaseModel):
    db_type: str            # "sqlite" | "postgresql"
    connection_string: str  # file path for sqlite, DSN for postgres
    session_id: Optional[str] = None


class QueryRequest(BaseModel):
    session_id: str
    question: str


class QueryResponse(BaseModel):
    session_id: str
    question: str
    sql: str
    data: list[dict]
    chart_config: dict
    clarification_needed: bool = False
    clarification_message: Optional[str] = None
    error: Optional[str] = None


class SchemaResponse(BaseModel):
    session_id: str
    schema: dict  # { table_name: [{ name, type }] }


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    schema: dict
    row_count: int


class SessionInfo(BaseModel):
    session_id: str
    db_type: str
    schema: dict
    history_length: int
