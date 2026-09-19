"""
Turns bug text into a 384-number vector ("embedding") that captures meaning.
Two bugs that mean the same thing get vectors that point the same way, even if
they use different words — that's what lets us catch duplicates that keyword
search alone would miss.

The model is loaded once and reused. The first run downloads ~90 MB.
"""
from functools import lru_cache

from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"  # small, fast, 384 dimensions


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    # lru_cache makes this run exactly once, the first time it's called.
    return SentenceTransformer(MODEL_NAME)


def embed(text: str):
    """Return a 384-length embedding for a single piece of text."""
    if not text or not text.strip():
        text = " "  # model dislikes empty strings; keep the dimension stable
    # normalize_embeddings=True so cosine similarity behaves nicely (0..1).
    return _model().encode(text, normalize_embeddings=True)


def embed_many(texts: list[str]):
    """Embed a batch of texts at once (much faster than one-by-one)."""
    cleaned = [t if (t and t.strip()) else " " for t in texts]
    return _model().encode(cleaned, normalize_embeddings=True)
