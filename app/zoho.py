"""
Thin client for the Zoho Projects v3 REST API.

We only need read access to bugs here. The token is passed in by the caller,
which is what keeps this multi-user safe: the sync code always passes THIS
tester's token, so we only ever see THIS tester's data.

Docs: https://projectsapi.zoho.com/api-docs  (v3)
Auth header format: "Authorization: Zoho-oauthtoken <token>"
"""
import httpx

from app.config import settings


class ZohoError(Exception):
    """
    Raised when Zoho returns an error we can't recover from.

    status_code is kept so callers can react to specific failures — sync
    refreshes the token on a 401 rather than giving up.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {access_token}"}


def _records(payload, *keys) -> list[dict]:
    """
    Pull the record list out of a v3 response.

    v3 is inconsistent: /portals and /projects answer with a bare JSON array,
    while /users and /bugs wrap the array in an object alongside page_info.
    Handle both so callers don't have to care.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def list_portals(access_token: str) -> list[dict]:
    """List the portals this token can see. Used to discover your portal_id."""
    url = f"{settings.zoho_api_base}/api/v3/portals"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    return _records(resp.json(), "portals", "portal")


def list_projects(access_token: str, portal_id: str) -> list[dict]:
    """List projects in a portal. Used to discover your project_id."""
    url = f"{settings.zoho_api_base}/api/v3/portal/{portal_id}/projects"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    return _records(resp.json(), "projects")


def get_user_info(access_token: str) -> dict:
    """
    Who this token belongs to, straight from the Zoho accounts server.

    Returns the raw payload, which contains ZUID (the global Zoho account id,
    stable across portals and across email changes), Email and Display_Name.
    Needs the AaaServer.profile.READ scope on the token.
    """
    url = f"{settings.zoho_accounts_base}/oauth/user/info"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    """
    Trade a refresh token for a new access token.

    Refresh tokens don't expire, so this is what removes the hourly trip to the
    API console — the grant code is needed only once, ever. Returns
    {"access_token": str, "expires_in": int}.
    """
    if not settings.zoho_client_id or not settings.zoho_client_secret:
        raise ZohoError(
            "Cannot refresh: ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET are not set "
            "in .env. They are required to exchange a refresh token."
        )

    url = f"{settings.zoho_accounts_base}/oauth/v2/token"
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.zoho_client_id,
                "client_secret": settings.zoho_client_secret,
            },
        )

    # The accounts server answers 200 even on failure, with an "error" key.
    payload = resp.json() if resp.content else {}
    if "access_token" not in payload:
        raise ZohoError(
            f"Refresh failed: {payload.get('error', payload)}. The refresh "
            "token may have been revoked — re-run get_token.py with a new "
            "grant code."
        )
    return payload


def list_portal_users(access_token: str, portal_id: str) -> list[dict]:
    """
    Members of a portal. Needs the ZohoProjects.users.READ scope.

    The id here is the PORTAL user id — a different number from ZUID, and the
    one that shows up on bug fields like assignee / reported_person.
    """
    url = f"{settings.zoho_api_base}/api/v3/portal/{portal_id}/users"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    return _records(resp.json(), "users")


def find_portal_user(access_token: str, portal_id: str, zuid: str, email: str) -> dict | None:
    """
    Find THIS account's row in a portal's member list.

    Matches on zuid first (exact and email-change-proof), then falls back to
    email, since not every portal payload carries a zuid field.
    """
    email = (email or "").strip().lower()
    candidates = list_portal_users(access_token, portal_id)

    for user in candidates:
        user_zuid = user.get("zuid") or user.get("zpuid")
        if zuid and user_zuid and str(user_zuid) == str(zuid):
            return user

    for user in candidates:
        if email and str(user.get("email", "")).strip().lower() == email:
            return user

    return None


def fetch_all_bugs(access_token: str, portal_id: str, project_id: str) -> list[dict]:
    """
    Fetch every bug in a project, following v3 page-based pagination until
    page_info.has_next_page is false.
    """
    url = f"{settings.zoho_api_base}/api/v3/portal/{portal_id}/projects/{project_id}/bugs"
    bugs: list[dict] = []
    page = 1
    per_page = 100  # v3 max page size

    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(
                url,
                headers=_headers(access_token),
                # is_desc_needed is mandatory on v3 (400 LESS_THAN_MIN_OCCURANCE
                # without it) and we need descriptions for the embeddings.
                params={
                    "page": page,
                    "per_page": per_page,
                    "is_desc_needed": "true",
                },
            )
            _raise_for_status(resp)
            data = resp.json()

            batch = _records(data, "bugs")
            bugs.extend(batch)

            # Prefer the server's own flag, but fall back to a short page when
            # the endpoint answers with a bare array and no page_info.
            page_info = data.get("page_info", {}) if isinstance(data, dict) else {}
            if page_info:
                if not page_info.get("has_next_page"):
                    break
            elif len(batch) < per_page:
                break
            page += 1

            # Safety valve so a bad has_next_page flag can't loop forever.
            if page > 1000:
                break

    return bugs


def _raise_for_status(resp: httpx.Response) -> None:
    """Turn HTTP errors into a clear ZohoError with the most useful hint."""
    if resp.is_success:
        return
    if resp.status_code == 401:
        raise ZohoError(
            "401 Unauthorized — access token expired or invalid. Sync refreshes "
            "automatically when a refresh token is stored; if you see this, the "
            "refresh token is missing or was revoked. Re-run get_token.py with a "
            "fresh grant code. Also check the region (.in vs .com).",
            401,
        )
    if resp.status_code == 403:
        raise ZohoError(
            "403 Forbidden — the token is valid but missing a scope. Identity "
            "lookups need AaaServer.profile.READ and ZohoProjects.users.READ; "
            "regenerate the Self Client token with those included.",
            403,
        )
    if resp.status_code == 404:
        raise ZohoError(
            "404 Not Found — check ZOHO_PORTAL_ID / ZOHO_PROJECT_ID, and that "
            "ZOHO_API_BASE region (.in vs .com) is correct.",
            404,
        )
    if resp.status_code == 429:
        raise ZohoError(
            "429 Rate limited by Zoho — wait ~10 minutes and retry.", 429
        )
    raise ZohoError(
        f"Zoho API error {resp.status_code}: {resp.text[:300]}", resp.status_code
    )


def normalize_bug(raw: dict) -> dict:
    """
    Map a raw Zoho bug object to just the fields we store. Zoho field names have
    shifted between API versions, so we check a few likely keys for each field.
    """
    def pick(*keys, default=None):
        for k in keys:
            if k in raw and raw[k] not in (None, ""):
                return raw[k]
        return default

    # status/severity come back as objects in v3 — {"id": .., "type": "Open"} —
    # where the human-readable label lives under "type". Older shapes used
    # "name"/"value", and plain strings are still possible.
    def name_of(value):
        if isinstance(value, dict):
            return value.get("type") or value.get("name") or value.get("value")
        return value

    return {
        "zoho_issue_id": str(pick("id_string", "id", "key")),
        "title": pick("title", "name", "bug_title", default="(no title)"),
        "description": pick("description", "content", default=""),
        "status": name_of(pick("status", "classification")),
        "severity": name_of(pick("severity")),
    }
