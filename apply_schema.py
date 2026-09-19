"""
Apply schema.sql to whatever DATABASE_URL points at.

With the bundled Docker Postgres, schema.sql runs itself the first time the
container is created. Against a hosted database (Supabase, Neon, ...) nothing
runs it for you, and psql may not be installed locally — so this does it over
the same psycopg connection the app already uses.

Every statement in schema.sql is IF NOT EXISTS, so re-running is safe and
leaves existing rows alone.

Run:  python apply_schema.py
"""
from pathlib import Path

from app.db import get_conn

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def main():
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with get_conn() as conn, conn.cursor() as cur:
        # psycopg sends this over the simple-query protocol, which accepts the
        # whole multi-statement script in one round trip.
        cur.execute(sql)

        cur.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
             ORDER BY table_name
            """
        )
        tables = [r["table_name"] for r in cur.fetchall()]

        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()

    print(f"tables : {', '.join(tables) or '(none)'}")
    print(f"pgvector: {row['extversion'] if row else 'NOT INSTALLED'}")
    print("Next: python seed.py")


if __name__ == "__main__":
    main()
