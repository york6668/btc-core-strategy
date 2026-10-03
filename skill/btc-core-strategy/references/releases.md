# 版本、节点与恢复

源码及方法来自 `york6668/btc-core-strategy` 的同一个提交；`market-roles` 用 submodule 固定提交。生产数据始终留在节点配置的 `project_dir`、reader 数据路径及既有 Codex 本地目录。

每轮先用本机版本入口执行 `enter --run-id <真实运行ID>`。返回物理 `release`、`skill`、`methods`、`project_dir` 和 `node_config` 路径。设置 `BTC_RELEASE`、`BTC_PROJECT`、`BTC_NODE_CONFIG` 和节点 `python` 对应的 `BTC_PYTHON`；随后每次命令均带这些值。不能从会变化的 Skill 链接或 `current` 再次选择代码。方法中的脚本路径都相对于本轮 `BTC_RELEASE/skill/btc-core-strategy`。

研究请求或投递任务创建后，用 `bind-context --run-id <原运行ID> --context <真实request_id或job_id>` 保存版本引用。恢复时 `enter --run-id <本次恢复ID> --resume-context <原上下文ID>` 返回原版本。原证据、研究阶段、代理、UNKNOWN 尝试和回读身份仍由原策略记录拥有；版本引用不代表完成、批准或送达。已有上下文没有版本记录时，先根据该上下文实际使用的代码版本补回引用，不猜测最新版，也不重开研究或重发。

本轮结束用 `finish --run-id <原运行ID>`。崩溃留下的 ACTIVE 标记由恢复任务先接续，确认本轮已停止后再 finish；不能按过期时间假定工作已结束。旧版本不自动清理，未完成研究和投递可以继续使用。

当旧研究因已确认的程序兼容缺陷无法登记，先在修复版本验证原冻结材料，再使用 `repair-context --context <原request_id> --from-commit <当前恢复版本> --to-commit <已安装修复版本> --reason <具体缺陷与复验依据>` 追加修复映射。原始绑定及修复历史保留；仅之后新建的恢复运行读取指定修复版本，已有运行仍固定原版本。该入口只接受原字节身份有效的研究请求，拒绝正在恢复的上下文、版本冲突和不可读的内容 revision；不能用于投递或通过安装自动触发。原 request/issue/代理/冻结成果和 parent_independent 不变，登记后继续原真实审阅与采纳，不生成批准或替换历史。普通恢复仍沿原版本，UNKNOWN 投递无此例外。

维护时修改源码仓库并运行相称验证，提交、推送后更新 `market-roles` 的 submodule；用指定提交安装本机独立发布目录。安装从 Git 提交取得完整源码和测试，验证成功后才切换。运行中只暂存待切换版本，在轮次间隙 `activate <commit>`；回退使用同一命令指向原版本。切换不改任何策略数据、账户事实、游标、报告与运行历史，失败保留当前版本。没有节点访问能力时保留待部署状态。

外层 v4 schema 不变。新代码可读内容 revision 1–6，新入口采用 revision 6；旧上下文保留原内容与固定代码。正式迁移只随真实评估和采纳，不自动写入配置决定；不改资产、历史、研究或 UNKNOWN 状态。RELEASE.json 声明 content_contract，可读范围不含当前正式 revision 的代码不能激活；旧上下文仍可 enter --resume-context 接续。恢复与备份沿现有节点机制，不通过回滚旧数据实现代码回退。

安装验证仅证明代码和接口。自然验收沿原定时任务观察：固定代码提交、方法路径与数据路径正确；既有研究接续；正式状态、发送与实际助手回读分别成立。缺少真实报告必要性时不为了验收制造报告或重新研究。

运行版 reader 可以只部署运行源码；安装验证需要含离线测试构造程序的完整 reader 导出。此时用 `install --reader <完整测试reader目录>` 明确提供，或先用 `scripts/prepare_reader.py` 准备固定依赖后传入 `.local/reader`。该参数只用于临时发布包验证，不改变节点的 reader 路径、服务或数据库。
