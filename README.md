# ManimAgent

面向长期学习记忆的个性化可视化教育智能体。

项目以 Manim 动画生成为基础，逐步加入学习材料解析、配音与音画同步、学习者长期记忆、个性化讲解规划和 Web 交互，形成从材料输入到学习反馈更新的完整流程。

当前提供第一阶段的 0.1.0 MVP：纯文本输入、讲解规划、逐场景 Manim 生成、执行纠错、VLM 检查与修订、正式渲染及视频合成，包含本地网页、任务 API 与 CLI。学习记忆和配音按后续阶段推进。

## 快速运行

需要 Python 3.12、uv 和运行中的 Docker Engine；Windows 使用 Docker Desktop 的 Linux 容器模式。

```bash
uv sync --frozen --extra dev
docker build -f docker/renderer.Dockerfile -t manimagent-renderer:0.1.0 .
```

复制 .env.example 为 .env，填写文本模型与视觉模型的接口地址、模型名和密钥。需要修改预算或角色配置时，再复制 config.example.yaml 为 config.yaml。

```bash
uv run manimagent doctor
uv run manimagent serve
```

打开 http://127.0.0.1:8000。Windows 已有项目环境时，也可直接运行 .\.venv\Scripts\manimagent.exe serve。完整说明见[运行与配置](docs/运行与配置.md)。

不配置模型也可运行 uv run manimagent smoke，验证真实渲染环境。测试范围和当前限制见[MVP 验证记录](docs/MVP验证记录.md)。

## 开发路线

| 阶段 | 能力 | 设计文档 |
|---|---|---|
| 1 | 文本到动画，包含执行纠错与 VLM 检查修订 | [基础动画生成](docs/stages/01-基础动画生成.md) |
| 2 | 多种学习材料输入，配音、字幕与音画同步 | [完整视听讲解](docs/stages/02-完整视听讲解.md) |
| 3 | 学习证据、知识状态、跨会话记忆与纠正 | [长期学习记忆](docs/stages/03-长期学习记忆.md) |
| 4 | 根据学习记忆调整讲解内容与视觉表达 | [记忆驱动的个性化规划](docs/stages/04-记忆驱动的个性化规划.md) |
| 5 | Web 交互、反馈更新、局部重讲与片段复用 | [交互集成与局部更新](docs/stages/05-交互集成与局部更新.md) |
| 6 | 对照评测、系统优化、文档与研究成果交付 | [评测优化与成果交付](docs/stages/06-评测优化与成果交付.md) |

第一阶段完成基础动画流程，学习记忆在第三阶段引入。前两个阶段形成的内容生成能力由后续阶段复用。

## 文档入口

- [运行与配置](docs/运行与配置.md)：安装、模型配置、网页、CLI 和测试。
- [MVP 验证记录](docs/MVP验证记录.md)：自动化测试、真实渲染与尚未验证的范围。
- [第一版 MVP 设计](docs/MVP设计.md)：纯文本输入、生成流程、接口、任务状态与验收规则。
- [文档目录](docs/README.md)：阅读顺序、文档职责与状态。
- [总体开发计划](docs/总体开发计划.md)：项目目标、六个总体阶段与验收标志。
- [技术栈与整体设计](docs/技术栈与整体设计.md)：完整系统的技术候选、模块和数据关系。
- [第一阶段任务清单](docs/第一版开发计划.md)：第一阶段内部任务的进一步拆分。

阶段设计为初稿，技术选择与验收门槛将依据实现和测试结果更新。测试样例、生成日志与实验记录从第一阶段开始积累。

## 参考项目

动画生成与视觉检查参考 [Paper2Manim](https://github.com/jwj1342/Paper2Manim)。学习者长期记忆与个性化教学规划按本项目的目标独立设计。
