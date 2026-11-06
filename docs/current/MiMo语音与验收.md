# MiMo 语音与全链路验收

更新：2026-11-06。语音合成已接入现有文字与 Manim 动画链路；可选用户记忆的当前规则和快照边界见[用户记忆与个性化](用户记忆与个性化.md)。本页 11 月 5 日真实语音报告保留当时版本的证据，不代替后续记忆功能验收。

## 配置和运行

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

环境变量 `MANIMAGENT_TTS_ENABLED` 在启动时覆盖 YAML 的 enabled。默认关闭，便于无密钥环境继续使用无声视频；本次本机配置已启用。服务需要重启才能加载 `.env` 的变化。本次真实测试还发现 DeepSeek 默认思考模式可能在规划检查阶段耗尽输出或等待预算，因此本机将 `plan_checker` 单独配置为 `thinking=disabled`，保留其他角色及全部检查，并将 `video_max_output_tokens` 设为 16000；这是本地模型配置选择，不是关闭检查或全局强制修改其他供应商。语音配置与文字模型独立；网页“模型设置”仍配置文字/规划模型，不接受 TTS 模型代替文字模型。

```powershell
uv run manimagent serve
uv run manimagent lesson --question "给定 a=(3,2)、b=(2,0)，解释正交投影并验证残差正交" --output lesson.mp4
```

网页选择“完整解答 + 教学动画”或调用 `output_mode=both`，会按服务的 `tts.enabled` 自动生成有声视频。仅文字请求不会调用 TTS。`POST /api/v2/videos` 可对已保存答案重新生成；`video_profile_id` 默认为 `auto`，跟随配置，也可显式指定 `standard_silent` 或 `standard_narrated`。最终计划会如实写 `standard_narrated` 或 `standard_silent`。

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
