# BTC 核心策略

BTC、QQQ、VOO 的研究、嗅探、正式状态、报告判断和交付恢复作为同一个版本维护。模型负责研究与综合判断；程序负责材料、校验、状态推进、去重和恢复。

本仓库为私有源码仓库，默认分支 `main`。账户事实、市场数据库、策略状态、研究记录、报告正文、游标、投递记录和凭据留在执行节点。

- `skill/btc-core-strategy/`：完整 Skill、模型方法、原有 38 个策略脚本、节点配置适配及 34 个策略测试模块。
- `project-methods/`：项目职责与价值因子指引，随代码发布，供模型阅读。
- `tasks/`：正式模型任务入口模板。
- `scripts/release.py`：独立发布目录、每轮版本固定、上下文接续、运行间隙切换与回退。
- `test_support/reader/`：固定来源的 reader 测试依赖；生产 reader 由 `market-roles` 维护和安装。
- `export-manifest.json`：本仓库拥有的导出清单，供 `market-roles` 组合 reader 与核心内容。

## 验证

需要 Python 3.9 或更高版本，核心与离线测试使用标准库，无需模型或账户权限：

```sh
python3 scripts/run_tests.py
```

测试只使用临时账户文件、合成历史报告和临时数据库。覆盖公开材料、市场与个人适配的分离、正式状态、报告生成、UNKNOWN 投递及回读、研究接续、附件缺口、重复运行和发布恢复。测试中的发送与回读是模拟传输，不代表真实送达。

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

按照 `enter` 返回的固定路径读取 Skill，并用该版本的脚本执行本轮。研究请求和投递任务产生时绑定上下文；恢复使用原上下文，不能因升级重新研究或重发。详细运行说明见 [发布与恢复](skill/btc-core-strategy/references/releases.md)。

代码回退只切换 `current`，不覆盖任何运行数据。本次拆分沿用现有 v4/revision 5 数据格式；后续格式变化必须提供迁移与兼容方案。原 revision 2 历史报告及已审阅语义留在节点的 `legacy_revision2_dir`；仓库只保存合成回放材料。

完整闭环仍由本机 Codex 定时任务实际调用模型、采集和发送。GitHub Actions 只运行离线检查。真实发送、实际助手回读及后续自然运行另行确认，不能由 CI 结果替代。
