from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from openai import OpenAI
from pypdf import PdfReader

import prompts

DEFAULT_SYSTEM_MESSAGE = "You are a helpful assistant."
DEFAULT_SAVE_DIR = Path(r"D:\Papers MAS\graph learning\gnnllm_notes")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MAX_DOCUMENT_CHARS = 120000
SCORE_DIMENSIONS = (
    ("innovation", "创新性"),
    ("method_rigor", "方法严谨性"),
    ("experiment_quality", "实验充分性"),
    ("writing_clarity", "写作清晰度"),
    ("application_value", "应用价值"),
)
SCORE_SUMMARY_HEADERS = [
    "filename",
    "provider",
    "model",
    "innovation",
    "method_rigor",
    "experiment_quality",
    "writing_clarity",
    "application_value",
    "total_score",
    "overall_comment",
    "report_path",
    "score_status",
]
PROVIDER_PRESETS = {
    "dashscope": {
        "model": "qwen-long",
        "base_url": DASHSCOPE_BASE_URL,
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    "openai": {
        "model": None,
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "model": None,
        "base_url": DEEPSEEK_BASE_URL,
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "custom": {
        "model": None,
        "base_url": None,
        "api_key_env": None,
    },
}
SUPPORTED_SOURCE_TYPES = {
    "file_path",
    "folder_path",
    "uploaded_pdf_bytes",
    "downloaded_file_path",
}


@dataclass
class ModelConfig:
    """Model selection settings shared by CLI and future frontend adapters."""

    provider: str = "dashscope"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None


@dataclass
class ResolvedModelConfig:
    """Concrete model config with defaults and environment variables resolved."""

    provider: str
    model: str
    base_url: Optional[str]
    api_key: str
    api_key_env: Optional[str]


@dataclass
class AnalyzeRequest:
    """Frontend-oriented analysis request contract."""

    source_type: str
    prompt: str
    save_dir: str = str(DEFAULT_SAVE_DIR)
    enable_score: bool = False
    model_config: ModelConfig = field(default_factory=ModelConfig)
    file_path: Optional[str] = None
    folder_path: Optional[str] = None
    uploaded_pdf_bytes: Optional[bytes] = None
    uploaded_filename: Optional[str] = None
    downloaded_file_path: Optional[str] = None


@dataclass
class AnalysisResult:
    """Single-paper analysis result returned to CLI or future API handlers."""

    filename: str
    provider: str
    model: str
    report_markdown: str
    report_path: Optional[str]
    score: Optional[Dict[str, Any]]
    score_status: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "provider": self.provider,
            "model": self.model,
            "report_markdown": self.report_markdown,
            "report_path": self.report_path,
            "score": self.score,
            "score_status": self.score_status,
            "error": self.error,
        }


@dataclass
class BatchAnalysisResult:
    """Batch analysis result with optional score summary CSV."""

    results: List[AnalysisResult]
    summary_csv_path: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [result.to_dict() for result in self.results],
            "summary_csv_path": self.summary_csv_path,
        }


@dataclass
class TaskSnapshot:
    """Async task state for future frontend polling."""

    task_id: str
    status: str
    progress_message: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress_message": self.progress_message,
            "result": self.result,
            "error": self.error,
        }


_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_TASK_LOCK = threading.Lock()
_TASKS: Dict[str, TaskSnapshot] = {}


def resolve_prompt_value(prompt_value: str) -> str:
    """Resolve a prompt from literal text, a prompt symbol, or a text file path."""

    if not prompt_value:
        return prompts.thoroughly2

    prompt_path = Path(prompt_value)
    if prompt_path.is_file():
        return prompt_path.read_text(encoding="utf-8")

    return getattr(prompts, prompt_value, prompt_value)


def resolve_model_config(model_config: ModelConfig) -> ResolvedModelConfig:
    """Resolve provider presets and validate the final model configuration."""

    provider = (model_config.provider or "dashscope").strip().lower()
    if provider not in PROVIDER_PRESETS:
        raise ValueError(f"不支持的 provider: {provider}")

    preset = PROVIDER_PRESETS[provider]
    model = model_config.model or preset["model"]
    base_url = model_config.base_url or preset["base_url"]
    api_key_env = model_config.api_key_env or preset["api_key_env"]
    api_key = model_config.api_key or (os.environ.get(api_key_env) if api_key_env else None)

    if provider in {"openai", "deepseek"} and not model:
        raise ValueError(f"{provider} provider 需要显式提供模型名称。")
    if provider == "custom" and not base_url:
        raise ValueError("custom provider 必须提供 base_url。")
    if not model:
        raise ValueError("未解析到模型名称。")
    if not api_key:
        raise ValueError("未提供 API key，请使用 --api-key 或配置对应环境变量。")

    return ResolvedModelConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )


def parse_score_response(raw_text: str) -> Dict[str, Any]:
    """Parse the strict JSON score response and compute the total score."""

    json_text = _extract_json_object(raw_text)
    payload = json.loads(json_text)
    score: Dict[str, Any] = {}
    total_score = 0.0

    for key, _label in SCORE_DIMENSIONS:
        entry = payload.get(key)
        if not isinstance(entry, dict):
            raise ValueError(f"评分结果缺少维度: {key}")
        numeric_score = float(entry.get("score"))
        if numeric_score < 0 or numeric_score > 10:
            raise ValueError(f"{key} 分数超出范围: {numeric_score}")
        rounded_score = round(numeric_score, 2)
        score[key] = {
            "score": rounded_score,
            "reason": str(entry.get("reason", "")).strip(),
        }
        total_score += rounded_score

    score["total_score"] = round(total_score, 2)
    score["overall_comment"] = str(payload.get("overall_comment", "")).strip()
    return score


def run_analysis_sync(request: AnalyzeRequest) -> Union[AnalysisResult, BatchAnalysisResult]:
    """Run the full analysis pipeline and return JSON-friendly results."""

    _validate_request(request)
    save_dir = Path(request.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    input_files, temp_dir = _prepare_input_files(request)
    try:
        if request.source_type == "folder_path" and not input_files:
            summary_path = _write_score_summary_csv(save_dir, [])
            return BatchAnalysisResult(results=[], summary_csv_path=summary_path)

        resolved_model = resolve_model_config(request.model_config)
        prompt_text = resolve_prompt_value(request.prompt)
        client = _build_client(resolved_model)

        results = [
            _analyze_single_file(
                client=client,
                resolved_model=resolved_model,
                source_path=source_path,
                prompt_text=prompt_text,
                save_dir=save_dir,
                enable_score=request.enable_score,
            )
            for source_path in input_files
        ]

        if request.source_type == "folder_path":
            summary_path = _write_score_summary_csv(save_dir, results)
            return BatchAnalysisResult(results=results, summary_csv_path=summary_path)

        if not results:
            raise ValueError("未找到可分析的 PDF 文件。")
        return results[0]
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def submit_analysis_task(request: AnalyzeRequest) -> str:
    """Submit a background analysis task and return a task identifier."""

    task_id = uuid.uuid4().hex
    with _TASK_LOCK:
        _TASKS[task_id] = TaskSnapshot(
            task_id=task_id,
            status="pending",
            progress_message="任务已创建，等待执行。",
        )
    _TASK_EXECUTOR.submit(_run_background_task, task_id, request)
    return task_id


def get_task_status(task_id: str) -> TaskSnapshot:
    """Get the current snapshot for a submitted background task."""

    with _TASK_LOCK:
        snapshot = _TASKS.get(task_id)
        if snapshot is None:
            raise KeyError(f"任务不存在: {task_id}")
        return TaskSnapshot(
            task_id=snapshot.task_id,
            status=snapshot.status,
            progress_message=snapshot.progress_message,
            result=snapshot.result,
            error=snapshot.error,
        )


def _run_background_task(task_id: str, request: AnalyzeRequest) -> None:
    _update_task(task_id, status="running", progress_message="任务执行中。")
    try:
        result = run_analysis_sync(request)
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        _update_task(
            task_id,
            status="succeeded",
            progress_message="任务执行完成。",
            result=payload,
            error=None,
        )
    except Exception as exc:
        _update_task(
            task_id,
            status="failed",
            progress_message="任务执行失败。",
            result=None,
            error=str(exc),
        )


def _update_task(
    task_id: str,
    *,
    status: str,
    progress_message: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    with _TASK_LOCK:
        snapshot = _TASKS[task_id]
        snapshot.status = status
        snapshot.progress_message = progress_message
        snapshot.result = result
        snapshot.error = error


def _validate_request(request: AnalyzeRequest) -> None:
    if request.source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"不支持的 source_type: {request.source_type}")

    if request.source_type == "file_path" and not request.file_path:
        raise ValueError("source_type=file_path 时必须提供 file_path。")
    if request.source_type == "folder_path" and not request.folder_path:
        raise ValueError("source_type=folder_path 时必须提供 folder_path。")
    if request.source_type == "downloaded_file_path" and not request.downloaded_file_path:
        raise ValueError("source_type=downloaded_file_path 时必须提供 downloaded_file_path。")
    if request.source_type == "uploaded_pdf_bytes":
        if not request.uploaded_pdf_bytes:
            raise ValueError("source_type=uploaded_pdf_bytes 时必须提供 uploaded_pdf_bytes。")
        if not request.uploaded_filename:
            raise ValueError("source_type=uploaded_pdf_bytes 时必须提供 uploaded_filename。")


def _prepare_input_files(
    request: AnalyzeRequest,
) -> Tuple[List[Path], Optional[tempfile.TemporaryDirectory]]:
    if request.source_type == "file_path":
        file_path = Path(request.file_path)
        _assert_existing_file(file_path)
        return [file_path], None

    if request.source_type == "downloaded_file_path":
        file_path = Path(request.downloaded_file_path)
        _assert_existing_file(file_path)
        return [file_path], None

    if request.source_type == "folder_path":
        folder_path = Path(request.folder_path)
        if not folder_path.exists():
            raise FileNotFoundError(f"目录不存在: {folder_path}")
        if not folder_path.is_dir():
            raise NotADirectoryError(f"不是有效目录: {folder_path}")
        return sorted(path for path in folder_path.rglob("*.pdf") if path.is_file()), None

    temp_dir = tempfile.TemporaryDirectory()
    filename = Path(request.uploaded_filename or "uploaded.pdf").name
    temp_path = Path(temp_dir.name) / filename
    temp_path.write_bytes(request.uploaded_pdf_bytes or b"")
    return [temp_path], temp_dir


def _assert_existing_file(file_path: Path) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if not file_path.is_file():
        raise FileNotFoundError(f"不是有效文件: {file_path}")


def _build_client(model_config: ResolvedModelConfig) -> OpenAI:
    client_kwargs = {"api_key": model_config.api_key}
    if model_config.base_url:
        client_kwargs["base_url"] = model_config.base_url
    return OpenAI(**client_kwargs)


def _analyze_single_file(
    *,
    client: OpenAI,
    resolved_model: ResolvedModelConfig,
    source_path: Path,
    prompt_text: str,
    save_dir: Path,
    enable_score: bool,
) -> AnalysisResult:
    filename = source_path.name
    try:
        document_text = _extract_pdf_text(source_path)
        report_markdown = _generate_markdown_report(
            client,
            resolved_model,
            filename,
            prompt_text,
            document_text,
        )
        score = None
        score_status = "disabled"
        score_error = None

        if enable_score:
            try:
                score = _generate_paper_score(client, resolved_model, filename, document_text)
                score_status = "succeeded"
            except Exception as exc:
                score_status = "failed"
                score_error = str(exc)

        final_markdown = report_markdown.rstrip()
        if enable_score:
            final_markdown = f"{final_markdown}\n\n{_build_score_markdown(score, score_status, score_error)}"

        report_path = _get_unique_output_path(save_dir, f"{source_path.stem}_report", ".md")
        report_path.write_text(final_markdown, encoding="utf-8")

        return AnalysisResult(
            filename=filename,
            provider=resolved_model.provider,
            model=resolved_model.model,
            report_markdown=final_markdown,
            report_path=str(report_path),
            score=score,
            score_status=score_status,
            error=None,
        )
    except Exception as exc:
        return AnalysisResult(
            filename=filename,
            provider=resolved_model.provider,
            model=resolved_model.model,
            report_markdown="",
            report_path=None,
            score=None,
            score_status="failed" if enable_score else "disabled",
            error=str(exc),
        )


def _generate_markdown_report(
    client: OpenAI,
    resolved_model: ResolvedModelConfig,
    filename: str,
    prompt_text: str,
    document_text: str,
) -> str:
    completion = client.chat.completions.create(
        model=resolved_model.model,
        messages=[
            {"role": "system", "content": DEFAULT_SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": _build_analysis_prompt(filename, prompt_text, document_text),
            },
        ],
        stream=True,
        stream_options={"include_usage": True},
    )
    content = _collect_stream_content(completion)
    if not content.strip():
        raise ValueError("模型未返回报告内容。")
    return content


def _generate_paper_score(
    client: OpenAI,
    resolved_model: ResolvedModelConfig,
    filename: str,
    document_text: str,
) -> Dict[str, Any]:
    completion = client.chat.completions.create(
        model=resolved_model.model,
        messages=[
            {"role": "system", "content": DEFAULT_SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": _build_scoring_prompt(filename, document_text),
            },
        ],
        stream=True,
        stream_options={"include_usage": True},
    )
    return parse_score_response(_collect_stream_content(completion))


def _extract_pdf_text(source_path: Path) -> str:
    """Extract text from a local PDF and trim it to a model-friendly size."""

    reader = PdfReader(str(source_path))
    pages: List[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        normalized = " ".join(page_text.split())
        if normalized:
            pages.append(normalized)

    document_text = "\n\n".join(pages).strip()
    if not document_text:
        raise ValueError(f"未能从 PDF 中提取可用文本: {source_path}")

    if len(document_text) > MAX_DOCUMENT_CHARS:
        document_text = document_text[:MAX_DOCUMENT_CHARS]
    return document_text


def _build_analysis_prompt(filename: str, prompt_text: str, document_text: str) -> str:
    """Build a single text prompt that combines user intent with extracted PDF text."""

    return (
        f"以下内容来自论文《{filename}》的本地提取文本。\n"
        "请只基于这些内容进行分析；如果文本缺失导致无法确定，请明确说明。\n\n"
        f"用户要求：\n{prompt_text}\n\n"
        f"论文文本：\n{document_text}"
    )


def _build_scoring_prompt(filename: str, document_text: str) -> str:
    """Build the scoring prompt with the locally extracted PDF text."""

    return (
        f"{prompts.paper_score_json}\n\n"
        f"论文标题或文件名：{filename}\n\n"
        "以下是从 PDF 中提取的论文文本，请基于这些内容打分：\n"
        f"{document_text}"
    )


def _collect_stream_content(completion: Any) -> str:
    parts: List[str] = []
    for chunk in completion:
        content = _extract_chunk_content(chunk)
        if content:
            parts.append(content)
    return "".join(parts)


def _extract_chunk_content(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if choices is None and isinstance(chunk, dict):
        choices = chunk.get("choices")
    if not choices:
        return ""

    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is None and isinstance(first_choice, dict):
        delta = first_choice.get("delta")
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    return content or ""


def _extract_json_object(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("评分结果中未找到有效 JSON。")
    return cleaned[start : end + 1]


def _build_score_markdown(
    score: Optional[Dict[str, Any]],
    score_status: str,
    score_error: Optional[str],
) -> str:
    lines = ["## 文献评分", ""]
    if score_status == "failed" or score is None:
        lines.append(f"评分生成失败：{score_error or '未知错误'}")
        return "\n".join(lines)

    lines.extend(["| 维度 | 分数 | 理由 |", "| --- | --- | --- |"])
    for key, label in SCORE_DIMENSIONS:
        detail = score[key]
        lines.append(f"| {label} | {detail['score']} | {detail['reason']} |")
    lines.append("")
    lines.append(f"**总分：{score['total_score']} / 50**")
    lines.append("")
    lines.append(f"**总体评价：** {score.get('overall_comment', '')}")
    return "\n".join(lines)


def _get_unique_output_path(save_dir: Path, base_name: str, extension: str) -> Path:
    candidate = save_dir / f"{base_name}{extension}"
    counter = 1
    while candidate.exists():
        candidate = save_dir / f"{base_name}_{counter}{extension}"
        counter += 1
    return candidate


def _write_score_summary_csv(save_dir: Path, results: List[AnalysisResult]) -> str:
    csv_path = _get_unique_output_path(save_dir, "scores_summary", ".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SCORE_SUMMARY_HEADERS)
        writer.writeheader()
        for result in results:
            row = {
                "filename": result.filename,
                "provider": result.provider,
                "model": result.model,
                "innovation": "",
                "method_rigor": "",
                "experiment_quality": "",
                "writing_clarity": "",
                "application_value": "",
                "total_score": "",
                "overall_comment": "",
                "report_path": result.report_path or "",
                "score_status": result.score_status,
            }
            if result.score:
                for key, _label in SCORE_DIMENSIONS:
                    row[key] = result.score[key]["score"]
                row["total_score"] = result.score["total_score"]
                row["overall_comment"] = result.score.get("overall_comment", "")
            writer.writerow(row)
    return str(csv_path)


__all__ = [
    "AnalyzeRequest",
    "AnalysisResult",
    "BatchAnalysisResult",
    "DEFAULT_SAVE_DIR",
    "ModelConfig",
    "TaskSnapshot",
    "get_task_status",
    "parse_score_response",
    "resolve_model_config",
    "resolve_prompt_value",
    "run_analysis_sync",
    "submit_analysis_task",
]
