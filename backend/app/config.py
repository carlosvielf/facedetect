import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parent.parent
# Root .env is the documented one; backend/.env (legacy copy) overrides it if present.
# Absolute paths: the API, Alembic and scripts work from any working directory.
ENV_FILES = (BACKEND.parent / '.env', BACKEND / '.env')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding='utf-8', extra='ignore')
    database_url: str
    jwt_secret: str = Field(min_length=32)
    embedding_key: str
    cors_origins: list[str] = ['http://localhost:5173']
    camera_hosts: list[str] = []
    threshold: float = Field(default=0.55, ge=0, le=1)
    ambiguity_margin: float = Field(default=0.08, ge=0, le=1)
    # Scores in [threshold - band, threshold) are shown as "uncertain", never as a match.
    uncertain_band: float = Field(default=0.05, ge=0, le=1)
    observations: int = Field(default=3, ge=2)
    interval_seconds: float = Field(default=2, ge=1)
    # Face quality gates. SCRFD scores clear frontal faces around 0.75-0.85,
    # so a 0.8 cut discards good faces; calibrate with backend/scripts/calibrate.py.
    min_detection_confidence: float = Field(default=0.6, ge=0, le=1)
    min_face_size: int = Field(default=64, ge=16)
    enroll_min_face_size: int = Field(default=80, ge=16)
    min_sharpness: float = Field(default=40, ge=0)
    # ESP32 CameraWebServer: /capture on port 80, /stream on server_port + 1 (81).
    camera_stream_port: int = Field(default=81, ge=1, le=65535)
    camera_source: str = Field(default='stream', pattern='^(stream|capture)$')
    camera_timeout: float = Field(default=5, gt=0)
    camera_idle_seconds: float = Field(default=10, ge=0)
    monitor_max_fps: float = Field(default=15, gt=0, le=30)
    inference_max_fps: float = Field(default=5, gt=0, le=30)


# Tests set ATTENDANCE_TEST so the developer's real .env never leaks into them.
settings = Settings(_env_file=None) if os.environ.get('ATTENDANCE_TEST') else Settings()
