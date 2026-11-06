"""One user-facing description of the local parser's actual input contract."""

from __future__ import annotations

from manimagent.schemas import ParseRequest, TextInput

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TEXT_EXTENSIONS = [
    ".txt",
    ".log",
    ".tex",
    ".rst",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".r",
    ".m",
    ".sh",
    ".ps1",
    ".bat",
    ".sql",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
]

FORMATS = (
    {
        "extensions": TEXT_EXTENSIONS,
        "label": "文本、代码与日志",
        "description": "UTF-8 正文、LaTeX、常见源代码和配置文件；只读取，不执行代码或命令。",
    },
    {
        "extensions": [".md", ".markdown"],
        "label": "Markdown",
        "description": "UTF-8 正文、文字公式和表格；图片链接不等于已读取图片。",
    },
    {
        "extensions": [".pdf"],
        "label": "PDF 文档与扫描件",
        "description": "可选连续页码。文字页本地提取；扫描、图形或异常文字页需要视觉模型读取。",
    },
    {
        "extensions": [".docx"],
        "label": "Word DOCX",
        "description": "段落、表格、常见公式和显式上下标；内嵌图片使用视觉模型，特殊对象需核对。",
    },
    {
        "extensions": [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"],
        "label": "截图与图片",
        "description": "需在核心 API 中配置视觉模型。支持静态图片；多帧 GIF/TIFF 请按页拆分。",
        "requires_vision": True,
    },
    {
        "extensions": [".csv", ".tsv"],
        "label": "CSV / TSV",
        "description": "逐行保留列值和来源行号，空列不丢弃。",
    },
    {
        "extensions": [".json", ".jsonl", ".ndjson", ".ipynb"],
        "label": "JSON 与 Notebook",
        "description": "JSON/JSONL 结构校验；Notebook 读取文本单元和文本输出，不执行代码。",
    },
    {
        "extensions": [".xlsx"],
        "label": "Excel XLSX",
        "description": "读取工作表、单元格、公式与已保存的计算值；公式无缓存或图表图片时需要核对，不自动计算。",
    },
    {
        "extensions": [".pptx"],
        "label": "PowerPoint PPTX",
        "description": "读取幻灯片文字、表格和讲者备注，可选幻灯片范围；图片或特殊对象需进一步读取或核对。",
    },
)
UPLOAD_EXTENSIONS = frozenset(extension for item in FORMATS for extension in item["extensions"])


def _maximum(field, name: str) -> int:
    return next(getattr(rule, name) for rule in field.metadata if hasattr(rule, name))


def input_capabilities(settings) -> dict:
    return {
        "formats": list(FORMATS),
        "unsupported": [
            {
                "label": "旧版 DOC/PPT/XLS、压缩包、音视频和其他二进制",
                "action": "旧 Office 请另存为 DOCX/PPTX/XLSX；压缩包先解压并选择需要的文件，音视频请提供文字转录。",
            },
        ],
        "limits": {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_parse_pages": settings.max_parse_pages,
            "max_parse_chars": settings.max_parse_chars,
            "max_visual_pages": settings.max_visual_pages,
            "max_inputs": _maximum(ParseRequest.model_fields["inputs"], "max_length"),
            "max_text_chars": _maximum(TextInput.model_fields["text"], "max_length"),
        },
    }
