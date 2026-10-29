# PR 自动验证

工作流为 [ci.yml](../.github/workflows/ci.yml)，名称 `PR validation`。这是提交与合并前的验证，不执行部署或发布。

## 触发与检查

对仓库发起或更新 PR 时自动运行，验证 GitHub 生成的 PR 合并结果。合入 `main` 后再次运行；也支持 Actions 页面手动执行与 GitHub merge queue。同一 PR 的新提交会取消尚未完成的旧验证。

| 检查 | 内容 |
|---|---|
| Current (ubuntu-latest) | Python 3.12，依赖锁一致性，Ruff、格式、JavaScript 语法和全部当前版自动化测试 |
| Current (windows-latest) | Python 3.12，依赖锁一致性和全部当前版自动化测试，覆盖 Windows 凭据存储路径 |
| Legacy MVP | 在 `legacy_mvp/` 使用自有锁文件、依赖环境和回归测试，排除需要 Docker 的集成测试 |
| Installable package | 当前回归通过后构建 wheel、sdist；检查历史／私有目录未混入、页面资源齐全；分别在临时环境安装并实际启动 CLI、HTTP 服务和页面静态资源 |
| PR checks | 汇总以上检查；任一失败、取消或跳过均不通过，适合作为固定的合并门槛 |

当前和历史 JUnit XML 分别存为 Actions artifacts；通过验证的当前版安装包也可下载，均保留 7 天。测试失败时查看对应 step 日志和 XML。所有任务设有超时，第三方 Action 固定到完整提交 SHA，工作流令牌仅有 `contents: read`。

CI 不需要任何 LLM 密钥，不读取本机私有配置，不运行 `scripts/test_deepseek_e2e.py`，不会产生真实模型调用费用。真实模型质量、视觉结果和 Docker 渲染不属于本流水线的通过承诺。

## 设置合并门槛

2026-10-29 已通过 GitHub API 为 `main` 启用分支保护：必须通过 PR 修改，`PR checks` 必须由 GitHub Actions 提供且通过，提交需与主分支同步；规则对管理员生效，禁止强推与删除。独立审批数量为 0，不要求单人仓库另找审批者。

主分支已安装通用的 `PR checks` 汇总门禁。当前文字版的完整检查通过[临时 PR #3](https://github.com/Zongyuan-Cai/manimagent/pull/3)验证，测试 PR 按要求关闭、不合并；主分支在当前开发版正式合入前继续使用原 MVP 测试。

后续可在仓库 Settings → Branches → `main` 的分支保护（或 Rules → Rulesets）维护：

1. Require a pull request before merging。
2. Require status checks to pass，选择 **PR checks**。
3. 如需确保检查涵盖最新主分支，开启 Require branches to be up to date before merging；使用 merge queue 时可由队列验证合并结果。

只提交 YAML 不会自动更改远端仓库规则；未启用规则时，红色检查不会自行阻止合并。对应行为见 [GitHub 状态检查说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)。

## 本地复现

```powershell
uv sync --locked --extra dev
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python -m pytest -q
node --check src/manimagent/web/app.js
node --check src/manimagent/web/llm-settings.js
uv build --out-dir dist/current
uv run --no-sync python scripts/check_distribution.py dist/current
```

打包检查要求 `dist/current/` 中恰好包含当前版本的 wheel 与 sdist，避免混用旧包。脚本会为每个包创建并关闭临时服务；绑定随机本机端口，不占用开发服务的 8000 端口。只安装生产依赖，页面与接口不依赖源码目录或开发依赖。

历史测试需在 `legacy_mvp/` 中单独执行 `uv sync --locked --extra dev` 与 `uv run --no-sync python -m pytest -q -m "not docker"`。
