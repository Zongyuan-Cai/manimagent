# 上传与 API 配置改进：安装包验证

日期：2026-11-06。目录：`E:/manimagent`。本次只构建与检查安装包，没有读取或修改用户 `.env`、本机模型配置或用户运行数据，没有调用真实 LLM、视觉或 TTS 服务。

## 执行命令

使用已有 uv 可执行文件，未安装额外全局工具：

```powershell
& 'C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe' lock --check
& 'C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe' build --out-dir dist/onboarding-20261106
.venv/Scripts/python.exe scripts/check_distribution.py dist/onboarding-20261106 --uv C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe
```

三个命令均返回 0。依赖锁检查解析 86 个包；构建先产生 sdist，再从该 sdist 构建 wheel。完成最后两处界面状态修复、统一格式及视觉配置注释后重建；真实 MiMo smoke 完成并更新相关文档后，再次执行构建，得到下表最终产物。最后一次仅更新文档，wheel 的字节数与哈希保持不变。

## 实际结果

原有检查脚本对首轮 wheel、sdist 分别建立新的临时虚拟环境，安装 33 个生产依赖，验证实际导入来源、CLI、启动后的 HTTP API、UI 和全部必需静态资源。两包均输出 `PASS`，临时服务按脚本正常关闭。最后重建没有改变依赖或静态资源清单，因此沿用该安装验证；没有把它记录成最终哈希包又执行了一次全新安装。

- 新增生产依赖已进入 wheel 元数据并在临时环境安装：`openpyxl 3.1.5`、`python-pptx 1.0.2`、`Pillow 12.3.0`、`pypdfium2 5.14.0`。
- 两包均包含并实际提供 `web/onboarding.js`、`web/tts-settings.js`、`web/memory.js`。
- 安装包路径隔离检查通过，不含用户配置、凭据、运行数据或旧 MVP。
- 最后重建后逐文件比较 wheel 中应用源码和当前 `src/manimagent/`：字节差异为 0，遗漏源码文件为 0（排除 Python 缓存）；sdist 中 `docs/` 文件与当前文档字节差异也为 0。包验证不替代其他报告中的多格式解析与真实渲染测试。

| 产物 | 大小 | 成员数 | SHA-256 |
|---|---:|---:|---|
| `dist/onboarding-20261106/manimagent-0.3.1-py3-none-any.whl` | 846684 字节 | 81 | `642d62dfef5411cf9fd1e94f48550caf1f7e4e9fb575a5587ebba28d738ae184` |
| `dist/onboarding-20261106/manimagent-0.3.1.tar.gz` | 1019918 字节 | 193 | `5938202325db0b3e798531b8c7a571df984c2883bdad3d2b3b0b546189caa52f` |

二进制产物保存在本机忽略目录，未提交版本库。本报告之后如再修改源码、文档或构建配置，应重新构建对应产物，不能沿用上述哈希描述新包。
