from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    anthropic_api_key: str
    openai_api_key: str
    allowed_telegram_user_id: str | None = None

    database_url: str
    redis_url: str

    bot_env: str = "production"

    github_repo: str | None = None
    github_token: str | None = None

    coolify_url: str | None = None
    coolify_api_token: str | None = None
    coolify_service_id: str | None = None

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
