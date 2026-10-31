"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "APAAR Ledger"
    app_version: str = "0.4.0-phase4"
    app_env: str = "dev"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://apaar:apaar@localhost:5432/apaar"

    # Auth (section 12): access 15 min, refresh 7 days.
    jwt_secret: str = "dev-only-insecure-jwt-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # Restricted CORS (NFR-1). Comma-separated list of allowed origins.
    cors_origins: str = "http://localhost:5173"

    # Field-level encryption at rest (FR-38). Empty = derive a DEV-ONLY key from JWT_SECRET
    # (see app/core/crypto.py); set a real Fernet key outside local demos.
    fernet_key: str = ""
    # HMAC pepper for privacy-preserving identity/contact tokens (FR-37).
    token_pepper: str = "dev-only-insecure-pepper"
    signing_key_passphrase: str = "dev-only-insecure-passphrase"

    # Seed / demo
    demo_password: str = "Demo@2026"
    # On API start, generate the national synthetic dataset if no student records exist.
    auto_seed_national: bool = True
    synth_seed: int = 42
    synth_institutions: int = 40
    synth_students: int = 20000

    # Name matching (spec 9.0). LaBSE is NOT implemented; the flag is reserved and must stay off.
    enable_labse: bool = False
    # Optional JSON file {"variant": "canonical", ...} merged into the built-in name variant table.
    name_variants_file: str = ""

    # Academic year new admissions are filed under unless the request names one.
    current_academic_year: str = "2025-26"

    # CSV uploads (FR-2): chunk and file limits, and the row count above which a dry run or
    # submission prefetches all enrolments/registry entries at once (NFR-5).
    upload_max_chunk_bytes: int = 5 * 1024 * 1024
    upload_max_file_bytes: int = 50 * 1024 * 1024
    upload_max_chunks: int = 2000
    upload_max_rows: int = 50000
    upload_prefetch_rows: int = 300

    # Integrations. Only "mock" exists: the MOCK APAAR registry (spec 2.2, FR-40).
    apaar_client: str = "mock"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
