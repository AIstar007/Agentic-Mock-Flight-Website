from __future__ import annotations
import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    mock_mode: bool = _bool("MOCK_MODE", True)
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "604800"))
    audit_ttl_seconds: int = int(os.getenv("AUDIT_TTL_SECONDS", "604800"))
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
    redis_url: str = os.getenv("REDIS_URL", "").strip()
    graph_redis_url: str = (os.getenv("LANGGRAPH_REDIS_URL", "").strip() or os.getenv("REDIS_URL", "").strip())
    mongodb_uri: str = os.getenv("MONGODB_URI", "").strip()
    mongodb_database: str = os.getenv("MONGODB_DATABASE", "emirates_checkin_demo")
    neo4j_uri: str = os.getenv("NEO4J_URI", "").strip()
    neo4j_username: str = os.getenv("NEO4J_USERNAME", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_required: bool = _bool("NEO4J_REQUIRED", False)
    require_persistent_storage: bool = _bool("REQUIRE_PERSISTENT_STORAGE", False)
    enable_provider_diagnostics: bool = _bool("ENABLE_PROVIDER_DIAGNOSTICS", False)
    provider_diagnostics_token: str = os.getenv("PROVIDER_DIAGNOSTICS_TOKEN", "").strip()
    sarvam_api_key: str = os.getenv("SARVAM_API_KEY", "").strip()
    sarvam_stt_model: str = os.getenv("SARVAM_STT_MODEL", "saaras:v4").strip()
    sarvam_tts_model: str = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3").strip()
    sarvam_tts_pace: float = float(os.getenv("SARVAM_TTS_PACE", "1.5"))
    sarvam_timeout_seconds: float = float(os.getenv("SARVAM_TIMEOUT_SECONDS", "45"))

    # Provider diagnostic adapters are opt-in and never replace the mock workflow automatically.
    navitaire_base_url: str = os.getenv("NAVITAIRE_BASE_URL", "").strip()
    navitaire_booking_path: str = os.getenv("NAVITAIRE_BOOKING_RETRIEVE_PATH", "/api/v2/graph/BookingRetrieve").strip()
    navitaire_booking_state_path: str = os.getenv("NAVITAIRE_BOOKING_STATE_PATH", "/api/nsk/v1/booking").strip()
    navitaire_checkin_requirements_path: str = os.getenv("NAVITAIRE_CHECKIN_REQUIREMENTS_PATH", "/api/nsk/v2/booking/checkin/journey/{journey_key}/requirements").strip()
    navitaire_document_check_path: str = os.getenv("NAVITAIRE_DOCUMENT_CHECK_PATH", "/api/nsk/v1/booking/passengers/{passenger_key}/documents/check").strip()
    navitaire_graphql_query: str = os.getenv("NAVITAIRE_GRAPHQL_QUERY", "").strip()
    navitaire_agent_token: str = os.getenv("NAVITAIRE_AGENT_TOKEN", "").strip()
    navitaire_user_key: str = os.getenv("NAVITAIRE_USER_KEY", "").strip()
    navitaire_api_key: str = os.getenv("NAVITAIRE_API_KEY", "").strip()
    navitaire_api_key_header: str = os.getenv("NAVITAIRE_API_KEY_HEADER", "").strip()
    navitaire_auth_scheme: str = os.getenv("NAVITAIRE_AUTH_SCHEME", "raw").strip().lower()
    navitaire_allowed_carrier: str = os.getenv("NAVITAIRE_ALLOWED_CARRIER", "").strip().upper()

    timatic_token_url: str = os.getenv(
        "TIMATIC_TOKEN_URL", "https://staging.timatic.iata.org/security-token-service/oauth2/token"
    ).strip()
    timatic_base_url: str = os.getenv(
        "TIMATIC_BASE_URL", "https://staging.timatic.iata.org/query-interface-service/api/v1"
    ).strip()
    timatic_username: str = os.getenv("TIMATIC_USERNAME", "").strip()
    timatic_password: str = os.getenv("TIMATIC_PASSWORD", "")
    timatic_grant_type: str = os.getenv("TIMATIC_GRANT_TYPE", "client_credentials").strip()
    timatic_user_key: str = os.getenv("TIMATIC_USER_KEY", "").strip()
    timatic_processing_entity: str = os.getenv("TIMATIC_PROCESSING_ENTITY", "").strip().upper()
    timatic_timeout_seconds: float = float(os.getenv("PROVIDER_HTTP_TIMEOUT_SECONDS", "15"))


settings = Settings()
