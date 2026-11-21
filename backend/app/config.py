from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    database_url: str = "sqlite:///./workspace.db"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    ai_api_key: str = ""
    ai_base_url: str = "https://api.groq.com/openai/v1"
    ai_model: str = ""
    firebase_project_id: str = "nexsync-2c780"
    firebase_database_url: str = ""
    redis_url: str = ""
    snapshot_interval: int = 500
    cors_origins: str = "http://localhost:5173"
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

settings = Settings()
