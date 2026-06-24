from __future__ import annotations

import asyncio
import base64
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from service.adapters.middle_to_popo import middle_json_to_popo_pages
from service.adapters.tree_builder import construct_json_tree_obj, tree_to_txt
from service.clients.mineru_client import MinerUClient, MinerUParseOptions
from service.config import ServiceConfig, get_service_config
from service.runtime.popo_memory import run_popo_in_memory

app = FastAPI(title="MinerU-Popo In-Memory Service", version="0.1.0")

_popo_semaphore: asyncio.Semaphore | None = None


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _config() -> ServiceConfig:
    return get_service_config()


def _popo_gate(config: ServiceConfig) -> asyncio.Semaphore:
    global _popo_semaphore
    if _popo_semaphore is None:
        _popo_semaphore = asyncio.Semaphore(max(1, config.max_popo_concurrency))
    return _popo_semaphore


def _build_response(
    *,
    request_id: str,
    doc_id: str,
    mineru_meta: dict[str, Any] | None,
    blocks: list[dict[str, Any]],
    return_blocks: bool,
    return_tree_txt: bool,
    middle_json: dict[str, Any] | None,
    return_middle_json: bool,
    timing_ms: dict[str, int],
    config: ServiceConfig,
) -> JSONResponse:
    tree = construct_json_tree_obj(blocks)
    result: dict[str, Any] = {"tree": tree}
    if return_blocks:
        result["blocks"] = blocks
    if return_tree_txt:
        result["tree_txt"] = tree_to_txt(tree)
    if return_middle_json and middle_json is not None:
        result["middle_json"] = middle_json

    return JSONResponse(
        {
            "request_id": request_id,
            "doc_id": doc_id,
            "status": "succeeded",
            "mineru": mineru_meta,
            "popo": {"model": config.popo.model, "base_url": config.popo.base_url},
            "result": result,
            "timing_ms": timing_ms,
        }
    )


@app.post("/v1/document/parse")
async def parse_document(
    file: UploadFile = File(...),
    lang: str | None = Form(None),
    parse_method: str | None = Form(None),
    mineru_backend: str | None = Form(None),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True),
    image_analysis: bool = Form(True),
    start_page_id: int = Form(0),
    end_page_id: int = Form(99999),
    return_blocks: bool = Form(True),
    return_tree_txt: bool = Form(False),
    return_middle_json: bool = Form(False),
):
    """Parse PDF with MinerU, then run MinerU-Popo post-processing in memory.

    No middle_json file, inference JSON file, or tree JSON file is written by
    this service. MinerU's own /file_parse implementation may still persist its
    internal task files on the MinerU service side.
    """
    request_id = str(uuid.uuid4())
    config = _config()
    total_start = _now_ms()

    filename = file.filename or f"{request_id}.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="only PDF files are supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    mineru_client = MinerUClient(
        file_parse_url=config.mineru.file_parse_url,
        timeout_seconds=config.mineru.timeout_seconds,
    )

    t0 = _now_ms()
    try:
        doc_id, middle_json, mineru_meta = await mineru_client.parse_pdf_to_middle(
            pdf_bytes=pdf_bytes,
            filename=filename,
            options=MinerUParseOptions(
                lang=lang or config.mineru.lang,
                backend=mineru_backend or config.mineru.backend,
                parse_method=parse_method or config.mineru.parse_method,
                formula_enable=formula_enable,
                table_enable=table_enable,
                image_analysis=image_analysis,
                start_page_id=start_page_id,
                end_page_id=end_page_id,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinerU /file_parse failed: {exc}") from exc
    t1 = _now_ms()

    try:
        pages = middle_json_to_popo_pages(doc_id, middle_json)
        if not pages:
            raise ValueError("converted Popo pages is empty")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"middle_json cannot be converted to Popo pages: {exc}") from exc
    t2 = _now_ms()

    try:
        async with _popo_gate(config):
            blocks = await asyncio.to_thread(
                run_popo_in_memory,
                doc_id=doc_id,
                pdf_bytes=pdf_bytes,
                pages=pages,
                config=config.popo,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Popo inference failed: {exc}") from exc
    t3 = _now_ms()

    try:
        response = _build_response(
            request_id=request_id,
            doc_id=doc_id,
            mineru_meta=mineru_meta,
            blocks=blocks,
            return_blocks=return_blocks,
            return_tree_txt=return_tree_txt,
            middle_json=middle_json,
            return_middle_json=return_middle_json,
            timing_ms={
                "mineru": t1 - t0,
                "normalize": t2 - t1,
                "popo": t3 - t2,
                "build_tree": _now_ms() - t3,
                "total": _now_ms() - total_start,
            },
            config=config,
        )
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"tree build failed: {exc}") from exc


class PopoOnlyRequest(BaseModel):
    doc_id: str = "document"
    pdf_base64: str = Field(..., description="Base64-encoded source PDF bytes")
    middle_json: dict[str, Any] | None = None
    pages: dict[str, list[dict[str, Any]]] | None = None
    return_blocks: bool = True
    return_tree_txt: bool = False


@app.post("/v1/document/popo")
async def popo_only(payload: PopoOnlyRequest):
    """Run only Popo post-processing from either middle_json or normalized pages."""
    request_id = str(uuid.uuid4())
    config = _config()
    total_start = _now_ms()

    try:
        pdf_bytes = base64.b64decode(payload.pdf_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid pdf_base64: {exc}") from exc

    if payload.pages is not None:
        pages = payload.pages
    elif payload.middle_json is not None:
        pages = middle_json_to_popo_pages(payload.doc_id, payload.middle_json)
    else:
        raise HTTPException(status_code=400, detail="either pages or middle_json is required")

    if not pages:
        raise HTTPException(status_code=422, detail="Popo pages is empty")

    t0 = _now_ms()
    try:
        async with _popo_gate(config):
            blocks = await asyncio.to_thread(
                run_popo_in_memory,
                doc_id=payload.doc_id,
                pdf_bytes=pdf_bytes,
                pages=pages,
                config=config.popo,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Popo inference failed: {exc}") from exc
    t1 = _now_ms()

    return _build_response(
        request_id=request_id,
        doc_id=payload.doc_id,
        mineru_meta=None,
        blocks=blocks,
        return_blocks=payload.return_blocks,
        return_tree_txt=payload.return_tree_txt,
        middle_json=payload.middle_json,
        return_middle_json=False,
        timing_ms={
            "mineru": 0,
            "normalize": 0,
            "popo": t1 - t0,
            "build_tree": _now_ms() - t1,
            "total": _now_ms() - total_start,
        },
        config=config,
    )


@app.get("/health")
async def health():
    try:
        config = _config()
        return {
            "status": "ok",
            "mineru_file_parse_url": config.mineru.file_parse_url,
            "popo_base_url": config.popo.base_url,
            "popo_model": config.popo.model,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
