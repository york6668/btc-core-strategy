# 交易系统核心策略

交易系统覆盖既有资产及组合，BTC 是其中一个标的；历史仓库名和技术标识不限定整体职责。

BTC、QQQ、VOO 的研究、嗅探、正式状态、报告判断和交付恢复作为同一个版本维护。模型负责研究与综合判断；程序负责材料、校验、状态推进、去重和恢复。

本仓库为源码仓库，默认分支 `main`。账户事实、市场数据库、策略状态、研究记录、报告正文、游标、投递记录和凭据留在执行节点。

- `skill/btc-core-strategy/`：完整 Skill、模型方法、策略脚本及节点配置适配。
- `project-methods/`：项目职责与价值因子指引，随代码发布，供模型阅读。
- `tasks/`：正式模型任务入口模板。
- `scripts/release.py`：独立发布目录、每轮版本固定、上下文接续、运行间隙切换与回退。
- 生产 reader 由 `market-roles` 维护和安装，其来源及配置由所属节点持有。
- `export-manifest.json`：本仓库拥有的导出清单，供 `market-roles` 组合 reader 与核心内容。

## 验证

源码使用 Python 3.9 或更高版本。针对当前有效要求和受影响行为，在仓库外或忽略的 `.local/` 下构造临时验证；案例、断言、mock、夹具、样本构造程序及验证依赖快照不进入 Git 或源码导出。需要原输入精确复现时保留原节点的有界归档，目的结束后删除或归档；历史测试不成为下一版本的业务契约或安装依赖。

验证覆盖实际变化的消费者及恢复路径；涉及 reader、Docker/MySQL 或模型时核对本次实际组合和资源，缺少依赖就明确该项未运行。源码结构、工程行为、独立验收、安装、自然运行和真实送达分别确认。CI 只检查声明源码的完整性及 Python 语法，不冒称业务回归；行为验收由本次临时验证和正常入口的实际结果提供。

## 节点运行

将 `config/node.example.json` 填为本机配置，保存在源码仓库外。现有数据路径保持不变。先安装一个已验证的提交：

```sh
python3 scripts/release.py --node-config /path/to/node.json install --source /path/to/btc-core-strategy --ref COMMIT
python3 scripts/release.py --node-config /path/to/node.json enter --run-id ACTUAL_RUN_ID
```

安装从同一提交的 `export-manifest.json` 打包正式源码与方法，核对完整性及配置解释器的语法兼容；不要求测试文件或测试 reader 常驻。行为验证在发布前针对明确候选完成，安装记录不把语法检查表述为测试通过。安装不修改 reader 服务。

通过 `status` 发现原在途运行和身份匹配的材料路径，再按真实触发身份进入；`enter` 返回同版的 `task`、Skill 和方法入口。相同已结束身份返回 `FINISHED/RETURN_FINISHED_RUN` 并保留原记录，恢复在途返回 `RESUME_ORIGINAL_RUN`。新轮才返回 `START_NEW_RUN`。重复 `finish` 保留原完成时间；旧记录缺少完成时间时仍为未知，不补造历史。研究请求和投递任务产生时绑定上下文，恢复使用原上下文。详细运行说明见 [发布与恢复](skill/btc-core-strategy/references/releases.md)。

调度只调用固定版本的 [任务入口](tasks/hourly.md)，项目方法和状态规则随源码维护；真实机器绑定与报告目的地留在节点。这个分工使用 OpenAI 的 [Scheduled tasks](https://learn.chatgpt.com/docs/automations) 与 [Skills](https://learn.chatgpt.com/docs/build-skills) 已有能力。投递沿现有 outbox、发送尝试和原生回读推进，重复触发不能复活已结束运行，UNKNOWN 不能自动重发；这对应 [outbox 与幂等消费](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) 的可靠性原则。原生工具的真实副作用及手机可见性仍分别验证。

代码回退只切换 `current`，不覆盖任何运行数据。外层 v4 格式不变，代码读取内容 revision 1–9，新入口准备 revision 9；正式状态仅经真实评估与采纳迁移，旧动作不自动补成风险评估。代码回退必须支持当前内容版本。原 revision 2 历史报告及已审阅语义留在节点的 `legacy_revision2_dir`；测试程序与样本构造逻辑按项目临时生命周期处理。

完整闭环仍由本机 Codex 定时任务实际调用模型、采集和发送。GitHub Actions 仅检查正式源码结构及语法。真实发送、实际助手回读及后续自然运行另行确认，不能由 CI 结果替代。

revision 7 的市场结论、个人资产风险评估、可选风险应对及独立验证见[完整闭环](skill/btc-core-strategy/references/closed-loop.md)。回放材料及构造程序在本次临时验证目录管理，源码导出不包含这些材料。

## Docker 持续分析

[数据服务与迁移](service/README.md)包含 Compose、MySQL 8.4、内容寻址证据、CLI/MCP、宿主适配、恢复及生命周期入口。[持续分析契约](skill/btc-core-strategy/references/continuous-analysis.md)规定业务对象接续、个人事实视图与自主验收。

核心、reader 与 collector 的组合采用由对应项目固定版本及真实消费者决定；升级一个仓库不自动更新另一仓库的依赖。跨仓行为变化须按明确组合实际验证，临时验证程序由本次任务准备，不依赖上游保留历史案例。

Docker 基础镜像由 `service/python_image.py` 与 `service/Dockerfile` 的固定来源确定，缺少实际环境时保留对应验证缺口。历史事故输入和失败在原节点按恢复需要管理，不作为新任务默认安装入口或永久资源保留要求。

当前开发入口准备内容 revision 9，内容 1–8 保留真实读取和恢复能力，不补造历史前瞻结果。判断生命周期、数据兼容和真实身份约束以有效业务契约及当前效果为据。
