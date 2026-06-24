from __future__ import annotations

from typing import Any

from post_processing.label_normalization import (
    NormalizedBlock,
    SKIP_TYPE,
    extract_block_content,
    finalize_reader_result,
    map_mineru_label,
    normalize_bbox_to_unit,
    to_popo_pages,
)


def middle_json_to_popo_pages(doc_id: str, middle_json: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert MinerU middle_json to the Popo post-processing pages schema.

    Input shape expected from MinerU middle_json:
    {
      "pdf_info": [
        {"page_idx": 0, "page_size": [w, h], "para_blocks": [...]}
      ]
    }

    Output shape consumed by post_processing.inference.main:
    {
      "1": [{"type": "title", "content": "...", "bbox": [0,0,1,1]}]
    }
    """
    if not isinstance(middle_json, dict):
        raise TypeError("middle_json must be a dict")

    blocks: list[NormalizedBlock] = []
    order = 0

    for page in middle_json.get("pdf_info", []):
        if not isinstance(page, dict):
            continue
        page_index = int(page.get("page_idx", 0)) + 1
        page_size = page.get("page_size") or [None, None]
        page_width = page_size[0] if len(page_size) > 0 else None
        page_height = page_size[1] if len(page_size) > 1 else None

        for item in page.get("para_blocks", []):
            if not isinstance(item, dict):
                continue

            canonical, popo_type = map_mineru_label(str(item.get("type", "text")))
            if popo_type == SKIP_TYPE:
                continue

            content = extract_block_content(item)
            if not content and canonical in {"text", "title", "caption"}:
                continue

            blocks.append(
                NormalizedBlock(
                    block_id=f"{doc_id}:{order}",
                    page=page_index,
                    bbox=normalize_bbox_to_unit(
                        item.get("bbox", [0, 0, 0, 0]),
                        page_width,
                        page_height,
                    ),
                    type=canonical,
                    content=content,
                    order=order,
                    popo_type=popo_type,
                    source_label=str(item.get("type", "")),
                )
            )
            order += 1

    result = finalize_reader_result("mineru", doc_id, blocks)
    return to_popo_pages(result.blocks)
