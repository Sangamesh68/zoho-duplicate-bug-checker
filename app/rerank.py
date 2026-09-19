"""
Second-stage scoring: a cross-encoder that reads two bug texts TOGETHER.

The embedding search in similarity.py is fast because it scores each bug on
its own vector, but that also caps how well it handles heavy paraphrase —
"vacation days go into the minus" vs "leave balance shows negative" scored
only 0.44 there. A cross-encoder sees both texts in one pass and judges the
pair directly, which is far more accurate. It's too slow to run over every
bug, so we only apply it to the ~20 nearest candidates the vector search
already found. Fast retrieval, accurate ranking.

The default model is trained on STS-B: "how similar in meaning are these two
sentences", scored 0..1, which slots straight into the existing threshold.
First run downloads ~330 MB. Toggle with RERANK_ENABLED in .env.
"""
from functools import lru_cache

from sentence_transformers import CrossEncoder

from app.config import settings


@lru_cache(maxsize=1)
def _model() -> CrossEncoder:
    return CrossEncoder(settings.rerank_model)


def rerank_scores(query: str, candidates: list[str]) -> list[float]:
    """
    Probability (0..1) that each candidate is a duplicate of the query.
    Returned in the same order as `candidates`.
    """
    if not candidates:
        return []
    pairs = [(query, c if c.strip() else " ") for c in candidates]
    scores = _model().predict(pairs)
    return [float(s) for s in scores]
