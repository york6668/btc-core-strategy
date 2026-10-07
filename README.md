# BTC 核心策略

BTC、QQQ、VOO 的研究、嗅探、正式状态、报告判断和交付恢复作为同一个版本维护。模型负责研究与综合判断；程序负责材料、校验、状态推进、去重和恢复。

本仓库为私有源码仓库，默认分支 `main`。账户事实、市场数据库、策略状态、研究记录、报告正文、游标、投递记录和凭据留在执行节点。

- `skill/btc-core-strategy/`：完整 Skill、模型方法、策略脚本、节点配置适配及 策略测试模块。
- `project-methods/`：项目职责与价值因子指引，随代码发布，供模型阅读。
- `tasks/`：正式模型任务入口模板。
- `scripts/release.py`：独立发布目录、每轮版本固定、上下文接续、运行间隙切换与回退。
- `test_support/reader.lock.json`：固定来源与提交的 reader 依赖声明，`scripts/prepare_reader.py` 获取后导出到忽略的 `.local/reader/`；生产 reader 由 `market-roles` 维护和安装。
- `export-manifest.json`：本仓库拥有的导出清单，供 `market-roles` 组合 reader 与核心内容。

## 验证

需要 Python 3.9 或更高版本，核心与离线测试使用标准库，无需模型或账户权限：

```sh
python3 scripts/prepare_reader.py
python3 scripts/run_tests.py
```

遵循共享 `codex-framework` 的源码与数据边界：合成、脱敏及测试用途不构成数据入库例外。测试准备在临时目录构造账户文件、历史报告和数据库，退出后清理，不读取旧仓库样本。

本机已配置 GitHub SSH 时，使用 `python3 scripts/prepare_reader.py --ssh`，无需修改全局 Git 配置。

首次准备依赖需要对 `market-roles` 私有仓库的读取权限；只有准备阶段访问 Git，离线测试阶段禁止网络。共享采集源码已由该仓库直接维护，reader 导出不递归获取核心仓库。已有独立 reader 可继续通过 `--reader` 传入。覆盖公开材料、市场与个人适配的分离、正式状态、报告生成、UNKNOWN 投递及回读、研究接续、附件缺口、重复运行和发布恢复。测试中的发送与回读是模拟传输，不代表真实送达。

与 `market-roles` 当前 reader 组合验证：

```sh
python3 scripts/run_tests.py --reader /path/to/exported-analysis/telegram_reader
```

## 节点运行

将 `config/node.example.json` 填为本机配置，保存在源码仓库外。现有数据路径保持不变。先安装一个已验证的提交：

```sh
python3 scripts/release.py --node-config /path/to/node.json install --source /path/to/btc-core-strategy --ref COMMIT
python3 scripts/release.py --node-config /path/to/node.json enter --run-id ACTUAL_RUN_ID
```

若节点的运行版 reader 不包含测试源码，安装时增加 `--reader /path/to/exported-reader`；可以传入前述 `.local/reader`。测试依赖与运行服务分开，安装不修改 reader 服务。

按照 `enter` 返回的固定路径读取 Skill，并用该版本的脚本执行本轮。研究请求和投递任务产生时绑定上下文；恢复使用原上下文，不能因升级重新研究或重发。详细运行说明见 [发布与恢复](skill/btc-core-strategy/references/releases.md)。

代码回退只切换 `current`，不覆盖任何运行数据。外层 v4 格式不变，代码读取内容 revision 1–7，新入口准备 revision 7；正式状态仅经真实评估与采纳迁移，旧动作不自动补成风险评估。代码回退必须支持当前内容版本。原 revision 2 历史报告及已审阅语义留在节点的 `legacy_revision2_dir`；仓库只保存测试程序与样本构造逻辑。

完整闭环仍由本机 Codex 定时任务实际调用模型、采集和发送。GitHub Actions 只运行离线检查。真实发送、实际助手回读及后续自然运行另行确认，不能由 CI 结果替代。

revision 7 的市场结论、个人资产风险评估、可选风险应对及独立验证见[完整闭环](skill/btc-core-strategy/references/closed-loop.md)。全部回放材料由 scripts/build_synthetic_fixtures.py 和 scripts/build_reader_fixtures.py 在测试临时目录构造，源码导出不包含测试数据文件。

## Docker 持续分析

[数据服务与迁移](service/README.md)包含 Compose、MySQL 8.4、内容寻址证据、CLI/MCP、宿主适配、恢复及生命周期入口。[持续分析契约](skill/btc-core-strategy/references/continuous-analysis.md)规定业务对象接续、个人事实视图与自主验收。

GitHub Actions 执行本仓库可独立重建的真实 Docker 数据层与恢复测试，不依赖跨仓 Secret。完整核心、reader 和 collector 组合验证由已取得源码的 `market-roles` 执行 `roles/validate_repository.py --with-data-service`，云端准备和新任务均须实际运行。两者的覆盖分别报告。

CI 的 reader 输入保存在 `test_support/ci_reader/`，仅供测试，不加入 `export-manifest.json`。`scripts/ci_reader.py` 先核对 `test_support/reader.lock.json` 固定的上游提交、完整 Git tree 和 `SNAPSHOT.json` 中逐文件 Git/SHA-256 摘要，再构建真实 reader 镜像并向原数据服务检查传入目录与镜像；缺项、改动或额外运行数据均明确失败。CI 不读取跨仓凭据，也不递归获取核心依赖。

该目录是固定版本的生成快照，不手工维护第二套 reader。重建时从锁定的 `market-roles` 提交取得 `roles/source-map.json` 的 analysis 中所有 `telegram_reader/` 映射，再取同提交的 `requirements.txt`、`roles/containers/Dockerfile` 和 `roles/containers/entrypoint.py`；按 `SNAPSHOT.json` 保存的来源路径、模式与摘要逐一复核，生成仅排除 `.git` 的 `.dockerignore`。来源提交、来源 tree、映射 blob 和全部文件摘要随快照记录；版本变化时同步更新来源记录和锁中的快照 tree。源码可含测试程序与样本构造逻辑，运行状态、配置实例、凭据及生成的测试数据不得进入快照。
