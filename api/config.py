from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://flood:flood@localhost:5432/flooddb"
    database_sync_url: str = "postgresql+psycopg2://flood:flood@localhost:5432/flooddb"
    database_test_url: str = "postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test"
    redis_url: str = "redis://localhost:6379"
    internal_cron_key: str = "changeme"
    anthropic_api_key: str = ""
    openai_api_key: str = ""  # FIR-83 Whisper fallback for caption-less YouTube videos
    youtube_cookies_file: str = ""  # FIR-83 Netscape cookies.txt for yt-dlp audio downloads
    mock_llm: bool = False  # set MOCK_LLM=true in .env to skip real Haiku calls

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
