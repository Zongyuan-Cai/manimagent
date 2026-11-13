# ManimAgent 0.3.1

将题目与文件转换为可追溯的结构化教学单元，派生完整 Markdown，再整体规划、分块生成可配音教学动画。支持文本／代码、表格数据、笔记本内容、PDF、DOCX、XLSX、PPTX，以及交给视觉模型读取的截图与扫描页；具体格式和未覆盖内容见[输入支持范围](docs/current/运行与配置.md#输入与结果)。动画使用本地可信 Manim 模板，不依赖 Docker；动画失败时保留已生成的完整文字。

已接入 MiMo TTS，支持按实测旁白时长排程的有声 MP4 与独立 WAV。视频目标为 2～6 分钟、720p／30 fps；节拍内高亮时间仍为估计，尚未实现词级强制对齐。配置与边界见 [MiMo 语音与验收](docs/current/MiMo语音与验收.md)。

可选用户记忆支持独立读取与学习：任务受理时冻结已有记忆，一次请求解释同时生成当前要求与规范偏好候选；后台仅执行确定性提交，生成失败仍可保存合法偏好。普通偏好按独立会话支持晋升，用户可明确设默认、纠正或忘记；临时要求仅影响当前任务。文字与后续视频共用冻结参数，新沉淀用于后续任务，网页默认关闭记忆。详见[用户记忆与个性化](docs/current/用户记忆与个性化.md)。

记忆同时提供可读的 Markdown 索引、独立条目与逐任务注入记录，方便核对证据、层级和实际请求内容。文件仅用于只读查看与导出，偏好变更使用受控管理入口，SQLite 是唯一生效依据；操作见[只读文件与迁移](docs/current/用户记忆与个性化.md#只读文件与迁移)。

## 快速启动

需要 Python 3.12 和 uv：

```powershell
uv sync --frozen --extra dev --extra animation
uv run manimagent serve
```

打开 http://127.0.0.1:8000，从顶部“设置”进入[核心模型](docs/current/LLM界面配置.md)：填写 Chat Completions 兼容地址、模型和密钥，测试后保存；截图／扫描页需要支持视觉的读取模型。需要配音时，切换到同一窗口的“语音配音”，独立配置 [MiMo 语音](docs/current/MiMo语音与验收.md)并试听。返回主页面，输入问题或上传材料，选择输出内容后开始生成；记忆管理从顶部“我的偏好”进入。

也可通过 `.env` 与 `config.yaml` 提供初始配置；仓库不附带密钥。只需文字时可省略 `--extra animation`。桌面源码交付目录提供 `start.cmd`。

```powershell
uv run manimagent doctor --animation
uv run manimagent answer --question "计算并解释 17+25" --output answer.md
uv run manimagent lesson --question "给定 a=(3,2)、b=(2,0)，解释正交投影并验证残差正交" --output lesson.mp4
```

## 文档与开发

**[文档导航](docs/README.md)** 汇总运行说明、当前能力、模块架构、设计规范、研究和验证证据。

架构见[固定教学流水线](docs/design/00-固定教学流水线架构.md)与[用户记忆设计](docs/design/05-长期记忆与个性化.md)：已接入固定请求解释、结构化教学单元、类型化偏好、独立记忆提交及可信渲染参数。实际行为与尚待验收项目见[开发计划](docs/current/开发计划.md)。

生产代码位于 `src/manimagent/`，按 `api`、`jobs`、`materials`、`answers`、`memory`、`video`、`llm`、`web` 分工；显式真实评测位于 `evals/`，确定性测试与共享样例位于 `tests/`。`scripts/` 只保留启动、打包、安装验证和材料生成工具。

```powershell
uv run python -m pytest -q
uv run ruff check src tests scripts evals
uv run ruff format --check src tests scripts evals
node --test tests/web/*.test.mjs
```

`testmaterial/` 与 `testresult/` 保留材料和真实成功／失败证据。真实评测可能产生模型用量，需显式运行，不由基础 CI 自动调用。旧 MVP 源码和过时文档已从当前目录移除，需要复现时从[固定历史版本](https://github.com/Zongyuan-Cai/manimagent/tree/3e1605c120cd31269406b146b22a6aec85075dbc)检出到独立目录。
