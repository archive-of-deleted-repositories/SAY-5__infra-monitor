"""Runtime configuration. Read from env vars / a YAML config; never bake secrets in."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFRA_MONITOR_", env_file=".env", extra="ignore")

    database_url: str = Field(default="sqlite:///./infra_monitor.db")
    config_path: Path = Field(default=Path("config/monitor.example.yaml"))
    webhook_secret: str = Field(default="change-me")
    dashboard_host: str = Field(default="127.0.0.1")
    dashboard_port: int = Field(default=8080)
    agent_interval_seconds: float = Field(default=5.0)
    remediation_timeout_seconds: int = Field(default=60)
    metric_retention_days: int = Field(default=30)


def get_settings() -> Settings:
    return Settings()
