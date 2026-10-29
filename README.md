# ManimAgent

面向长期学习记忆的个性化可视化教育智能体。

项目近期围绕 LLM 接入、材料输入与解析、独立文本回答和动画生成组织开发。TTS 服务后接，旁白与音画对齐契约在正式版规划阶段建立，长期学习记忆和个性化暂缓。

当前提供 0.1.0 MVP：纯文本输入、讲解规划、逐场景 Manim 生成、执行纠错、VLM 检查与修订、正式渲染及视频合成，包含本地网页、任务 API 与 CLI。真实配音尚未接入，学习记忆与个性化暂缓。

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

## 文档与开发方向

统一入口见 [文档总览](docs/index.md)。文档按能力组织，近期推进前四项，长期记忆与个性化暂缓。

| 模块 | 设计文档 |
|---|---|
| 多厂商、多协议与统一模型调用 | [LLM 接入与调用](docs/01-LLM接入与调用.md) |
| 文件上传、格式解析与来源保留 | [材料输入与解析](docs/02-材料输入与解析.md) |
| 独立回答、来源引用与修订 | [文本回答生成](docs/03-文本回答生成.md) |
| 分镜、Manim、检查、渲染与合成 | [动画生成](docs/04-动画生成.md) |
| 长期学习证据与个性化方向，暂缓 | [长期记忆与个性化](docs/05-长期记忆与个性化.md) |

[开发计划](docs/开发计划.md) 维护实施顺序；[运行与配置](docs/运行与配置.md) 说明当前 MVP 用法；[MVP 验证记录](docs/MVP验证记录.md) 和 [输入与验收样例](docs/mvp/输入与验收样例.md) 保留运行证据与回归基线。

当前独立材料、回答、视频资源和 `/api/v2` 仍是后续设计，0.1.0 继续使用纯文本到动画的原有入口。

## 正式版 Pipeline 与音画对齐设计

本次仅更新文档，当前运行代码仍为上述 0.1.0 MVP。本地 0.2.0 文字链路阶段成果与代码迁移另行提交，不随本次文档推送发布。

正式版按“固定 Answer → 旁白与画面语义节拍 → cue／对象／事件绑定 → 共同时间线 → 渲染与检查”组织。真实 TTS 暂缓，但旁白脚本和事件契约现在就需要确定；估计时间与未来音频实测明确区分。

- [动画 Pipeline](docs/04-动画生成.md)：生产流程、版本与失败边界。
- [Prompt 与检查规范](docs/动画Prompt与检查规范.md)：角色输入输出、题型差异与同步检查。
- [时长与生产验收](docs/动画时长与生产验收.md)：2～6 分钟范围、生成耗时和评测，均为待验证目标。
- [音画对齐开源项目与论文调研](docs/research/音画对齐开源项目与论文调研.md)：固定版本源码、论文机制和证据限制。
- [历史动画 MVP 流程](docs/mvp/动画MVP流程.md)：当前已提交版本的输入、API、预算和检查基线。

## 参考项目

动画生成与视觉检查参考 [Paper2Manim](https://github.com/jwj1342/Paper2Manim)。学习者长期记忆与个性化教学规划按本项目的目标独立设计。
