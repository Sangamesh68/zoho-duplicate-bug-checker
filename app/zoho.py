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
    """Raised when Zoho returns an error we can't recover from."""


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {access_token}"}


def list_portals(access_token: str) -> list[dict]:
    """List the portals this token can see. Used to discover your portal_id."""
    url = f"{settings.zoho_api_base}/api/v3/portals"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    data = resp.json()
    # v3 returns the list under a module key; be defensive about the exact name.
    return data.get("portals") or data.get("portal") or []


def list_projects(access_token: str, portal_id: str) -> list[dict]:
    """List projects in a portal. Used to discover your project_id."""
    url = f"{settings.zoho_api_base}/api/v3/portal/{portal_id}/projects"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(access_token))
    _raise_for_status(resp)
    data = resp.json()
    return data.get("projects") or []


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
    data = resp.json()
    return data.get("users") or []


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
                params={"page": page, "per_page": per_page},
            )
            _raise_for_status(resp)
            data = resp.json()

            # v3 lists the records under the module key ("bugs").
            batch = data.get("bugs", [])
            bugs.extend(batch)

            page_info = data.get("page_info", {})
            if not page_info.get("has_next_page"):
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
            "401 Unauthorized — token is expired or invalid. In Self Client "
            "mode, generate a fresh token. Also check ZOHO_API_BASE matches "
            "your account region (.in vs .com)."
        )
    if resp.status_code == 403:
        raise ZohoError(
            "403 Forbidden — the token is valid but missing a scope. Identity "
            "lookups need AaaServer.profile.READ and ZohoProjects.users.READ; "
            "regenerate the Self Client token with those included."
        )
    if resp.status_code == 404:
        raise ZohoError(
            "404 Not Found — check ZOHO_PORTAL_ID / ZOHO_PROJECT_ID, and that "
            "ZOHO_API_BASE region (.in vs .com) is correct."
        )
    if resp.status_code == 429:
        raise ZohoError("429 Rate limited by Zoho — wait ~10 minutes and retry.")
    raise ZohoError(f"Zoho API error {resp.status_code}: {resp.text[:300]}")


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

    # status/severity can be nested objects in v3 ({"name": "..."}) or strings.
    def name_of(value):
        if isinstance(value, dict):
            return value.get("name") or value.get("value")
        return value

    return {
        "zoho_issue_id": str(pick("id_string", "id", "key")),
        "title": pick("title", "name", "bug_title", default="(no title)"),
        "description": pick("description", "content", default=""),
        "status": name_of(pick("status", "classification")),
        "severity": name_of(pick("severity")),
    }
