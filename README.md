# ManimAgent 0.3.1

将题目与文件转换为可追溯的完整 Markdown，再按教学内容整体切分、分块生成可配音教学动画。支持直接文本、UTF-8 TXT／Markdown、文本型 PDF（可选页）和 DOCX 普通段落／表格；动画使用本地可信 Manim 模板，不依赖 Docker。动画失败时保留已生成的完整文字。

已接入 MiMo TTS，支持按实测旁白时长排程的有声 MP4 与独立 WAV。视频目标为 2～6 分钟、720p／30 fps；节拍内高亮时间仍为估计，尚未实现词级强制对齐。配置与边界见 [MiMo 语音与验收](docs/current/MiMo语音与验收.md)。

可选用户记忆支持从表达生成有依据的短期条目、独立会话重复后进入深层，以及确认、纠正、删除和清理。文字与后续视频共用生成时的记忆快照；网页默认关闭，可在“用户记忆”面板管理。首版采用本机单用户规则，不包含生成器经验或学习效果结论，详见 [用户记忆与个性化](docs/current/用户记忆与个性化.md)。

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

**[文档导航](docs/README.md)** 汇总运行说明、当前能力、模块架构、设计规范、研究和验证证据。

生产代码位于 `src/manimagent/`，按 `api`、`jobs`、`materials`、`answers`、`memory`、`video`、`llm`、`web` 分工；显式真实评测位于 `evals/`，确定性测试与共享样例位于 `tests/`。`scripts/` 只保留启动、打包、安装验证和材料生成工具。

```powershell
uv run python -m pytest -q
uv run ruff check src tests scripts evals
uv run ruff format --check src tests scripts evals
node --test tests/web/*.test.mjs
```

`testmaterial/` 与 `testresult/` 保留材料和真实成功／失败证据。真实评测可能产生模型用量，需显式运行，不由基础 CI 自动调用。旧 MVP 源码和过时文档已从当前目录移除，需要复现时从[固定历史版本](https://github.com/Zongyuan-Cai/manimagent/tree/3e1605c120cd31269406b146b22a6aec85075dbc)检出到独立目录。
