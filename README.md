# ManimAgent 0.3.0

当前版本提供“问题与文件 → 可追溯材料 → 完整 Markdown → 整体教学切分 → 分块生成 → 无声动画”。支持 UTF-8 TXT、Markdown、文本型 PDF（可选页）和 DOCX；保留来源、内容检查、下载和修订。视频采用本地可信 Manim 模板，目标 2～6 分钟，720p／30 fps，不依赖 Docker。动画失败时完整文字仍保留。

## 运行

需要 Python 3.12 和 uv，无需 Docker：

```powershell
uv sync --frozen --extra dev --extra animation
uv run manimagent serve
```

打开 http://127.0.0.1:8000，在“LLM 配置”填写服务地址、模型和 API Key，测试并保存。也可以使用 `.env` 与 `config.yaml` 提供初始配置。当前模型配置为 DeepSeek 官方 `deepseek-flash`，密钥仅保存在本机私有凭据存储中。

```powershell
uv run manimagent doctor
uv run manimagent answer --question "求 a 在 b 方向上的投影" --file notes.pdf --output answer.md
```

只需文字时可不安装 animation 可选依赖。桌面完整交付目录可直接双击 `start.cmd`。动画表达范围、预算、版本复用和 API 见[动画实现与本地运行](docs/动画实现与本地运行.md)，模型设置见[LLM 界面配置](docs/LLM界面配置.md)。

```powershell
uv run manimagent doctor --animation
uv run manimagent lesson --question "给定 a=(3,2)、b=(2,0)，解释并计算正交投影，验证残差正交" --output lesson.mp4
```

PR 自动运行 Windows／Linux 回归、代码检查与独立安装包验证，结果统一汇总为 `PR checks`。配置与合并门槛见 [PR 流水线](docs/PR流水线.md)。

## 当前版本与历史 MVP

| 项目 | 当前开发版本 | 历史动画 MVP |
|---|---|---|
| 项目根目录 | 本目录 | `legacy_mvp/` |
| 版本 | 0.3.0 | 独立冻结的 0.1.0 |
| 虚拟环境与锁文件 | `.venv/`、`uv.lock` | 自有 `.venv/`、`uv.lock` |
| 默认端口 | 8000 | 8001 |
| 默认数据 | `runs/current/private/` | `legacy_mvp/runs/` |
| API 与命令 | `/api/v2`；serve、answer、lesson、video、doctor | 原 `/api/jobs`；generate、smoke、doctor |
| 自动化测试 | `tests/unit/`、`tests/integration/` | `legacy_mvp/tests/` |

当前程序不导入历史动画模块，不包含 LangGraph／Docker 依赖和旧 API。历史程序只在[独立归档项目](legacy_mvp/README.md)中运行，不与当前版本混装到同一虚拟环境。当前发布包排除历史项目、测试材料、结果和私有数据。原生进程只运行项目自己的模板，不执行模型生成的 Python，也不携带模型凭据。

## 测试与审查

```powershell
uv run python -m pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

`testmaterial/` 保存可复现的上传样本，`testresult/` 保存真实测试证据与报告；`scripts/create_test_materials.py` 可重建材料，`scripts/test_deepseek_e2e.py` 显式运行真实模型测试，会使用服务已配置的模型和用量，CI 不自动执行。密钥不会写入材料、报告或日志。

`scripts/test_animation_e2e.py` 显式测试真实 DeepSeek＋本地动画完整链路，保存正文、计划、MP4、逐节拍关键帧和用量。动画复测报告见[正式动画真实测试](testresult/animation-20261030/真实测试报告.md)。当前为受约束模板的语义／几何／布局／媒体检查；视觉模型未调用，实际音画同步未验证，不把估计时间冒充音频实测。

最新的 [2026-10-30 真实测试报告](testresult/review-20261030/真实测试报告.md)与[代码审查](testresult/review-20261030/代码审查.md)覆盖多模型配置、输入格式和完整正文交付，记录实际 DeepSeek 调用及修复证据。此前的[测试报告](testresult/测试报告.md)与[代码审查](testresult/代码审查.md)保留为历史记录。

正式版方案见[动画 Pipeline](docs/04-动画生成.md)、[Prompt 与检查规范](docs/动画Prompt与检查规范.md)、[时长与生产验收](docs/动画时长与生产验收.md)。[音画对齐调研](docs/research/音画对齐开源项目与论文调研.md)进一步明确：TTS 服务暂缓，但旁白、语义锚点和画面事件现在就要共用时间线；当前估时不能冒充真实同步。2～6 分钟和生成耗时均为待验证目标。实施顺序见[开发计划](docs/开发计划.md)，记忆与个性化继续暂缓。
