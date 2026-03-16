from __future__ import annotations

import argparse
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import prompts
from analysis_service import (
    AnalyzeRequest,
    AnalysisResult,
    BatchAnalysisResult,
    DEFAULT_SAVE_DIR,
    ModelConfig,
    run_analysis_sync,
)


def download_from_arxiv(keyword: str, max_results: int, download_dir: str) -> Optional[str]:
    """Search arXiv, download selected PDFs, and return the folder path."""

    folder = Path(download_dir)
    folder.mkdir(parents=True, exist_ok=True)

    query = urllib.parse.quote(keyword)
    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}"
    print(f"\n[*] 正在 arXiv 检索关键词: '{keyword}'，最多获取 {max_results} 篇...\n")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        response = urllib.request.urlopen(request)
        xml_data = response.read()
    except Exception as exc:
        print(f"[!] 请求 arXiv API 失败: {exc}")
        return None

    root = ET.fromstring(xml_data)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    entries = root.findall("atom:entry", namespaces)
    if not entries:
        print("[-] 未检索到相关论文。")
        return None

    downloaded_any = False
    for entry in entries:
        raw_title = entry.find("atom:title", namespaces).text.replace("\n", " ").strip()
        published = entry.find("atom:published", namespaces).text[:10]
        authors = [
            author.find("atom:name", namespaces).text
            for author in entry.findall("atom:author", namespaces)
        ]
        journal_ref = entry.find("arxiv:journal_ref", namespaces)
        comment = entry.find("arxiv:comment", namespaces)
        journal_info = "未提供 (通常为预印本)"
        if journal_ref is not None:
            journal_info = journal_ref.text
        elif comment is not None:
            journal_info = f"备注: {comment.text}"

        print("-" * 60)
        print(f"标题: {raw_title}")
        print(f"作者: {', '.join(authors)}")
        print(f"时间: {published}")
        print(f"期刊/会议: {journal_info}")
        print("-" * 60)

        choice = input("是否下载并分析这篇论文？(y/n/q 退出检索): ").strip().lower()
        if choice == "q":
            print("[*] 已退出下载环节。")
            break
        if choice != "y":
            print("[-] 跳过该论文。\n")
            continue

        safe_title = "".join(
            character
            for character in raw_title
            if character.isalpha() or character.isdigit() or character in (" ", "-", "_")
        ).strip()
        if not safe_title:
            paper_id = entry.find("atom:id", namespaces)
            safe_title = (
                paper_id.text.split("/")[-1]
                if paper_id is not None
                else f"arxiv_paper_{int(time.time())}"
            )

        pdf_link = None
        for link in entry.findall("atom:link", namespaces):
            if link.attrib.get("title") == "pdf":
                pdf_link = link.attrib.get("href")
                break
        if not pdf_link:
            continue

        pdf_url = pdf_link if pdf_link.endswith(".pdf") else f"{pdf_link}.pdf"
        file_path = folder / f"{safe_title}.pdf"
        if file_path.exists():
            print(f"[-] 文件已存在: {file_path.name}\n")
            downloaded_any = True
            continue

        print("[*] 正在下载...")
        try:
            pdf_request = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(pdf_request) as response_pdf, file_path.open("wb") as output:
                output.write(response_pdf.read())
            print(f"[+] 下载成功: {file_path.name}\n")
            downloaded_any = True
            time.sleep(2)
        except Exception as exc:
            print(f"[!] 下载失败: {exc}\n")

    return str(folder) if downloaded_any else None


def resolve_prompt_argument(prompt_value: str) -> str:
    """Resolve CLI prompt input from a symbol, path, or inline text."""

    if not prompt_value:
        return prompts.thoroughly2
    prompt_path = Path(prompt_value)
    if prompt_path.is_file():
        return prompt_path.read_text(encoding="utf-8")
    return getattr(prompts, prompt_value, prompt_value)


def build_model_config(args: argparse.Namespace) -> ModelConfig:
    """Create the shared model config object from CLI arguments."""

    return ModelConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
    )


def print_result_summary(result: BatchAnalysisResult | AnalysisResult) -> None:
    """Print a concise CLI summary for the generated analysis results."""

    if isinstance(result, BatchAnalysisResult):
        print(f"[*] 批量分析完成，共处理 {len(result.results)} 篇论文。")
        for item in result.results:
            if item.error:
                print(f"[!] {item.filename} 失败: {item.error}")
                continue
            print(
                f"[+] {item.filename} -> {item.report_path} "
                f"(评分状态: {item.score_status}, 模型: {item.provider}/{item.model})"
            )
        if result.summary_csv_path:
            print(f"[*] 评分汇总已保存: {result.summary_csv_path}")
        return

    if result.error:
        print(f"[!] 分析失败: {result.error}")
        return

    print(f"[+] 报告已保存: {result.report_path}")
    print(f"[*] 模型: {result.provider}/{result.model}")
    print(f"[*] 评分状态: {result.score_status}")
    if result.score and result.score_status == "succeeded":
        print(f"[*] 总分: {result.score['total_score']} / 50")


def main() -> None:
    parser = argparse.ArgumentParser(description="论文检索与智能分析工具")
    parser.add_argument("--file", default=None, type=str, help="单个 pdf 文件路径")
    parser.add_argument("--folder", default=None, type=str, help="多个 pdf 文件的文件夹路径")
    parser.add_argument(
        "--p",
        default="thoroughly2",
        type=str,
        help="提示词名称、提示词文件路径或直接传入的提示词内容",
    )
    parser.add_argument("--save", default=str(DEFAULT_SAVE_DIR), type=str, help="报告保存路径")
    parser.add_argument("--query", default=None, type=str, help="arXiv 检索关键词")
    parser.add_argument("--max_papers", default=3, type=int, help="从 arXiv 检索的最大论文数量")
    parser.add_argument("--arxiv_dir", default="./arxiv_downloads", type=str, help="arXiv 下载目录")
    parser.add_argument(
        "--provider",
        default="dashscope",
        type=str,
        help="模型 provider: dashscope/openai/deepseek/custom",
    )
    parser.add_argument("--model", default=None, type=str, help="模型名称")
    parser.add_argument("--base-url", default=None, type=str, help="覆盖 provider 的 base_url")
    parser.add_argument("--api-key", default=None, type=str, help="直接传入 API key")
    parser.add_argument("--api-key-env", default=None, type=str, help="从指定环境变量读取 API key")
    parser.add_argument("--score", action="store_true", help="启用文献评分")

    args = parser.parse_args()
    prompt_text = resolve_prompt_argument(args.p)
    model_config = build_model_config(args)

    folder_path = args.folder
    if args.query:
        downloaded_folder = download_from_arxiv(args.query, args.max_papers, args.arxiv_dir)
        if not downloaded_folder:
            print("[*] 没有下载任何论文，程序结束。")
            return
        folder_path = downloaded_folder

    if folder_path:
        request = AnalyzeRequest(
            source_type="folder_path",
            folder_path=folder_path,
            prompt=prompt_text,
            save_dir=args.save,
            enable_score=args.score,
            model_config=model_config,
        )
    elif args.file:
        request = AnalyzeRequest(
            source_type="file_path",
            file_path=args.file,
            prompt=prompt_text,
            save_dir=args.save,
            enable_score=args.score,
            model_config=model_config,
        )
    else:
        print("请提供 --query、--folder 或 --file 参数来启动工具！")
        return

    try:
        result = run_analysis_sync(request)
    except Exception as exc:
        print(f"[!] 执行失败: {exc}")
        return

    print_result_summary(result)


if __name__ == "__main__":
    main()
