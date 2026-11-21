# 持续分析数据节点

## 正常交易闭环

现有节点由宿主受控入口提交 `trading.*` 请求。市场判断、组合及成员决定、原条件复核和发件箱由同一 MySQL 写入端登记。个人阶段的 `trading.portfolio_decision` 在一次事务内保存组合比较、实际依赖及确切成员版本，沿既有条件复评推进。新个人报告引用相应的组合决定；已登记投递沿原件和原身份恢复。公共查询可从既有 MCP 使用，个人资料、状态写入和原生投递经宿主准入后的正常任务入口。

正式切换前运行与候选源码一致的 core 服务。原有文件式记录仅通过其只读来源承接。后续节中关于旧 CLI、目录投影和节点备用调度的入口用于已部署的旧运行及其证据复核。

`compose.yaml` 的 analysis、reader、collector profile 共用部署定义。分析节点使用独立 MySQL 8.4 数据卷与单独挂载的内容寻址证据目录；核心服务、reader、维护任务在容器内，模型与原生发送/回读、Keychain、三份确认文件在宿主。数据目录、凭据与机器配置只属于节点，不进入源码、镜像构建上下文或云端任务。镜像构建白名单仅包含 service 源码及依赖声明。

可选 `node-scheduler` profile 只启动同一 `btc_node scheduler-loop` 的节点时钟，正式配置默认 `scheduler_mode=OFF`。该循环调用节点自身的 `schedule.tick` 登记真实增量和原到期待办；宿主 CLI worker 只在节点窗口新鲜且无调度故障时领取，不拥有业务时钟。先在一次性本地节点手动复测正常入口、单次领取、重复触发、UNKNOWN 锁止、节点重启和失败恢复，并核对宿主 CLI 的实际登录身份、可用模型及原报告目的地发送与回读；本地通过后才进入正式自然运行验证。`node-scheduler` profile、`ACTIVE` 与 worker 均须在旧正式入口已核验停用、原目的地投递回读已验证后启用。宿主可用适合其实际 Docker/CLI 身份的进程管理器，示例 launchd 只是备选。

`btc_node.api` 是 CLI/MCP 的同一服务层。entities 管理对象版本、引用及复核；series 保存产品身份和双时间序列；projection/history 读取已验证的既有契约；fact_views 绑定文件版本及逐行来源；catalog 保留旧 ID、原字节、SQLite 行及游标；execution 对实际旧 CLI 做持久准入、唯一写入及中断恢复。报告与研究仍使用原状态机，触价不会推导确认或成交。完整业务使用见 [持续分析契约](../skill/btc-core-strategy/references/continuous-analysis.md)。

core 计算解释器固定为 Python 3.12.14，与节点保留的冻结行情生产环境一致；镜像来源固定官方多架构摘要。V1 窗口记录的浮点求和末位受生产解释器语义影响，不能用 Python 3.11 的默认求和重新计算 Python 3.12 生产的原件并据此判定原件损坏。升级时以实际解释器和同一冻结原件的正常严格校验核收，数据库路径、源字节、证据身份及精确校验不变，不通过增加容差或重写窗口消除错误。旧顺序求和 V1 的既有精确兼容分支仍保留。

承接旧研究时，不可变登记中的原生证明可能仍引用宿主临时路径。可用既有适配层的 `node_bridge.archive_proof_location(source, native_proof_dir)` 将原证明及其摘要绑定的来源文件完整保存，按原路径的词法摘要区分位置；同名不同路径不会互相覆盖，同一路径内容冲突明确失败。消费者仅在原文件缺失时，从节点明确配置的 `native_proof_dir` 查询已完成归档，校验原字节后仍执行原作者、回合、事件及来源摘要检查。位置记录不授予批准、不改写研究身份或正式策略；已有损坏原件不会被归档副本掩盖。新容器配置由 `setup_node.py` 显式写入该目录，既有节点需单独完成配置及程序的正常更新；本机文件归档成功不等于服务已能消费。

`model.deep.status/claim/finish/reconcile` 复用 MySQL 写入锁和 `node_metadata`，按原 issue/scope/phase/input 和稳定 claim_id 原子防重复调用，项目不设东京自然日次数上限。旧 `model.deep.BTC.days` 及全部旧票据保留原样；新调用写同一状态的 `claims[claim_id]`，status.claims 合并两者，V1 可直接读取，旧 timezone/日期仅为历史兼容；首个新调用把顶层 schema 标为 V2，使旧服务写入器明确拒绝而非忽略新在途记录。只有 CLAIMED_NEW 可启动，精确完成结果跨日 REUSE；原 claim_id 不能换身份，在途和 UNKNOWN 先核清，丢失同 operation 响应只返回核查提示。新阶段可在同日合法预留，但不得冒用旧阶段证明或绕过真实故障、平台容量/用量/安全限制；本规则不宣称任何官方额度。客户端、tasks 和登记消费者须组合采用新 claims 视图；V2 状态不降级或丢弃；旧服务回退将明确拒绝，需保留支持 V2 的 gate/证明消费者或恢复已验证的新版本，不能让旧写入器忽略新记录，旧版固定研究仍可验证旧 days 票据。`model.defect.open/resolve` 保留受影响范围的故障及修复证据。平台代理启动不经过本服务，调用者仍须预留，服务不能技术拦截外部未经它发起的调用。

研究登记的完成凭据使用不可变实际模型轨迹的 SHA256，不以完成说明文件路径代替。已完成票据若误存了非摘要引用，可在同一 `model.deep.reconcile` 中明确提供原 `claim_id`、`outcome=COMPLETE`、`expected_result_ref`、经核实的摘要 `result_ref` 和实际纠错 `evidence_ref`。它只纠正该非摘要引用并追加原值及证据；结果、原完成时间、issue/phase/input 与历史日记录均不变，已有摘要绑定不能替换。登记仍须核验原模型、阶段及真实轨迹，引用纠正不授予后继阶段批准。

### 原生投递回执

通过已进入的宿主 `btc-core trading` 调用 `trading.record_delivery {report_id,action,receipt}`。下表的字段均在 `receipt` 内。使用原报告、原目的地对象和 `trading.begin_delivery` 返回的尝试身份。地址中的项目字段沿用原对象。 `record_refs` 填实际取得的非空记录引用。当前稿取得完整原生请求及完成正文时，依次登记请求绑定和确认。归档确认直接沿已保存的原绑定处理。

| 动作 | 回执字段 |
| --- | --- |
| `ACKNOWLEDGE` | `schema=BTC_TRANSPORT_RECEIPT_V1`，`attempt_id,recorded_at,status,record_refs,detail`。运输状态按实际响应取 `ACCEPTED`、`PROCESSING` 或 `FAILED`。 |
| `BIND_REQUEST` | `schema=BTC_SENT_REQUEST_V1`，`attempt_id,destination,request_ref,record_refs,request_body,anchor_ref,request_started_at`。 |
| `CONFIRM`、`CONFIRM_ARCHIVED` | `schema=BTC_CLOUD_READBACK_V5`，`destination,checked_at,attempt_id,request_ref,assistant,record_refs,absence_confirmed,review`。`absence_confirmed=false`。归档核收使用原请求和原尝试。 |
| `UNKNOWN` | `schema=BTC_UNCONFIRMED_READBACK_V1`，`attempt_id,checked_at,record_refs,detail`。登记实际回读及仍无法确认的效果。 |
| `NOT_SENT`、`BLOCKED` | `schema=BTC_CLOUD_READBACK_V5`，`destination,checked_at,attempt_id,request_ref,assistant,record_refs,absence_confirmed,review,dispatch_evidence`。`request_ref`、`assistant`、`review` 为 null，`absence_confirmed=true`。`dispatch_evidence` 包含 `outcome=NOT_DISPATCHED,record_ref,detail`，其中 `record_ref` 必须列入 `record_refs`。适用于原生记录明确表明本次未派发。 |

- **运输受理。** 使用发送工具实际返回的记录引用。仅返回目标聊天 ID 时，该回执只支持受理。已经读到原请求时可直接登记 `BIND_REQUEST`。
- **实际请求。** 在目标聊天回读中找到游标之后实际提交的 `userMessage`，使用其 `id` 和完整正文作为 `request_ref`、`request_body`，用所属回合的 `startedAt` 作 `request_started_at`。把实收请求身份列入 `record_refs`。
- **实际正文。** 从同一完成回合取得 `agentMessage`。`assistant` 包含 `record_ref,parent_request_ref,role,body,completed,tool_calls,completed_at`。使用 `agentMessage.id`、原 `userMessage.id`、`role=assistant`、实收原文、实际完成状态、回合实际工具调用和该回合的 `completedAt`。请求与助手身份都列入 `record_refs`。
- **核对证据。** 原生回合时间若为 Unix 秒，保留小数并转换为 UTC ISO 8601。`checked_at` 是本次实际核对时刻，`recorded_at` 是运输回执登记时刻。`review` 包含 `reviewer_role=MAIN,agent_id,source_hash,display_hash,bindings=[],checks={},unresolved_issues`。摘要分别来自冻结正文和实收原文的 UTF-8 字节，`agent_id` 为实际核对者。确认送达要求实际完成、无工具调用、无未解问题和全文匹配。正文缺失、标为截断或缺少原生完成时间时，按现有证据保留原尝试。

## 准备与迁移

已完成研究的跨节点承接使用 `append-dependencies --root <归档根目录> --manifest <明确清单>`，先带 `--check-only` 核对源字节和目标登记。清单为 `BTC_RESEARCH_DEPENDENCIES_V1`，`files` 每项只含相对 `path` 与 `sha256:…` 格式的原始字节摘要；路径相对原 `.strategy`，仅允许已有研究、证据及投资记录归档目录，拒绝策略版本、current、资产事实和符号链接。它在已有节点事务内增加缺失条目，并将清单内已有相同字节的 RAW／到期等登记提升为 KEY、清除相应回收标记；预检及回执将新增、已有内容不变、保留属性调整和回收清理分开计数。同路径异内容整批阻断；不初始化数据库、不删除或覆盖命名空间、不投影业务状态，也不物化正式文件视图。原作者、请求、结果、原始字节及其原基线不改写，清单外登记不变；内容摘要不是作者或批准证明，跨基线适用性仍由正常 MAIN 组装和原精确批准契约验证。未决操作先由原入口核清，不在其恢复过程中增补。

目录归档引用保存在原 `node_metadata`/`metadata_blobs` 中，正常 `Catalog.sync` 不删除尚未物化的归档条目，并拒绝同路径不同字节。整命名空间 `import` 仍用于完整迁移，不能拿研究子集替换正式命名空间。增补失败时文档及元数据事务一起回滚；已有 CAS 机制允许留下无引用不可变字节，由原缓冲回收处理。实际采用前仍需通过正常读取路径准备相应文件视图及依赖，增补登记成功不代表策略已采用、报告已消费或平台拒绝已解除。

管理入口 `import` / `compare` 显式使用 live-view 加已验证归档的比较范围，返回未物化的 `archive_only` 清单并核验其登记与 CAS 字节；不是忽略缺文件。完整快照及备份调用的 `Catalog.compare` 默认仍要求全部路径物化，归档缺失、损坏或内容冲突不能被两类比较放行。

需要正常文件读取时，再用 `materialize-dependencies --manifest <同一清单> --check-only` 核验已登记的原字节和节点配置中的 `compatibility_root`；去掉检查参数才创建缺失原件，不接受另行指定正式目标。它不覆盖已有文件或链接，不改数据库、策略 current、资产事实及清单外文件。完整临时文件 fsync 后以不替换目标的原子链接发布；异常或中断保留已完整发布的原件，不能通过检查后按路径删除来冒险清理并发替换文件。由只读清单核收并幂等接续缺项，不出现半写文件；冲突保持不覆盖，视图完整仍不代表业务批准或策略采用。

`python3 service/setup_node.py --node <现有 node.json> --data <节点专属目录> --docker <实际 Docker CLI> --compose-project <节点名>` 只准备关闭的接入配置。使用生成的 compose.env 与覆盖文件启动 mysql/core，检查实际健康。镜像从本仓库 service 源码构建；reader/collector 镜像由 market-roles 的 `roles/containers/build.py` 从源码白名单导出构建。

报告目的地由宿主节点配置持有。核心服务通过现有运行目录的只读挂载读取该原件；节点设置记录配置路径，容器执行配置不提供报告目的地。使用目录挂载使宿主原子替换配置后，后续读取仍取得同一来源。已有稿件及发送尝试各自保留冻结时的原目的地。配置不可用时，事实、判断和反馈待办仍在正式服务中接续。

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

Reader 的唯一运行入口为原 LaunchAgent 调用 core 内的 `reader-poll`。频道、游标、收件、历史投影和附件原文由 MySQL/CAS 管理，研判与修复读取同一份数据。旧节点升级可离线 `import --namespace reader` 校对最后一次 SQLite 快照，再执行一次 `reader-cutover --checkpoint-ms <已核对时间>`；Docker Reader 轮询读回成功后，退出旧 SQLite 文件、WAL/SHM、重复登记和挂载。已启用的 Docker Reader 禁止重新导入旧归档。

Keychain token 仍经 stdin 交给一次轮询。收件和游标在同一 MySQL 事务中提交，附件下载在事务外执行并按原身份落库和重试。分析的 `run-prepare` 和价格投影从同一 MySQL/CAS 读取，旧轮继续使用自己的冻结文件。以 `reader.status` 核对真实后置游标和成功时间。完成切换后解除 reader SQLite 挂载，保留旧库用于归档。collector 若单独迁移，继续沿其原节点的队列、游标、传输和 UNKNOWN 契约执行。

已核验共享 inode 的 hash 原文目录保存文件集合、目录时钟和文档摘要索引。目录与索引均未变化时复用原核验，避免每次分析重新逐个检查历史原文；新增、删除、恢复或目录变化仍完整核对，可变文件始终按原规则检查。活动保护和保留期限仍逐份按原逻辑时钟更新。正常写入结果记录命令、归档和投影耗时，便于定位写锁占用；只读引用和 help 的返回值保持原契约。

reader 的 SQLite 兼容挂载允许共享内存协调文件写入，catalog 连接仍使用 `mode=ro`；WAL 模式数据库在 reader 退出后可能需要重新创建 `-shm`，不能将整个目录挂成只读。

Docker Reader 和分析投影增量处理原频道已收到的附件价格历史，每批最多 32 份；复用实际 reader 的解码与辅助证据校验。原附件、接收时间、产品及单位进入统一序列，模型冻结时间另存。`project` 返回 `reader_receipts.remaining` 与 `gaps`；迁移时在可恢复边界重复 project 直到积压为零，核对实际缺口，不触发 Telegram 轮询。无效附件留存期间仍可追查原文及缺口，最新附件无效也不能回退旧材料作为当前分析。

## 故障恢复与回退

`model.defect.check` 的 `allowed` 仅判断已登记 OPEN 故障的范围交叠，不证明模型、执行环境或具体工具的能力。返回保留失败证据、影响/原因引用及能关联的原票据和阶段；旧记录的多项匹配全部列出，不猜唯一来源，也不重写历史。新失败结算保留原 `failure_ref` 与票据身份。`model.deep.claim` 的对应拒绝明确标为 `LOCAL_MODEL_GATE` 并附同一上下文，不能把它转述为平台安全拒绝、模型整体失效或用户授权缺失。诊断上下文不会清除故障、放宽前缀匹配或豁免 DIAGNOSTIC/消息审阅；后继仍须核真实依赖和适用准入。

命令先保存 operation_id/输入摘要，再执行原 CLI，最后归档实际结果。相同 ID 与相同输入返回原结果；并发旧版本拒绝；DB 不可用不会退回宿主写入。服务返回恢复 ID 时先 `core.recover`，核对持久 journal 与实际文件，不能重发报告或换 ID 重跑不确定动作。

`run-value-ref` 沿固定版本校验原冻结 run、证据及允许路径，返回精确引用哈希；服务仍串行准入并持久保存同一操作结果，但不重复归档策略目录、重建业务投影或生成 continuous 材料。原准备及写入阶段负责这些材料，其他命令保持完整提交链；中断引用操作也只核对原 journal，不重新执行。

`backup --directory <新目录>` 保存一致性 MySQL 行集和被引用的不可变内容；`restore-database` 只允许空数据库。`restore --namespace strategy --root <空目录>` 导出最新兼容状态，包含当前 MySQL 的观察、研究和投递记录，并保存操作水位。只有无未决操作且导出水位仍等于最新水位，才能将 writer 改回 HOST；停止 Docker 入口后再切换宿主指针、reader 和原调度。回退使用最新导出，不能恢复迁移前快照覆盖新业务或发送状态。

备份在短写锁内建立独立 MySQL 一致性读视图并暂时保护其原文，随后释放节点写锁再导出行集、复制与校验内容。复制期间正常分析和 reader 可以提交，备份只包含快照时点的数据；清单分别记录快照和完成时间。临时保护由备份连接单独持有的 MySQL 锁维持，进程中断后由下一次备份或清理事务回收；恢复归档不包含这些临时引用。未完成目录没有有效清单，不作可恢复备份。

维护首次启用前验证实际恢复。maintenance_enabled 默认 false；启用后每日先备份再清理，备份默认七天且至少保留两份。引用保护、回收缓冲和生命周期均由 lifecycle 执行；Compose 限制日志轮转。每日记录 MySQL 权威的去重内容增长、表分配量和各证据文件系统的实时可用/总字节；`storage.status.live_filesystem_headroom` 每次请求重新读取文件系统余量，无须遍历备份硬链接。外部写入和同卷其他项目的变化都会反映在可用字节及逐日差值中；它只能发现压力，不能把变化归因到本节点。若需要本节点文件占用的精确归因，`storage.status.last_physical_measurement` 保留最近一次按 inode 去重的审计值与真实测量时间，未测量自然日标为 `NOT_MEASURED`，不得当作零或当天实际占用。显式执行 `python -m btc_node --config <节点配置> capacity-audit` 获取新审计值；它仍逐项读取配置范围内的文件元数据，应安排在资源允许的窗口，不与共享 VM 的其他重负载重叠。每日健康使用文件系统余量，不依赖人工启动审计；删除行不等于 InnoDB 卷缩小。

Reader 原附件沿同一次日维护退出。按接收与已知成功取回时间保留 30 天；仍未完成投影、存在价格原件或证据引用、或者是无法安全归类的当前孤立附件时，保留原件。到期且无这些依赖的附件留下过期状态，避免回退至旧报告；移除附件对 CAS 的引用。CAS 再按现有 48 小时缓冲及其他引用规则删除物理文件；备份按原保留期轮换。删除 MySQL 行后其表空间可复用。

日维护遇到其他正常写入占用 MySQL 写锁时，只等待当前阶段的入锁。已经完成的备份、清理和容量写入不重放；进程中断、连接失败或阶段本身执行失败仍按原故障流程核对。

备份保留期也适用于有本节点进度记录、没有有效清单且没有活动写入租约的中断备份目录。清理只处理已确认的过期残留；有效备份和正在写入的目录按原规则保留。

故障过程材料通过 `failure.get` / `failure.resolve` 接续。兼容导入识别 `failure-receipt.json`、`*-failure.json`、`*-error.json` 中的失败记录；没有证据绑定的明确关闭时保持 ACTIVE。关闭要求当前 document_key、原文 digest、expected_version、已成功提交的核心恢复或验收修复 operation_id 和原因；文件旧、后续运行成功或接口登记本身均不推断恢复。14 天从本次实际关闭确认计时，重复关闭不延后；同路径新故障恢复 OPEN。原文、关闭证据和历史仍由精简记录引用保存，文件回收仍检查依赖和 48 小时缓冲。

默认备份目录位于证据挂载内的 `objects/.backups`，使同一不可变内容可以通过硬链接复用；不同 bind mount 即使显示相同设备号也可能返回 EXDEV 并退回整份复制。旧节点可在维护空闲时迁入同一挂载并保留原路径软链接；逐份校验数据库归档、清单及内容哈希后才合并相同内容，备份名称、恢复入口和保留期限不变。活跃 CAS 删除自己的链接不会删除备份链接；容量按 inode 去重统计，不能把备份逻辑大小当作新增磁盘用量。

宿主每天运行 `service/node_capacity.py --node <现有 node.json>` 保存 Docker 实际用量。node.native_logs 逐项指定项目日志的 path、launchd_label、max_bytes 和 keep_files；仅在对应任务空闲时轮转，运行中留待下次。node.capacity_extra_paths 明确列出迁移缓冲等额外占用，不能扫描或清理 Codex 全局历史及其他项目。

显式容量遍历在清理事务和节点写锁之外进行，记录测量起止时段；统计备份和证据时跳过重复的子目录遍历，并按 inode 去重。分析与 reader 可在容量测量期间正常写入；引用检查、回收及短暂的统计提交仍保留写锁。缺失或不可读的配置路径使本次统计失败，不能记作零占用。每日逻辑记录与显式精确物理审计分别标明，历史物理值不会冒充新一日的测量。

## 持续验证

验证依据当前变化及真实消费者选择 CLI/MCP、并发、版本冲突、数据生命周期、备份恢复及故障恢复等必要行为。案例、合成输入与临时 Compose 由本次任务在仓库外或忽略位置管理；原运行器和锁定案例不进入正式源码，也不成为后续启动条件。

隔离验证前核实际 Docker/MySQL、reader 来源、在途工作和资源预算；限制本次资源并保留原失败，退出后只清理本次创建且可重建的对象。原事故记录及未知效果按原范围核实，不能因旧容器名称、历史测试数量或过期阈值扩大阻断。缺少真实组合、故障恢复或目标规模的执行证据时分别保留缺口，不以离线或安装通过代替。

自然验收使用已有分析任务的真实输入、状态变化、消费结果及送达证据，不增设写入方，不因日期届满或测试标签自动通过。工程回放、实际采用与业务效果分别记录。

关键缺陷以 `acceptance.defect` 保留失败和受影响窗口，实际修复复验后调用 `acceptance.repair` 绑定缺陷序号、发布及证据。它从次日重开七个完整自然日，原检查与旧窗口历史保留；未解决缺陷始终阻止自然验收通过。

局部行为纠偏沿现有任务追踪。按问题所需审阅锁定真实输入、完整结果和正文消费；采纳、送达、自然执行及后继效果仍需各自实际证据。`acceptance.repair(preserve_window=true, verified_scope=[...], remaining_scope=[...])` 可在原缺陷编号下记录已修范围，`acceptance.status.partial_repairs` 回读仍待场景；未清空 `remaining_scope` 时该编号仍在 `open_defects`。后续仅能从已记录待办中逐项核实并追加证据，全部完成后才关闭该缺陷。旧的不带范围调用保持原整体关闭语义，保留窗口与历史。安装和合成测试不构成内容验收；具体接续见核心 Skill 的 runtime-maintenance.md。

`acceptance.status.scope=LOCAL_NATURAL_ACCEPTANCE` 只表示本机自然验收；源码发布、采集节点迁移、云端环境发布和全新任务验证分别核实，不由本机状态推导，也不作为其他操作的一概前置条件。卖竭投影保留附件前30天原料但不将预热行写成派生观测，避免滚动导出边界撤销旧有效值。

`prices.query` 的投影 `provenance.retrieval.raw_ref` 对应 `original_digest` 保存的实际原件：reader 附件使用 `/measurements/seller_exhaustion_daily`，冻结快照使用 `/assets/BTC/datasets/seller_exhaustion_daily`。引用哈希绑定该位置的原值，不能用规范化后的结构替代原件。已存错误引用通过显式写操作 `prices.repair_seller_references`（`limit`、`after` 分页）追加修正；原观测、原获知时间和原引用不变，不增加数值修订。查询仅在 `as_of` 不早于修正获知时间时提供附加 `reference_correction`，其中 `corrected_ref` 可按 `original_digest` 解析。缺原件或原料不一致明确返回 `gaps`，不猜测修正，也不阻断正常市场分析。

研究 claim 的可选 model 字段记录当次请求选择，不是执行资格或实际型号证明。真实作者、输入、阶段、完成和结果摘要分别核验。按 issue/scope/phase/input 防重复，型号表示或选择变化不会重开已完成研究；保留原 claim 字节和历史用量。原生证据已暴露的运行配置如实记录，未暴露的保持未知。宿主发布包和 Docker service 都消费这份源码，必须分别安装并核对实际字节。
