from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "ETBP"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me"
    api_prefix: str = "/api"

    # Database
    database_url: str = "postgresql+asyncpg://etbp:etbp_secret@localhost:5432/etbp"
    database_echo: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://localhost:3002", "http://localhost:5173"]

    # Paystack
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""

    # Google Maps
    google_maps_api_key: str = ""

    # Termii
    termii_api_key: str = ""
    termii_sender_id: str = "ETBP"

    # AWS
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "eu-west-1"
    aws_s3_bucket: str = "etbp-uploads"
    aws_ses_sender_email: str = "noreply@etbp.com"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Superadmin
    superadmin_email: str = "admin@etbp.com"
    superadmin_password: str = "change-me"

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")


settings = Settings()
