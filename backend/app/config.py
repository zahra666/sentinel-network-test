from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "sqlite:///./sentinel.db"
    app_secret: str = "local-development-only-change-me"
    allowed_origins: str = "http://localhost:5173"
    mcp_enabled: bool = False
    mcp_allowed_hosts: str = "127.0.0.1:8001,localhost:8001"
    mcp_allowed_origins: str = ""
    schedule_enabled: bool = False
    schedule_hour: int = 2
    schedule_timezone: str = "Asia/Kuala_Lumpur"
    alert_reminder_minutes: int = 1440
    alert_escalation_minutes: int = 2880
    chain_rpc_url: str = ""
    chain_private_key: str = ""
    chain_contract_address: str = ""
    chain_id: int = 11155111
    chain_researcher_address: str = ""

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
