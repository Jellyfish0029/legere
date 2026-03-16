import sys
import os
import threading
from pathlib import Path
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import prompts
import urllib
import xml.etree.ElementTree as ET
import time
from analysis_service import (
    AnalyzeRequest,
    AnalysisResult,
    BatchAnalysisResult,
    DEFAULT_SAVE_DIR,
    ModelConfig,
    run_analysis_sync,
)

class StreamRedirector(QObject):
    """重定向标准输出到信号"""
    text_written = pyqtSignal(str)
    
    def write(self, text):
        self.text_written.emit(str(text))
    
    def flush(self):
        pass

class PaperInfoDialog(QDialog):
    """论文信息展示和选择对话框"""
    def __init__(self, paper_info, parent=None):
        super().__init__(parent)
        self.paper_info = paper_info
        self.selected_action = None  # 'download', 'skip', 'quit'
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("论文信息")
        self.setModal(True)
        self.resize(600, 400)
        
        layout = QVBoxLayout()
        
        # 论文信息显示
        info_group = QGroupBox("论文详细信息")
        info_layout = QVBoxLayout()
        
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setStyleSheet("""
            QTextEdit {
                background-color: #f8f9fa;
                font-family: 'Microsoft YaHei', sans-serif;
                font-size: 12px;
                line-height: 1.5;
            }
        """)
        
        # 格式化显示论文信息
        info_content = f"""
        <h3>{self.paper_info.get('title', '无标题')}</h3>
        
        <p><b>作者：</b>{self.paper_info.get('authors', '未知')}</p>
        
        <p><b>发布时间：</b>{self.paper_info.get('published', '未知')}</p>
        
        <p><b>期刊/会议信息：</b>{self.paper_info.get('journal_info', '未提供')}</p>
        
        <p><b>摘要：</b></p>
        <p style="text-align: justify;">{self.paper_info.get('summary', '无摘要')}</p>
        """
        
        self.info_text.setHtml(info_content)
        info_layout.addWidget(self.info_text)
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        self.download_btn = QPushButton("下载")
        self.download_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.download_btn.clicked.connect(lambda: self.accept_with_action('download'))
        btn_layout.addWidget(self.download_btn)
        
        self.skip_btn = QPushButton("跳过")
        self.skip_btn.setStyleSheet("background-color: #ff9800; color: white;")
        self.skip_btn.clicked.connect(lambda: self.accept_with_action('skip'))
        btn_layout.addWidget(self.skip_btn)
        
        self.quit_btn = QPushButton("退出检索")
        self.quit_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.quit_btn.clicked.connect(lambda: self.accept_with_action('quit'))
        btn_layout.addWidget(self.quit_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def accept_with_action(self, action):
        self.selected_action = action
        self.accept()

class PaperAssistantGUI(QMainWindow):
    # 定义信号
    update_output_signal = pyqtSignal(str)
    add_paper_signal = pyqtSignal(str, str, str, str)
    show_paper_dialog_signal = pyqtSignal(dict)
    dialog_result_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        
        self.assistant = None
        self.processing_thread = None
        self.stop_flag = False
        self.arxiv_papers = []  # 存储arXiv检索到的论文
        self.current_paper_index = 0
        
        # 初始化模型配置
        self.model_config = ModelConfig(
            provider=None,
            model=None,
            base_url=None,
            api_key=None,
            api_key_env=None,
        )
        
        self.init_ui()
        
        # 连接信号 - 在所有初始化完成后
        self.update_output_signal.connect(self.append_output)
        self.add_paper_signal.connect(self.add_paper_to_table)
        self.show_paper_dialog_signal.connect(self.show_paper_dialog)
    
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("论文智能助手 - 检索与分析工具")
        self.setGeometry(100, 100, 1300, 900)
        
        # 设置全局样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 15px;
                border-radius: 4px;
                font-weight: bold;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
            QPushButton.secondary {
                background-color: #2196F3;
            }
            QPushButton.secondary:hover {
                background-color: #1976D2;
            }
            QPushButton.warning {
                background-color: #ff9800;
            }
            QPushButton.warning:hover {
                background-color: #f57c00;
            }
            QPushButton.danger {
                background-color: #f44336;
            }
            QPushButton.danger:hover {
                background-color: #d32f2f;
            }
            QTextEdit, QPlainTextEdit, QListWidget {
                background-color: white;
                border: 1px solid #cccccc;
                border-radius: 4px;
                font-family: Consolas, Monaco, monospace;
            }
            QLineEdit {
                padding: 5px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QComboBox {
                padding: 5px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QTableWidget {
                background-color: white;
                alternate-background-color: #f8f9fa;
            }
        """)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 创建标签页
        tab_widget = QTabWidget()
        main_layout.addWidget(tab_widget)
        
        # 添加各个功能标签页
        tab_widget.addTab(self.create_arxiv_tab(), "🔍 arXiv交互检索")
        tab_widget.addTab(self.create_analysis_tab(), "📄 单文件分析")
        tab_widget.addTab(self.create_batch_tab(), "📚 批量处理")
        tab_widget.addTab(self.create_settings_tab(), "⚙️ 设置")
        
        # 底部显示面板（放在标签页下方）
        bottom_panel = QWidget()
        bottom_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setAlignment(Qt.AlignTop)
        #bottom_layout.setContentsMargins(0, 0, 0, 0)

        

        # 输出区域
        output_group = QGroupBox("📝 运行日志")
        output_layout = QVBoxLayout()
        
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumHeight(200)
        output_layout.addWidget(self.output_text)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        output_layout.addWidget(self.progress_bar)
        
        output_group.setLayout(output_layout)
        bottom_layout.addWidget(output_group)
        
        # 添加到底部显示面板
        main_layout.addWidget(bottom_panel)

        # 状态栏
        self.statusBar().showMessage("就绪")
        
        # 重定向输出
        self.redirector = StreamRedirector()
        self.redirector.text_written.connect(self.append_output)
        sys.stdout = self.redirector
        
    def create_arxiv_tab(self):
        """创建arXiv检索下载标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 检索参数区域
        search_group = QGroupBox("检索参数")
        search_layout = QGridLayout()
        
        # 关键词
        search_layout.addWidget(QLabel("关键词:"), 0, 0)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("例如: graph neural networks")
        search_layout.addWidget(self.keyword_edit, 0, 1, 1, 2)
        
        # 最大检索数量
        search_layout.addWidget(QLabel("最大下载数:"), 1, 0)
        self.max_papers_spin = QSpinBox()
        self.max_papers_spin.setRange(1, 50)
        self.max_papers_spin.setValue(5)
        search_layout.addWidget(self.max_papers_spin, 1, 1)
        
        # 下载目录
        search_layout.addWidget(QLabel("下载目录:"), 2, 0)
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setText("./arxiv_downloads")
        search_layout.addWidget(self.download_dir_edit, 2, 1)
        
        self.browse_download_btn = QPushButton("浏览...")
        self.browse_download_btn.setMaximumWidth(80)
        self.browse_download_btn.clicked.connect(self.browse_download_dir)
        search_layout.addWidget(self.browse_download_btn, 2, 2)
        
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        self.search_btn = QPushButton("开始检索")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.setStyleSheet("background-color: #2196F3;")
        self.search_btn.clicked.connect(self.start_arxiv_search)
        btn_layout.addWidget(self.search_btn)
        
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setProperty("class", "danger")
        self.stop_btn.setStyleSheet("background-color: #f44336;")
        self.stop_btn.clicked.connect(self.stop_processing)
        self.stop_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_btn)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 操作说明
        info_group = QGroupBox("使用说明")
        info_layout = QVBoxLayout()
        info_text = QLabel(
            "1. 输入关键词开始检索\n"
            "2. 系统会逐篇显示论文信息\n"
            "3. 您可以选择：\n"
            "   - 下载并分析该论文\n"
            "   - 跳过该论文\n"
            "   - 退出检索\n"
            "4. 下载的论文会自动添加到论文列表"
        )
        
        info_text.setWordWrap(True)
        info_text.setStyleSheet("background-color: #e3f2fd; padding: 10px; border-radius: 4px;")
        info_layout.addWidget(info_text)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # 论文列表
        papers_group = QGroupBox("📋 论文列表")
        papers_layout = QVBoxLayout()
        
        self.papers_table = QTableWidget()
        self.papers_table.setColumnCount(4)
        self.papers_table.setHorizontalHeaderLabels(["状态", "标题", "作者", "时间"])
        self.papers_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.papers_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.papers_table.setSelectionBehavior(QTableWidget.SelectRows)
        papers_layout.addWidget(self.papers_table)
        
        papers_group.setLayout(papers_layout)
        layout.addWidget(papers_group)

        layout.addStretch()
        return tab
        
        # # 结果显示区域
        # result_group = QGroupBox("下载结果")
        # result_layout = QVBoxLayout()
        
        # self.download_result_list = QListWidget()
        # result_layout.addWidget(self.download_result_list)
        
        # result_group.setLayout(result_layout)
        # layout.addWidget(result_group)
        
        return tab
    
    def create_analysis_tab(self):
        """创建论文分析标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 文件选择区域
        file_group = QGroupBox("选择要分析的文件")
        file_layout = QHBoxLayout()
        
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("请选择PDF文件...")
        file_layout.addWidget(self.file_path_edit)
        
        self.browse_file_btn = QPushButton("浏览...")
        self.browse_file_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(self.browse_file_btn)
        
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
        
        # 提示词选择
        prompt_group = QGroupBox("分析提示词")
        prompt_layout = QVBoxLayout()
        
        # 预设提示词
        prompt_preset_layout = QHBoxLayout()
        prompt_preset_layout.addWidget(QLabel("预设提示词:"))
        
        self.prompt_combo = QComboBox()
        # 从prompts模块获取预设提示词
        self.prompt_combo.addItems([p for p in dir(prompts) if not p.startswith('_')])
        self.prompt_combo.currentTextChanged.connect(self.load_preset_prompt)
        prompt_preset_layout.addWidget(self.prompt_combo)
        
        prompt_preset_layout.addStretch()
        prompt_layout.addLayout(prompt_preset_layout)
        
        # 自定义提示词
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("输入自定义提示词，或选择预设提示词...")
        self.prompt_edit.setMaximumHeight(150)
        prompt_layout.addWidget(self.prompt_edit)
        
        prompt_group.setLayout(prompt_layout)
        layout.addWidget(prompt_group)
        
        # 输出设置
        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout()
        
        output_layout.addWidget(QLabel("保存目录:"))
        dir_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setText("D:\\legere\\test_notes")
        dir_layout.addWidget(self.output_dir_edit)
        
        self.browse_output_btn = QPushButton("浏览...")
        self.browse_output_btn.clicked.connect(self.browse_output_dir)
        dir_layout.addWidget(self.browse_output_btn)
        output_layout.addLayout(dir_layout)
        
        # 添加评分复选框
        self.score_checkbox = QCheckBox("启用文献评分")
        output_layout.addWidget(self.score_checkbox)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        self.analyze_btn = QPushButton("开始分析")
        self.analyze_btn.clicked.connect(self.start_analysis)
        btn_layout.addWidget(self.analyze_btn)
        
        self.stop_btn2 = QPushButton("停止")
        self.stop_btn2.setProperty("class", "danger")
        self.stop_btn2.setStyleSheet("background-color: #f44336;")
        self.stop_btn2.clicked.connect(self.stop_processing)
        self.stop_btn2.setEnabled(False)
        btn_layout.addWidget(self.stop_btn2)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        return tab
    
    def create_batch_tab(self):
        """创建批量处理标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 文件夹选择
        folder_group = QGroupBox("选择包含PDF的文件夹")
        folder_layout = QHBoxLayout()
        
        self.folder_path_edit = QLineEdit()
        self.folder_path_edit.setPlaceholderText("请选择包含PDF文件的文件夹...")
        folder_layout.addWidget(self.folder_path_edit)
        
        self.browse_folder_btn = QPushButton("浏览...")
        self.browse_folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.browse_folder_btn)
        
        folder_group.setLayout(folder_layout)
        layout.addWidget(folder_group)
        
        # 文件列表
        files_group = QGroupBox("待处理的PDF文件")
        files_layout = QVBoxLayout()
        
        self.files_list = QListWidget()
        self.files_list.setSelectionMode(QListWidget.MultiSelection)
        files_layout.addWidget(self.files_list)
        
        # 全选/取消按钮
        select_btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self.select_all_files)
        select_btn_layout.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.clicked.connect(self.deselect_all_files)
        select_btn_layout.addWidget(self.deselect_all_btn)
        
        select_btn_layout.addStretch()
        files_layout.addLayout(select_btn_layout)
        
        files_group.setLayout(files_layout)
        layout.addWidget(files_group)
        
        # 分析参数（复用分析标签页的部分）
        prompt_group = QGroupBox("分析提示词")
        prompt_layout = QVBoxLayout()
        
        # 预设提示词
        prompt_preset_layout = QHBoxLayout()
        prompt_preset_layout.addWidget(QLabel("预设提示词:"))
        
        self.batch_prompt_combo = QComboBox()
        self.batch_prompt_combo.addItems([p for p in dir(prompts) if not p.startswith('_')])
        self.batch_prompt_combo.currentTextChanged.connect(self.load_batch_preset_prompt)
        prompt_preset_layout.addWidget(self.batch_prompt_combo)
        
        prompt_preset_layout.addStretch()
        prompt_layout.addLayout(prompt_preset_layout)
        
        self.batch_prompt_edit = QTextEdit()
        self.batch_prompt_edit.setPlaceholderText("输入自定义提示词，或选择预设提示词...")
        self.batch_prompt_edit.setMaximumHeight(100)
        prompt_layout.addWidget(self.batch_prompt_edit)
        
        prompt_group.setLayout(prompt_layout)
        layout.addWidget(prompt_group)
        
        # 输出设置
        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout()
        
        output_layout.addWidget(QLabel("保存目录:"))
        dir_layout = QHBoxLayout()
        self.batch_output_edit = QLineEdit()
        self.batch_output_edit.setText("D:\\legere\\test_notes")
        dir_layout.addWidget(self.batch_output_edit)
        
        self.browse_batch_output_btn = QPushButton("浏览...")
        self.browse_batch_output_btn.clicked.connect(self.browse_batch_output_dir)
        dir_layout.addWidget(self.browse_batch_output_btn)
        output_layout.addLayout(dir_layout)
        
        # 添加评分复选框
        self.batch_score_checkbox = QCheckBox("启用文献评分")
        output_layout.addWidget(self.batch_score_checkbox)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        self.batch_analyze_btn = QPushButton("开始批量分析")
        self.batch_analyze_btn.clicked.connect(self.start_batch_analysis)
        btn_layout.addWidget(self.batch_analyze_btn)
        
        self.stop_btn3 = QPushButton("停止")
        self.stop_btn3.setProperty("class", "danger")
        self.stop_btn3.setStyleSheet("background-color: #f44336;")
        self.stop_btn3.clicked.connect(self.stop_processing)
        self.stop_btn3.setEnabled(False)
        btn_layout.addWidget(self.stop_btn3)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        return tab
    
    def create_settings_tab(self):
        """创建设置标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 模型设置
        api_group = QGroupBox("模型设置")
        api_layout = QGridLayout()

        api_layout.addWidget(QLabel("提供商:"), 0, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["dashscope", "openai", "deepseek", "custom"])
        self.provider_combo.setCurrentText("dashscope")  # 默认值
        self.model_config.provider = self.provider_combo.currentText()  # 初始化模型配置的provider
        api_layout.addWidget(self.provider_combo, 0, 1)
        
        api_layout.addWidget(QLabel("模型名称:"), 1, 0)
        self.model_edit = QLineEdit()
        self.model_edit.setText("qwen-turbo")  # 默认值
        self.model_config.model = self.model_edit.text()  # 初始化模型配置的model
        api_layout.addWidget(self.model_edit, 1, 1)
        
        api_layout.addWidget(QLabel("API Key:"), 2, 0)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.model_config.api_key = self.api_key_edit.text()  # 初始化模型配置的api_key
        api_layout.addWidget(self.api_key_edit, 2, 1)

        api_layout.addWidget(QLabel("API Key环境变量:"), 3, 0)
        self.api_key_env_edit = QLineEdit()
        self.api_key_env_edit.setPlaceholderText("例如: OPENAI_API_KEY")
        self.model_config.api_key_env = self.api_key_env_edit.text()  # 初始化模型配置的api_key_env
        api_layout.addWidget(self.api_key_env_edit, 3, 1)
        
        api_layout.addWidget(QLabel("Base URL:"), 4, 0)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setText("https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model_config.base_url = self.base_url_edit.text()  # 初始化模型配置的base_url
        api_layout.addWidget(self.base_url_edit, 4, 1)

        
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)
        
        # 其他设置
        other_group = QGroupBox("其他设置")
        other_layout = QGridLayout()
        
        other_layout.addWidget(QLabel("默认输出目录:"), 0, 0)
        self.default_output_edit = QLineEdit()
        self.default_output_edit.setText("D:\\legere\\test_notes")
        other_layout.addWidget(self.default_output_edit, 0, 1)
        
        other_layout.addWidget(QLabel("下载间隔(秒):"), 1, 0)
        self.download_interval_spin = QSpinBox()
        self.download_interval_spin.setRange(1, 10)
        self.download_interval_spin.setValue(3)
        other_layout.addWidget(self.download_interval_spin, 1, 1)
        
        other_group.setLayout(other_layout)
        layout.addWidget(other_group)
        
        # 保存按钮
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
        
        layout.addStretch()
        
        return tab
   
    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择PDF文件", "", "PDF Files (*.pdf)")
        if file_path:
            self.file_path_edit.setText(file_path)
    
    def browse_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder_path:
            self.folder_path_edit.setText(folder_path)
            self.load_pdf_files(folder_path)
    
    def browse_download_dir(self):
        folder_path = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if folder_path:
            self.download_dir_edit.setText(folder_path)
    
    def browse_output_dir(self):
        folder_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder_path:
            self.output_dir_edit.setText(folder_path)
    
    def browse_batch_output_dir(self):
        folder_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder_path:
            self.batch_output_edit.setText(folder_path)
    
    def load_pdf_files(self, folder_path):
        self.files_list.clear()
        folder = Path(folder_path)
        if folder.exists():
            for pdf_file in folder.glob("*.pdf"):
                self.files_list.addItem(str(pdf_file))
                self.add_paper_to_table("待处理", pdf_file.name, "", "")
    
    def select_all_files(self):
        for i in range(self.files_list.count()):
            self.files_list.item(i).setSelected(True)
    
    def deselect_all_files(self):
        for i in range(self.files_list.count()):
            self.files_list.item(i).setSelected(False)
    
    def load_preset_prompt(self, prompt_name):
        if hasattr(prompts, prompt_name):
            prompt_content = getattr(prompts, prompt_name)
            self.prompt_edit.setText(prompt_content)
    
    def load_batch_preset_prompt(self, prompt_name):
        if hasattr(prompts, prompt_name):
            prompt_content = getattr(prompts, prompt_name)
            self.batch_prompt_edit.setText(prompt_content)
    
    def save_settings(self):
        # 更新模型配置
        self.model_config = ModelConfig(
            provider=self.provider_combo.currentText(),
            model=self.model_edit.text(),
            base_url=self.base_url_edit.text(),
            api_key=self.api_key_edit.text(),
            api_key_env=None,
        )
        QMessageBox.information(self, "提示", "设置已保存")
    
    @pyqtSlot(dict)
    def show_paper_dialog(self, paper_info):
        """在主线程中显示论文信息对话框"""
        dialog = PaperInfoDialog(paper_info)
        result = dialog.exec_()
        if result == QDialog.Accepted:
            action = dialog.selected_action
        else:
            action = 'skip'
        self.dialog_result_signal.emit(action)

    @pyqtSlot(str, str, str, str)
    def add_paper_to_table(self, status, title, authors, date):
        """添加论文到右侧表格"""
        row = self.papers_table.rowCount()
        self.papers_table.insertRow(row)
        
        status_item = QTableWidgetItem(status)
        title_item = QTableWidgetItem(title[:50] + "..." if len(title) > 50 else title)
        authors_item = QTableWidgetItem(authors[:30] + "..." if len(authors) > 30 else authors)
        date_item = QTableWidgetItem(date)
        
        # 设置颜色
        if status == "已下载":
            status_item.setForeground(QBrush(QColor("#4CAF50")))
        elif status == "处理中":
            status_item.setForeground(QBrush(QColor("#2196F3")))
        elif status == "已完成":
            status_item.setForeground(QBrush(QColor("#9C27B0")))
        
        self.papers_table.setItem(row, 0, status_item)
        self.papers_table.setItem(row, 1, title_item)
        self.papers_table.setItem(row, 2, authors_item)
        self.papers_table.setItem(row, 3, date_item)
        
        # 调整列宽
        self.papers_table.resizeColumnsToContents()

    def start_download(self):
        keyword = self.keyword_edit.text()
        if not keyword:
            QMessageBox.warning(self, "警告", "请输入检索关键词")
            return
        
        self.download_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_flag = False
        
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)  # 无限进度条
        
        self.processing_thread = threading.Thread(target=self.download_thread)
        self.processing_thread.start()
    
    def start_arxiv_search(self):
        """开始arXiv检索"""
        keyword = self.keyword_edit.text()
        if not keyword:
            QMessageBox.warning(self, "警告", "请输入检索关键词")
            return
        
        self.search_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_flag = False
        
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)  # 无限进度条
        
        self.processing_thread = threading.Thread(target=self.arxiv_search_thread)
        self.processing_thread.start()
    
    def arxiv_search_thread(self):
        """arXiv检索线程"""
        try:
            # 检索论文
            keyword = self.keyword_edit.text()
            max_results = self.max_papers_spin.value()
            download_dir = self.download_dir_edit.text()
            
            # 构建查询URL
            query = urllib.parse.quote(keyword)
            url = f'http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}'
            
            self.update_output_signal.emit(f"\n[*] 正在检索关键词: '{keyword}'...\n")
            
            # 发送请求
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            response = urllib.request.urlopen(req)
            xml_data = response.read()
            
            # 解析XML
            root = ET.fromstring(xml_data)
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'arxiv': 'http://arxiv.org/schemas/atom'
            }
            entries = root.findall('atom:entry', ns)
            
            if not entries:
                self.update_output_signal.emit("[-] 未检索到相关论文。\n")
                return
            
            downloaded_any = False
            
            for entry in entries:
                if self.stop_flag:
                    break
                
                # 提取论文信息
                raw_title = entry.find('atom:title', ns).text.replace('\n', ' ').strip()
                published = entry.find('atom:published', ns).text[:10]
                
                authors = [author.find('atom:name', ns).text for author in entry.findall('atom:author', ns)]
                authors_str = ", ".join(authors)
                
                # 获取摘要
                summary = entry.find('atom:summary', ns).text.strip()
                
                # 获取期刊信息
                journal_ref_elem = entry.find('arxiv:journal_ref', ns)
                comment_elem = entry.find('arxiv:comment', ns)
                
                if journal_ref_elem is not None:
                    journal_info = journal_ref_elem.text
                elif comment_elem is not None:
                    journal_info = f"备注: {comment_elem.text}"
                else:
                    journal_info = "未提供 (通常为预印本)"
                
                # 获取PDF链接
                pdf_link = None
                for link in entry.findall('atom:link', ns):
                    if link.attrib.get('title') == 'pdf':
                        pdf_link = link.attrib.get('href')
                        break
                
                # 创建论文信息字典
                paper_info = {
                    'title': raw_title,
                    'authors': authors_str,
                    'published': published,
                    'journal_info': journal_info,
                    'summary': summary,
                    'pdf_link': pdf_link,
                    'entry': entry,
                    'ns': ns
                }
                
                # 发射信号显示对话框，并等待结果
                self.show_paper_dialog_signal.emit(paper_info)
                
                # 等待对话框结果
                loop = QEventLoop()
                result_received = [None]  # 使用列表来修改闭包变量
                def on_result(action):
                    result_received[0] = action
                    loop.exit()
                self.dialog_result_signal.connect(on_result)
                loop.exec_()
                self.dialog_result_signal.disconnect(on_result)
                
                action = result_received[0]
                
                if action == 'download' and pdf_link:
                    # 下载论文
                    safe_title = "".join([c for c in raw_title if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()
                    if not safe_title:
                        id_element = entry.find('atom:id', ns)
                        safe_title = id_element.text.split('/')[-1] if id_element is not None else f"arxiv_paper_{int(time.time())}"
                    
                    pdf_url = pdf_link + '.pdf' if not pdf_link.endswith('.pdf') else pdf_link
                    file_path = Path(download_dir) / f"{safe_title}.pdf"
                    
                    if not file_path.exists():
                        self.update_output_signal.emit(f"[*] 正在下载: {raw_title}\n")
                        try:
                            req = urllib.request.Request(pdf_url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req) as response_pdf, open(file_path, 'wb') as out_file:
                                out_file.write(response_pdf.read())
                            self.update_output_signal.emit(f"[+] 下载成功\n")
                            
                            # 添加到论文列表
                            self.add_paper_signal.emit("已下载", raw_title, authors_str, published)
                            downloaded_any = True
                            
                            time.sleep(self.download_interval_spin.value())
                        except Exception as e:
                            self.update_output_signal.emit(f"[!] 下载失败: {e}\n")
                    else:
                        self.update_output_signal.emit(f"[-] 文件已存在\n")
                        downloaded_any = True
                
                elif action == 'quit':
                    self.update_output_signal.emit("[*] 已退出检索\n")
                    break
                elif action == 'skip':
                    self.update_output_signal.emit("[-] 跳过该论文\n")
                    continue
            
            if downloaded_any:
                self.update_output_signal.emit(f"[*] 下载完成，文件保存在: {download_dir}\n")
            else:
                self.update_output_signal.emit("[*] 没有下载任何论文\n")
                
        except Exception as e:
            # 注意：这里不能直接调用 QMessageBox，因为在子线程中
            self.update_output_signal.emit(f"[!] 检索过程出错：{str(e)}\n")
        finally:
            self.progress_bar.hide()
            self.search_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
    
    
    def start_analysis(self):
        file_path = self.file_path_edit.text()
        if not file_path:
            QMessageBox.warning(self, "警告", "请选择要分析的PDF文件")
            return
        
        self.analyze_btn.setEnabled(False)
        self.stop_btn2.setEnabled(True)
        self.stop_flag = False
        
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        
        self.add_paper_signal.emit("处理中", Path(file_path).name, "", "")
        
        self.processing_thread = threading.Thread(target=self.analysis_thread)
        self.processing_thread.start()
    
    def analysis_thread(self):
        #不再导入Paper_Assistant，而使用analysis_service
        request = AnalyzeRequest(
            source_type="file_path",
            file_path=self.file_path_edit.text(),
            prompt=self.prompt_edit.toPlainText() or "请总结这篇论文",
            save_dir=self.output_dir_edit.text(),
            enable_score=self.score_checkbox.isChecked(),
            model_config=self.model_config,
        )
        try:
            result = run_analysis_sync(request)
            if result and not result.error:
                self.add_paper_signal.emit("已完成", Path(self.file_path_edit.text()).name, "", "")
                self.update_output_signal.emit(f"[+] 分析完成: {result.report_path}\n")
            else:
                self.update_output_signal.emit(f"[!] 分析失败: {result.error if result else '未知错误'}\n")
        except Exception as e:
            # 注意：不能在子线程中调用 QMessageBox
            self.update_output_signal.emit(f"[!] 分析过程出错：{str(e)}\n")
        finally:
            self.progress_bar.hide()
            self.analyze_btn.setEnabled(True)
            self.stop_btn2.setEnabled(False)
    
    def start_batch_analysis(self):
        # 获取选中的文件
        selected_items = self.files_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请选择要分析的PDF文件")
            return
        
        self.batch_analyze_btn.setEnabled(False)
        self.stop_btn3.setEnabled(True)
        self.stop_flag = False
        
        self.progress_bar.show()
        self.progress_bar.setRange(0, len(selected_items))
        
        self.processing_thread = threading.Thread(target=self.batch_analysis_thread, args=(selected_items,))
        self.processing_thread.start()
    
    def batch_analysis_thread(self, selected_items):
        try:
            # 使用analysis_service的request接口来处理批量分析
            request = AnalyzeRequest(
                source_type="folder_path",
                folder_path=self.folder_path_edit.text(),
                prompt=self.batch_prompt_edit.toPlainText() or "请总结这篇论文",
                save_dir=self.batch_output_edit.text(),
                enable_score=self.batch_score_checkbox.isChecked(),
                model_config=self.model_config,
            )
            result = run_analysis_sync(request)
            if isinstance(result, BatchAnalysisResult):
                for item in result.results:
                    if item.error:
                        self.update_output_signal.emit(f"[!] {item.filename} 失败: {item.error}\n")
                    else:
                        self.add_paper_signal.emit("已完成", item.filename, "", "")
                        self.update_output_signal.emit(f"[+] {item.filename} -> {item.report_path}\n")
                if result.summary_csv_path:
                    self.update_output_signal.emit(f"[*] 评分汇总已保存: {result.summary_csv_path}\n")
            else:
                self.update_output_signal.emit(f"[!] 批量分析失败: {result.error if result else '未知错误'}\n")
        except Exception as e:
            # 注意：不能在子线程中调用 QMessageBox
            self.update_output_signal.emit(f"[!] 批量分析过程出错：{str(e)}\n")
        finally:
            self.progress_bar.hide()
            self.batch_analyze_btn.setEnabled(True)
            self.stop_btn3.setEnabled(False)
    
    def stop_processing(self):
        self.stop_flag = True
        self.append_output("正在停止处理...\n")
    
    @pyqtSlot(str)
    def append_output(self, text):
        self.output_text.insertPlainText(text)
        self.output_text.ensureCursorVisible()
        self.statusBar().showMessage("运行中..." if not self.stop_flag else "已停止")
    
    def closeEvent(self, event):
        # 恢复标准输出
        sys.stdout = sys.__stdout__
        event.accept()

def main():
    app = QApplication(sys.argv)
    
    # 设置应用样式
    app.setStyle('Fusion')

    # 设置应用图标（如果有的话）
    app.setWindowIcon(QIcon())
    
    window = PaperAssistantGUI()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()