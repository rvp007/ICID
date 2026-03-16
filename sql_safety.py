import re

# Destructive or dangerous SQL keywords we never allow
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


class UnsafeSQLError(Exception):
    pass


def validate_sql(sql: str) -> str:
    """
    Raises UnsafeSQLError if the SQL contains any destructive operation.
    Returns the cleaned SQL string on success.
    """
    sql = sql.strip().rstrip(";")

    match = _BLOCKED_RE.search(sql)
    if match:
        raise UnsafeSQLError(
            f"Query contains a blocked operation: '{match.group()}'. "
            "Only SELECT statements are permitted."
        )

    # Must start with SELECT (after stripping comments/whitespace)
    first_token = re.sub(r"--.*?\n|/\*.*?\*/", "", sql, flags=re.DOTALL).strip()
    if not first_token.upper().startswith("SELECT") and not first_token.upper().startswith("WITH"):
        raise UnsafeSQLError(
            "Only SELECT (and WITH ... SELECT) queries are allowed."
        )

    return sql
