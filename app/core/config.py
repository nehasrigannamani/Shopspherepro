from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    app_name: str = "ShopSphere Pro"
    app_env: str = "development"
    debug: bool = True

    database_url: str

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    email_verification_expire_hours: int = 48

    # Stripe (test mode / sandbox)
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_success_url: str = (
        "http://localhost:8000/payment-success?session_id={CHECKOUT_SESSION_ID}"
    )
    stripe_cancel_url: str = "http://localhost:8000/payment-cancelled"

    # Public base URL used when building links in emails
    public_base_url: str = "http://localhost:8000"

    # Exports and background jobs
    export_dir: str = "exports"
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 60
    job_max_attempts: int = 3

    # Inventory reservations (checkout holds)
    reservation_ttl_minutes: int = 15
    reservation_max_ttl_minutes: int = 120

    # Loyalty
    loyalty_point_value: float = 0.25          # currency value of one point when redeeming
    loyalty_min_redeem_points: int = 50
    loyalty_max_redeem_percent: int = 50       # max share of the (pre-shipping) payable amount payable by points
    loyalty_default_expiry_days: int = 365

    # Payments: PENDING payments older than this are reconciled / timed out
    payment_timeout_minutes: int = 60

    # Rate limiting (in-process; see app/core/ratelimit.py)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120

    # Audit middleware
    audit_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
