# 开源 Agent 文件上传与 LLM 输入调研

调研日期：2026年10月29日。

正式设计已纳入 [材料输入与解析](../02-材料输入与解析.md)、[文本回答生成](../03-文本回答生成.md)和 [LLM 接入与调用](../01-LLM接入与调用.md)。本文保留调研时的项目比较和建议，接口、职责及实施顺序以对应正式设计与 [开发计划](../开发计划.md)为准。

面向 ManimAgent 的建议是：保留现有 `FileAsset → MaterialIR → Answer` 边界，先实现可追溯的文档解析与全文上下文组装，再按材料规模增加检索和按需读取。模型原生文件输入作为可选解析路径，由提供商适配器处理。数学题需要同时保留题设、公式和图示，检索命中不能作为材料完整的证明。

以下区分开源项目的实际行为与本项目的设计建议。依据为官方文档和固定提交的源码，覆盖上传、持久化、解析、上下文组装和模型输入；结论不包含解析精度、延迟或成本的实测比较。Dify 和 Open WebUI 提供应用编排或聊天宿主，LibreChat、OpenHands 提供 Agent 产品，Deep Agents 提供框架层能力，比较的是各自的文件处理链路。

## 文件交给模型的四种方式

上传接口保存文件后，仍需要决定模型如何取得内容。几个项目组合使用以下方式：

| 方式 | 模型实际收到什么 | 适用场景 | 主要限制 |
|---|---|---|---|
| 解析后全文注入 | 解析出的文本、表格、公式及来源标记，进入消息正文 | 单题、短讲义、明确页码范围 | 受上下文预算限制，解析可能丢失版面和图示 |
| 检索后注入 | 根据问题找到的若干内容块及来源 | 长文档、多文档问答 | 召回结果可能缺少定义、前提和相邻条件 |
| 原生文件输入 | 提供商支持的文件或多模态内容块，可能携带字节、URL 或远端引用 | 有对应文件能力的模型 | 格式和协议有差异，支持上传不等于支持读取 |
| 工具按需读取 | 初始文件清单；模型调用读取、搜索或执行工具后取得内容 | 工作区任务、数据分析、需要反复查看材料的 Agent | 必须有可用工具和可访问文件，读取范围仍需控制 |

这四种方式可以组合。例如先注入题目全文，再提供工具读取邻近定义；也可以用视觉模型解析题图，把结果保存成 MaterialIR，随后用另一个文本模型回答。

## Dify 将上传和文档提取分开

### 上传与持久化

`FileService.upload_file()` 检查文件名、扩展名规则和大小，使用包含租户与 UUID 的存储键保存字节，再写入 `UploadFile` 元数据。原始文件名用于展示，存储键由服务端生成。记录包括大小、媒体类型、创建者、存储类型和内容摘要；这个版本的摘要算法是 SHA3-256。[上传服务源码](https://github.com/langgenius/dify/blob/70975c1873aeab9e80ffcf5b2fec3796eda77059/api/services/file_service.py#L74-L168)

### 文档怎样进入 LLM

一条明确的文档路径是：

```text
文件变量 → Document Extractor → text 变量 → LLM 提示词
```

单文件输出字符串，多文件输出字符串数组。文档提取本身可以独立于 LLM 节点执行，下游通过变量引用其文本输出。[官方节点说明](https://docs.dify.ai/en/cloud/use-dify/nodes/doc-extractor)

当前 Dify 将这个节点的实现放在 `langgenius/graphon` 依赖中。Dify 的依赖声明固定了 Graphon 提交；节点按扩展名或 MIME 分派解析器，文本型 PDF 先用 PDFium 提取，结果为空时再尝试 pypdf；DOCX 使用 python-docx，按内容顺序提取段落和表格。[依赖版本](https://github.com/langgenius/dify/blob/70975c1873aeab9e80ffcf5b2fec3796eda77059/api/pyproject.toml#L64-L67)、[解析分派](https://github.com/langgenius/graphon/blob/4fa22141774dff80e90e4dfa98ea3e6fbf0421bb/src/graphon/nodes/document_extractor/node.py#L759-L781)、[PDF 提取](https://github.com/langgenius/graphon/blob/4fa22141774dff80e90e4dfa98ea3e6fbf0421bb/src/graphon/nodes/document_extractor/node.py#L508-L542)、[DOCX 提取](https://github.com/langgenius/graphon/blob/4fa22141774dff80e90e4dfa98ea3e6fbf0421bb/src/graphon/nodes/document_extractor/node.py#L648-L666)

### 对 ManimAgent 的启发

可以借鉴其上传、存储、解析节点的职责划分。但此节点的公开输出主要是 `text`，PDF 这条实现还会将各页文字拼接成字符串。ManimAgent 应在提取时保留页码、内容块、公式和图像资产，随后再派生用于提示词的文本；后补来源位置会更困难。

这里讨论的是 Document Extractor 路径，不代表 Dify 的所有文件都必须先转成文本。

## Open WebUI 将全文和检索上下文统一组装

### 上传与解析

上传接口接收 `UploadFile`，经 Storage 保存原件，创建 File 记录，并可将处理任务放到后台。文件数据有 `pending` 等处理状态。索引流程通过可配置 Loader 提取 Document 列表，保存提取后的全文，再按配置切块、生成向量和入库。[上传入口](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/routers/files.py#L277-L296)、[后台处理](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/routers/files.py#L404-L452)、[解析及全文保存](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/routers/retrieval.py#L1681-L1729)、[切块实现](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/routers/retrieval.py#L1432-L1460)

### 模型实际收到的内容

聊天中间件读取附件列表。检索模式会生成查询并取得相关来源；全文模式通过 `context == 'full'` 等配置选择完整内容。随后把来源内容包进带编号的 `<source>` 标签，经 RAG 模板写入消息。根据配置，可以附加到 system 消息或 user 消息。[附件上下文处理](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/utils/middleware.py#L1706-L1791)、[来源标记与消息组装](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/utils/middleware.py#L613-L680)

它还区分自动注入的 File Context 和模型调用的 Builtin Tools。关闭 File Context 不会自动把附件改为模型原生文件输入；启用相应工具后，模型可以按需列出、搜索和读取附件。另一个消息处理函数会将附件元数据整理为文件清单。[官方行为说明](https://docs.openwebui.com/features/chat-conversations/rag/)、[附件清单源码](https://github.com/open-webui/open-webui/blob/33c1978d5bad52a31df75ea55143025dfad18d16/backend/open_webui/utils/middleware.py#L1444-L1482)

### 对 ManimAgent 的启发

值得借鉴的是“解析结果可以复用，选取内容在回答时发生”。同一份 MaterialIR 可以支持全文、页码范围和检索选取，不必针对每个问题重新解析原件。

ManimAgent 还需要记录本次注入了哪些块、来自哪一页，以及哪些依赖条件一起提供。检索的相关度和材料的完整性应分别判断。

## LibreChat 按用途和实际模型能力分流

### 当前入口和路由

当前文档中的统一上传入口会根据文件类型与端点能力选择原生提供商输入、文本提取或仅供工具使用的存储路径。旧版的手动用途选择仍可通过配置启用。File Search、Code Interpreter、聊天临时文本附件和 Agent 的持久 File Context 有不同消费方式。[统一上传说明](https://www.librechat.ai/docs/features/upload_as_text)、[Agent 文件能力](https://www.librechat.ai/docs/features/agents)

源码把模型交付方式表示为 `provider`、`text` 和 `none`。其中 `none` 表示不直接放入模型输入，文件仍可能由搜索或代码工具消费。路由读取端点配置、MIME、协议选择和工具消费能力，并可在实际回合确定模型之后重新计算。[交付路径规则](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/packages/data-provider/src/resolve-llm-delivery-path.ts#L150-L249)、[回合路由](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/packages/api/src/agents/files/delivery.ts#L34-L80)

### 文本路径

上传服务按配置调用 OCR、内置文档解析器、语音转写或文本提取，保存原件与提取文本。文本记录标记为 `llmDeliveryPath: 'text'`。特定解析失败有针对性的回退；原件保存或数据库失败不应误触发第二次解析。[文本保存](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/api/server/services/Files/process.js#L949-L1060)、[解析选择和回退](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/api/server/services/Files/process.js#L1080-L1190)

`extractFileContext()` 把文件名和已提取文本拼成 Markdown 附件上下文，并按 `fileTokenLimit` 对单文件文本限额处理；另一层还检查本回合附件数量、字节总量和提取文本字符总量。Agent Builder 的持久 File Context 会进入 Agent instructions，聊天临时上传则用于当前会话。[上下文组装](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/packages/api/src/files/context.ts#L41-L97)、[聚合限额](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/packages/api/src/agents/attachments.ts#L322-L359)、[File Context 说明](https://www.librechat.ai/docs/features/agents)

### 原生文档路径

`formatDocumentBlock()` 根据提供商和协议构造不同内容块：该版本中有 Anthropic 的 `document`、Google 的 `media`、Responses 风格的 `input_file`、部分 OpenAI 兼容端点的 `file`。它还包含文件类型过滤和大小校验。这是 LibreChat 适配层的实现，不能据此推定任意兼容接口都支持同样的文件格式。[文档编码器](https://github.com/LibreChat-AI/LibreChat/blob/8d3da9bbb220a0a96be9330a2e1cbd385fb786d5/packages/api/src/files/encode/document.ts#L125-L235)

### 对 ManimAgent 的启发

应分别表达“文件保存在哪里”“由哪种解析路径处理”“本次模型收到什么”。文件存在，不代表当前模型或工具能读取。

可借鉴其路由和多层限额，但学习材料应作为有来源的输入数据提供，系统规则由应用维护。全文超预算时，应明确报告未覆盖范围或请求缩小页码；单文件截断再拼接不能保证数学题设完整。

## OpenHands 将普通附件放入会话工作区

上传函数根据 conversation 找到本地或云端运行环境，创建 RemoteWorkspace，计算上传路径，并通过 `workspace.fileUpload()` 把文件送入工作区。每个文件分别返回上传成功或跳过原因。[工作区上传源码](https://github.com/OpenHands/OpenHands/blob/72886b6ea5bff39e795cc3b79bc2f444cb9669d6/src/api/conversation-file-upload.api.ts#L70-L175)

发送消息时，普通附件先上传，再将成功文件的名称追加到用户文本中；这条入口没有将普通附件全文展开到消息。作为视觉输入的图片会转成 base64，并加入独立图片内容块；图片也可以选择作为工作区文件上传。[消息与附件组装](https://github.com/OpenHands/OpenHands/blob/72886b6ea5bff39e795cc3b79bc2f444cb9669d6/src/utils/send-message-with-attachments.ts#L38-L85)

这意味着普通文件的进一步使用依赖 Agent 的运行环境和工具：文件名用于提示文件存在，读取或执行工具负责取得内容。仅向 LLM 发送一个文件名或宿主机路径不会让它自动取得文件字节。

ManimAgent 可以借鉴其原件可访问性和逐文件上传结果。学习材料的解析适合单独完成，随后让动画模块消费确定的 Answer；近期不需要为文档上传引入通用 shell Agent。

## Deep Agents 提供后端文件接口和按需读取

框架的 BackendProtocol 定义 `upload_files([(path, bytes), ...])`、分页 `read()`、`grep()` 等操作。这里的上传是后端文件接口，应用仍需实现面向用户的 HTTP 上传、授权与资产登记。[后端协议](https://github.com/langchain-ai/deepagents/blob/98773a73843421f85a2113e6c3b0262f53d9bcd6/libs/deepagents/deepagents/backends/protocol.py#L454-L507)、[上传协议](https://github.com/langchain-ai/deepagents/blob/98773a73843421f85a2113e6c3b0262f53d9bcd6/libs/deepagents/deepagents/backends/protocol.py#L754-L767)

FilesystemMiddleware 暴露 `ls`、`read_file`、`glob`、`grep` 等工具，并能将超长工具结果移到文件系统。`read_file` 对文本支持范围读取；对二进制或媒体可以返回内容块，模型是否能消费这些块还取决于后续适配与能力处理。这些接口本身不承诺统一的文档结构或数学公式提取质量。[中间件职责](https://github.com/langchain-ai/deepagents/blob/98773a73843421f85a2113e6c3b0262f53d9bcd6/libs/deepagents/deepagents/middleware/filesystem.py#L1820-L1841)、[读取结果处理](https://github.com/langchain-ai/deepagents/blob/98773a73843421f85a2113e6c3b0262f53d9bcd6/libs/deepagents/deepagents/middleware/filesystem.py#L2236-L2269)

仓库中的 Talon 通道适配还展示了附件暂存方式：检查来源目录和文件大小，将文档复制到沙箱内的随机路径，并把消息元数据中的宿主路径替换成沙箱路径。[附件暂存](https://github.com/langchain-ai/deepagents/blob/98773a73843421f85a2113e6c3b0262f53d9bcd6/libs/talon/deepagents_talon/attachments.py#L31-L115)

ManimAgent 已使用 LangGraph，可以借鉴其后端协议和分页读取思路，后续提供面向 MaterialIR 的 `read_material_blocks`、`search_material`、`get_source_image`。这些是本项目的建议接口，不是 Deep Agents 已有的同名工具。

## ManimAgent 的落地方案

### 当前实现与既有设计

当前运行代码只有纯文本生成请求，`GenerationRequest` 将文本限制为 1 至 3000 字符；API 没有材料上传端点。`ChatProvider.complete()` 把业务 payload 序列化成 user 文本，传入图片时追加 base64 `image_url` 内容块。因此现有视觉检查能力可供后续页面图像核对参考，但文档文件输入和多协议适配仍需单独实现。[请求校验](../../src/manimagent/schemas.py)、[API](../../src/manimagent/api.py)、[模型调用](../../src/manimagent/providers.py)

现有[材料输入与解析设计](../02-材料输入与解析.md)已经规定原件、MaterialIR、来源位置、解析路由和待核对状态；[LLM 接入设计](../01-LLM接入与调用.md)已经规定 ProviderFileRef 与能力记录。可以沿用这些边界，将本次调研的做法用于具体实现。

### 建议处理链路

```text
用户上传
  → FileStore 保存原件和元数据，返回 file_id
  → Parse Job 按模式、格式和能力选择解析路径
      → 应用解析器：文本、PDF、DOCX，必要时 OCR 或视觉识别
      → 提供商原生文件输入：由适配器准备引用或编码字节
  → 校验内容、来源和覆盖范围，保存 MaterialIR
  → ContextAssembler 根据问题和预算选取完整内容或内容块
  → answer_writer 与 answer_checker
  → 保存带 source_refs 的 Answer
  → 按需生成动画
```

这里的 `ContextAssembler` 是建议新增的内部组件，负责把已有 MaterialIR 变成一次模型调用所需的上下文。它属于回答模块；解析路由仍由材料模块决定，提供商请求格式仍由 LLM 适配层决定。

### 三个独立的决定

| 决定 | 归属 | 示例 |
|---|---|---|
| 原件存储方式 | FileStore | 本地资产目录，后续可替换为对象存储 |
| 原件解析方式 | 材料模块与 LLM 适配层协作 | `local` 或 `native`，最终保存 MaterialIR |
| 回答时选取什么内容 | 回答模块 | 全部内容、指定页码、检索块、按需读取 |

例如，“本地解析 PDF”既可以随后全文注入，也可以随后检索；“原生模型解析 PDF”也可以先保存结构化结果，再由其他提供商生成回答。不要用同一个枚举混合这些决定。

### 近期实现顺序

| 阶段 | 建议交付 | 借鉴依据 |
|---|---|---|
| 1 原件与文本链路 | 独立 FileStore、文件元数据、上传与读取；纯文本 MaterialIR 和独立 Answer | Dify 的资源划分，现有项目契约 |
| 2 文档解析与全文组装 | 文本型 PDF、DOCX 解析器，按页或段落保留来源，短材料完整注入 | Dify 的解析分派，LibreChat 的文本上下文组装 |
| 3 图示与公式核对 | 保存页图和裁剪图，对需要识别的区域调用 OCR 或视觉模型，记录未解决歧义 | 现有 MaterialIR 的 formula、image 和 review_state |
| 4 长材料选取 | 标题和页码导航、内容块搜索，必要时增加向量检索；补齐定义和题设依赖 | Open WebUI 的上下文组装，Deep Agents 的按需读取 |
| 5 原生文件适配 | 逐提供商验证格式、大小、读取及结构化提取，维护远端引用与清理 | LibreChat 的编码和路由，现有 ProviderFileRef 设计 |

这一顺序聚焦材料输入能力；与主[开发计划](../开发计划.md)中的整体模块拆分协调执行。先支持短材料和明确页码范围，可以更早验证题设、公式与来源是否可靠。

### 全文上下文应保留什么

下面是应用内部 payload 的示意，不是某个提供商的原生请求格式：

```json
{
  "instruction": "解释第 3 页例题，保留矩阵、向量和条件。",
  "materials": [{
    "material_id": "material_demo",
    "blocks": [{
      "block_id": "block_demo",
      "kind": "formula",
      "content": "A v = \\lambda v",
      "source": {
        "input_index": 0,
        "file_id": "file_demo",
        "locator": {"page": 3},
        "verification": "verified"
      },
      "asset_ids": [],
      "review_state": "unverified"
    }]
  }]
}
```

`source.verification` 只表示位置可靠性；例子中的 `review_state: unverified` 表示公式内容尚未核对。来源编号由服务端维护，回答引用限制为本次材料中真实存在的 `{material_id, block_id}`。

有关键图示时，除文本块外还需提供对应的图像内容块或受控读取工具。图片描述不能自动代替原图中未识别的标注、角度和几何关系。

### 预算和复用

上下文预算需要同时容纳系统规则、用户要求、材料、历史或工具结果，以及输出和协议开销的预留。文件字节上限、解析页数上限、材料 token 上限和视觉输入预算分别设置；不能用单文件大小替代模型上下文检查。

ContextAssembler 保存本次所选 block_id、来源范围、选取策略和预算估计。预算不足时，提示缩小页码、分批处理，或切换到有覆盖记录的检索流程。需要回答整章或核对整题时，不能把局部召回包装成全文已读。

解析缓存可按文件摘要、解析器与配置版本、选择范围、实际解析路径等建立复用键；原生解析还需要包含相关模型与适配器版本。回答缓存另包含问题和材料版本。复用始终以当前请求有权访问资产为前提。

### 文件生命周期和故障

| 情况 | 建议行为 |
|---|---|
| 原件上传成功，解析失败 | 保留原件和失败记录，允许独立重试解析 |
| PDF 有文字，但题图或公式未可靠提取 | 记录未覆盖或待核对块，按配置补充识别；关键歧义进入 needs_review |
| 提供商上传成功，生成端点不能读取 | 根据明确的能力错误处理回退，不能把上传成功当成读取成功 |
| 鉴权、配额、超时或限流 | 分类报告或按预算重试，沿用现有设计的回退规则 |
| 工具模式文件不存在或沙箱失效 | 恢复文件或返回需要重新附加的问题，再发起依赖它的回答 |
| 文件名含路径，或多个同名文件 | 服务端生成存储键，显示名与存储路径分离，以 ID 关联来源 |
| 原件、派生图、解析结果和远端临时文件需要清理 | 分别记录引用与生命周期，避免删除原件后留下不可追溯的解析或检索结果 |

文档内容作为待处理数据进入模型，附件中的文字不能改写系统规则。解析器只访问已登记资产；后续工具读取也应限制到当前请求有权访问的材料，避免接受任意宿主机路径。

### 验收样例

1. 文本型 PDF 中的负号、上下标、矩阵和跨页定义可以与原文逐项对照。
2. 扫描页和混合型 PDF 的无文字页面不会被当成已完成解析。
3. DOCX 段落和表格按顺序保留；公式对象或图片无法处理时有明确结果。
4. 两个同名文件的页码、块编号和回答引用不会互相混淆。
5. 题目引用前页定义时，全文范围或检索流程会一并提供该定义。
6. 超预算、解析失败、远端读取失败都能显示实际未完成范围。
7. 同一原件换问题时复用解析结果，换解析器时生成新 MaterialIR。
8. 没有模型或 Docker 时仍能上传和读取原件元数据。

## 源码快照

以下链接固定实现版本，便于后续复查。官方产品文档随项目更新；源码快照与文档分别按各自范围理解。

| 项目 | 本次源码提交 | 主要用途 |
|---|---|---|
| Dify | `70975c1873aeab9e80ffcf5b2fec3796eda77059` | 原件存储、文件记录、Graphon 依赖 |
| Graphon | `4fa22141774dff80e90e4dfa98ea3e6fbf0421bb` | Dify 固定依赖的文档提取节点 |
| Open WebUI | `33c1978d5bad52a31df75ea55143025dfad18d16` | 上传、全文保存、索引与上下文组装 |
| LibreChat | `8d3da9bbb220a0a96be9330a2e1cbd385fb786d5` | 文本提取、交付路由、限额、提供商文档编码 |
| OpenHands | `72886b6ea5bff39e795cc3b79bc2f444cb9669d6` | 工作区上传和用户消息组装 |
| Deep Agents | `98773a73843421f85a2113e6c3b0262f53d9bcd6` | 文件后端协议、读取工具和附件暂存 |

[返回文档总览](../index.md) · [材料输入与解析](../02-材料输入与解析.md)
