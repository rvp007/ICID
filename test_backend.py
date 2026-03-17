import requests
import json
import os
import sys

# ── CONFIG — update these paths ──────────────────────────────────
BASE_URL    = "http://localhost:8000"
SQLITE_PATH = "./sales.db"          # path to your SQLite file
CSV_PATH    = "./inventory.csv"     # path to your CSV file
# ─────────────────────────────────────────────────────────────────

PASS  = "\033[92m  PASS\033[0m"
FAIL  = "\033[91m  FAIL\033[0m"
WARN  = "\033[93m  WARN\033[0m"
HEAD  = "\033[96m{}\033[0m"

results = {"pass": 0, "fail": 0, "warn": 0}


def check(label: str, condition: bool, detail: str = "", warn_only: bool = False):
    if condition:
        print(f"{PASS} {label}")
        results["pass"] += 1
    elif warn_only:
        print(f"{WARN} {label}  ← {detail}")
        results["warn"] += 1
    else:
        print(f"{FAIL} {label}  ← {detail}")
        results["fail"] += 1


def section(title: str):
    print(f"\n{HEAD.format('━' * 60)}")
    print(HEAD.format(f"  {title}"))
    print(HEAD.format('━' * 60))


def post(path, **kwargs):
    return requests.post(f"{BASE_URL}{path}", timeout=60, **kwargs)

def get(path, **kwargs):
    return requests.get(f"{BASE_URL}{path}", timeout=15, **kwargs)

def delete(path, **kwargs):
    return requests.delete(f"{BASE_URL}{path}", timeout=15, **kwargs)


# ══════════════════════════════════════════════════════════════════
# 1. HEALTH CHECK
# ══════════════════════════════════════════════════════════════════
section("1. Health Check")

try:
    r = get("/health")
    check("Server is reachable", r.status_code == 200, f"status={r.status_code}")
    check("Returns ok status", r.json().get("status") == "ok", str(r.json()))
except Exception as e:
    check("Server is reachable", False, str(e))
    print("\n  ❌ Server is not running. Start it with:")
    print("     python -m uvicorn main:app --reload --port 8000\n")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# 2. SQLite CONNECTION
# ══════════════════════════════════════════════════════════════════
section("2. SQLite Connection")

sqlite_session_id = None

r = post("/api/connect", json={"db_type": "sqlite", "connection_string": SQLITE_PATH})
check("SQLite connect returns 200",         r.status_code == 200,             str(r.text[:200]))
check("Response has session_id",            "session_id" in r.json(),         str(r.json()))
check("Response has schema",                "schema" in r.json(),             str(r.json()))

if r.status_code == 200:
    sqlite_session_id = r.json()["session_id"]
    schema = r.json()["schema"]
    check("Schema contains tables",         len(schema) > 0,                  "schema is empty")
    check("Schema has 'sales' table",       "sales" in schema,                str(list(schema.keys())))
    check("Sales table has columns",        len(schema.get("sales", [])) > 0, "no columns found")
    print(f"     Session ID: {sqlite_session_id}")
    print(f"     Tables: {list(schema.keys())}")

# Bad path test
r2 = post("/api/connect", json={"db_type": "sqlite", "connection_string": "/nonexistent/path.db"})
check("Non-existent file returns 404",      r2.status_code == 404,            f"got {r2.status_code}")

# Bad db_type test
r3 = post("/api/connect", json={"db_type": "oracle", "connection_string": "whatever"})
check("Unsupported db_type returns 400",    r3.status_code == 400,            f"got {r3.status_code}")


# ══════════════════════════════════════════════════════════════════
# 3. CSV UPLOAD
# ══════════════════════════════════════════════════════════════════
section("3. CSV Upload")

csv_session_id = None

with open(CSV_PATH, "rb") as f:
    r = post("/api/upload", files={"file": ("inventory.csv", f, "text/csv")})

check("CSV upload returns 200",             r.status_code == 200,             str(r.text[:200]))
check("Response has session_id",            "session_id" in r.json(),         str(r.json()))
check("Response has row_count",             "row_count" in r.json(),          str(r.json()))
check("Row count is correct",               r.json().get("row_count") == 5,   f"got {r.json().get('row_count')}")

if r.status_code == 200:
    csv_session_id = r.json()["session_id"]
    schema = r.json().get("schema", {})
    check("CSV schema extracted",           len(schema) > 0,                  "schema empty")
    print(f"     Session ID: {csv_session_id}")
    print(f"     Tables: {list(schema.keys())}")

# Non-CSV upload test
r2 = post("/api/upload", files={"file": ("test.txt", b"hello", "text/plain")})
check("Non-CSV upload returns 400",         r2.status_code == 400,            f"got {r2.status_code}")


# ══════════════════════════════════════════════════════════════════
# 4. SCHEMA ENDPOINT
# ══════════════════════════════════════════════════════════════════
section("4. Schema Retrieval")

if sqlite_session_id:
    r = get(f"/api/schema/{sqlite_session_id}")
    check("GET /schema returns 200",        r.status_code == 200,             str(r.text[:200]))
    check("Schema matches connect response",len(r.json().get("schema", {})) > 0, "empty schema")

r = get("/api/schema/nonexistent-session-id")
check("Invalid session_id returns 404",     r.status_code == 404,             f"got {r.status_code}")


# ══════════════════════════════════════════════════════════════════
# 5. NATURAL LANGUAGE QUERIES — ACCURACY
# ══════════════════════════════════════════════════════════════════
section("5. NL Queries — Accuracy (Evaluation: 40pts)")

def run_query(session_id, question, label):
    r = post("/api/query", json={"session_id": session_id, "question": question})
    result = r.json() if r.status_code == 200 else {}
    print(f"\n  ▶ [{label}] \"{question}\"")
    if r.status_code != 200:
        check(f"  Request succeeded",       False, f"HTTP {r.status_code}: {r.text[:200]}")
        return {}
    check(f"  HTTP 200",                    True)
    check(f"  Has SQL",                     bool(result.get("sql")),          "sql is empty")
    check(f"  Has data",                    len(result.get("data", [])) > 0,  "data is empty")
    check(f"  Has chart_config",            bool(result.get("chart_config")), "chart_config missing")
    if result.get("sql"):
        print(f"     SQL: {result['sql'][:120]}")
    if result.get("chart_config"):
        cfg = result["chart_config"]
        print(f"     Chart: type={cfg.get('type')} | x={cfg.get('x_key')} | y={cfg.get('y_keys')}")
    if result.get("data"):
        print(f"     Rows returned: {len(result['data'])}")
    return result

if sqlite_session_id:
    # Simple retrieval
    run_query(sqlite_session_id, "Show me all sales", "SIMPLE SELECT")

    # Aggregation
    r1 = run_query(sqlite_session_id, "What is the total revenue per region?", "AGGREGATION")
    if r1.get("chart_config"):
        check("  Chart type is bar (category comparison)",
              r1["chart_config"].get("type") == "bar",
              f"got {r1['chart_config'].get('type')}", warn_only=True)

    # Time series
    r2 = run_query(sqlite_session_id, "Show me monthly revenue trend over time", "TIME SERIES")
    if r2.get("chart_config"):
        check("  Chart type is line (time trend)",
              r2["chart_config"].get("type") in ("line", "area"),
              f"got {r2['chart_config'].get('type')}", warn_only=True)

    # Multi-table join
    run_query(sqlite_session_id, "Show me total revenue and customer count by region", "JOIN")

    # Top-N
    run_query(sqlite_session_id, "What are the top 3 products by total revenue?", "TOP-N")

    # COUNT
    run_query(sqlite_session_id, "How many sales records are there?", "COUNT")

    # Pie chart trigger
    r3 = run_query(sqlite_session_id, "What percentage of revenue comes from each region?", "PIE CHART")
    if r3.get("chart_config"):
        check("  Chart type is pie (proportion)",
              r3["chart_config"].get("type") == "pie",
              f"got {r3['chart_config'].get('type')}", warn_only=True)

if csv_session_id:
    run_query(csv_session_id, "Show me total stock by category", "CSV QUERY")
    run_query(csv_session_id, "What is the most expensive product?", "CSV MAX")


# ══════════════════════════════════════════════════════════════════
# 6. CONVERSATIONAL MEMORY (follow-up questions)
# ══════════════════════════════════════════════════════════════════
section("6. Conversational Memory — Follow-up Questions")

if sqlite_session_id:
    print("\n  ▶ Asking initial question...")
    r1 = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "Show me revenue by region"
    })
    check("Initial query succeeds",         r1.status_code == 200,            str(r1.text[:200]))

    print("  ▶ Asking follow-up (should remember context)...")
    r2 = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "Now filter that to only show the North region"
    })
    check("Follow-up query succeeds",       r2.status_code == 200,            str(r2.text[:200]))
    if r2.status_code == 200:
        data = r2.json().get("data", [])
        sql  = r2.json().get("sql", "")
        check("Follow-up SQL contains WHERE or filter",
              "north" in sql.lower() or "WHERE" in sql.upper(),
              f"SQL: {sql[:150]}", warn_only=True)
        check("Follow-up returns data",     len(data) > 0,                    "no rows returned")

    # Check session history length increased
    r3 = get(f"/api/session/{sqlite_session_id}")
    if r3.status_code == 200:
        history_len = r3.json().get("history_length", 0)
        check("History is being accumulated", history_len >= 2,               f"history_length={history_len}")


# ══════════════════════════════════════════════════════════════════
# 7. ERROR HANDLING & HALLUCINATION PREVENTION
# ══════════════════════════════════════════════════════════════════
section("7. Error Handling & Hallucination Prevention (Evaluation: 40pts)")

if sqlite_session_id:
    # Completely impossible question
    print("\n  ▶ Asking unanswerable question...")
    r = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "What is the weather forecast for New York tomorrow?"
    })
    check("Returns 200 (not a crash)",      r.status_code == 200,             str(r.text[:200]))
    if r.status_code == 200:
        result = r.json()
        check("clarification_needed=True or can_answer=False",
              result.get("clarification_needed") or not result.get("can_answer", True),
              f"got: can_answer={result.get('can_answer')} clarification={result.get('clarification_needed')}",
              warn_only=True)
        check("Has clarification message",
              bool(result.get("clarification_message")),
              "clarification_message is empty", warn_only=True)

    # Vague question
    print("\n  ▶ Asking vague question...")
    r = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "show me stuff"
    })
    check("Vague question returns 200",     r.status_code == 200,             str(r.text[:200]))
    if r.status_code == 200:
        result = r.json()
        answered = bool(result.get("data")) or result.get("clarification_needed")
        check("Handles vague query gracefully (data or clarification)",
              answered, "neither data nor clarification returned", warn_only=True)

    # Non-existent column
    print("\n  ▶ Asking about non-existent column...")
    r = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "What is the total profit margin percentage by store location?"
    })
    check("Non-existent column query returns 200", r.status_code == 200,     str(r.text[:200]))
    if r.status_code == 200:
        result = r.json()
        check("System declines or asks for clarification (no hallucination)",
              result.get("clarification_needed") or not result.get("can_answer", True) or result.get("error"),
              f"data returned: {result.get('data', [])[:1]}", warn_only=True)

# Missing session ID
print("\n  ▶ Querying with invalid session...")
r = post("/api/query", json={"session_id": "fake-session-xyz", "question": "show me data"})
check("Invalid session returns 404",        r.status_code == 404,             f"got {r.status_code}")

# Missing fields in request body
print("\n  ▶ Sending malformed request body...")
r = post("/api/query", json={"question": "show me data"})  # missing session_id
check("Missing session_id returns 422",     r.status_code == 422,             f"got {r.status_code}")


# ══════════════════════════════════════════════════════════════════
# 8. SQL SAFETY
# ══════════════════════════════════════════════════════════════════
section("8. SQL Safety Validator")

# We test the validator directly via a mocked prompt scenario
# by checking the endpoint rejects injected SQL via prompt injection
if sqlite_session_id:
    dangerous_prompts = [
        "DROP TABLE sales",
        "DELETE FROM sales WHERE 1=1",
        "INSERT INTO sales VALUES (999,'2024-01-01','X','Y',1,1.0)",
        "UPDATE sales SET revenue=0",
        "; DROP TABLE sales; --",
    ]
    for prompt in dangerous_prompts:
        r = post("/api/query", json={"session_id": sqlite_session_id, "question": prompt})
        # Should either get clarification_needed or 400, never execute
        safe = (
            r.status_code in (200, 400) and
            not (r.status_code == 200 and (r.json().get("error") or "").startswith("SQL execution"))
        )
        check(f"  Injection blocked: '{prompt[:40]}'", safe,
              f"status={r.status_code} error={r.json().get('error','') if r.status_code==200 else ''}")


# ══════════════════════════════════════════════════════════════════
# 9. SESSION MANAGEMENT
# ══════════════════════════════════════════════════════════════════
section("9. Session Management")

if sqlite_session_id:
    # Get session info
    r = get(f"/api/session/{sqlite_session_id}")
    check("GET /session returns 200",       r.status_code == 200,             str(r.text[:200]))
    if r.status_code == 200:
        info = r.json()
        check("Session has db_type",        "db_type" in info,                str(info))
        check("Session has history_length", "history_length" in info,         str(info))
        print(f"     db_type={info.get('db_type')} history_length={info.get('history_length')}")

    # Clear history
    r = delete(f"/api/session/{sqlite_session_id}/history")
    check("DELETE history returns 200",     r.status_code == 200,             str(r.text))

    r = get(f"/api/session/{sqlite_session_id}")
    if r.status_code == 200:
        check("History is 0 after clear",   r.json().get("history_length") == 0,
              f"got {r.json().get('history_length')}")

if csv_session_id:
    # Delete full session
    r = delete(f"/api/session/{csv_session_id}")
    check("DELETE session returns 200",     r.status_code == 200,             str(r.text))

    # Verify it's gone
    r2 = get(f"/api/session/{csv_session_id}")
    check("Deleted session returns 404",    r2.status_code == 404,            f"got {r2.status_code}")

    # Query on deleted session should fail cleanly
    r3 = post("/api/query", json={"session_id": csv_session_id, "question": "show data"})
    check("Query on deleted session returns 404", r3.status_code == 404,     f"got {r3.status_code}")


# ══════════════════════════════════════════════════════════════════
# 10. CHART CONFIG VALIDATION
# ══════════════════════════════════════════════════════════════════
section("10. Chart Config Structure Validation")

required_chart_keys = {"type", "title", "x_key", "y_keys", "colors", "description"}
valid_chart_types   = {"bar","line","area","pie","scatter","bubble","table","histogram"}

if sqlite_session_id:
    r = post("/api/query", json={
        "session_id": sqlite_session_id,
        "question": "Show total revenue by product"
    })
    if r.status_code == 200 and r.json().get("chart_config"):
        cfg = r.json()["chart_config"]
        missing = required_chart_keys - set(cfg.keys())
        check("chart_config has all required keys",     len(missing) == 0,    f"missing: {missing}")
        check("chart_config.type is a valid type",
              cfg.get("type") in valid_chart_types,
              f"got '{cfg.get('type')}'")
        check("chart_config.y_keys is a list",          isinstance(cfg.get("y_keys"), list),
              f"got {type(cfg.get('y_keys'))}")
        check("chart_config.colors is a list",          isinstance(cfg.get("colors"), list),
              f"got {type(cfg.get('colors'))}")
        check("chart_config.title is non-empty",        bool(cfg.get("title")),   "title is empty")


# ══════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════
total = results["pass"] + results["fail"] + results["warn"]
print(f"\n{'━'*60}")
print(f"  RESULTS:  "
      f"\033[92m{results['pass']} passed\033[0m  "
      f"\033[91m{results['fail']} failed\033[0m  "
      f"\033[93m{results['warn']} warnings\033[0m  "
      f"({total} total)")
print(f"{'━'*60}")

if results["fail"] == 0:
    print("\n  ✅ All tests passed! Backend is solid.\n")
elif results["fail"] <= 3:
    print("\n  ⚠️  A few failures — check the FAIL lines above.\n")
else:
    print("\n  ❌ Multiple failures — review the output above carefully.\n")
