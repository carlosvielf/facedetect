from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str
    jwt_secret: str = Field(min_length=32)
    embedding_key: str
    cors_origins: list[str] = ['http://localhost:5173']
    camera_hosts: list[str] = []
    threshold: float = Field(default=0.55, ge=0, le=1)
    ambiguity_margin: float = Field(default=0.08, ge=0, le=1)
    observations: int = Field(default=3, ge=2)
    interval_seconds: float = Field(default=2, ge=1)


settings = Settings()
