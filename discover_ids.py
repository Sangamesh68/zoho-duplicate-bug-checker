"""
Find your Zoho portal_id and project_id.

You need these two numbers for .env. This script uses your ZOHO_ACCESS_TOKEN
to list the portals and projects your token can see, and prints their IDs.

Run:  python discover_ids.py
"""
from app.config import settings
from app import zoho


def main():
    token = settings.zoho_access_token
    if not token or "paste-" in token:
        raise SystemExit("Set ZOHO_ACCESS_TOKEN in .env first.")

    print(f"Using API base: {settings.zoho_api_base}")
    print("(If this errors with 401/404, switch .in <-> .com in ZOHO_API_BASE)\n")

    # Identity behind the token. Informational only — seed.py stores these.
    try:
        info = zoho.get_user_info(token)
        print(f"YOU  zuid={info.get('ZUID')}  email={info.get('Email')}")
    except zoho.ZohoError as exc:
        print(f"Could not read your Zoho identity: {exc}")
    print()

    try:
        portals = zoho.list_portals(token)
    except zoho.ZohoError as exc:
        raise SystemExit(f"Could not list portals: {exc}")

    if not portals:
        raise SystemExit("No portals returned for this token.")

    for p in portals:
        pid = p.get("id_string") or p.get("id")
        pname = p.get("portal_name") or p.get("name") or p.get("org_name") or "(unnamed)"
        print(f"PORTAL  id={pid}  name={pname}")
        try:
            projects = zoho.list_projects(token, str(pid))
        except zoho.ZohoError as exc:
            print(f"   could not list projects: {exc}")
            continue
        for pr in projects:
            prid = pr.get("id_string") or pr.get("id")
            prname = pr.get("name") or "(unnamed)"
            print(f"   PROJECT  id={prid}  name={prname}")
        print()

    print("Copy the portal id and the project id you want into .env:")
    print("   ZOHO_PORTAL_ID=...")
    print("   ZOHO_PROJECT_ID=...")


if __name__ == "__main__":
    main()
