# ManimAgent 0.3.1

将题目与文件转换为可追溯的完整 Markdown，再按教学内容整体切分、分块生成无声动画。支持直接文本、UTF-8 TXT／Markdown、文本型 PDF（可选页）和 DOCX 普通段落／表格；动画使用本地可信 Manim 模板，不依赖 Docker。动画失败时保留已生成的完整文字。

当前使用通用 system prompt，暂不接入声音合成、个性化或长期记忆。视频目标为 2～6 分钟、720p／30 fps；旁白稿与画面事件共用估计时间线，尚不代表实际音画同步或生产质量已达标。

## 快速启动

需要 Python 3.12 和 uv：

```powershell
uv sync --frozen --extra dev --extra animation
uv run manimagent serve
```

打开 http://127.0.0.1:8000，在“模型设置”填写 Chat Completions 兼容地址、模型和密钥，测试后保存。也可通过 `.env` 与 `config.yaml` 提供初始配置；仓库不附带密钥。只需文字时可省略 `--extra animation`。桌面源码交付目录提供 `start.cmd`。

```powershell
uv run manimagent doctor --animation
uv run manimagent answer --question "计算并解释 17+25" --output answer.md
uv run manimagent lesson --question "给定 a=(3,2)、b=(2,0)，解释正交投影并验证残差正交" --output lesson.mp4
```

## 文档与开发

**[文档导航](docs/README.md)** 是运行说明、当前能力、模块架构、设计规范、研究及历史证据的唯一入口。

生产代码位于 `src/manimagent/`，按 `api`、`jobs`、`materials`、`answers`、`video`、`llm`、`web` 分工；显式真实评测位于 `evals/`，确定性测试与共享样例位于 `tests/`。`scripts/` 只保留启动、打包、安装验证和材料生成工具。

```powershell
uv run python -m pytest -q
uv run ruff check src tests scripts evals
uv run ruff format --check src tests scripts evals
node --test tests/web/*.test.mjs
```

`testmaterial/` 与 `testresult/` 保留材料和真实成功／失败证据。真实评测可能产生模型用量，需显式运行，不由基础 CI 自动调用。历史 0.1.0 项目独立冻结在 `legacy_mvp/`，使用自己的环境、锁文件和端口，不参与当前运行包。
