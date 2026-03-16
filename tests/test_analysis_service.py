import csv
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analysis_service
from analysis_service import (
    AnalyzeRequest,
    AnalysisResult,
    BatchAnalysisResult,
    ModelConfig,
    get_task_status,
    parse_score_response,
    resolve_model_config,
    run_analysis_sync,
    submit_analysis_task,
)


SAMPLE_SCORE_JSON = """
{
  "innovation": {"score": 8, "reason": "创新明确"},
  "method_rigor": {"score": 7, "reason": "设计完整"},
  "experiment_quality": {"score": 9, "reason": "实验充分"},
  "writing_clarity": {"score": 6, "reason": "表达尚可"},
  "application_value": {"score": 8, "reason": "有实际价值"},
  "overall_comment": "整体质量较高"
}
"""


class FakeChatCompletions:
    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def create(self, *, model, messages, stream, stream_options):
        self.owner.calls.append({"model": model, "messages": messages})
        user_content = messages[-1]["content"]
        response_text = (
            self.owner.score_response
            if analysis_service.prompts.paper_score_json in user_content
            else self.owner.report_response
        )
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=response_text))]
            )
        ]


class FakeChatAPI:
    def __init__(self, owner: "FakeClient") -> None:
        self.completions = FakeChatCompletions(owner)


class FakeClient:
    def __init__(self, report_response="报告内容", score_response=SAMPLE_SCORE_JSON) -> None:
        self.report_response = report_response
        self.score_response = score_response
        self.calls = []
        self.chat = FakeChatAPI(self)


class AnalysisServiceTests(unittest.TestCase):
    def test_resolve_model_config_uses_provider_defaults(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "dash-token"}, clear=True):
            resolved = resolve_model_config(ModelConfig())
        self.assertEqual(resolved.provider, "dashscope")
        self.assertEqual(resolved.model, "qwen-long")
        self.assertEqual(resolved.base_url, analysis_service.DASHSCOPE_BASE_URL)
        self.assertEqual(resolved.api_key, "dash-token")

    def test_resolve_model_config_allows_manual_override(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "openai-token"}, clear=True):
            resolved = resolve_model_config(
                ModelConfig(
                    provider="openai",
                    model="gpt-4.1-mini",
                    base_url="https://example.com/v1",
                    api_key="manual-token",
                    api_key_env="CUSTOM_KEY",
                )
            )
        self.assertEqual(resolved.model, "gpt-4.1-mini")
        self.assertEqual(resolved.base_url, "https://example.com/v1")
        self.assertEqual(resolved.api_key, "manual-token")
        self.assertEqual(resolved.api_key_env, "CUSTOM_KEY")

    def test_resolve_model_config_requires_missing_fields(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "API key"):
                resolve_model_config(ModelConfig())
            with self.assertRaisesRegex(ValueError, "模型名称"):
                resolve_model_config(ModelConfig(provider="deepseek", api_key="token"))
            with self.assertRaisesRegex(ValueError, "base_url"):
                resolve_model_config(ModelConfig(provider="custom", model="x", api_key="token"))

    def test_parse_score_response_calculates_total_score(self):
        score = parse_score_response(SAMPLE_SCORE_JSON)
        self.assertEqual(score["innovation"]["score"], 8.0)
        self.assertEqual(score["total_score"], 38.0)
        self.assertEqual(score["overall_comment"], "整体质量较高")

    def test_run_analysis_sync_supports_uploaded_pdf_bytes(self):
        fake_client = FakeClient()
        extracted_paths = []

        def fake_extract(path):
            extracted_paths.append(Path(path))
            self.assertTrue(Path(path).exists())
            return "这是提取出的论文内容。"

        with tempfile.TemporaryDirectory() as save_dir:
            request = AnalyzeRequest(
                source_type="uploaded_pdf_bytes",
                uploaded_pdf_bytes=b"%PDF-1.4",
                uploaded_filename="uploaded.pdf",
                prompt="请总结",
                save_dir=save_dir,
                enable_score=False,
                model_config=ModelConfig(api_key="token", base_url="https://example.com/v1", model="demo-model"),
            )
            with patch("analysis_service.OpenAI", return_value=fake_client), patch(
                "analysis_service._extract_pdf_text", side_effect=fake_extract
            ):
                result = run_analysis_sync(request)

            self.assertIsInstance(result, AnalysisResult)
            self.assertEqual(result.filename, "uploaded.pdf")
            self.assertTrue(extracted_paths)
            self.assertTrue(Path(result.report_path).exists())
            self.assertEqual(result.score_status, "disabled")
            self.assertIn("这是提取出的论文内容", fake_client.calls[0]["messages"][-1]["content"])

    def test_run_analysis_sync_writes_batch_csv_summary(self):
        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as workspace:
            folder = Path(workspace) / "papers"
            folder.mkdir()
            (folder / "paper_a.pdf").write_bytes(b"%PDF-1.4 A")
            (folder / "paper_b.pdf").write_bytes(b"%PDF-1.4 B")
            request = AnalyzeRequest(
                source_type="folder_path",
                folder_path=str(folder),
                prompt="请总结",
                save_dir=workspace,
                enable_score=True,
                model_config=ModelConfig(api_key="token", base_url="https://example.com/v1", model="demo-model"),
            )
            with patch("analysis_service.OpenAI", return_value=fake_client), patch(
                "analysis_service._extract_pdf_text", return_value="批量测试文本。"
            ):
                result = run_analysis_sync(request)

            self.assertIsInstance(result, BatchAnalysisResult)
            self.assertEqual(len(result.results), 2)
            self.assertTrue(Path(result.summary_csv_path).exists())
            with open(result.summary_csv_path, encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["total_score"], "38.0")
            self.assertEqual(rows[0]["score_status"], "succeeded")

    def test_submit_analysis_task_updates_status_and_result(self):
        request = AnalyzeRequest(
            source_type="file_path",
            file_path="dummy.pdf",
            prompt="请总结",
            model_config=ModelConfig(api_key="token"),
        )
        fake_result = AnalysisResult(
            filename="dummy.pdf",
            provider="dashscope",
            model="qwen-long",
            report_markdown="ok",
            report_path="dummy_report.md",
            score=None,
            score_status="disabled",
            error=None,
        )

        def delayed_success(_request):
            time.sleep(0.05)
            return fake_result

        with patch("analysis_service.run_analysis_sync", side_effect=delayed_success):
            task_id = submit_analysis_task(request)
            deadline = time.time() + 2
            snapshot = get_task_status(task_id)
            while snapshot.status not in {"succeeded", "failed"} and time.time() < deadline:
                time.sleep(0.02)
                snapshot = get_task_status(task_id)

        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(snapshot.result["filename"], "dummy.pdf")
        self.assertIsNone(snapshot.error)

    def test_submit_analysis_task_records_failure(self):
        request = AnalyzeRequest(
            source_type="file_path",
            file_path="dummy.pdf",
            prompt="请总结",
            model_config=ModelConfig(api_key="token"),
        )

        with patch("analysis_service.run_analysis_sync", side_effect=RuntimeError("boom")):
            task_id = submit_analysis_task(request)
            deadline = time.time() + 2
            snapshot = get_task_status(task_id)
            while snapshot.status not in {"succeeded", "failed"} and time.time() < deadline:
                time.sleep(0.02)
                snapshot = get_task_status(task_id)

        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.error, "boom")


if __name__ == "__main__":
    unittest.main()
