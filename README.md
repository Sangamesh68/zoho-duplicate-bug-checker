# Duplicate Bug Checker

Checks a bug you're about to file in Zoho Projects against the bugs already
there, and warns if it looks like a duplicate. It combines **meaning**
(AI embeddings) with **keywords** (Postgres full-text search) so it catches
duplicates worded differently, not just exact matches.

Built **local-first but multi-user-ready**: every table and query is scoped by
`user_id`, so the local single-user version becomes the hosted multi-tester
version (Zoho OAuth login) by changing **one function** — no rewrite.

---

## What you need first

- **Python 3.11+** — check with `python --version`
- **Docker Desktop** — for the local Postgres database (easiest path)
- A **Zoho Projects** account with a project that has some bugs

---

## Step 1 — Install the code

```bash
cd duplicate-bug-checker
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> ⚠ **Heads-up on size:** `sentence-transformers` pulls in PyTorch, which is a
> large download (several hundred MB). The first duplicate check also downloads
> the AI model (~90 MB) once. After that it's cached and fast.

## Step 2 — Start the database

```bash
docker compose up -d
```

This starts Postgres with the `pgvector` extension and runs `schema.sql`
automatically the first time. Check it's healthy:

```bash
docker compose ps
```

## Step 3 — Get a Zoho Self Client token

For local development you use one token you generate yourself (no hosting
needed yet).

1. Go to **https://api-console.zoho.in** (or `.com` for non-India accounts).
2. Create a **Self Client**.
3. In the **Generate Code** tab, enter the scope
   `ZohoProjects.bugs.READ,ZohoProjects.portals.READ,ZohoProjects.projects.READ,ZohoProjects.users.READ,AaaServer.profile.READ`,
   pick a duration, and generate a code.
   The last two scopes are what let the app read **who you are** (your ZUID and
   your portal member id) — without them `seed.py` stops with a 403.
4. Copy the **client id** and **client secret** from the Client Secret tab into
   `.env` as `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET`, put the code from step 3
   into `ZOHO_GRANT_CODE`, then run:

   ```bash
   python get_token.py
   ```

   That exchanges the code and writes `ZOHO_ACCESS_TOKEN` into `.env` for you.

> ⚠ **The grant code is not the access token.** They look identical
> (`1000.<32 hex>.<32 hex>`), so pasting the code straight into
> `ZOHO_ACCESS_TOKEN` is an easy mistake — and it fails with a misleading
> `401 INVALID_OAUTHTOKEN` rather than anything mentioning the code. Grant
> codes are also single-use and expire in minutes, so generate the code and
> run `get_token.py` back to back.

> **You only ever need one grant code.** Access tokens last about an hour, but
> `get_token.py` also stores a **refresh token**, which does not expire. When a
> sync hits a 401, it exchanges the refresh token for a new access token and
> retries — no trip back to the API console.
>
> You need a new grant code only if you change the scopes, revoke the token, or
> delete the Self Client.

## Step 4 — Set up your .env

```bash
cp .env.example .env
```

Open `.env` and:
- paste your `ZOHO_ACCESS_TOKEN`
- set `ZOHO_API_BASE` to `.in` (India) or `.com` (elsewhere)
- set `ZOHO_ACCOUNTS_BASE` to the **same region** (`https://accounts.zoho.in`
  or `https://accounts.zoho.com`) — a region mismatch here shows up as a 401
  on identity lookup only, while bug sync keeps working
- leave the IDs for the next step

## Step 5 — Find your portal & project IDs

```bash
python discover_ids.py
```

It prints your portals and projects with their IDs. Copy the ones you want into
`.env` as `ZOHO_PORTAL_ID` and `ZOHO_PROJECT_ID`.

## Step 6 — Create your local user

```bash
python seed.py
```

This asks Zoho who your token belongs to, then writes your user + token into
the database (the same rows OAuth will write later). It stores two Zoho ids on
your user row:

- **`zoho_user_id`** — your ZUID, the global Zoho account id. Stable across
  portals and across email changes, so it's the key hosted OAuth will match on.
- **`zoho_portal_user_id`** — your member id *inside that portal*. Different
  number, and the one that appears on bug fields like assignee / reported_person.

If the portal lookup fails the seed still succeeds and leaves that column NULL.

## Step 7 — Run the app

```bash
uvicorn app.main:app --reload
```

Open **http://localhost:8000**.

## Step 8 — Use it

1. Click **Sync bugs from Zoho** (first run is slow — model download).
2. Type a bug title/description and click **Check for duplicates**.
3. You'll see the closest existing bugs with a match % and a verdict.

---

## How the detection works

For a proposed bug, one SQL query (scoped to your `user_id`) returns the 20
closest bugs by meaning, each with two scores:

- **semantic** — cosine similarity of AI embeddings (same meaning, any words)
- **keyword** — Postgres `ts_rank_cd` full-text score (shared words)

They're blended (`SEMANTIC_WEIGHT` / `KEYWORD_WEIGHT` in `.env`) into a final
score. Above `DUPLICATE_THRESHOLD` it's flagged. Tune those three numbers if it
feels too strict or too loose.

## Project layout

```
duplicate-bug-checker/
├── docker-compose.yml    # local Postgres + pgvector
├── schema.sql            # tables (all scoped by user_id)
├── requirements.txt
├── .env.example          # copy to .env
├── get_token.py          # grant code -> access token, written to .env
├── discover_ids.py       # find portal/project IDs
├── seed.py               # create the local user (local stand-in for OAuth)
├── static/index.html     # test UI (no Chrome extension needed yet)
└── app/
    ├── config.py         # settings from .env
    ├── db.py             # connection pool + pgvector
    ├── embeddings.py     # text -> vector
    ├── zoho.py           # Zoho Projects v3 API client
    ├── sync.py           # fetch + store bugs (per user)
    ├── similarity.py     # the duplicate detector
    └── main.py           # API routes + the current-user seam
```

## Later: going hosted (multi-tester, Plan B)

Because the data is already per-user, three things get added — nothing gets
rewritten:

1. **OAuth endpoints** `/auth/zoho` and `/auth/zoho/callback` that write a row
   into `zoho_credentials` (with a refresh token) instead of `seed.py`. The
   callback matches the tester on `users.zoho_user_id` (ZUID), which `seed.py`
   already populates today.
2. **`get_current_user_id()`** in `app/main.py` reads the logged-in user from
   the session instead of returning the single seeded user.
3. **Deploy** to Render/Railway so Zoho has a real callback URL.

> 💤 **Render free-tier cost note:** the free web service sleeps after
> inactivity, so the first request after idle takes ~30–50s to wake. Fine for an
> occasional QA tool; just don't mistake the cold start for a bug.

## Troubleshooting

- **401 on sync** → sync refreshes expired tokens by itself, so a 401 that
  reaches you means the refresh token is missing or revoked. Check
  `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` are set, then re-run `get_token.py`
  with a new grant code followed by `seed.py`. Also check the region.
- **403 on seed** → token is missing `AaaServer.profile.READ` /
  `ZohoProjects.users.READ`; regenerate it with those scopes.
- **`column "zoho_user_id" does not exist`** → your DB predates that column.
  `schema.sql` only auto-runs on a fresh volume, so apply it by hand:
  `docker compose exec -T db psql -U dupchecker -d dupchecker -f /docker-entrypoint-initdb.d/schema.sql`
  (every statement in it is `IF NOT EXISTS`, so re-running is safe and keeps
  your synced bugs).
- **404 on sync** → wrong `ZOHO_PORTAL_ID` / `ZOHO_PROJECT_ID`, or wrong region.
- **429** → Zoho rate limit; wait ~10 minutes.
- **DB connection errors** → is `docker compose ps` healthy? Does `DATABASE_URL`
  match `docker-compose.yml`?

## Security notes

- The real `.env` is git-ignored — never commit it.
- In local dev the Zoho token is stored **in plaintext** in your local
  database. That's acceptable for a throwaway local DB, but **before hosting**,
  encrypt tokens at rest (e.g. app-level encryption or a secrets manager) since
  the DB then holds every tester's credentials.
```
