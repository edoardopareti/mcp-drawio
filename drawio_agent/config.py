from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_or_none(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


@dataclass
class Config:
    llm_provider: str = "azure"
    azure_endpoint: str | None = None
    azure_api_key: str | None = None
    azure_api_version: str = "2024-10-21"
    azure_deployment: str | None = None
    azure_use_entra_id: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_base_url: str | None = None
    temperature: float | None = None
    max_tool_rounds: int = 12
    workspace: Path = field(default_factory=lambda: Path("./diagrams"))
    import_dirs: list[Path] = field(default_factory=list)
    mcp_server_url: str | None = None


def load_config() -> Config:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()  # also honour a .env in the current working directory
    env = os.environ.get
    import_dirs = [Path(p).expanduser() for p in (env("DRAWIO_IMPORT_DIRS") or "").split(os.pathsep) if p.strip()]
    return Config(
        llm_provider=(env("LLM_PROVIDER") or "azure").strip().lower(),
        azure_endpoint=env("AZURE_OPENAI_ENDPOINT") or None,
        azure_api_key=env("AZURE_OPENAI_API_KEY") or None,
        azure_api_version=env("AZURE_OPENAI_API_VERSION") or "2024-10-21",
        azure_deployment=env("AZURE_OPENAI_DEPLOYMENT") or None,
        azure_use_entra_id=_bool(env("AZURE_USE_ENTRA_ID")),
        openai_api_key=env("OPENAI_API_KEY") or None,
        openai_model=env("OPENAI_MODEL") or "gpt-4.1",
        openai_base_url=env("OPENAI_BASE_URL") or None,
        temperature=_float_or_none(env("LLM_TEMPERATURE", "")),
        max_tool_rounds=int(env("MAX_TOOL_ROUNDS") or 12),
        workspace=Path(env("DRAWIO_WORKSPACE") or "./diagrams").expanduser(),
        import_dirs=import_dirs,
        mcp_server_url=env("MCP_SERVER_URL") or None,
    )
