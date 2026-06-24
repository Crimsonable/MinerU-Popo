from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MinerUConfig:
    file_parse_url: str
    timeout_seconds: float = 600.0
    backend: str = "pipeline"
    parse_method: str = "auto"
    lang: str = "ch"


@dataclass(frozen=True)
class PopoConfig:
    base_url: str
    api_key: str = "EMPTY"
    model: str = "Popo"
    timeout_seconds: float = 300.0
    max_tokens: int = 8192
    temperature: float = 0.0
    max_prompt_chars: int = 100000


@dataclass(frozen=True)
class ServiceConfig:
    mineru: MinerUConfig
    popo: PopoConfig
    max_popo_concurrency: int = 1


def get_service_config() -> ServiceConfig:
    file_parse_url = os.getenv("MINERU_FILE_PARSE_URL", "").strip()
    if not file_parse_url:
        raise RuntimeError("MINERU_FILE_PARSE_URL is required, for example http://mineru-api:8000/file_parse")

    popo_base_url = os.getenv("POPO_VLLM_BASE_URL", "").strip()
    if not popo_base_url:
        raise RuntimeError("POPO_VLLM_BASE_URL is required, for example http://popo-vllm:8000/v1")

    return ServiceConfig(
        mineru=MinerUConfig(
            file_parse_url=file_parse_url,
            timeout_seconds=float(os.getenv("MINERU_TIMEOUT", "600")),
            backend=os.getenv("MINERU_BACKEND", "pipeline"),
            parse_method=os.getenv("MINERU_PARSE_METHOD", "auto"),
            lang=os.getenv("MINERU_LANG", "ch"),
        ),
        popo=PopoConfig(
            base_url=popo_base_url,
            api_key=os.getenv("POPO_VLLM_API_KEY", "EMPTY"),
            model=os.getenv("POPO_VLLM_MODEL", "Popo"),
            timeout_seconds=float(os.getenv("POPO_TIMEOUT", "300")),
            max_tokens=int(os.getenv("POPO_MAX_TOKENS", "8192")),
            temperature=float(os.getenv("POPO_TEMPERATURE", "0")),
            max_prompt_chars=int(os.getenv("POPO_MAX_PROMPT_CHARS", "100000")),
        ),
        max_popo_concurrency=int(os.getenv("SERVICE_MAX_POPO_CONCURRENCY", "1")),
    )
