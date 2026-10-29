# ManimAgent 0.2.0

当前开发版本提供“问题与文件输入 → 可追溯材料 → 完整 Markdown 回答”。支持 UTF-8 TXT、Markdown、文本型 PDF（可选页）和 DOCX；提供来源引用、内容检查、公式与表格展示、下载和人工修订。本阶段不生成动画。

## 运行

需要 Python 3.12 和 uv，无需 Docker：

```powershell
uv sync --frozen --extra dev
uv run manimagent serve
```

打开 http://127.0.0.1:8000，在“LLM 配置”填写服务地址、模型和 API Key，测试并保存。也可以使用 `.env` 与 `config.yaml` 提供初始配置。当前模型配置为 DeepSeek 官方 `deepseek-flash`，密钥仅保存在本机私有凭据存储中。

```powershell
uv run manimagent doctor
uv run manimagent answer --question "求 a 在 b 方向上的投影" --file notes.pdf --output answer.md
```

详细使用与边界见[运行与配置](docs/运行与配置.md)、[LLM 界面配置](docs/LLM界面配置.md)。

PR 自动运行 Windows／Linux 回归、代码检查与独立安装包验证，结果统一汇总为 `PR checks`。配置与合并门槛见 [PR 流水线](docs/PR流水线.md)。

## 当前版本与历史 MVP

| 项目 | 当前开发版本 | 历史动画 MVP |
|---|---|---|
| 项目根目录 | 本目录 | `legacy_mvp/` |
| 版本 | 0.2.0 | 独立冻结的 0.1.0 |
| 虚拟环境与锁文件 | `.venv/`、`uv.lock` | 自有 `.venv/`、`uv.lock` |
| 默认端口 | 8000 | 8001 |
| 默认数据 | `runs/current/private/` | `legacy_mvp/runs/` |
| API 与命令 | `/api/v2`；serve、answer、doctor | 原 `/api/jobs`；generate、smoke、doctor |
| 自动化测试 | `tests/unit/`、`tests/integration/` | `legacy_mvp/tests/` |

当前程序不导入历史动画模块，不包含 LangGraph／Docker 依赖和旧 API。历史程序只在[独立归档项目](legacy_mvp/README.md)中运行，不与当前版本混装到同一虚拟环境。当前发布包排除历史项目、测试材料、结果和私有数据。

## 测试与审查

```powershell
uv run python -m pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

`testmaterial/` 保存可复现的上传样本，`testresult/` 保存真实测试证据与报告；`scripts/create_test_materials.py` 可重建材料，`scripts/test_deepseek_e2e.py` 显式运行真实模型测试，会使用服务已配置的模型和用量，CI 不自动执行。密钥不会写入材料、报告或日志。

本轮[真实测试报告](testresult/测试报告.md)与[代码审查](testresult/代码审查.md)维护验证结果、修复与剩余边界。长期完整文字／动画双输出、记忆与个性化仍属于[设计文档](docs/index.md)的后续工作。
