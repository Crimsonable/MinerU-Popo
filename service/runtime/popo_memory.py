from __future__ import annotations

import base64
import builtins
import copy
import io
import json
import os
import re
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import fitz
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from service.config import PopoConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
POST_PROCESSING_DIR = REPO_ROOT / "post_processing"
DATA_ENGINE_DIR = REPO_ROOT / "data_engine"
for path in (POST_PROCESSING_DIR, DATA_ENGINE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Import after sys.path adjustment because post_processing/inference.py uses
# module-level imports such as "from model_utils import *".
import inference as popo_inference  # noqa: E402

_PATCH_LOCK = threading.Lock()


class _NonClosingStringIO(io.StringIO):
    def close(self) -> None:  # keep getvalue() usable after a with-block
        self.flush()


def _safe_doc_id(doc_id: str) -> str:
    value = Path(str(doc_id)).stem or "document"
    return re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", value)


def render_pdf_pages_to_base64(
    pdf_bytes: bytes,
    pages: list[int],
    border_width: int = 5,
    border_color: str = "black",
) -> str:
    """Render selected PDF pages into one vertically concatenated JPEG image."""
    if not pdf_bytes:
        raise ValueError("pdf_bytes is empty")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pil_images: list[Image.Image] = []
    try:
        if not pages:
            pages = [1]

        for page_num in pages:
            page = doc[page_num - 1]
            pix = page.get_pixmap()
            img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")

            draw = ImageDraw.Draw(img)
            avg_cell_size = (page.rect.width + page.rect.height) / 2
            font_size = max(12, int(avg_cell_size * 0.1))
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
            draw.text((10, 10), str(page_num), fill=(255, 0, 0), font=font)
            pil_images.append(img)
    finally:
        doc.close()

    if not pil_images:
        raise ValueError("no PDF pages were rendered")

    total_height = sum(img.height for img in pil_images) + border_width * (len(pil_images) - 1)
    max_width = max(img.width for img in pil_images)
    result = Image.new("RGB", (max_width, total_height), color="white")

    y_offset = 0
    for i, img in enumerate(pil_images):
        x_offset = (max_width - img.width) // 2
        result.paste(img, (x_offset, y_offset))
        if i < len(pil_images) - 1:
            y_border = y_offset + img.height
            draw = ImageDraw.Draw(result)
            draw.rectangle([0, y_border, max_width - 1, y_border + border_width - 1], fill=border_color)
            y_offset = y_border + border_width
        else:
            y_offset += img.height

    buffered = io.BytesIO()
    result.save(buffered, format="JPEG", quality=100, optimize=True)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def _build_messages(prompt: str, base64_image: str | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if base64_image:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
            }
        )
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def generate_with_vllm(prompt: str, base64_image: str | None, config: PopoConfig) -> str:
    """Call the Popo model through a vLLM OpenAI-compatible endpoint."""
    prompt = prompt[: config.max_prompt_chars] if len(prompt) > config.max_prompt_chars else prompt
    client = OpenAI(base_url=config.base_url, api_key=config.api_key, timeout=config.timeout_seconds)
    response = client.chat.completions.create(
        model=config.model,
        messages=_build_messages(prompt, base64_image),
        max_tokens=config.max_tokens,
        temperature=config.temperature,
    )
    return response.choices[0].message.content or ""


@contextmanager
def _capture_json_write(target_path: str) -> Iterator[_NonClosingStringIO]:
    """Capture the final JSON file write performed by inference.main()."""
    target_path = os.path.normpath(target_path)
    old_open = builtins.open
    buffer = _NonClosingStringIO()

    def patched_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        try:
            current_path = os.path.normpath(os.fspath(file))
        except TypeError:
            current_path = ""
        if current_path == target_path and "w" in mode:
            return buffer
        return old_open(file, mode, *args, **kwargs)

    builtins.open = patched_open
    try:
        yield buffer
    finally:
        builtins.open = old_open


def run_popo_in_memory(
    *,
    doc_id: str,
    pdf_bytes: bytes,
    pages: dict[str, list[dict[str, Any]]],
    config: PopoConfig,
) -> list[dict[str, Any]]:
    """Run the original Popo pipeline without writing middle or final JSON files.

    The upstream inference.main() writes its final block list to a JSON file and
    renders images by opening a PDF path. To keep this service-side flow
    in-memory while minimizing upstream edits, this function temporarily patches:

    - popo_inference.concatenate_pdf_pages_with_border -> render from pdf_bytes
    - popo_inference.popo_generate -> call vLLM from service config
    - builtins.open for the exact final output path -> capture StringIO

    The patch is protected by a process-local lock because these are module-level
    globals in the upstream implementation.
    """
    if not isinstance(pages, dict) or not pages:
        raise ValueError("pages must be a non-empty dict")

    safe_doc_id = _safe_doc_id(doc_id)
    input_label = f"{safe_doc_id}.pdf"
    output_dir = f"/__popo_memory_output__/{uuid.uuid4().hex}"
    target_path = os.path.normpath(os.path.join(output_dir, f"{safe_doc_id}.json"))

    def render_from_bytes(_input_label: str, selected_pages: list[int], border_width: int = 5, border_color: str = "black") -> str:
        return render_pdf_pages_to_base64(pdf_bytes, selected_pages, border_width, border_color)

    def configured_generate(prompt: str, base64_image: str | None) -> str:
        return generate_with_vllm(prompt, base64_image, config)

    with _PATCH_LOCK:
        old_renderer = popo_inference.concatenate_pdf_pages_with_border
        old_generate = popo_inference.popo_generate
        popo_inference.concatenate_pdf_pages_with_border = render_from_bytes
        popo_inference.popo_generate = configured_generate
        try:
            with _capture_json_write(target_path) as captured:
                popo_inference.main(
                    input_label,
                    copy.deepcopy(pages),
                    output_dir,
                    raw_output_dir=None,
                )
            raw = captured.getvalue()
            if not raw.strip():
                raise RuntimeError("Popo inference finished without producing a block JSON payload")
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise RuntimeError(f"Popo block payload must be a list, got {type(parsed).__name__}")
            return parsed
        finally:
            popo_inference.concatenate_pdf_pages_with_border = old_renderer
            popo_inference.popo_generate = old_generate
