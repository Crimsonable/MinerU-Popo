from __future__ import annotations

import copy
from typing import Any

from post_processing.table_merge_utils import merge_cross_page_tables

SPECIAL_TYPES = [
    "table_footnote",
    "table",
    "chart",
    "table_caption",
    "image_footnote",
    "image",
    "image_caption",
    "seal",
]
LARGE_BLOCK_TYPES = ["super", "list", "ref_block", "equation_block", "image_block"]
SUPPLEMENT_TYPES = ["page_title", "page_number", "page_footnote", "header", "aside_text", "footer"]
SUPPLEMENT_SOURCE_LABEL_MAP = {
    "page_title": "page_title",
    "page_number": "page_number",
    "page_footnote": "page_footnote",
    "header": "header",
    "aside_text": "aside_text",
    "footer": "footer",
    "number": "page_number",
    "footnote": "page_footnote",
}


def cp_init(
    cp_type: str = "",
    title: str = "",
    metadata: str = "",
    content: str = "",
    level: int = -1,
    location: list[dict[str, Any]] | None = None,
    block_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "type": cp_type,
        "title": title,
        "metadata": metadata,
        "content": content,
        "level": level,
        "location": [] if location is None else location,
        "block_ids": [] if block_ids is None else block_ids,
    }


def construct_json_tree_obj(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the final document tree from Popo-enhanced blocks in memory."""
    elements = copy.deepcopy(elements)

    for element in elements:
        source_label = element.get("source_label")
        if element.get("type") == "text" and source_label in SUPPLEMENT_SOURCE_LABEL_MAP:
            element["type"] = SUPPLEMENT_SOURCE_LABEL_MAP[source_label]

    elements = merge_cross_page_tables(elements)

    def get_text_components(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        text_components: list[dict[str, Any]] = []
        contd_list: list[int] = []
        cur_text_title = "Default Title"
        cur_text_cp = cp_init(cp_type="text", title=cur_text_title, level=1)

        for element in items:
            if element["type"] == "title" and element.get("level", -1) < 0:
                element["type"] = "text"

            if element["type"] == "title":
                cur_text_title = element.get("content", "")
                if cur_text_cp["title"] != "Default Title" or cur_text_cp["content"] != "":
                    text_components.append(cur_text_cp)
                cur_text_cp = cp_init(
                    cp_type="text",
                    title=cur_text_title,
                    level=element.get("level", -1),
                    location=[{"bbox": element.get("bbox"), "page": element.get("page")}],
                    block_ids=[element.get("id")],
                )
            elif element["type"] not in SPECIAL_TYPES + LARGE_BLOCK_TYPES + SUPPLEMENT_TYPES:
                if element.get("contd", -1) >= 0:
                    contd_list.append(element["contd"])
                contd_label = "<|txt_contd|>" if element.get("id") in contd_list else "<|txt_split|>"
                content = element.get("content", "")
                cur_text_cp["content"] = (
                    cur_text_cp["content"] + contd_label + content if cur_text_cp["content"] else content
                )
                cur_text_cp["location"].append({"bbox": element.get("bbox"), "page": element.get("page")})
                cur_text_cp["block_ids"].append(element.get("id"))

        text_components.append(cur_text_cp)
        return text_components

    def construct_by_level(text_components: list[dict[str, Any]]) -> dict[str, Any]:
        root = cp_init(cp_type="root", level=0)
        root["children"] = []
        stack = [{"node": root, "level": 0}]

        for cp in text_components:
            cp["children"] = []
            level = cp["level"] if cp.get("level", -1) > 0 else 100
            while stack[-1]["level"] >= level:
                stack.pop()
            parent = stack[-1]["node"]
            parent["children"].append(cp)
            stack.append({"node": cp, "level": level})

        return root

    text_tree = construct_by_level(get_text_components(elements))

    def add_special_elements(text_tree: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        visual_components: list[dict[str, Any]] = []
        for element in items:
            if element.get("type") in ["table", "chart", "image", "seal", "image_block"]:
                locations = element.get(
                    "merged_locations",
                    [{"bbox": element.get("bbox"), "page": element.get("page")}],
                )
                block_ids = element.get("merged_block_ids", [element.get("id")])
                visual_component = cp_init(
                    cp_type=element.get("type", ""),
                    content=element.get("content", ""),
                    level=element.get("image", -1),
                    location=locations,
                    block_ids=block_ids,
                )

                for elem in items:
                    if elem.get("image") == element.get("id"):
                        if "caption" in elem.get("type", ""):
                            visual_component["title"] = (
                                visual_component["title"] + " " + elem.get("content", "")
                                if visual_component["title"]
                                else elem.get("content", "")
                            )
                            visual_component["location"].append({"bbox": elem.get("bbox"), "page": elem.get("page")})
                            visual_component["block_ids"].append(elem.get("id"))
                        elif "footnote" in elem.get("type", ""):
                            visual_component["metadata"] = (
                                visual_component["metadata"] + " " + elem.get("content", "")
                                if visual_component["metadata"]
                                else elem.get("content", "")
                            )
                            visual_component["location"].append({"bbox": elem.get("bbox"), "page": elem.get("page")})
                            visual_component["block_ids"].append(elem.get("id"))
                visual_components.append(visual_component)

        for visual_component in visual_components:
            visual_component["children"] = []

        for visual_component in list(visual_components):
            for v_cp in list(visual_components):
                if visual_component.get("level") in v_cp.get("block_ids", []):
                    v_cp["children"].append(visual_component)
                    if visual_component in visual_components:
                        visual_components.remove(visual_component)

        def get_node_by_id(root: dict[str, Any], idx: int | None) -> dict[str, Any] | None:
            if idx in root.get("block_ids", []):
                return root
            for child in root.get("children", []):
                found = get_node_by_id(child, idx)
                if found is not None:
                    return found
            return None

        def find_former_title(items: list[dict[str, Any]], idx: int | None) -> int:
            former_title = 0
            if idx is None:
                return former_title
            for element in items:
                if element.get("type") == "title" and element.get("id", 0) < idx and element.get("id", 0) > former_title:
                    former_title = element.get("id", 0)
            return former_title

        for visual_component in visual_components:
            block_ids = [bid for bid in visual_component.get("block_ids", []) if bid is not None]
            if not block_ids:
                continue
            idx = (
                visual_component.get("level")
                if visual_component.get("level", -1) >= 0
                else find_former_title(items, min(block_ids))
            )
            tree_node = get_node_by_id(text_tree, idx)
            if tree_node:
                tree_node["children"].append(visual_component)

        return text_tree

    add_special_elements(text_tree, elements)

    def add_supplement(text_tree: dict[str, Any], items: list[dict[str, Any]]) -> None:
        existing_titles: list[str] = []
        for element in items:
            if element.get("type") in SUPPLEMENT_TYPES:
                title = f"Page {element.get('page')} - {element.get('type')}"
                cnt = 0
                while title in existing_titles:
                    cnt += 1
                    title = f"Page {element.get('page')} - {element.get('type')} - {cnt}"
                existing_titles.append(title)
                supp_component = cp_init(
                    cp_type=element.get("type", ""),
                    title=title,
                    metadata=element.get("content", ""),
                    content=element.get("content", ""),
                    location=[{"bbox": element.get("bbox"), "page": element.get("page")}],
                    block_ids=[element.get("id")],
                )
                text_tree["children"].append(supp_component)

    add_supplement(text_tree, elements)
    return text_tree


def tree_to_txt(tree: dict[str, Any]) -> str:
    lines: list[str] = []

    def traverse(node: dict[str, Any], depth: int = 0) -> None:
        indent = " " * (depth * 4)
        title = node.get("title", "N/A")
        data = node.get("content", "") or ""
        data_preview = data[:30] + ("..." if len(data) > 30 else "")
        lines.append(f"{indent}{title}|{data_preview}")
        for child in node.get("children", []):
            traverse(child, depth + 1)

    traverse(tree)
    return "\n".join(lines)
