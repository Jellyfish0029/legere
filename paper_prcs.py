import os
from pathlib import Path
from openai import OpenAI
from typing import Dict, List, Optional, Generator
import argparse
import prompts

class Paper_Assistant:
    client = OpenAI(
        api_key="",  # 如果您没有配置环境变量，请在此处替换您的API-KEY
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # 填写DashScope服务base_url
    )

    def __init__(self,paper_directory=None,analyze_file=None,output_folder="D:\Papers MAS\graph learning\\ai_notes"):
        self.paper_directory = paper_directory
        self.analyze_file = analyze_file
        self.output_folder = output_folder

    def process_directory(self) -> List[Dict]:
        file_list = []
        dir_path = Path(self.paper_directory)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {dir_path}")
        for file_path in dir_path.rglob("*.pdf"):
            try:
                file_object = self.client.files.create(file=Path(file_path), purpose="file-extract")
                file_list.append(file_object)
            except Exception as e:
                print(f"处理文件 {file_path} 失败: {str(e)}")

        return file_list

    def process_single_file(self):
        try:
            file_object = self.client.files.create(file=Path(self.analyze_file), purpose="file-extract")
        except Exception as e:
            print(f"处理文件失败: {str(e)}")
        return file_object

    def get_unique_filename(self, base_filename, extension):
        folder = Path(self.output_folder)
        counter = 1
        new_filename = f"{base_filename}{extension}"
        file_path = folder / new_filename
        
        # 检查文件是否存在，如果存在则添加后缀
        while file_path.exists():
            new_filename = f"{base_filename}_{counter}{extension}"
            file_path = folder / new_filename
            counter += 1
        
        return file_path


def main():

    parser = argparse.ArgumentParser(description="论文分析工具")
    parser.add_argument('--file', default=None,type=str, help="单个pdf文件路径")
    parser.add_argument('--folder', default=None,type=str, help="多个pdf文件的文件夹路径")
    parser.add_argument('--p', default=prompts.thoroughly2,type=str, help="提示词路径")
    parser.add_argument('--save',default="D:\Papers MAS\graph learning\\gnnllm_notes",type=str,help="保存路径")

    args = parser.parse_args()
    paper_directory = args.folder
    analyze_file = args.file
    user_prompt = args.p
    output_folder = args.save

    ass = Paper_Assistant(paper_directory,analyze_file,output_folder)
    
    
    if ass.paper_directory:
        file_list = ass.process_directory()
        for file in file_list:
            try:
                # 初始化messages列表
                completion = ass.client.chat.completions.create(
                    model="qwen-long",
                    messages=[
                        {'role': 'system', 'content': 'You are a helpful assistant.'},
                        # 请将 'file-fe-xxx'替换为您实际对话场景所使用的 fileid。
                        {'role': 'system', 'content': f'fileid://'+file.id},
                        {'role': 'user', 'content': user_prompt}
                    ],
                    # 所有代码示例均采用流式输出，以清晰和直观地展示模型输出过程。如果您希望查看非流式输出的案例，请参见https://help.aliyun.com/zh/model-studio/text-generation
                    stream=True,
                    stream_options={"include_usage": True}
                )

                full_content = ""
                for chunk in completion:
                    if chunk.choices and chunk.choices[0].delta.content:
                        # 拼接输出内容
                        full_content += chunk.choices[0].delta.content
                        #print(chunk.model_dump())

                #print(full_content)

                if full_content:
                    # 保存报告
                    note_path = ass.get_unique_filename(f"{file.filename}_report",".md")
                    with open(note_path, 'w', encoding='utf-8') as f:
                        f.write(full_content)
                    print(f"已生成报告。")
                else:
                    print(f"未能生成报告。")

            except:
                print(f"错误!")
    
    elif ass.analyze_file:
        file = ass.process_single_file()
        try:
            # 初始化messages列表
            completion = ass.client.chat.completions.create(
                model="qwen-long",
                messages=[
                    {'role': 'system', 'content': 'You are a helpful assistant.'},
                    # 请将 'file-fe-xxx'替换为您实际对话场景所使用的 fileid。
                    {'role': 'system', 'content': f'fileid://'+file.id},
                    {'role': 'user', 'content': user_prompt}
                ],
                # 所有代码示例均采用流式输出，以清晰和直观地展示模型输出过程。如果您希望查看非流式输出的案例，请参见https://help.aliyun.com/zh/model-studio/text-generation
                stream=True,
                stream_options={"include_usage": True}
            )

            full_content = ""
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta.content:
                    # 拼接输出内容
                    full_content += chunk.choices[0].delta.content
                    #print(chunk.model_dump())

            #print(full_content)

            if full_content:
                # 保存报告
                note_path = ass.get_unique_filename(f"{file.filename}_report",".md")
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write(full_content)
                print(f"已生成报告。")
            else:
                print(f"未能生成报告。")

        except:
            print(f"错误。")
            #print("请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code")


if __name__ == "__main__":
    main()
