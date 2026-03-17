import re

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
