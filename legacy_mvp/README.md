# 历史 MVP 独立归档

此目录为 0.1.0 动画 MVP 的独立项目。仅在本目录建立并运行自己的虚拟环境，不与根目录当前文字版本混装。默认服务端口 8001，配置、运行数据与测试均留在此目录。

原始快照提交：`24b027e1a56765c1d2c397562356716b0df300d7`。除默认端口外保留该提交实现；不包含用户本地密钥或运行数据。

# ManimAgent

面向长期学习记忆的个性化可视化教育智能体。

项目近期围绕 LLM 接入、材料输入与解析、独立文本回答和动画生成组织开发。配音与音画同步按后续需求扩展，长期学习记忆和个性化暂缓。

当前提供 0.1.0 MVP：纯文本输入、讲解规划、逐场景 Manim 生成、执行纠错、VLM 检查与修订、正式渲染及视频合成，包含本地网页、任务 API 与 CLI。配音为后续扩展，学习记忆与个性化暂缓。

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

## 参考项目

动画生成与视觉检查参考 [Paper2Manim](https://github.com/jwj1342/Paper2Manim)。学习者长期记忆与个性化教学规划按本项目的目标独立设计。
