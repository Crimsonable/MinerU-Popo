from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class MinerUParseOptions:
    lang: str = "ch"
    backend: str = "pipeline"
    parse_method: str = "auto"
    formula_enable: bool = True
    table_enable: bool = True
    image_analysis: bool = True
    start_page_id: int = 0
    end_page_id: int = 99999


class MinerUClient:
    """Client for MinerU's /file_parse endpoint.

    This wrapper asks MinerU to return middle_json in the HTTP response. The
    aggregation service keeps that payload in memory and does not write the
    middle file locally.
    """

    def __init__(self, file_parse_url: str, timeout_seconds: float = 600.0) -> None:
        self.file_parse_url = file_parse_url.rstrip("/") if file_parse_url.endswith("/file_parse/") else file_parse_url
        self.timeout_seconds = timeout_seconds

    async def parse_pdf_to_middle(
        self,
        *,
        pdf_bytes: bytes,
        filename: str,
        options: MinerUParseOptions,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if not pdf_bytes:
            raise ValueError("pdf_bytes is empty")

        form_data = {
            "lang_list": options.lang,
            "backend": options.backend,
            "parse_method": options.parse_method,
            "formula_enable": str(options.formula_enable).lower(),
            "table_enable": str(options.table_enable).lower(),
            "image_analysis": str(options.image_analysis).lower(),
            "return_md": "false",
            "return_middle_json": "true",
            "return_model_output": "false",
            "return_content_list": "false",
            "return_images": "false",
            "response_format_zip": "false",
            "return_original_file": "false",
            "client_side_output_generation": "false",
            "start_page_id": str(options.start_page_id),
            "end_page_id": str(options.end_page_id),
        }
        files = {
            "files": (filename, pdf_bytes, "application/pdf"),
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.file_parse_url, data=form_data, files=files)

        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") or {}
        if not isinstance(results, dict) or not results:
            raise RuntimeError(f"MinerU returned empty results: {payload}")

        doc_id, result_item = next(iter(results.items()))
        if not isinstance(result_item, dict):
            raise RuntimeError(f"MinerU result item is not an object: {type(result_item).__name__}")

        middle_raw = result_item.get("middle_json")
        if middle_raw is None:
            raise RuntimeError(f"MinerU response missing middle_json for {doc_id}")

        if isinstance(middle_raw, str):
            middle_json = json.loads(middle_raw)
        elif isinstance(middle_raw, dict):
            middle_json = middle_raw
        else:
            raise RuntimeError(f"Unsupported middle_json type: {type(middle_raw).__name__}")

        meta = {
            "backend": payload.get("backend"),
            "version": payload.get("version"),
        }
        return str(doc_id), middle_json, meta
