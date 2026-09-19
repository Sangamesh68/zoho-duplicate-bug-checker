"""
Central config. Every setting comes from the .env file (see .env.example).
Loading it in one place means the rest of the code never reads os.environ
directly and never hardcodes a secret.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Where to find the .env file, and ignore unknown keys instead of crashing.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    zoho_api_base: str = "https://projectsapi.zoho.in"

    # Accounts server (identity, not Projects data). Same region split as
    # zoho_api_base — if one is .in the other must be too.
    zoho_accounts_base: str = "https://accounts.zoho.in"

    # Self Client app credentials, used only by get_token.py to swap a
    # grant code for an access token.
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_grant_code: str = ""

    # Local single-user seed values (Self Client mode)
    local_user_email: str = "you@arcitech.ai"
    zoho_access_token: str = ""
    zoho_refresh_token: str = ""
    zoho_portal_id: str = ""
    zoho_project_id: str = ""

    # Duplicate detection tuning
    duplicate_threshold: float = 0.72
    semantic_weight: float = 0.65
    keyword_weight: float = 0.35

    # Second-stage reranking of the top candidates (see app/rerank.py).
    # Off = the original embedding+keyword blend only.
    rerank_enabled: bool = True
    # STS-B ("how similar in meaning are these two sentences", 0..1). The
    # Quora duplicate-question model was tried first and scored even obvious
    # duplicates near 0 — it expects question phrasing, which bug titles lack.
    rerank_model: str = "cross-encoder/stsb-distilroberta-base"
    # How much the reranker's verdict counts vs the first-stage score.
    rerank_weight: float = 0.7

    # Auto-sync: the extension re-pulls from Zoho when the last successful
    # sync is older than this, so checks run against fresh data.
    sync_stale_minutes: int = 10


# One shared instance imported everywhere: `from app.config import settings`
settings = Settings()
