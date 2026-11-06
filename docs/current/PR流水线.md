# PR 自动验证

工作流为 [ci.yml](../../.github/workflows/ci.yml)，名称 `PR validation`。这是提交与合并前的验证，不执行部署或发布。

## 触发与检查

对仓库发起或更新 PR 时自动运行，验证 GitHub 生成的 PR 合并结果。合入 `main` 后再次运行；也支持 Actions 页面手动执行与 GitHub merge queue。同一 PR 的新提交会取消尚未完成的旧验证。

| 检查 | 内容 |
|---|---|
| Current (ubuntu-latest) | Python 3.12，依赖锁一致性，Ruff、格式、JavaScript 语法、页面异步回归和全部当前版基础自动化测试 |
| Current (windows-latest) | Python 3.12，依赖锁一致性和全部当前版自动化测试，覆盖 Windows 凭据存储路径 |
| Native animation | 安装 animation 依赖和中文字体，真实渲染矩阵／几何／公式，检查精度与间距，合成 2 分钟以上视频并完整解码 |
| Installable package | 当前回归通过后构建 wheel、sdist；检查历史／私有目录未混入，逐字节核对全部运行文件与当前源码一致；分别在临时环境安装并实际启动 CLI、HTTP 服务和页面静态资源 |
| PR checks | 汇总以上检查；任一失败、取消或跳过均不通过，适合作为固定的合并门槛 |

当前 JUnit XML 存为 Actions artifacts；通过验证的当前版安装包也可下载，均保留 7 天。测试失败时查看对应 step 日志和 XML。所有任务设有超时，第三方 Action 固定到完整提交 SHA，工作流令牌仅有 `contents: read`。

CI 不需要任何 LLM 密钥，不读取本机私有配置，不运行 `evals/` 中的真实模型评测，不会产生真实模型调用费用。真实模型质量和完整人工视觉验收不属于本流水线的通过承诺。Native animation 使用本地可信模板，不依赖 Docker。

## 设置合并门槛

主维护者已明确授权直接推送 `main`。管理员可直接提交；不强制主维护者创建 PR 或另找审批者。其他贡献者仍可通过 PR 使用同一组检查。禁止强推与删除主分支；主分支 push 也运行全部 CI。

后续可在仓库 Settings → Branches → `main` 的分支保护（或 Rules → Rulesets）维护：

1. 按协作需要设置 Require a pull request before merging；保留主维护者／管理员的直接提交权限。
2. Require status checks to pass，选择 **PR checks**。
3. 如需确保检查涵盖最新主分支，开启 Require branches to be up to date before merging；使用 merge queue 时可由队列验证合并结果。

只提交 YAML 不会自动更改远端仓库规则；未启用规则时，红色检查不会自行阻止合并。对应行为见 [GitHub 状态检查说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)。

## 本地复现

```powershell
uv sync --locked --extra dev
uv run --no-sync ruff check src tests scripts evals
uv run --no-sync ruff format --check src tests scripts evals
uv run --no-sync python -m pytest -q
Get-ChildItem src/manimagent/web/*.js | ForEach-Object { node --check $_.FullName }
node --test tests/web/*.test.mjs
uv build --out-dir dist/current
uv run --no-sync python scripts/check_distribution.py dist/current
```

打包检查要求 `dist/current/` 中恰好包含当前版本的 wheel 与 sdist，避免混用旧包。脚本逐文件检查缺失、多余和内容不一致的运行源码／静态资源，新增模块不需要手工加入固定清单才能被检查。随后为每个包创建并关闭临时服务；绑定随机本机端口，不占用开发服务的 8000 端口。只安装生产依赖，页面与接口不依赖源码目录或开发依赖。

旧 MVP 已从当前维护树与 CI 移除；如需复现，使用文档导航中的固定历史版本。

原生渲染本地复现：先 `uv sync --locked --extra dev --extra animation`，然后在 PowerShell 中设置 `$env:RUN_NATIVE_TESTS="1"` 并运行 `uv run --no-sync python -m pytest -q tests/native`。基础测试环境显式跳过这些测试；单独的 Native animation CI 必须通过。

这里只描述检查职责和复现命令；对应提交是否通过，应查看该提交的实际 CI 结果。

[返回文档导航](../README.md)
