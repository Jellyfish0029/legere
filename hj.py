import os
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from openai import OpenAI
from typing import Dict, List, Optional
import argparse
import prompts

class Paper_Assistant:
    client = OpenAI(
        # 建议通过环境变量配置 API KEY，防止代码泄露
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),  
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  
    )

    def __init__(self, paper_directory=None, analyze_file=None, output_folder="D:\Papers MAS\graph learning\\ai_notes"):
        self.paper_directory = paper_directory
        self.analyze_file = analyze_file
        self.output_folder = output_folder
        
        Path(self.output_folder).mkdir(parents=True, exist_ok=True)

    def download_from_arxiv(self, keyword: str, max_results: int, download_dir: str) -> Optional[str]:
        """
        调用 arXiv API 检索关键词，展示论文信息并让用户选择是否下载
        """
        folder = Path(download_dir)
        folder.mkdir(parents=True, exist_ok=True)
        
        query = urllib.parse.quote(keyword)
        url = f'http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}'
        
        print(f"\n[*] 正在 arXiv 检索关键词: '{keyword}'，最多获取 {max_results} 篇...\n")
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            response = urllib.request.urlopen(req)
            xml_data = response.read()
        except Exception as e:
            print(f"[!] 请求 arXiv API 失败: {e}")
            return None

        # arXiv API 使用 Atom 和自定义的 arxiv 命名空间
        root = ET.fromstring(xml_data)
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom'
        }
        entries = root.findall('atom:entry', ns)

        if not entries:
            print("[-] 未检索到相关论文。")
            return None

        downloaded_any = False

        for entry in entries:
            # 1. 提取基础信息
            raw_title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
            published = entry.find('atom:published', ns).text[:10]  # 取 YYYY-MM-DD 格式
            
            authors = [author.find('atom:name', ns).text for author in entry.findall('atom:author', ns)]
            authors_str = ", ".join(authors)
            
            # 2. 尝试获取期刊或会议信息
            journal_ref_elem = entry.find('arxiv:journal_ref', ns)
            comment_elem = entry.find('arxiv:comment', ns)
            
            if journal_ref_elem is not None:
                journal_info = journal_ref_elem.text
            elif comment_elem is not None:
                journal_info = f"备注: {comment_elem.text}"
            else:
                journal_info = "未提供 (通常为预印本)"

            # 3. 打印信息并请求用户输入
            print("-" * 60)
            print(f"标题: {raw_title}")
            print(f"作者: {authors_str}")
            print(f"时间: {published}")
            print(f"期刊/会议: {journal_info}")
            print("-" * 60)
            
            choice = input("是否下载并分析这篇论文？(y/n/q 退出检索): ").strip().lower()
            
            if choice == 'q':
                print("[*] 已退出下载环节。")
                break
            elif choice != 'y':
                print("[-] 跳过该论文。\n")
                continue

            # 4. 执行下载逻辑
            safe_title = "".join([c for c in raw_title if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()
            if not safe_title:
                id_element = entry.find('atom:id', ns)
                safe_title = id_element.text.split('/')[-1] if id_element is not None else f"arxiv_paper_{int(time.time())}"
            
            pdf_link = None
            for link in entry.findall('atom:link', ns):
                if link.attrib.get('title') == 'pdf':
                    pdf_link = link.attrib.get('href')
                    break
            
            if pdf_link:
                pdf_url = pdf_link + '.pdf' if not pdf_link.endswith('.pdf') else pdf_link
                file_path = folder / f"{safe_title}.pdf"
                
                if file_path.exists():
                    print(f"[-] 文件已存在: {safe_title}.pdf\n")
                    downloaded_any = True
                    continue
                    
                print(f"[*] 正在下载...")
                try:
                    req = urllib.request.Request(pdf_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as response_pdf, open(file_path, 'wb') as out_file:
                        out_file.write(response_pdf.read())
                    print(f"[+] 下载成功: {safe_title}.pdf\n")
                    downloaded_any = True
                    time.sleep(2)  # 防止请求过快
                except Exception as e:
                    print(f"[!] 下载失败: {e}\n")

        return str(folder) if downloaded_any else None

    def process_directory(self) -> List[Dict]:
        file_list = []
        dir_path = Path(self.paper_directory)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {dir_path}")
        
        print(f"[*] 开始将 {dir_path} 中的 PDF 上传至云端解析...")
        for file_path in dir_path.rglob("*.pdf"):
            try:
                file_object = self.client.files.create(file=Path(file_path), purpose="file-extract")
                file_list.append((file_object, file_path.name))
                print(f"[+] 成功解析: {file_path.name}")
            except Exception as e:
                print(f"[!] 处理文件 {file_path} 失败: {str(e)}")

        return file_list

    def process_single_file(self):
        try:
            print(f"[*] 开始上传并解析单文件: {self.analyze_file}...")
            file_object = self.client.files.create(file=Path(self.analyze_file), purpose="file-extract")
            return file_object
        except Exception as e:
            print(f"[!] 处理文件失败: {str(e)}")
            return None

    def get_unique_filename(self, base_filename, extension):
        folder = Path(self.output_folder)
        counter = 1
        new_filename = f"{base_filename}{extension}"
        file_path = folder / new_filename
        
        while file_path.exists():
            new_filename = f"{base_filename}_{counter}{extension}"
            file_path = folder / new_filename
            counter += 1
        
        return file_path

    def analyze_and_generate_report(self, file_id, original_filename, user_prompt):
        try:
            print(f"[*] 正在生成报告: {original_filename} ...")
            completion = self.client.chat.completions.create(
                model="qwen-long",
                messages=[
                    {'role': 'system', 'content': 'You are a helpful assistant.'},
                    {'role': 'system', 'content': f'fileid://{file_id}'},
                    {'role': 'user', 'content': user_prompt}
                ],
                stream=True,
                stream_options={"include_usage": True}
            )

            full_content = ""
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta.content:
                    full_content += chunk.choices[0].delta.content

            if full_content:
                note_path = self.get_unique_filename(f"{original_filename}_report", ".md")
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write(full_content)
                print(f"[+] 已成功生成并保存报告: {note_path}\n")
            else:
                print(f"[-] 未能生成报告: {original_filename}\n")

        except Exception as e:
            print(f"[!] 分析 {original_filename} 时发生错误: {str(e)}\n")

def main():
    parser = argparse.ArgumentParser(description="论文检索与智能分析工具")
    parser.add_argument('--file', default=None, type=str, help="单个pdf文件路径")
    parser.add_argument('--folder', default=None, type=str, help="多个pdf文件的文件夹路径")
    parser.add_argument('--p', default=getattr(prompts, 'thoroughly2', '请总结这篇论文'), type=str, help="提示词内容或路径")
    parser.add_argument('--save', default="D:\Papers MAS\graph learning\\gnnllm_notes", type=str, help="保存路径")
    parser.add_argument('--query', default=None, type=str, help="arXiv检索关键词")
    parser.add_argument('--max_papers', default=3, type=int, help="从arXiv检索的最大论文数量")
    parser.add_argument('--arxiv_dir', default="./arxiv_downloads", type=str, help="arXiv论文的临时下载目录")

    args = parser.parse_args()

    ass = Paper_Assistant(paper_directory=args.folder, analyze_file=args.file, output_folder=args.save)
    
    if args.query:
        downloaded_folder = ass.download_from_arxiv(args.query, args.max_papers, args.arxiv_dir)
        if downloaded_folder:
            ass.paper_directory = downloaded_folder
        else:
            print("[*] 没有下载任何论文，程序结束。")
            return
    
    if ass.paper_directory:
        file_list = ass.process_directory()
        for file_object, original_filename in file_list:
            ass.analyze_and_generate_report(file_object.id, original_filename, args.p)
            
    elif ass.analyze_file:
        file_object = ass.process_single_file()
        if file_object:
            original_filename = Path(args.file).name
            ass.analyze_and_generate_report(file_object.id, original_filename, args.p)
    else:
        print("请提供 --query、--folder 或 --file 参数来启动工具！")

if __name__ == "__main__":
    main()