# 📚 Research Assistant Agent: 智能学术文献检索与阅读助手

Research Assistant Agent 是一个基于大语言模型（LLM）的自动化科研辅助工具。它能够根据你自定义的检索条件（如年份、模型、关键词等）从 arXiv 批量获取并下载论文，随后利用 AI 代理自动阅读原文、提取核心创新点，并根据你的研究偏好对文献进行智能打分。通过内置的 Web 交互页面，你可以直观地管理、筛选和对话式探索你的专属文献库。

## ✨ 核心功能

* 🔍 **自定义定向检索**: 支持从 arXiv API 精准拉取数据，可按年份（如 2023-2024）、特定领域、特定模型或作者进行组合检索，并自动下载 PDF 全文。
* 🤖 **大模型批量精读**: 调用主流大模型对文献进行批量阅读，自动生成摘要、提取核心方法和 Baseline。
* 📊 **智能文献打分**: 基于预设的评审 Prompt，为检索到的每篇论文生成 1-10 的推荐指数，帮你快速过滤水文，锁定高价值文献。
* 💻 **可视化交互页面**: 开箱即用的 Web UI，支持：
    * 可视化配置检索参数。
    * 论文卡片式展示（包含标题、打分、AI 总结）。
    * 针对单篇或多篇论文进行多轮对话（Q&A）。

## 🛠️ 安装步骤

建议提前配置好你的虚拟环境。

**1. 克隆项目到本地**
```bash
git clone [https://github.com/Jellyfish0029/legere.git]
cd legere 
```

**2. 创建虚拟环境并安装依赖**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. 配置环境变量**
在项目根目录下创建 .env 文件，并填入您的API密钥及相关配置项：
```bash
# 大模型 API Key (例如 OpenAI 或 Gemini)
LLM_API_KEY="your_api_key_here"

# 默认文献 PDF 存储路径与 ChromaDB 存储路径
PAPER_STORAGE_DIR="./data/pdf_files"
VECTOR_DB_PATH="./data/chroma_db"
```

##🚀使用方法
启用web交互界面
**在终端中执行以下命令以启动前端服务**
```bash
python gui.py
```
