"""
Sync a tester's bugs from Zoho into our database.

Flow: read this user's token -> fetch their bugs -> embed each bug ->
store (insert or update) scoped to this user. Every write carries user_id,
so one tester's sync can never touch another tester's rows.
"""
from app.db import get_conn
from app.embeddings import embed_many
from app import zoho


def get_credentials(user_id: str) -> dict:
    """Load this user's Zoho token + portal/project from the database."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT access_token, refresh_token, portal_id, project_id
            FROM zoho_credentials
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(
            f"No Zoho credentials for user {user_id}. Run seed.py first."
        )
    return row


def refresh_credentials(user_id: str, refresh_token: str) -> str:
    """
    Get a new access token and store it, returning the new token.

    Access tokens last an hour; refresh tokens don't expire. Doing this
    on demand is what keeps the tool usable day to day without going back
    to the API console for a new grant code.
    """
    payload = zoho.refresh_access_token(refresh_token)
    access_token = payload["access_token"]
    expires_in = int(payload.get("expires_in") or 3600)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE zoho_credentials
               SET access_token = %s,
                   expires_at   = now() + make_interval(secs => %s),
                   updated_at   = now()
             WHERE user_id = %s
            """,
            (access_token, expires_in, user_id),
        )
    return access_token


def sync_user_bugs(user_id: str) -> dict:
    """Fetch and store all bugs for one user. Returns a small summary."""
    creds = get_credentials(user_id)

    # Record that a sync started, so a crash is visible in sync_runs.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_runs (user_id, status)
            VALUES (%s, 'running') RETURNING id
            """,
            (user_id,),
        )
        run_id = cur.fetchone()["id"]

    try:
        try:
            raw_bugs = zoho.fetch_all_bugs(
                creds["access_token"], creds["portal_id"], creds["project_id"]
            )
        except zoho.ZohoError as exc:
            # Access tokens last an hour. Rather than failing and sending the
            # user back to the API console, swap in a fresh one and retry once.
            if exc.status_code != 401 or not creds.get("refresh_token"):
                raise
            access_token = refresh_credentials(user_id, creds["refresh_token"])
            raw_bugs = zoho.fetch_all_bugs(
                access_token, creds["portal_id"], creds["project_id"]
            )

        normalized = [zoho.normalize_bug(b) for b in raw_bugs]

        # Embed titles+descriptions in one batch (fast).
        texts = [f"{b['title']} {b['description'] or ''}" for b in normalized]
        embeddings = embed_many(texts) if texts else []

        with get_conn() as conn:
            with conn.cursor() as cur:
                for bug, emb in zip(normalized, embeddings):
                    # UPSERT: insert new bugs, refresh existing ones. The
                    # UNIQUE(user_id, zoho_issue_id) constraint drives the
                    # conflict target, so re-syncing never creates duplicates.
                    cur.execute(
                        """
                        INSERT INTO bugs (user_id, zoho_project_id, zoho_issue_id,
                                          zoho_issue_key, title, description,
                                          status, severity, embedding, synced_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (user_id, zoho_issue_id) DO UPDATE SET
                            zoho_issue_key = EXCLUDED.zoho_issue_key,
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            status = EXCLUDED.status,
                            severity = EXCLUDED.severity,
                            embedding = EXCLUDED.embedding,
                            synced_at = now()
                        """,
                        (
                            user_id,
                            creds["project_id"],
                            bug["zoho_issue_id"],
                            bug["zoho_issue_key"],
                            bug["title"],
                            bug["description"],
                            bug["status"],
                            bug["severity"],
                            emb,
                        ),
                    )

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sync_runs
                SET status = 'success', finished_at = now(), bugs_fetched = %s
                WHERE id = %s
                """,
                (len(normalized), run_id),
            )

        return {"synced": len(normalized), "run_id": str(run_id)}

    except Exception as exc:
        # Record the failure so it's debuggable, then re-raise for the API layer.
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sync_runs
                SET status = 'failed', finished_at = now(), error = %s
                WHERE id = %s
                """,
                (str(exc)[:1000], run_id),
            )
        raise
