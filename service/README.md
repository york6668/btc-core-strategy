# 持续分析数据节点

`compose.yaml` 的 analysis、reader、collector profile 共用部署定义。分析节点使用独立 MySQL 8.4 数据卷与单独挂载的内容寻址证据目录；核心服务、reader、维护任务在容器内，模型与原生发送/回读、Keychain、三份确认文件在宿主。数据目录、凭据与机器配置只属于节点，不进入源码、镜像构建上下文或云端任务。镜像构建白名单仅包含 service 源码及依赖声明。

`btc_node.api` 是 CLI/MCP 的同一服务层。entities 管理对象版本、引用及复核；series 保存产品身份和双时间序列；projection/history 读取已验证的既有契约；fact_views 绑定文件版本及逐行来源；catalog 保留旧 ID、原字节、SQLite 行及游标；execution 对实际旧 CLI 做持久准入、唯一写入及中断恢复。报告与研究仍使用原状态机，触价不会推导确认或成交。完整业务使用见 [持续分析契约](../skill/btc-core-strategy/references/continuous-analysis.md)。

## 准备与迁移

`python3 service/setup_node.py --node <现有 node.json> --data <节点专属目录> --docker <实际 Docker CLI> --compose-project <节点名>` 只准备关闭的接入配置。使用生成的 compose.env 与覆盖文件启动 mysql/core，检查实际健康。镜像从本仓库 service 源码构建；reader/collector 镜像由 market-roles 的 `roles/containers/build.py` 从源码白名单导出构建。

在没有新写入者的边界短暂停止原调度，使用 `service/snapshot.py --project <项目> --destination <新快照目录>` 获取一致性快照，再恢复原调度。导入与对照阶段不执行分析 CLI、不发送消息。为原生导入配置本机地址、专属数据库端口、凭据文件和快照项目路径，运行：

```sh
PYTHONPATH=service python3 -m btc_node --config <导入配置> import --namespace strategy --root <快照>/.strategy
PYTHONPATH=service python3 -m btc_node --config <导入配置> project --release <固定发布目录>
PYTHONPATH=service python3 -m btc_node --config <导入配置> compare --namespace strategy --root <快照>/.strategy
```

reader 使用独立 namespace 导入与对比。校验包括原字节/hash、路径、符号链接、SQLite schema/user_version/行集/自增高水位，以及真实当前指针、事实版本、研究和 UNKNOWN。旧格式仅在不能安全结构化时保留原件与明确缺口，不清空旧状态。history 按原始时间逐步投影，已处理的相同内容跳过。

切换前完成隔离恢复演练；再短暂停止新调度和 reader，确认无 ACTIVE 写入运行，导入最终增量并对比，恢复到空 compatibility_root。切换 writer=DOCKER，节点 docker_service 启用并指向已验证 Compose；原项目 .strategy 保留原目录备份后以符号链接接到 compatibility_root。同一时刻只允许一套入口写入。原运行继续其固定发布，旧 SQLite-only 发布通过导出/导入兼容当前 MySQL 观察库；不得中途换方法版本。恢复原调度及 reader bridge 后实际运行原 CLI 与 MCP，核对真正消费的新状态。

宿主解释器接入 `service/host_python.py`，node.native_python 保留原解释器，node.python 指向节点生成的启动器。适配层按实际 strategy_v4.py 路径选择原固定发布，所以恢复早于 Docker 的发布也进入同一服务；不改旧发布文件或用 current 替换其方法。其他 Python 工具仍使用原生解释器。

容器将兼容目录直接挂载到原项目 `.strategy` 路径，容器内该路径不是符号链接；旧运行中的绝对路径和模板绑定因此保持一致。宿主可使用符号链接访问同一目录。模板保留原运行路径并校验原字节，不能像普通模型输入一样改写到新路径。

reader bridge 在单次 Telegram 轮询前后保存 reader checkpoint；前一次 offset 持久化后才允许下次 getUpdates 确认。Keychain token 经 stdin 注入一次性容器，不落入镜像、环境文件或日志。collector 在其原节点暂停旧进程并持有原 writer locks，按实际 state_paths 获取 SQLite backup 与字节快照；核对队列及游标后按原周期启动容器，保留原传输和 UNKNOWN 语义。

reader 的 SQLite 兼容挂载允许共享内存协调文件写入，catalog 连接仍使用 `mode=ro`；WAL 模式数据库在 reader 退出后可能需要重新创建 `-shm`，不能将整个目录挂成只读。

## 故障恢复与回退

命令先保存 operation_id/输入摘要，再执行原 CLI，最后归档实际结果。相同 ID 与相同输入返回原结果；并发旧版本拒绝；DB 不可用不会退回宿主写入。服务返回恢复 ID 时先 `core.recover`，核对持久 journal 与实际文件，不能重发报告或换 ID 重跑不确定动作。

`backup --directory <新目录>` 保存一致性 MySQL 行集和被引用的不可变内容；`restore-database` 只允许空数据库。`restore --namespace strategy --root <空目录>` 导出最新兼容状态，包含当前 MySQL 的观察、研究和投递记录，并保存操作水位。只有无未决操作且导出水位仍等于最新水位，才能将 writer 改回 HOST；停止 Docker 入口后再切换宿主指针、reader 和原调度。回退使用最新导出，不能恢复迁移前快照覆盖新业务或发送状态。

维护首次启用前验证实际恢复。maintenance_enabled 默认 false；启用后每日先备份再清理，备份默认七天且至少保留两份。引用保护、回收缓冲和生命周期均由 lifecycle 执行；Compose 限制日志轮转。storage.status 区分逻辑字节、去重字节、实际文件占用、MySQL 表分配量和宿主 Docker 占用；删除行不等于 InnoDB 卷缩小。

宿主每天运行 `service/node_capacity.py --node <现有 node.json>` 保存 Docker 实际用量。node.native_logs 逐项指定项目日志的 path、launchd_label、max_bytes 和 keep_files；仅在对应任务空闲时轮转，运行中留待下次。node.capacity_extra_paths 明确列出迁移缓冲等额外占用，不能扫描或清理 Codex 全局历史及其他项目。

## 持续验证

`python3 -B service/check.py --docker <实际 Docker CLI>` 创建临时 MySQL/core 节点，临时生成输入，实际执行 CLI/MCP、原分析 prepare/observe、并发和版本冲突、来源修订、期限复核、事实分离、生命周期、备份/空库恢复、中断恢复、数据库停机及容器重启；逐表内容与 CAS 在恢复前后逐项一致。结束只清理该临时节点。缺 Docker/MySQL 不跳过或伪装通过。

完整核心测试继续使用 `scripts/run_tests.py`；跨角色验收在 market-roles 执行 `roles/validate_repository.py --with-data-service`，同时从白名单重建 collector/analysis。云端准备显式要求 Docker/Compose/MySQL 可运行，不能仅安装 Python 包后声称部署完成。自然七日验收使用原分析任务，不增设第二个分析写入方。acceptance 的每项结论必须绑定实际输入、状态变化、消费结果与可读取证据；日期届满不会自动通过。

关键缺陷以 `acceptance.defect` 保留失败和受影响窗口，实际修复复验后调用 `acceptance.repair` 绑定缺陷序号、发布及证据。它从次日重开七个完整自然日，原检查与旧窗口历史保留；未解决缺陷始终阻止自然验收通过。
