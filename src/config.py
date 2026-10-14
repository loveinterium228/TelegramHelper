"""Application configuration and environment settings."""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMDefaults:
    """Default active LLM model identifiers."""
    OPENAI_CHAT_LIGHT = "gpt-4o-mini"
    OPENAI_CHAT_HEAVY = "gpt-4o"
    OPENAI_EMBED = "text-embedding-3-small"

    GEMINI_CHAT_LIGHT = "gemini-2.5-flash"
    GEMINI_CHAT_HEAVY = "gemini-2.5-pro"
    GEMINI_EMBED = "text-embedding-004"

    GROQ_CHAT_LIGHT = "llama-3.3-70b-versatile"
    GROQ_CHAT_HEAVY = "llama-3.3-70b-versatile"


class Settings(BaseSettings):
    """Global application settings loaded from environment or .env file."""
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(
        default="0000000000:AA_dummy_token_for_unconfigured_env",
        description="Telegram control bot token from @BotFather",
    )
    owner_telegram_id: int = Field(
        default=0,
        description="Numeric Telegram user_id of the authorized owner",
    )
    encryption_key: str = Field(
        default="2roYO8FNFIBMvbYC1y7HaWAAHIoOpYew-nKO9QOT6n4=",
        description="Fernet 32-byte base64 encryption key",
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///data/app.db",
        description="Async SQLAlchemy database connection URI",
    )

    @property
    def data_dir(self) -> Path:
        """Ensure and return the data directory."""
        path = PROJECT_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
