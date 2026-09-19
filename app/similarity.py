"""
The duplicate detector.

It combines two signals:
  1. SEMANTIC  — does the new bug MEAN the same as an existing one?
                 (pgvector cosine similarity over embeddings)
  2. KEYWORD   — does it use the same WORDS?
                 (Postgres full-text ts_rank_cd — the "BM25-style" half)

We retrieve the semantically-closest candidates for THIS user, blend the two
scores, then (by default) hand the shortlist to a cross-encoder reranker —
see app/rerank.py — which reads each pair together and is much better at
heavy paraphrase. Every query is scoped by user_id, so a tester only ever
matches against their own bugs.

Note: ts_rank_cd is Postgres's built-in text ranking, not literally BM25. It
plays the same role well here; if you later want true BM25 you can swap this
one query without touching anything else.
"""
from app.db import get_conn
from app.embeddings import embed
from app.config import settings
from app.rerank import rerank_scores


def find_duplicates(
    user_id: str,
    title: str,
    description: str = "",
    project_id: str | None = None,
    top_k: int = 5,
) -> dict:
    """
    Return the most likely duplicate bugs for a proposed new bug.
    """
    query_text = f"{title} {description}".strip()
    query_embedding = embed(query_text)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                zoho_issue_id,
                zoho_issue_key,
                zoho_project_id,
                title,
                description,
                status,
                severity,
                1 - (embedding <=> %(emb)s) AS semantic_score,
                ts_rank_cd(
                    to_tsvector('english', title || ' ' || coalesce(description, '')),
                    plainto_tsquery('english', %(qtext)s)
                ) AS keyword_score
            FROM bugs
            WHERE user_id = %(uid)s
              -- cast needed: a bare NULL parameter leaves Postgres unable to
              -- infer the type, which errors with AmbiguousParameter
              AND (%(pid)s::text IS NULL OR zoho_project_id = %(pid)s::text)
            ORDER BY embedding <=> %(emb)s          -- nearest by meaning first
            LIMIT 20
            """,
            {
                "emb": query_embedding,
                "qtext": query_text,
                "uid": user_id,
                "pid": project_id,
            },
        )
        candidates = cur.fetchall()

    if not candidates:
        return {"is_duplicate": False, "matches": []}

    # Normalize keyword scores to 0..1 (ts_rank_cd is unbounded) so the two
    # signals are on the same scale before we blend them.
    max_kw = max((c["keyword_score"] or 0.0) for c in candidates)

    # A reworded duplicate shares no words, so every keyword score can be 0.
    # Blending then caps the final score at SEMANTIC_WEIGHT (0.65) — below the
    # 0.72 threshold no matter how perfect the semantic match. When there is no
    # keyword signal at all, score on meaning alone instead of silently making
    # the threshold unreachable.
    keyword_signal = max_kw > 0

    # Second stage: the cross-encoder judges each (query, candidate) pair
    # directly. Only ~20 pairs, so it's cheap here even though it would be far
    # too slow to run across every stored bug.
    rerank = None
    if settings.rerank_enabled:
        candidate_texts = [
            f"{c['title']} {c['description'] or ''}".strip() for c in candidates
        ]
        rerank = rerank_scores(query_text, candidate_texts)

    matches = []
    for i, c in enumerate(candidates):
        semantic = float(c["semantic_score"] or 0.0)
        keyword = float(c["keyword_score"] or 0.0) / max_kw if keyword_signal else 0.0
        if keyword_signal:
            first_stage = (
                settings.semantic_weight * semantic
                + settings.keyword_weight * keyword
            )
        else:
            first_stage = semantic

        # The reranker gets most of the say, but the first-stage score keeps
        # a vote so an exact-keyword hit (an error code, say) still counts.
        if rerank is not None:
            rerank_score = rerank[i]
            final = (
                settings.rerank_weight * rerank_score
                + (1 - settings.rerank_weight) * first_stage
            )
        else:
            rerank_score = None
            final = first_stage

        matches.append(
            {
                "zoho_issue_id": c["zoho_issue_id"],
                # The browser extension builds a Zoho UI link from these two.
                "zoho_issue_key": c["zoho_issue_key"],
                "zoho_project_id": c["zoho_project_id"],
                "title": c["title"],
                "status": c["status"],
                "severity": c["severity"],
                "semantic_score": round(semantic, 3),
                "keyword_score": round(keyword, 3),
                "rerank_score": round(rerank_score, 3) if rerank_score is not None else None,
                "final_score": round(final, 3),
                "verdict": _verdict(final, semantic),
            }
        )

    # Best matches first.
    matches.sort(key=lambda m: m["final_score"], reverse=True)
    matches = matches[:top_k]

    # Keep this in step with _verdict: a match labelled "likely duplicate" via
    # the high-semantic escape hatch must also set the top-level flag, or the
    # UI shows a duplicate warning next to is_duplicate = false.
    is_duplicate = any(m["verdict"] == "likely duplicate" for m in matches)
    return {"is_duplicate": is_duplicate, "matches": matches}


def _verdict(final: float, semantic: float) -> str:
    """Human-readable label for a single match."""
    if final >= settings.duplicate_threshold or semantic >= 0.85:
        return "likely duplicate"
    if final >= settings.duplicate_threshold - 0.15:
        return "possible duplicate"
    return "probably different"
