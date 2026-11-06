# 记忆与缓存修复：分发包校验

日期：2026-11-06。校验对象为 `E:/manimagent` 工作树，基础提交 `8f5dd8b0d935455b8615e018b35f4d876243f367` 加本轮未提交修改；源码逐文件摘要见 [package-source-manifest.json](package-source-manifest.json)。Python 3.12.14，uv 0.13.6。

## 实际命令与结果

工作目录均为 `E:/manimagent`。`uv` 不在当前 shell 的 PATH，因此使用现有可执行文件的绝对路径。

```powershell
& 'C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe' lock --check
& 'C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe' build --out-dir E:/manimagent/dist/memory-cache-20261106
.venv/Scripts/python.exe scripts/check_distribution.py E:/manimagent/dist/memory-cache-20261106 --uv C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe
.venv/Scripts/python.exe -m pytest -q tests/unit/distribution/test_distribution.py --junitxml=testresult/memory-cache-20261106/distribution.xml
```

前三项由本轮证据脚本 [validate_packages.py](validate_packages.py) 执行并记录，均退出 0。`lock --check` 成功解析 86 个依赖；构建成功生成 sdist，再从 sdist 生成 wheel；分发测试 **14 passed**。命令、退出码与耗时见 [package-commands.json](package-commands.json)，测试结果见 [distribution.xml](distribution.xml)。

构建与安装原始日志：[依赖锁](package-lock.log)、[构建](package-build.log)、[安装与启动](package-install-smoke.log)。

## 包内容与独立安装

| 产物 | 大小 | 校验结果 |
|---|---:|---|
| `dist/memory-cache-20261106/manimagent-0.3.1-py3-none-any.whl` | 851979 字节 | 80 个运行文件与构建时源码字节一致；独立安装通过 |
| `dist/memory-cache-20261106/manimagent-0.3.1.tar.gz` | 1040885 字节 | 80 个运行文件与构建时源码字节一致；独立安装通过 |

两份包均包含 `llm/context.py`、`llm/usage.py`、`memory/projection.py` 及网页资源。最后一次检查确认运行源码在安装测试后未变，wheel 中包括最新 `memory-manage-link`、`memory.open` 绑定和方法；结果见 [package-final-ui-check.json](package-final-ui-check.json)。`evals/` 未进入运行 wheel，sdist 按现有配置保留评测源码。

`scripts/check_distribution.py` 同时检查归档路径、必需资源及禁止的私有／历史内容。两份包分别安装到临时虚拟环境，验证导入来自该环境、CLI `--help`、无核心密钥配置下的服务启动、`/api/v2/health`、`/openapi.json` 版本、首页和所有必需静态资源。临时服务在检查后关闭。

产物 SHA-256 见 [package-archives.json](package-archives.json)：

```text
wheel dcef683bc61edf0b3529128e521f9e9c6f9877223affeaba5666707291ca0fb1
sdist 530b3e6336f9e8dd90989a299046670b446d9536996d727fa297f4b0d9438154
```

本轮只验证内容、安装与基本运行，没有调用真实模型或 TTS API，也没有在安装环境重新渲染视频。后续文档和评测说明修订不改变本次已校验的运行源码；本报告不将这些检查解释为缓存收益或完整生成质量测量。
