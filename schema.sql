-- ===========================================================================
-- Duplicate Bug Checker — database schema
--
-- KEY IDEA: user_id is on every table and every query filters by it.
-- In local mode there is exactly ONE user. In hosted mode, OAuth creates one
-- user row per tester. The schema is identical either way — that is what makes
-- the local build convertible to the hosted build with no rewrite.
-- ===========================================================================

-- pgvector gives us the VECTOR type + similarity search. Enable once.
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per tester --------------------------------------------------------
-- zoho_user_id (ZUID) is the real identity key: it survives an email change
-- and is what the hosted OAuth callback will match on. Email is kept for
-- display and for the local seed path.
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT UNIQUE NOT NULL,
    zoho_user_id        TEXT UNIQUE,   -- global Zoho account id (ZUID)
    zoho_portal_user_id TEXT,          -- per-portal member id (assignee/reporter)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing local databases: schema.sql only runs on first container creation,
-- so these keep an already-populated DB in step with the table above.
ALTER TABLE users ADD COLUMN IF NOT EXISTS zoho_user_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS zoho_portal_user_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_zoho_user_id
    ON users(zoho_user_id);

-- Each tester's Zoho connection (one row per user) --------------------------
-- This table is where "local Self Client" vs "hosted OAuth" actually differs.
-- Local: you insert one row by hand (via seed.py), refresh_token stays NULL.
-- Hosted: the OAuth callback writes rows here, with a refresh_token too.
-- The rest of the app just reads tokens from here and never cares which way.
CREATE TABLE IF NOT EXISTS zoho_credentials (
    user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,            -- NULL in Self Client mode
    expires_at    TIMESTAMPTZ,     -- when access_token stops working
    portal_id     TEXT,            -- which Zoho portal
    project_id    TEXT,            -- which project to check within
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Synced bugs, always tied to the tester who owns them ----------------------
CREATE TABLE IF NOT EXISTS bugs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    zoho_project_id TEXT NOT NULL,
    zoho_issue_id   TEXT NOT NULL,   -- the id Zoho assigns the bug
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT,
    severity        TEXT,
    embedding       VECTOR(384),     -- 384 = all-MiniLM-L6-v2 output size
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, zoho_issue_id)  -- same bug is never stored twice per user
);

-- A record of each sync, so failures are debuggable -------------------------
CREATE TABLE IF NOT EXISTS sync_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    bugs_fetched INTEGER,
    status       TEXT,             -- 'running' | 'success' | 'failed'
    error        TEXT              -- populated when status = 'failed'
);

-- Indexes -------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_bugs_user         ON bugs(user_id);
CREATE INDEX IF NOT EXISTS idx_bugs_user_project ON bugs(user_id, zoho_project_id);

-- Vector similarity (semantic search). Queries STILL add user_id on top.
CREATE INDEX IF NOT EXISTS idx_bugs_embedding
    ON bugs USING hnsw (embedding vector_cosine_ops);

-- Keyword search (the full-text / "BM25-style" half).
CREATE INDEX IF NOT EXISTS idx_bugs_fts ON bugs
    USING gin (to_tsvector('english', title || ' ' || coalesce(description, '')));
