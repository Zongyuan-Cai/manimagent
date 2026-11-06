# 核心 API 与视觉读取配置

更新：2026-11-06。本文说明当前网页配置和私有凭据行为。

启动 `uv run manimagent serve`，打开 [本地页面](http://127.0.0.1:8000)，从顶部“设置”进入“核心模型”。这组连接负责文字、动画和可选的图片读取；“语音配音”位于同一设置窗口中，仍独立保存，操作见 [MiMo 语音](MiMo语音与验收.md)。只需文字或无声视频时不必配置 TTS。

## 使用方法

1. 选择 API 服务预设，或选择自定义／本地兼容接口，填写 API 地址、实际模型 ID 和密钥。根地址包含版本前缀，例如 `https://gateway.example/v1`；粘贴以 `/chat/completions` 结尾的完整地址也可以，界面会将它保存为完整生成地址，不再重复追加路径。
2. 点击“使用此模型”，先让一个模型承担回答、内容检查、动画规划、分块生成和计划检查。需要不同模型时，再展开“高级 · 为不同任务分配模型”。
3. 点击“测试连接”检查当前模型；“测试结构化输出”位于该模型卡片的“高级设置”中。测试使用当前填写的草稿，会产生少量真实用量；**测试不保存配置**。
4. 点击“保存核心配置”。新任务立即生效，无需重启；已接收任务继续使用原配置与密钥引用。视频使用正文前始终要求有效内容检查。

| 预设 | 填入内容 | 仍需用户提供 |
|---|---|---|
| DeepSeek 官方 | `https://api.deepseek.com`、`deepseek-flash`、Bearer 鉴权、`max_tokens` | 自己账户的 API Key；可换其他可用模型 |
| MiMo 官方 · 核心模型 | `https://api.xiaomimimo.com/v1`、`api-key` 请求头、`max_completion_tokens` | 可用的文字／视觉模型 ID 与 API Key；不能填写 TTS 模型替代回答模型 |
| 自定义／本地兼容接口 | 保留可编辑地址和参数 | 服务商的 Chat Completions 地址、模型 ID、鉴权方式；本地无密钥服务在高级选项选“无鉴权” |

这些预设配置的是同一种 Chat Completions 协议，不会转换 Anthropic Messages、Gemini 原生协议或 Responses。地址和默认调用参数依据 [DeepSeek 官方首个调用](https://api-docs.deepseek.com/) 与 [MiMo Chat Completions 说明](https://mimo.mi.com/docs/api/text-generation/openai-api)核对；预设存在不代表已完成该厂商的真实全链路验收。

## 截图与扫描页使用哪个模型

确认服务商提供的模型支持 `image_url` 图片输入后，展开该模型卡片的“高级设置”，勾选“支持图片输入”。在角色分配中，将“截图与扫描页读取模型”指向该连接；留空表示跟随回答模型。只有明确声明支持视觉的连接出现在专用读取模型选项里。普通文字文件不需要视觉模型，图片读取与 TTS 无关。

“使用此模型”会同时把支持视觉的连接绑定为读取模型；纯文本连接不作这样的声明。勾选能力是配置声明，文本／结构化连接测试不验证视觉能力，也不能证明某张图里的数学符号读对了。

按 2026-11-06 的 [MiMo 图片理解文档](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/multimodal-understanding/image-understanding)，当前支持的型号包含 `mimo-v2.6-flash`、`mimo-v2.6-pro` 和 `mimo-v2.6-pro-ultraspeed`，支持公开图片 URL 与 Base64 图片输入。本项目的本地图像通过有界处理后作为 `image_url` 的 data URL 发送，不把本机路径当作公网地址。本轮使用 `mimo-v2.6-flash` 完成了一次真实截图→读取→回答→检查，见[输入与配置验收](../../testresult/onboarding-20261106/报告.md)；其他型号未实测，单例不代表复杂公式识读正确率。实际可用型号、账户权限和输入限制仍以服务商为准。旧 `mimo-v2.5`／`mimo-v2.5-pro` 的[文字型号退役公告](https://mimo.mi.com/docs/en-US/news/v2.5-news)不等于独立语音型号 `mimo-v2.5-tts` 退役。

## 高级参数与密钥

“新增模型”可以为另一个接口或模型建立配置，当前最多 16 项。高级选项包括完整生成 URL、自定义鉴权头名称、最大输出 token、模型上下文上限、输出长度参数、thinking 和检查报告的 JSON 模式。完整生成 URL 填写后优先于根地址；普通文字回答始终不强制 JSON。

同一服务、同一模型也可以建立两项配置，分别设置思考模式，再按角色绑定。[DeepSeek Flash 示例](examples/deepseek-checked.yaml)对五个角色启用 thinking。历史复测中的一次对照里，快速检查漏过一处向量起终点颠倒；开启推理后检出该错误约用 6.7 秒，正确正文约用 2.2 秒。少量对照不代表正确率保证，完整成功率和耗时见[复测报告](../../testresult/review-animation-20261103/报告.md)。其他服务保持 `thinking: default`，仅在确认接口支持时发送该参数。更换检查模型或思考模式后，旧正文进入视频前会重新做内容检查。

有效上下文限制为模型上限与回答总预算的较小值，包含输出预留。连接测试只验证选择的文本或结构化能力，不代表视觉识读、工具或完整回答质量已经验证。修改基础地址会清掉旧的完整生成地址覆盖；换到另一服务来源时，必须提供新密钥或明确清除旧密钥关联，不会自动把原密钥发给新服务。

API Key 留空表示保留当前密钥。“清除此模型保存的密钥关联”使新任务不再使用该密钥；不能同时填入新密钥和勾选清除。为保持已接受任务可执行，旧密钥版本会继续保存在私有凭据存储中；本版没有凭据历史删除界面。更新 API Key 会创建新引用，不覆盖旧任务使用的凭据。当前模型列表支持新增和修改；没有删除已保存连接的界面。

## 保存位置和优先级

界面从现有 `models/roles` 读取初始配置；保存的配置覆盖后续任务的对应设置。不会改写 `.env` 或 `config.yaml`，不通过修改进程环境变量切换模型或账户。网页保存后，使用同一私有目录的 `answer` 命令和 `doctor` 也能读取配置。

配置位于 `private_dir/configuration/llm.json`；未配置 private_dir 时为 `runs/current/private/configuration/llm.json`。其中只包含模型、参数、角色和凭据引用，不含密钥值。API Key 在单独的 `credentials.json` 中保存：Windows 使用当前用户 DPAPI 加密，复制到其他用户或系统后需重新输入；其他系统使用权限为 0600 的本地文件。该私有目录不进入 Git 和发布包。

公共接口只返回密钥是否已配置，不返回密钥值；页面保存后清空密码框，不将密钥写到 localStorage。测试错误不直接回显上游错误体，任务快照和诊断只保留凭据引用。配置修改接口检查本地 Host 和同源 Origin；本项目仍按本地单用户服务运行。

## 接口与实现

| 方法与路径 | 用途 |
|---|---|
| GET `/api/v2/llm-settings` | 配置 revision、模型和角色、密钥配置状态 |
| PUT `/api/v2/llm-settings` | 保存配置与可选新密钥，携带 expected_revision；版本冲突为 409 |
| POST `/api/v2/llm-settings/test` | 使用 model_id、draft 与 text／structured scope 测试草稿；不持久化密钥或配置 |

这些接口管理当前文字与动画角色配置；MiMo 语音使用单独的 TTS 配置。测试最多同时执行一个，每项最多两次调用、30 秒总预算；测试成功不自动切换或保存模型。

代码位置为 `llm/settings.py`、`llm/credentials.py`、`api/settings.py`、`web/settings.js`。调用层通过 `CredentialStore.resolve()` 读取私有引用或环境变量；`JobManager` 在同一把锁内接受配置快照与更新默认设置，防止提交任务时混用新旧参数。角色选择统一使用 `Settings.model_id_for()`：显式角色及其继承优先，默认优先 `text`，其次第一个配置；空配置仍不可调用。

对应回归用例位于 `tests/unit/llm/test_settings.py`：保存后立即生成、重启读取、空密钥保留、密钥版本、明确停用、版本冲突、两类草稿测试、错误脱敏、引用边界、跨来源拒绝和环境密钥状态。浏览器操作验证使用隔离目录与受控提供商，不替换实际用户配置，也不调用真实供应商。

[返回文档导航](../README.md)
