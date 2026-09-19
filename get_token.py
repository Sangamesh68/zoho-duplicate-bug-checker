"""
Swap a Zoho Self Client GRANT CODE for an ACCESS TOKEN, and write it to .env.

Why this exists: the grant code from the api-console "Generate Code" tab looks
exactly like an access token (1000.<32 hex>.<32 hex>), so it is very easy to
paste the wrong one into ZOHO_ACCESS_TOKEN. Zoho then answers every request
with 401 INVALID_OAUTHTOKEN. This script does the exchange the console expects
and writes the result straight into .env, so the two values never get mixed up
by hand.

Fill these in .env first:
    ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET   (Self Client -> Client Secret tab)
    ZOHO_GRANT_CODE                      (Self Client -> Generate Code tab)

Then run:  python get_token.py

Grant codes expire in a few minutes and are single-use, so generate the code
and run this right away. Nothing secret is printed — tokens go to .env only.
"""
import re
from pathlib import Path

import httpx

from app.config import settings

ENV_PATH = Path(__file__).with_name(".env")


def write_env_value(key: str, value: str) -> None:
    """Replace key's value in .env, appending the key if it isn't there yet."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")

    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(token: str) -> str:
    """Show just enough to confirm which token landed, never the whole thing."""
    return f"{token[:8]}...{token[-4:]}" if len(token) > 12 else "(short value)"


def main():
    missing = [
        name
        for name, value in (
            ("ZOHO_CLIENT_ID", settings.zoho_client_id),
            ("ZOHO_CLIENT_SECRET", settings.zoho_client_secret),
            ("ZOHO_GRANT_CODE", settings.zoho_grant_code),
        )
        if not value or value.startswith("paste-")
    ]
    if missing:
        raise SystemExit(f"Set these in .env first: {', '.join(missing)}")

    url = f"{settings.zoho_accounts_base}/oauth/v2/token"
    resp = httpx.post(
        url,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.zoho_client_id,
            "client_secret": settings.zoho_client_secret,
            "code": settings.zoho_grant_code,
        },
        timeout=30,
    )

    # Zoho returns HTTP 200 even for failures, with an "error" key in the body.
    payload = resp.json()
    if "access_token" not in payload:
        error = payload.get("error", payload)
        hint = ""
        if error == "invalid_code":
            hint = (
                "\nThe code is expired, already used, or was copied from the "
                "wrong tab. Generate a fresh one and re-run within a minute."
            )
        elif error == "invalid_client":
            hint = (
                "\nClient id/secret mismatch, or the Self Client lives in the "
                "other region — check ZOHO_ACCOUNTS_BASE (.in vs .com)."
            )
        raise SystemExit(f"Token exchange failed: {error}{hint}")

    write_env_value("ZOHO_ACCESS_TOKEN", payload["access_token"])
    print(f"ZOHO_ACCESS_TOKEN written to .env  ({mask(payload['access_token'])})")

    # Present only when the code was generated with offline access.
    if payload.get("refresh_token"):
        write_env_value("ZOHO_REFRESH_TOKEN", payload["refresh_token"])
        print("ZOHO_REFRESH_TOKEN written to .env")

    print(f"expires in {payload.get('expires_in', '?')} seconds")
    print("Next: python discover_ids.py")


if __name__ == "__main__":
    main()
