"""Environment configuration; credentials stay out of representations and logs."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_name: str = "音拾"
    app_env: Literal["local", "production"] = "production"
    app_password: SecretStr = Field(min_length=12, max_length=128)
    session_secret: SecretStr = Field(min_length=32)
    cookie_secure: bool = True
    session_max_age: int = Field(default=604800, ge=300, le=604800)
    max_body_bytes: int = Field(default=16384, ge=1024, le=1048576)
    data_dir: Path = Path("data")
    max_media_bytes: int = Field(default=200 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_duration_seconds: int = Field(default=1800, ge=1, le=7200)
    upload_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    task_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    result_ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    max_retained_tasks: int = Field(default=32, ge=1, le=64)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    douyin_parser_url: str = ""
    huoshan_parser_url: str = ""
    parser_api_token: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def require_secure_production_session(self) -> "Settings":
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("生产环境必须设置 COOKIE_SECURE=true，并通过 HTTPS 访问")
        return self
