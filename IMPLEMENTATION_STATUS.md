# Legere Implementation Status

## Current Status

The current maintained entrypoint is `hj.py`.
`paper_prcs.py` is retained as an older script and is not being extended.

The project now supports:

- Model selection through a unified OpenAI-compatible interface
- Local PDF text extraction before sending content to the model
- Structured paper scoring with five dimensions
- Batch score CSV output
- Service-layer functions intended for future frontend integration

## Implemented Changes

### 1. Service Layer

The core analysis flow has been moved into `analysis_service.py`.

Available service functions:

- `run_analysis_sync(request)`
- `submit_analysis_task(request)`
- `get_task_status(task_id)`

These functions return JSON-friendly structures and are suitable for a future FastAPI or Flask wrapper.

### 2. Model Selection

The CLI in `hj.py` now supports:

- `--provider`
- `--model`
- `--base-url`
- `--api-key`
- `--api-key-env`
- `--score`

For unified third-party gateways such as AiHubMix, the recommended mode is:

```powershell
--provider custom --base-url https://aihubmix.com/v1
```

### 3. PDF Reading Strategy

The previous remote `file-extract` and `fileid://...` workflow was incompatible with the tested AiHubMix route.

The current implementation now:

1. Reads PDF text locally with `pypdf`
2. Normalizes and truncates extracted text
3. Sends the text directly to the selected chat model
4. Parses scoring JSON in memory
5. Writes report markdown and optional CSV summary

### 4. Scoring

Scoring uses five dimensions:

- `innovation`
- `method_rigor`
- `experiment_quality`
- `writing_clarity`
- `application_value`

The model returns JSON-like scoring content, which is parsed into structured data.
At present, scoring is stored in memory and rendered into markdown and CSV, but not yet saved as a standalone `*_score.json` file.

## Real Test Results

Real tests against `https://aihubmix.com/v1` succeeded after switching to local PDF extraction.

Validated scenarios:

- `gpt-4o-mini` on local PDF analysis
- `qwen3.5-35b-a3b` on local PDF analysis with scoring enabled

Observed outcome:

- Report generation succeeded
- Model switching succeeded
- Scoring succeeded
- Score output was written into markdown

## Current Limitations

- A single run can only use one model configuration
- Report generation model and scoring model cannot yet be separated in one request
- There is no HTTP API layer yet
- Score output is not yet written to a standalone JSON file

## Recommended Next Steps

1. Add a dedicated score JSON output file such as `*_score.json`
2. Add separate model options for report generation and scoring
3. Wrap `analysis_service.py` with FastAPI endpoints for frontend integration
4. Add request/response examples for frontend developers

## Recommended Commit Scope

The files that should be pushed for this stage are:

- `analysis_service.py`
- `hj.py`
- `prompts.py`
- `tests/test_analysis_service.py`
- `requirements.txt`
- `.gitignore`
- `IMPLEMENTATION_STATUS.md`

The following should not be pushed:

- `__pycache__/`
