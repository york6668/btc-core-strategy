# 持续分析数据节点

`compose.yaml` 的 analysis、reader、collector profile 共用部署定义。分析节点使用独立 MySQL 8.4 数据卷与单独挂载的内容寻址证据目录；核心服务、reader、维护任务在容器内，模型与原生发送/回读、Keychain、三份确认文件在宿主。数据目录、凭据与机器配置只属于节点，不进入源码、镜像构建上下文或云端任务。镜像构建白名单仅包含 service 源码及依赖声明。

可选 `node-scheduler` profile 只启动同一 `btc_node scheduler-loop` 的节点时钟，正式配置默认 `scheduler_mode=OFF`。该循环调用节点自身的 `schedule.tick` 登记真实增量和原到期待办；宿主 CLI worker 只在节点窗口新鲜且无调度故障时领取，不拥有业务时钟。先在一次性本地节点手动复测正常入口、单次领取、重复触发、UNKNOWN 锁止、节点重启和失败恢复，并核对宿主 CLI 的实际登录身份、可用模型及原报告目的地发送与回读；本地通过后才进入正式自然运行验证。`node-scheduler` profile、`ACTIVE` 与 worker 均须在旧正式入口已核验停用、原目的地投递回读已验证后启用。宿主可用适合其实际 Docker/CLI 身份的进程管理器，示例 launchd 只是备选。

`btc_node.api` 是 CLI/MCP 的同一服务层。entities 管理对象版本、引用及复核；series 保存产品身份和双时间序列；projection/history 读取已验证的既有契约；fact_views 绑定文件版本及逐行来源；catalog 保留旧 ID、原字节、SQLite 行及游标；execution 对实际旧 CLI 做持久准入、唯一写入及中断恢复。报告与研究仍使用原状态机，触价不会推导确认或成交。完整业务使用见 [持续分析契约](../skill/btc-core-strategy/references/continuous-analysis.md)。

`model.deep.status/claim/finish/reconcile` 复用 MySQL 写入锁和 `node_metadata`，按原 issue/scope/phase/input/model 和稳定 claim_id 原子防重复调用，项目不设东京自然日次数上限。旧 `model.deep.BTC.days` 及全部旧票据保留原样；新调用写同一状态的 `claims[claim_id]`，status.claims 合并两者，V1 可直接读取，旧 timezone/日期仅为历史兼容；首个新调用把顶层 schema 标为 V2，使旧服务写入器明确拒绝而非忽略新在途记录。只有 CLAIMED_NEW 可启动，精确完成结果跨日 REUSE；原 claim_id 不能换身份，在途和 UNKNOWN 先核清，丢失同 operation 响应只返回核查提示。新阶段可在同日合法预留，但不得冒用旧阶段证明或绕过真实故障、平台容量/用量/安全限制；本规则不宣称任何官方额度。客户端、tasks 和登记消费者须组合采用新 claims 视图；V2 状态不降级或丢弃；旧服务回退将明确拒绝，需保留支持 V2 的 gate/证明消费者或恢复已验证的新版本，不能让旧写入器忽略新记录，旧版固定研究仍可验证旧 days 票据。`model.defect.open/resolve` 保留受影响范围的故障及修复证据。平台代理启动不经过本服务，调用者仍须预留，服务不能技术拦截外部未经它发起的调用。

研究登记的完成凭据使用不可变实际模型轨迹的 SHA256，不以完成说明文件路径代替。已完成票据若误存了非摘要引用，可在同一 `model.deep.reconcile` 中明确提供原 `claim_id`、`outcome=COMPLETE`、`expected_result_ref`、经核实的摘要 `result_ref` 和实际纠错 `evidence_ref`。它只纠正该非摘要引用并追加原值及证据；结果、原完成时间、issue/phase/input 与历史日记录均不变，已有摘要绑定不能替换。登记仍须核验原模型、阶段及真实轨迹，引用纠正不授予后继阶段批准。

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

证据卷同时保存 CAS、`.strategy` 兼容视图和 `.backups`，将该卷整体挂到原项目路径。容器内 `.strategy` 仍是真目录，旧运行中的绝对路径和模板绑定保持一致；CAS 使用该项目挂载的根目录作为 `objects`，而不是另一个 bind mount 别名。hash 命名的 raw 原文以及经原 run/evidence 哈希绑定核验的 market/collector 冻结输入共享 inode；不能仅凭文件名将普通可变 JSON 硬链接到 CAS。确认文件在卷根部只保留指向只读事实挂载的链接，宿主三份文件仍为权威。

旧节点需在原运行、reader 和维护空闲的边界，将兼容目录原子移到证据卷内 `.strategy`，保留宿主旧路径软链接，替换原项目挂载并移除独立 `.strategy` 子挂载。先核对最新备份、原字节及引用，再验证原固定版本和模板能接续；不得恢复旧业务快照。`require_shared_evidence=true` 使无法硬链接的原文显式失败，不能用隐蔽复制冒充去重。未变的原文和已验证冻结输入也重新核对并复用；保留原文档时间和过期时间，不能用 CAS inode 时间改变保留期限。既有备份原件可在核对哈希后与当前原文合并 inode，备份名称、清单和恢复入口不变。精确的子命令 `--help` 保留准入、输入校验和同 operation 恢复，省去没有业务变化的全目录归档和投影；其他参数布局仍走原写入流程。

reader bridge 在单次 Telegram 轮询前后保存 reader checkpoint；前一次 offset 持久化后才允许下次 getUpdates 确认。Keychain token 经 stdin 注入一次性容器，不落入镜像、环境文件或日志。collector 在其原节点暂停旧进程并持有原 writer locks，按实际 state_paths 获取 SQLite backup 与字节快照；核对队列及游标后按原周期启动容器，保留原传输和 UNKNOWN 语义。

已核验共享 inode 的 hash 原文目录保存文件集合、目录时钟和文档摘要索引。目录与索引均未变化时复用原核验，避免每次分析重新逐个检查历史原文；新增、删除、恢复或目录变化仍完整核对，可变文件始终按原规则检查。活动保护和保留期限仍逐份按原逻辑时钟更新。正常写入结果记录命令、归档和投影耗时，便于定位写锁占用；只读引用和 help 的返回值保持原契约。

reader 的 SQLite 兼容挂载允许共享内存协调文件写入，catalog 连接仍使用 `mode=ro`；WAL 模式数据库在 reader 退出后可能需要重新创建 `-shm`，不能将整个目录挂成只读。

reader 检查点和分析投影增量处理原频道已收到的附件价格历史，每批最多 32 份；复用实际 reader 的解码与辅助证据校验。原附件、接收时间、产品及单位进入统一序列，模型冻结时间另存。`project` 返回 `reader_receipts.remaining` 与 `gaps`；迁移时在可恢复边界重复 project 直到积压为零，核对实际缺口，不触发 Telegram 轮询。无效旧附件仅保留原文及缺口，最新附件无效也不能回退旧材料作为当前分析。

## 故障恢复与回退

命令先保存 operation_id/输入摘要，再执行原 CLI，最后归档实际结果。相同 ID 与相同输入返回原结果；并发旧版本拒绝；DB 不可用不会退回宿主写入。服务返回恢复 ID 时先 `core.recover`，核对持久 journal 与实际文件，不能重发报告或换 ID 重跑不确定动作。

`run-value-ref` 沿固定版本校验原冻结 run、证据及允许路径，返回精确引用哈希；服务仍串行准入并持久保存同一操作结果，但不重复归档策略目录、重建业务投影或生成 continuous 材料。原准备及写入阶段负责这些材料，其他命令保持完整提交链；中断引用操作也只核对原 journal，不重新执行。

`backup --directory <新目录>` 保存一致性 MySQL 行集和被引用的不可变内容；`restore-database` 只允许空数据库。`restore --namespace strategy --root <空目录>` 导出最新兼容状态，包含当前 MySQL 的观察、研究和投递记录，并保存操作水位。只有无未决操作且导出水位仍等于最新水位，才能将 writer 改回 HOST；停止 Docker 入口后再切换宿主指针、reader 和原调度。回退使用最新导出，不能恢复迁移前快照覆盖新业务或发送状态。

备份在短写锁内建立独立 MySQL 一致性读视图并暂时保护其原文，随后释放节点写锁再导出行集、复制与校验内容。复制期间正常分析和 reader 可以提交，备份只包含快照时点的数据；清单分别记录快照和完成时间。临时保护由备份连接单独持有的 MySQL 锁维持，进程中断后由下一次备份或清理事务回收；恢复归档不包含这些临时引用。未完成目录没有有效清单，不作可恢复备份。

维护首次启用前验证实际恢复。maintenance_enabled 默认 false；启用后每日先备份再清理，备份默认七天且至少保留两份。引用保护、回收缓冲和生命周期均由 lifecycle 执行；Compose 限制日志轮转。每日记录 MySQL 权威的去重内容增长、表分配量和各证据文件系统的实时可用/总字节；`storage.status.live_filesystem_headroom` 每次请求重新读取文件系统余量，无须遍历备份硬链接。外部写入和同卷其他项目的变化都会反映在可用字节及逐日差值中；它只能发现压力，不能把变化归因到本节点。若需要本节点文件占用的精确归因，`storage.status.last_physical_measurement` 保留最近一次按 inode 去重的审计值与真实测量时间，未测量自然日标为 `NOT_MEASURED`，不得当作零或当天实际占用。显式执行 `python -m btc_node --config <节点配置> capacity-audit` 获取新审计值；它仍逐项读取配置范围内的文件元数据，应安排在资源允许的窗口，不与共享 VM 的其他重负载重叠。每日健康使用文件系统余量，不依赖人工启动审计；删除行不等于 InnoDB 卷缩小。

故障过程材料通过 `failure.get` / `failure.resolve` 接续。兼容导入识别 `failure-receipt.json`、`*-failure.json`、`*-error.json` 中的失败记录；没有证据绑定的明确关闭时保持 ACTIVE。关闭要求当前 document_key、原文 digest、expected_version、已成功提交的核心恢复或验收修复 operation_id 和原因；文件旧、后续运行成功或接口登记本身均不推断恢复。14 天从本次实际关闭确认计时，重复关闭不延后；同路径新故障恢复 OPEN。原文、关闭证据和历史仍由精简记录引用保存，文件回收仍检查依赖和 48 小时缓冲。

默认备份目录位于证据挂载内的 `objects/.backups`，使同一不可变内容可以通过硬链接复用；不同 bind mount 即使显示相同设备号也可能返回 EXDEV 并退回整份复制。旧节点可在维护空闲时迁入同一挂载并保留原路径软链接；逐份校验数据库归档、清单及内容哈希后才合并相同内容，备份名称、恢复入口和保留期限不变。活跃 CAS 删除自己的链接不会删除备份链接；容量按 inode 去重统计，不能把备份逻辑大小当作新增磁盘用量。

宿主每天运行 `service/node_capacity.py --node <现有 node.json>` 保存 Docker 实际用量。node.native_logs 逐项指定项目日志的 path、launchd_label、max_bytes 和 keep_files；仅在对应任务空闲时轮转，运行中留待下次。node.capacity_extra_paths 明确列出迁移缓冲等额外占用，不能扫描或清理 Codex 全局历史及其他项目。

显式容量遍历在清理事务和节点写锁之外进行，记录测量起止时段；统计备份和证据时跳过重复的子目录遍历，并按 inode 去重。分析与 reader 可在容量测量期间正常写入；引用检查、回收及短暂的统计提交仍保留写锁。缺失或不可读的配置路径使本次统计失败，不能记作零占用。每日逻辑记录与显式精确物理审计分别标明，历史物理值不会冒充新一日的测量。

## 持续验证

`python3 -B service/check.py --docker <实际 Docker CLI>` 创建临时 MySQL/core 节点，临时生成输入，实际执行 CLI/MCP、原分析 prepare/observe、并发和版本冲突、来源修订、期限复核、事实分离、生命周期、备份/空库恢复、中断恢复、数据库停机及容器重启；逐表内容与 CAS 在恢复前后逐项一致。结束只清理该临时节点。缺 Docker/MySQL 不跳过或伪装通过。

事故后增加隔离验收准入：本机文件锁与 Docker daemon 内固定名称的原子 claim 防止跨进程/客户端并发；历史 `btc-test-*` 或 `btc-adoption-*` 容器、网络和卷即使已停止也阻断新增，需先检查其日志和数据。检查 Docker VM 实际 `MemAvailable`、交换余量与最近一小时 OOM；macOS 上读取 Docker VM 内核 `init.log`，因为 Docker events 不一定记录全局进程 OOM。本套件临时 core/MySQL/scheduler/maintenance 的硬上限合计 2.75 GiB，另保留 2 GiB VM 余量，因此至少需要 4.75 GiB 可用内存。各容器有 CPU/内存上限且禁用自动重启；MySQL 数据放在本次独有的 bind mount，不再生成匿名保留卷。准入使用本地已有的 `service/Dockerfile` 所固定摘要的 Python 镜像运行一次只读内存探针；镜像不存在则失败，不在准入时拉取。`service/test_validation_guard_offline.py` 用模拟 Docker 命令检查并发、旧栈、预算、近期 OOM、信号和清理失败，无需启动 Docker 栈。

确需保留已取证的静态 MySQL 卷时，可显式传入 `--preserved-volume-manifest <本机私有 JSON>`；默认继续阻断。清单的 schema 为 `BTC_PRESERVED_VALIDATION_VOLUMES_V1`，必须包含当前 Docker daemon 的完整 `daemon_id` 与最多 32 个 `volumes`；每项精确写入 `name`、`project`、`driver=local`、`created_at`、`mountpoint`、`state=PRESERVED`。Docker 命名卷没有独立 ID，完整卷名结合 daemon ID、创建时间、挂载点与 Compose 标签作为核对身份。守卫逐卷读回并确认没有任何容器引用、同项目没有容器、网络或其他卷；清单增删、卷重建、标签或挂载点变化都重新阻断。此清单不授权删除卷，也不绕过单实例锁、内存、swap 或 OOM 准入。必须在旧容器及网络完成取证和获准清理后才能建立这份清单；具体历史资源是否符合条件由原节点当前证据判断，源码说明不保存资源现状。

成功、业务失败或超时都会尝试 `compose down` 并读回确认本次容器和网络消失，再删除本次生成的临时文件。任何清理失败都会保留文件、容器、日志和 daemon claim，明确报错并阻断下次验收；过期 claim 只报告，不自动删除。既有保留卷不在这一步清除。事故后正式环境未达到这项准入，不能用离线测试通过表示已完成 Docker 集成或稳定性验收。

完整核心测试继续使用 `scripts/run_tests.py`；核心仓库单独检查数据服务时使用 `service/check.py --reader <完整reader导出>`，在 market-roles 执行 `roles/validate_repository.py --with-data-service` 则自动使用白名单重建的 collector/analysis 与 reader。卖竭投影复用该 reader 的公式、来源和日期校验；缺失模块保留投影缺口并可重试，不猜测来源。云端默认准备只覆盖依赖和离线验证；显式 `--with-data-service` 模式要求 Docker/Compose/MySQL 可运行，不能将离线通过当作数据服务或环境发布通过。实际 reader 镜像组合验证通过 `service/check.py --reader-image <已构建镜像>` 执行，涵盖真实角色 CLI、参数透传、只读源码及可写嵌套数据挂载。自然七日验收使用原分析任务，不增设第二个分析写入方。acceptance 的每项结论必须绑定实际输入、状态变化、消费结果与可读取证据；日期届满不会自动通过。

关键缺陷以 `acceptance.defect` 保留失败和受影响窗口，实际修复复验后调用 `acceptance.repair` 绑定缺陷序号、发布及证据。它从次日重开七个完整自然日，原检查与旧窗口历史保留；未解决缺陷始终阻止自然验收通过。

局部行为纠偏沿现有任务追踪。按问题所需审阅锁定真实输入、完整结果和正文消费；采纳、送达、自然执行及后继效果仍需各自实际证据。`acceptance.repair(preserve_window=true, verified_scope=[...], remaining_scope=[...])` 可在原缺陷编号下记录已修范围，`acceptance.status.partial_repairs` 回读仍待场景；未清空 `remaining_scope` 时该编号仍在 `open_defects`。后续仅能从已记录待办中逐项核实并追加证据，全部完成后才关闭该缺陷。旧的不带范围调用保持原整体关闭语义，保留窗口与历史。安装和合成测试不构成内容验收；具体接续见核心 Skill 的 runtime-maintenance.md。

`acceptance.status.scope=LOCAL_NATURAL_ACCEPTANCE` 只表示本机自然验收；源码发布、采集节点迁移、云端环境发布和全新任务验证分别核实，不由本机状态推导，也不作为其他操作的一概前置条件。卖竭投影保留附件前30天原料但不将预热行写成派生观测，避免滚动导出边界撤销旧有效值。

`prices.query` 的投影 `provenance.retrieval.raw_ref` 对应 `original_digest` 保存的实际原件：reader 附件使用 `/measurements/seller_exhaustion_daily`，冻结快照使用 `/assets/BTC/datasets/seller_exhaustion_daily`。引用哈希绑定该位置的原值，不能用规范化后的结构替代原件。已存错误引用通过显式写操作 `prices.repair_seller_references`（`limit`、`after` 分页）追加修正；原观测、原获知时间和原引用不变，不增加数值修订。查询仅在 `as_of` 不早于修正获知时间时提供附加 `reference_correction`，其中 `corrected_ref` 可按 `original_digest` 解析。缺原件或原料不一致明确返回 `gaps`，不猜测修正，也不阻断正常市场分析。
