"""MemoryAgent - episodic recall over a user's past advice exchanges.

Most personalization knobs in KindCaddy are aggregate (handicap, miss tendencies,
fatigue signals).  This tool adds *episodic* memory: when the golfer asks a
question similar to one they've asked in a previous round, the caddy can
surface what it said before.  The lookup is a brute-force cosine search over
embeddings stored in ``round_messages`` — fine for SQLite at our volumes and
swappable for sqlite-vec / FAISS later.

The agent is intentionally **best-effort**: any embedding/network failure
returns no hits rather than blocking the live advice path.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional, TYPE_CHECKING

from .base import Alert

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState

log = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "text-embedding-3-small"
MAX_QA_CHARS = 220   # truncate each cached Q/A so we don't blow the prompt budget
MIN_QUERY_CHARS = 12  # below this, embedding is rarely informative


class MemoryAgent:
    """Retrieves the most semantically similar past prompts and replies.

    Construction parameters drive a single user's recall surface:

    * ``user_id``: scopes lookups to one golfer's history.
    * ``round_id``: the *current* round's id — excluded from results so we
      don't echo something said five seconds ago.
    * ``embed_fn``: optional callable that maps text → list[float]. When
      ``None`` the agent uses :func:`embed_with_openai`. Passing a custom
      embedder lets the private (Qwen/Ollama) deployment use a local model.
    * ``top_k`` / ``min_similarity``: tuning knobs for retrieval quality.

    Implements the AgentTool protocol but ``check()`` is a no-op — memory is
    pulled on demand by the caddy, not as a periodic alert.
    """

    name: str = "memory"

    def __init__(
        self,
        user_id: Optional[str],
        round_id: Optional[str] = None,
        embed_fn: Optional[Callable[[str], Optional[list[float]]]] = None,
        top_k: int = 3,
        min_similarity: float = 0.78,
        embed_model: str = DEFAULT_EMBED_MODEL,
    ) -> None:
        self.user_id = user_id
        self.round_id = round_id
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.embed_model = embed_model
        self._embed_fn = embed_fn or self._default_embed_fn()
        self._last_hits: list[dict] = []

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """No proactive alerts — memory is consumed by the caddy on demand."""
        return None

    def reset(self) -> None:
        self._last_hits = []

    def set_round_id(self, round_id: Optional[str]) -> None:
        """Update the active-round filter without rebuilding the tool."""
        self.round_id = round_id

    def execute(self, params: dict) -> dict:
        """Return up to ``top_k`` past Q/A pairs relevant to ``params['user_text']``.

        Result shape::

            {"hits": [{"user_text", "assistant_text", "hole", "round_id",
                       "similarity", "created_at"}, ...]}

        Returns an empty list when the user has opted out, the query is too
        short to embed, the embedder errors, or no past prompt clears the
        similarity threshold.
        """
        from kindcaddy import db

        text = (params.get("user_text") or "").strip()
        if not text or len(text) < MIN_QUERY_CHARS:
            self._last_hits = []
            return {"hits": []}
        if not self.user_id:
            self._last_hits = []
            return {"hits": []}
        try:
            if not db.is_memory_enabled(self.user_id):
                self._last_hits = []
                return {"hits": []}
        except Exception:
            log.debug("memory toggle lookup failed", exc_info=True)

        try:
            vec = self._embed_fn(text)
        except Exception:
            log.warning("memory embedding call failed", exc_info=True)
            vec = None
        if not vec:
            self._last_hits = []
            return {"hits": []}

        try:
            hits = db.search_user_messages_by_embedding(
                self.user_id,
                vec,
                top_k=self.top_k,
                min_similarity=self.min_similarity,
                exclude_round_id=self.round_id,
            )
        except Exception:
            log.warning("memory similarity search failed", exc_info=True)
            hits = []

        self._last_hits = hits
        return {"hits": hits}

    def render_prompt_block(self, hits: Optional[list[dict]] = None) -> str:
        """Render hits as a prompt-friendly bullet list. Empty input → ``''``."""
        chosen = hits if hits is not None else self._last_hits
        if not chosen:
            return ""
        lines: list[str] = []
        for h in chosen:
            user_text = _truncate(h.get("user_text") or "")
            asst_text = _truncate(h.get("assistant_text") or "")
            hole = h.get("hole")
            hole_str = f" (hole {hole})" if hole else ""
            lines.append(f"- Past Q{hole_str}: \"{user_text}\"")
            if asst_text:
                lines.append(f"  You said: \"{asst_text}\"")
        return "\n".join(lines)

    def embed_text(self, text: str) -> Optional[list[float]]:
        """Public hook used by the live advice path to embed a stored prompt."""
        if not text or len(text.strip()) < MIN_QUERY_CHARS:
            return None
        try:
            return self._embed_fn(text)
        except Exception:
            log.debug("embedding call failed", exc_info=True)
            return None

    def _default_embed_fn(self) -> Callable[[str], Optional[list[float]]]:
        """Pick the cheapest/safest available embedder.

        * If ``OPENAI_API_KEY`` is set, use OpenAI's small embedding model.
        * Otherwise return a stub that always yields ``None`` — memory then
          degrades to "no hits" silently.
        """
        if os.environ.get("OPENAI_API_KEY"):
            model = self.embed_model
            return lambda txt: embed_with_openai(txt, model=model)
        return lambda _txt: None


def embed_with_openai(text: str, model: str = DEFAULT_EMBED_MODEL) -> Optional[list[float]]:
    """Embed ``text`` with OpenAI. Returns ``None`` on any failure."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = client.embeddings.create(model=model, input=text)
        data = resp.data
        if not data:
            return None
        return list(data[0].embedding)
    except Exception:
        log.debug("OpenAI embedding call failed", exc_info=True)
        return None


def _truncate(text: str, limit: int = MAX_QA_CHARS) -> str:
    """Single-line, length-capped version of ``text`` for prompt injection."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"
