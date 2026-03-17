import json
import re
import os
import google.generativeai as genai
from session_manager import session_manager

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
    session_manager.add_user_message(session_id, question)
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
