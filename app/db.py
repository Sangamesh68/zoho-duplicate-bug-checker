"""
Database access. Uses a small connection pool so we don't open a new
connection on every request. pgvector is registered on each connection so we
can pass Python lists/arrays straight into VECTOR columns.
"""
from contextlib import contextmanager

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector

from app.config import settings


def _configure(conn):
    # Called once per physical connection when the pool creates it.
    register_vector(conn)


# open=True builds the pool immediately so a bad DATABASE_URL fails loudly
# at startup instead of on the first request.
pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    configure=_configure,
    kwargs={"row_factory": dict_row},  # rows come back as dicts, not tuples
    open=True,
)


@contextmanager
def get_conn():
    """
    Usage:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    The connection is returned to the pool automatically, and the transaction
    is committed on success / rolled back on error.
    """
    with pool.connection() as conn:
        yield conn
