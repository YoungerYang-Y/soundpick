"""Environment configuration; credentials stay out of representations and logs."""

from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_name: str = "音拾"
    app_env: Literal["local", "production"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_password: SecretStr = Field(min_length=12, max_length=128)
    session_secret: SecretStr = Field(min_length=32)
    cookie_secure: bool = True
    session_max_age: int = Field(default=604800, ge=300, le=604800)
    max_body_bytes: int = Field(default=16384, ge=1024, le=1048576)
    request_body_timeout_seconds: int = Field(default=15, ge=1, le=60)
    data_dir: Path = Path("data")
    redis_url: SecretStr = SecretStr("")
    redis_key_prefix: str = Field(
        default="soundpick:{protection}",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_{}:-]+$",
    )
    redis_timeout_seconds: float = Field(default=0.1, ge=0.01, le=1)
    redis_retry_seconds: float = Field(default=30, ge=1, le=300)
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

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            url = urlsplit(raw)
            query = parse_qsl(url.query, keep_blank_values=True)
            if (
                url.scheme not in {"redis", "rediss"}
                or not url.hostname
                or url.fragment
                or any(key != "db" or not val.isdecimal() for key, val in query)
                or len(query) > 1
                or (url.path and not url.path.lstrip("/").isdecimal())
            ):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ValueError("REDIS_URL 必须是 redis/rediss 地址，查询参数仅允许 db") from None
        return value

    @model_validator(mode="after")
    def require_secure_production_session(self) -> "Settings":
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("生产环境必须设置 COOKIE_SECURE=true，并通过 HTTPS 访问")
        return self
