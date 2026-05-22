from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    anthropic_api_key: str
    openai_api_key: str  # for Whisper voice transcription

    database_url: str
    redis_url: str

    allowed_telegram_user_id: str | None = None
    bot_env: str = "production"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
