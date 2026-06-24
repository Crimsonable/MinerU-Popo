# MinerU-Popo In-Memory Service

This service wraps MinerU OCR and MinerU-Popo post-processing into one HTTP API.

The aggregation service keeps intermediate data in memory:

```text
PDF upload
  -> MinerU /file_parse
  -> middle_json response body
  -> Popo pages object
  -> Popo-enhanced blocks
  -> document tree
  -> JSON response
```

It does **not** write these files on the aggregation-service side:

```text
*_middle.json
outputs/inference/*.json
outputs/build_tree/*.json
```

MinerU's own `/file_parse` service may still persist its internal task files in its own container or host output directory. To avoid physical disk writes there, configure the MinerU output root to a tmpfs volume or patch MinerU itself.

## Endpoints

### `POST /v1/document/parse`

Full pipeline:

```text
PDF -> MinerU OCR -> MinerU-Popo -> tree JSON
```

Request type: `multipart/form-data`

Fields:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `file` | file | required | PDF file |
| `lang` | string | `MINERU_LANG` or `ch` | OCR language |
| `parse_method` | string | `MINERU_PARSE_METHOD` or `auto` | `auto`, `txt`, or `ocr` |
| `mineru_backend` | string | `MINERU_BACKEND` or `pipeline` | MinerU backend |
| `formula_enable` | bool | `true` | Enable formula parsing |
| `table_enable` | bool | `true` | Enable table parsing |
| `image_analysis` | bool | `true` | Enable image/chart analysis |
| `start_page_id` | int | `0` | Start page, zero-based |
| `end_page_id` | int | `99999` | End page, zero-based |
| `return_blocks` | bool | `true` | Return Popo-enhanced block list |
| `return_tree_txt` | bool | `false` | Return text preview of the tree |
| `return_middle_json` | bool | `false` | Include MinerU middle_json in the response |

Example:

```bash
curl -X POST http://127.0.0.1:9000/v1/document/parse \
  -F "file=@test.pdf" \
  -F "lang=ch" \
  -F "parse_method=auto" \
  -F "mineru_backend=pipeline" \
  -F "return_blocks=true" \
  -F "return_tree_txt=true"
```

Response shape:

```json
{
  "request_id": "...",
  "doc_id": "test",
  "status": "succeeded",
  "mineru": {
    "backend": "pipeline",
    "version": "..."
  },
  "popo": {
    "model": "Popo",
    "base_url": "http://127.0.0.1:8000/v1"
  },
  "result": {
    "tree": {},
    "blocks": [],
    "tree_txt": ""
  },
  "timing_ms": {
    "mineru": 0,
    "normalize": 0,
    "popo": 0,
    "build_tree": 0,
    "total": 0
  }
}
```

### `POST /v1/document/popo`

Popo-only debug endpoint. It skips MinerU and accepts either MinerU `middle_json` or already-normalized Popo `pages`.

Request type: `application/json`

```json
{
  "doc_id": "test",
  "pdf_base64": "...",
  "middle_json": {
    "pdf_info": []
  },
  "return_blocks": true,
  "return_tree_txt": true
}
```

or:

```json
{
  "doc_id": "test",
  "pdf_base64": "...",
  "pages": {
    "1": [
      {
        "type": "title",
        "content": "1 总则",
        "bbox": [0.1, 0.1, 0.8, 0.15]
      }
    ]
  }
}
```

### `GET /health`

Returns configured service endpoints.

## Environment Variables

```bash
# MinerU OCR service
export MINERU_FILE_PARSE_URL=http://127.0.0.1:8888/file_parse
export MINERU_TIMEOUT=600
export MINERU_BACKEND=pipeline
export MINERU_PARSE_METHOD=auto
export MINERU_LANG=ch

# Popo vLLM service
export POPO_VLLM_BASE_URL=http://127.0.0.1:8000/v1
export POPO_VLLM_API_KEY=EMPTY
export POPO_VLLM_MODEL=Popo
export POPO_TIMEOUT=300
export POPO_MAX_TOKENS=8192
export POPO_TEMPERATURE=0

# Aggregation service
export SERVICE_MAX_POPO_CONCURRENCY=1
```

## Start vLLM for Popo

```bash
vllm serve /models/MinerU-Popo \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name Popo \
  --api-key EMPTY \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --limit-mm-per-prompt '{"image":1}'
```

## Start the Aggregation Service

Install the extra service dependencies:

```bash
pip install -r requirements-service.txt
```

Start from the repository root:

```bash
uvicorn service.app:app --host 0.0.0.0 --port 9000
```

## Design Notes

The upstream Popo implementation is file-oriented. The service avoids a large invasive rewrite by temporarily patching, under a process-local lock:

- PDF page rendering: render from uploaded `pdf_bytes` instead of opening a PDF path.
- Popo generation: call the configured vLLM OpenAI-compatible endpoint.
- Final block JSON write: capture the exact final write into an in-memory buffer.

Because these are module-level patches, `SERVICE_MAX_POPO_CONCURRENCY` defaults to `1`. Increase it only after replacing the patch-based runtime with a fully refactored pure function implementation.
