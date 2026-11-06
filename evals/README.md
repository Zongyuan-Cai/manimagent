# 真实链路评测入口

`evals/` 存放显式启动的真实服务验收程序；`tests/` 存放默认回归与受控故障测试。下面各入口覆盖不同边界，不能把它们的通过数相加当作内容正确率。普通 `pytest` 不会自动执行这些真实模型评测。

## 入口选择

| 模块 | 覆盖范围 | 服务与凭据 | 输入与结果 |
|---|---|---|---|
| `evals.text` | 已启动服务的上传、解析、文字回答与下载 | 服务端已配置官方 DeepSeek `deepseek-flash` | 读取 `testmaterial/`；写入 `testresult/<label>/` |
| `evals.text_review` | 隔离 API 应用中的题型、文件格式、来源引用与拒绝处理 | 官方 DeepSeek；`MANIMAGENT_REVIEW_DEEPSEEK_KEY` 或可选 `--secret-file` | 用例在 `cases/text.py`；逐例结果与 `summary.json` |
| `evals.text_supplemental` | 模型配置、文件边界、正文复用、修订与持久化 | 官方 DeepSeek；必填 `--secret-file` | 读取部分 `testmaterial/`；逐例结果与汇总 |
| `evals.cli` | CLI 子进程退出码、持久化任务、正文及错误状态 | 官方 DeepSeek；必填 `--secret-file` | 自建小型 TXT/PDF；逐例结果与汇总 |
| `evals.video` | 单题静音动画，以及指定既有正文或失败任务的续跑 | 官方 DeepSeek；`MANIMAGENT_ANIMATION_TEST_KEY` 或可选 `--secret-file` | 默认向量投影，可传题目；正文、计划、成片、关键帧和 `result.json` |
| `evals.video_benchmark` | 固定源码、多题重复的静音动画验收 | 同 `evals.video`；复用该单题入口 | `cases/video.py` 的十题，默认各三次；完整尝试和源码摘要 |
| `evals.narrated` | 已配置 LLM → MiMo TTS → 原生渲染 → API 媒体下载；可选用户记忆 | 当前本地配置与网页保存的角色配置，MiMo 凭据 | 正文、计划、MP4、WAV、关键帧、报告及源码摘要 |

`--secret-file` 是由当前 Windows 用户 DPAPI 保护的密钥文件，不是明文 `.env` 或配置 JSON；`cli` 和 `text_supplemental` 当前保留这一 Windows 验收入口。`text_review`、`video` 和 `video_benchmark` 可通过对应环境变量运行。`narrated` 通过当前配置解析凭据，并为评测建立隔离运行目录；`--memory-note` 只在该目录中生成、确认并使用测试记忆。

## 运行

从仓库根目录执行，先按[运行与配置](../docs/current/运行与配置.md)安装依赖。文字评测需要开发依赖；视频评测另外需要 `animation` 依赖和渲染环境。先用 `python -m evals.<模块> --help` 查看参数，不会调用模型。

以下命令都会调用真实模型并产生用量。每轮选择新的输出目录；付费评测不属于普通 CI。运行期间固定源码与配置，保留失败和中断记录。

```powershell
# 当前已配置服务；首次运行前补齐上传样本，包含不提交的超限文件。
python scripts/create_test_materials.py
python -m evals.text --base-url http://127.0.0.1:8000 --label text-new-run

# 已在环境中设置 MANIMAGENT_REVIEW_DEEPSEEK_KEY；先跑少量输入用例。
python -m evals.text_review --output testresult/text-review-new-run --workers 1 --limit 3

# Windows DPAPI 凭据文件，路径按实际位置填写。
python -m evals.text_supplemental --output testresult/text-extra-new-run --secret-file C:\private\deepseek.dpapi
python -m evals.cli --output testresult/cli-new-run --secret-file C:\private\deepseek.dpapi

# 已在环境中设置 MANIMAGENT_ANIMATION_TEST_KEY。
python -m evals.video --output testresult/video-new-run --thinking disabled
python -m evals.video_benchmark --output testresult/video-benchmark-new-run --topics projection,quadratic --repeats 1 --workers 1 --thinking disabled

# 使用当前配置中的模型角色与 MiMo；可重复 --file 添加材料。
python -m evals.narrated --output testresult/narrated-new-run --file testmaterial/tts-二次方程.txt --question "依据材料完整讲解二次方程的因式分解、求根公式与代回核验。"
python -m evals.narrated --output testresult/memory-new-run --memory-note "我喜欢分步骤讲解，每一步都先说明依据。"
```

静音 `video` 的 `--answer-id` / `--resume-job-id` 需要同一输出目录下已持久化的资源；显式续跑应单独记录成本，不能覆盖为首次成功。`video_benchmark` 与 `narrated` 会拒绝非空输出目录。

## 如何解释证据

- `passed` 是对应程序的工作流、契约或媒体断言结果。缺条件、扫描件等按预期被拒绝也可算该用例通过。
- 内容正确、讲解覆盖、可读性、跨场景一致性需要另外审核；媒体能完整解码不等于教学内容正确。
- `narrated` 校验节拍使用真实音频时长；节拍内部 cue 仍是估时，不代表词级对齐。
- 保留真实尝试分母、耗时、调用量、模型配置与源码摘要；源码变化后的新结果不能回填旧一轮成功率。
- `runtime/` 包含私有输入和运行记录，留在本机；对外整理报告时只提交核对过的汇总与必要证据，不提交凭据。

当前报告入口见[验收证据](../testresult/README.md)，研究评测要求见[时长与生产验收](../docs/design/动画时长与生产验收.md)。
