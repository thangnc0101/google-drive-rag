from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    log_level: str = "INFO"


@dataclass
class AuthConfig:
    username: str = ""
    password: str = ""


@dataclass
class NamespaceConfig:
    name: str = ""
    description: str = ""
    folder_ids: list[str] = field(default_factory=list)


@dataclass
class GoogleDriveConfig:
    credentials_file: str = ""
    poll_interval_seconds: int = 300
    sync_on_startup: bool = True
    file_types: list[str] = field(default_factory=lambda: [
        "application/pdf",
        "application/vnd.google-apps.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    ])
    max_file_size_mb: int = 50


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    enrichment_model: str = "google/gemini-2.0-flash-001"
    query_model: str = "google/gemini-2.5-flash"
    max_concurrent_requests: int = 5


@dataclass
class EmbeddingConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "openai/text-embedding-3-small"
    dimensions: int = 512


@dataclass
class ChunkingConfig:
    max_chunk_tokens: int = 512
    overlap_tokens: int = 50


@dataclass
class RetrievalConfig:
    default_mode: str = "hybrid"
    default_namespaces: list[str] = field(default_factory=list)
    top_k: int = 5
    rrf_k: int = 60
    graph_traversal_depth: int = 2
    chunk_context_default_window: int = 2
    max_entity_tokens: int = 4000
    max_relation_tokens: int = 4000
    max_total_tokens: int = 16000
    enable_community_search: bool = True
    enable_gleaning: bool = False
    enable_batch_contextual_enrichment: bool = False
    batch_contextual_enrichment_size: int = 5
    enable_llm_summary: bool = False
    llm_summary_min_descriptions: int = 6
    show_references: bool = False
    query_response_markdown: bool = False


@dataclass
class StorageConfig:
    data_dir: str = "./data"


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    namespaces: list[NamespaceConfig] = field(default_factory=list)
    google_drive: GoogleDriveConfig = field(default_factory=GoogleDriveConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _resolve_env_vars(value):
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            return match.group(0)
        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


_TYPE_MAP = {"int": int, "float": float, "bool": bool, "str": str}


def _coerce(value, type_str: str):
    if not isinstance(value, str):
        return value
    if type_str == "bool":
        return value.lower() in ("true", "1", "yes")
    target = _TYPE_MAP.get(type_str)
    if target is not None:
        return target(value)
    return value


def _build_dataclass(cls, data: dict):
    if data is None:
        return cls()
    filtered = {}
    for f_name, f_obj in cls.__dataclass_fields__.items():
        if f_name in data:
            val = data[f_name]
            if isinstance(val, str) and f_obj.type in _TYPE_MAP:
                val = _coerce(val, f_obj.type)
            filtered[f_name] = val
    return cls(**filtered)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str = "config.yaml") -> AppConfig:
    default_path = Path(__file__).parent / "config.default.yaml"
    if not default_path.exists():
        raise FileNotFoundError(f"Default config not found: {default_path}")

    with open(default_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config_path = Path(path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_raw = yaml.safe_load(f) or {}
        raw = _deep_merge(raw, user_raw)

    raw = _resolve_env_vars(raw) or {}

    namespaces = []
    for ns_data in raw.get("namespaces", []):
        namespaces.append(_build_dataclass(NamespaceConfig, ns_data))

    return AppConfig(
        server=_build_dataclass(ServerConfig, raw.get("server")),
        auth=_build_dataclass(AuthConfig, raw.get("auth")),
        namespaces=namespaces,
        google_drive=_build_dataclass(GoogleDriveConfig, raw.get("google_drive")),
        llm=_build_dataclass(LLMConfig, raw.get("llm")),
        embedding=_build_dataclass(EmbeddingConfig, raw.get("embedding")),
        chunking=_build_dataclass(ChunkingConfig, raw.get("chunking")),
        retrieval=_build_dataclass(RetrievalConfig, raw.get("retrieval")),
        storage=_build_dataclass(StorageConfig, raw.get("storage")),
    )
