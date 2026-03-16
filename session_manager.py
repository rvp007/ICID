from collections import defaultdict
from typing import Optional


class SessionManager:
    """
    Maintains per-session chat history so the LLM can handle
    follow-up questions that refine or filter previous results.

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
            # Always keep pairs (user + model), so trim from front by 2
            self._history[session_id] = history[-self.MAX_HISTORY:]


# Singleton
session_manager = SessionManager()
