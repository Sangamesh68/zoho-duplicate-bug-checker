"""
Create the ONE local development user and store its Zoho Self Client token.

This is the local-mode stand-in for the hosted OAuth login. It reads the
values from your .env and writes them into the same tables the OAuth callback
will write to later — so when you switch to hosted mode, the rest of the app
doesn't change.

Run:  python seed.py
"""
from app import zoho
from app.config import settings
from app.db import get_conn


def main():
    if not settings.zoho_access_token or "paste-" in settings.zoho_access_token:
        raise SystemExit(
            "ZOHO_ACCESS_TOKEN is not set in .env. Fill it in first "
            "(see README step on the Self Client token)."
        )
    if not settings.zoho_portal_id or "paste-" in settings.zoho_portal_id:
        raise SystemExit(
            "ZOHO_PORTAL_ID / ZOHO_PROJECT_ID not set. Run: python discover_ids.py"
        )

    # Ask Zoho who this token belongs to, rather than trusting .env. This is
    # the same identity the hosted OAuth callback will get back later.
    try:
        info = zoho.get_user_info(settings.zoho_access_token)
    except zoho.ZohoError as exc:
        raise SystemExit(f"Could not identify the token's Zoho user: {exc}")

    zuid = str(info.get("ZUID") or info.get("zuid") or "")
    email = info.get("Email") or info.get("email") or settings.local_user_email
    if not zuid:
        raise SystemExit(
            "Zoho returned no ZUID. The token is probably missing the "
            "AaaServer.profile.READ scope — regenerate it with that included."
        )

    # The per-portal id is optional: without ZohoProjects.users.READ, or on a
    # portal where the lookup comes back empty, we just leave it NULL.
    portal_user_id = None
    try:
        member = zoho.find_portal_user(
            settings.zoho_access_token, settings.zoho_portal_id, zuid, email
        )
        if member:
            portal_user_id = str(member.get("id_string") or member.get("id") or "") or None
    except zoho.ZohoError as exc:
        print(f"Note: could not read portal members ({exc}) — continuing without it.")

    with get_conn() as conn, conn.cursor() as cur:
        # Adopt an existing row first — by ZUID, or by either email for rows
        # seeded before ZUID existed — so previously synced bugs keep their
        # owner instead of being orphaned under a brand new user.
        cur.execute(
            """
            UPDATE users
               SET email               = %s,
                   zoho_user_id        = %s,
                   zoho_portal_user_id = COALESCE(%s, zoho_portal_user_id)
             WHERE zoho_user_id = %s OR email IN (%s, %s)
            RETURNING id
            """,
            (email, zuid, portal_user_id, zuid, email, settings.local_user_email),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                INSERT INTO users (email, zoho_user_id, zoho_portal_user_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (email, zuid, portal_user_id),
            )
            row = cur.fetchone()

        user_id = row["id"]

        # Store / refresh this user's Zoho connection.
        cur.execute(
            """
            INSERT INTO zoho_credentials
                (user_id, access_token, refresh_token, portal_id, project_id, updated_at)
            VALUES (%s, %s, NULL, %s, %s, now())
            ON CONFLICT (user_id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                portal_id    = EXCLUDED.portal_id,
                project_id   = EXCLUDED.project_id,
                updated_at   = now()
            """,
            (
                user_id,
                settings.zoho_access_token,
                settings.zoho_portal_id,
                settings.zoho_project_id,
            ),
        )

    print(f"Seeded local user {email}")
    print(f"user_id             = {user_id}")
    print(f"zoho_user_id (ZUID) = {zuid}")
    print(f"zoho_portal_user_id = {portal_user_id or '(not resolved)'}")
    print("Next: start the server and hit /api/sync")


if __name__ == "__main__":
    main()
