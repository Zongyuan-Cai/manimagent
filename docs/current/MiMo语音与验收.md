# MiMo 语音与全链路验收

更新：2026-11-06。语音合成已接入现有文字与 Manim 动画链路；可选用户记忆的当前规则和快照边界见[用户记忆与个性化](用户记忆与个性化.md)。本页 11 月 5 日真实语音报告保留当时版本的证据，不代替后续记忆功能验收。

## 在网页中配置

1. 打开顶部“设置”，切换到“语音配音”。核心 API 与 TTS 在同一窗口中分开管理；即使使用同一服务商，也需要在语音面板设置自己的密钥关联。
2. 输入 MiMo API Key，选择服务商支持的音色 ID（默认 `mimo_default`），填写朗读要求。高级选项默认使用 `https://api.xiaomimimo.com/v1` 与 `mimo-v2.5-tts`；可粘贴完整 `/chat/completions` 地址，界面会归一为根地址。
3. 用短文本点击“试听当前配置”，完成后点击音频播放器核对声音。试听会产生少量真实用量，但**不保存配置，也不自动启用默认配音**。
4. 勾选“默认给新动画配音”后点击“保存语音配置”。不勾选时仍可保存密钥；回到主页面后，“动画旁白”可选择“使用语音设置中的默认选择”“本次配音”或“本次不配音”。仅文字请求不调用 TTS。

当前接入的是 MiMo TTS 请求及 WAV 响应协议，不是任意语音 API 适配器。其他厂商的 `/audio/speech` 接口不能仅靠替换地址使用。换服务来源时需要新密钥或明确清除原关联。API Key 留空保留，保存后密码框清空；试听和保存期间锁定表单，避免异步结果覆盖正在修改的草稿。

网页配置保存在 `private_dir/configuration/tts.json`，密钥与核心模型一起由独立 `credentials.json` 管理；保存后用于新任务，不改写 `.env`／YAML，也不更改已接收任务的配置快照。重启仍能读取；同一私有目录的 CLI 也使用保存值。

| 接口 | 请求与结果 |
|---|---|
| GET `/api/v2/tts-settings` | 返回 revision、config、key_configured、key_source、state 和 issues；不返回密钥值 |
| PUT `/api/v2/tts-settings` | `{expected_revision, draft: {config, api_key?, clear_api_key?}}`；冲突返回 409 |
| POST `/api/v2/tts-settings/test` | `{draft, text?}`；返回短 WAV 的 Base64、时长与用量，不保存草稿、不永久保存试听文件 |

状态分别为默认关闭 `disabled`、配音配置完整 `ready`、尚缺配置 `needs_configuration`；配置完整只表示可以尝试调用，鉴权及实际音色仍需试听验证。当前试听文本最多 200 字符，最多返回 30 秒／4 MiB 音频。

## 初始文件配置与命令行

在运行工作目录的 `.env` 中配置（实际凭据只保存在本机，不写入示例、报告或 Git）：

```dotenv
MANIMAGENT_TTS_ENABLED=true
MIMO_API_KEY=your-key
```

可选 `config.yaml`：

```yaml
tts:
  enabled: true
  base_url: https://api.xiaomimimo.com/v1
  model: mimo-v2.5-tts
  api_key_env: MIMO_API_KEY
  voice: mimo_default
  timeout: 60
  max_calls: 64
  max_retries: 1
```

环境变量 `MANIMAGENT_TTS_ENABLED` 在启动时覆盖 YAML 的 enabled；网页保存值再覆盖初始文件配置。默认关闭，便于无密钥环境继续使用无声视频。服务需要重启才能加载 `.env` 变化，网页保存无需重启。历史真实测试曾将 `plan_checker` 单独配置为 `thinking=disabled`，保留全部检查，并将 `video_max_output_tokens` 设为 16000；这是当时的本地模型选择，不是全局强制修改其他供应商。TTS 模型不能代替核心回答模型。

```powershell
uv run manimagent serve
uv run manimagent lesson --question "给定 a=(3,2)、b=(2,0)，解释正交投影并验证残差正交" --output lesson.mp4
```

网页选择“完整解答 + 教学动画”后，可用本次旁白选项覆盖默认配置；仅文字请求不会调用 TTS。`POST /api/v2/generations` 的视频模式、以及 `POST /api/v2/videos` 的 `video_profile_id`，都可使用 `auto` 跟随配置，或显式指定 `standard_silent`／`standard_narrated`。有声选项缺少可用语音配置时会提示补全，不悄悄改成无声。最终计划如实记录实际模式。

## 实现与时间含义

1. 固定完整 Answer；派生可朗读的中文旁白、屏幕文字、公式和画面事件。提示词要求公式在旁白中写成自然中文读法。
2. 先按已有流程检查分镜、内容覆盖和阅读时间。
3. 逐节拍请求 MiMo，检查 `finish_reason=stop`、Base64 和完整 PCM WAV，按样本数计算时长。
4. 在每拍画面稳定后 1 秒处开始朗读。该拍时长取原有阅读窗口与“实测旁白 + 开场/结束缓冲”中的较大值，按 30 fps 向上取整。禁止截断旁白、变速压缩或把全片声音简单塞进旧时间线。单拍超过 30 秒、全片超过 360 秒则停止并保留正文。
5. 用新时间线渲染；将每段 PCM 放到相应的全片绝对时间，生成 `narration.wav`。FFmpeg 复制 H.264 视频流，编码 AAC 音频，发布 `teaching.mp4`。
6. 完整解码音视频，核对 720p/30 fps、单视频流/单音频流、AAC 起点、时长和音频样本数，最后绑定同一 Answer ID/hash 发布。

`time_source=audio_measured` 只表示旁白片段边界来自实测。`cue_time_source=estimated` 表示片段内部高亮位置仍按文本比例推算。MiMo 当前接口不返回词级时间戳，本实现没有强制对齐，因此不能声称词级精确同步。屏幕字幕仍是教学短句，不是逐字字幕；尚未生成 SRT。

`VideoResult.audio_asset_id` 指向可单独下载的 WAV：`GET /api/v2/files/{audio_asset_id}/content`。视频播放/下载接口保持不变。无声模式及历史结果仍可读取。

## 失败、凭据与预算

语音请求独立于文字模型调用计数，默认最多 64 次（含重试）；每次最多重试一次，仅用于网络、超时、限流或服务端错误。所有请求、渲染和合成继续共享视频与任务 deadline，不扩大原来的 600/900 秒硬限。TTS 用量保存在任务诊断中，不混入文字模型计数。

401/403、配额失败、不完整或损坏的音频、超时、渲染失败都不发布假成功；双输出任务保留已保存 Answer 并标记 `partial`。错误不回显服务端响应体、API Key 或请求头。渲染子进程继续使用环境变量白名单，不接收模型密钥；HTTPS 请求禁止自动重定向。不同采样格式的片段不会被悄悄拼接。

## 验收

确定性回归包含请求格式、鉴权、错误保密、截断音频、重试/deadline、PCM排程、TTS失败保留正文及成片绑定。原生测试包含真实 FFmpeg 音视频封装和解码，但其中的合成音调明确不属于 MiMo 验证。

2026-11-06 的独立语音配置接口已通过真实短句试听：一次调用返回 5.12 秒 WAV，试听前后保存 revision 不变。页面交互使用隔离模拟服务检查试听、保存及配置反馈；两类证据分开记录在[输入与配置验收](../../testresult/onboarding-20261106/报告.md)，不将短句试听等同于新一轮完整视频质量评测。

显式真实端到端脚本复用当前模型设置与凭据、隔离运行数据，保存所有尝试，不覆盖失败结果：

```powershell
uv run python -m evals.narrated --output testresult/tts-20261105/new-run
# 文件输入还可添加：--file testmaterial/某个样本.txt
```

脚本会产生真实模型用量，并检查正文、同版本计划、MP4/WAV、逐拍关键帧、Range 下载、音视频全解码和源代码指纹。具体执行结果见 [本轮验收报告](../../testresult/tts-20261105/报告.md)。单个样例通过不能替代多题、多次生成或学习效果实验。

后续记忆与有声链路联合测试可添加 `--memory-note "我希望先说明每一步的依据，再给出结论。"`，脚本经真实记忆 API 提取、确认，再开启新任务的自动提取并检查 Answer／视频快照一致性。使用新的输出目录保存每次尝试；实际结果见[记忆验收报告](../../testresult/memory-20261106/报告.md)。

## 官方依据

- [MiMo TTS API](https://mimo.mi.com/docs/en-US/api/audio/tts-post)：Chat Completions 入口、assistant 消息作为朗读文本、WAV 返回结构与鉴权。
- [MiMo 当前模型列表](https://mimo.mi.com/docs/quick-start/model)：本次使用 `mimo-v2.5-tts`，不沿用已过时的模型名。
