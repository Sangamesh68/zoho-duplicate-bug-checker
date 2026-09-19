"""
FastAPI application — the HTTP layer.

Routes:
  GET  /                      -> the test web page
  GET  /api/health           -> quick "is it alive" check
  POST /api/sync             -> pull this user's bugs from Zoho
  POST /api/check-duplicate  -> check a proposed bug against stored ones

CURRENT-USER SEAM
-----------------
Everything is scoped by user_id. In local mode there is one user, so
get_current_user_id() just returns it. When you add hosted OAuth later, this
is the ONE function you change — it will read the user from the login session
instead. Nothing else in the app needs to move.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import get_conn
from app.similarity import find_duplicates
from app.sync import sync_user_bugs
from app.zoho import ZohoError

app = FastAPI(title="Duplicate Bug Checker")


def get_current_user_id() -> str:
    """
    LOCAL MODE: return the single seeded user.
    HOSTED MODE (later): return the logged-in user's id from the session.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users ORDER BY created_at LIMIT 1")
        row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=400,
            detail="No user found. Run seed.py to create your local user.",
        )
    return str(row["id"])


# ---- request bodies -------------------------------------------------------
class DuplicateCheckRequest(BaseModel):
    title: str
    description: str = ""
    project_id: str | None = None  # None = search across all synced projects


# ---- routes ---------------------------------------------------------------
@app.get("/api/health")
def health():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database down: {exc}")


@app.post("/api/sync")
def sync():
    """Pull the current user's bugs from Zoho into the database."""
    user_id = get_current_user_id()
    try:
        return sync_user_bugs(user_id)
    except ZohoError as exc:
        # Expected, explainable failures (bad token, wrong region, etc.)
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")


@app.post("/api/check-duplicate")
def check_duplicate(req: DuplicateCheckRequest):
    """Check a proposed bug against this user's stored bugs."""
    if not req.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    user_id = get_current_user_id()
    try:
        return find_duplicates(
            user_id=user_id,
            title=req.title,
            description=req.description,
            project_id=req.project_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Check failed: {exc}")


# ---- static test UI -------------------------------------------------------
# Serve the little HTML tester at "/". Kept last so it doesn't shadow /api/*.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
