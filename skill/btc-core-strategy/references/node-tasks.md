# 节点任务职责与切换验收

业务结论仍只由 `.strategy/v4/current` 的 DecisionSnapshot 拥有；研究请求、原始证据、投递日志和持续对象保留原 Owner。`btc_node` 的 `task.*` 只记录**执行义务及其原问题引用**，不能据此形成市场判断、确认交易或宣布报告已送达。

## 任务选择

数据节点是目标唯一调度源。采集与 reader 按数据实际发布时间、已收盘窗口及补漏机会运行；相同原始值或重取记录只更新来源/健康状态，不调用模型。模型嗅探的准入依据是已采纳结论的反证、机制、价值、风险、个人事实或前瞻检验条件可能受新证据影响；程序保存实际差异和来源，但不从阈值、单次触价、到期或普通风险变化推断市场意义。

Sol 对被准入的问题做当前证据、累积变化及相反解释的实际核验。到期复核不滚动延长原预测；真实新事实先检验原预期，再登记新的预期。只有首次无完整基线、已证实重大反证、原论点失去支持、重大遗漏或用户明确完整重评，才需建立/接续深评问题。新研究、原研究续接、映射修复和正式采纳各有不同阶段。东京自然日全系统最多启动一次 Astra 深评；失败、UNKNOWN、取消仍消耗名额，其他工作由 Sol 完成。复测不调用 Astra 或正式投递。

报告相对最后真实展示判断意义；重要变化、实际危险、纠错和日本每日反馈分别处理。普通观察可静默，静默不关闭研究和投资决策待办。报告发送仍由现有原生交付协议拥有，`task.finish` 的 DELIVERY 阶段只能引用已投影的 `DELIVERED` 记录；UNKNOWN 不自动重发。

## 节点执行状态

`task.submit` 以稳定 `task_id`、`issue_id`、固定输入摘要、物理发布版本、阶段、来源及业务理由登记 `NEEDED`；相同输入复用原记录。`task.assess` 把原问题与当前准入分开：依赖未齐、开放缺陷、未到实际检验窗口或当天深评名额不可用时为 `BLOCKED`，实际可做才为 `READY`。`task.start` 原子声明一次尝试，深评同时领取原 `model.deep.BTC` 日名额；`task.finish` 验证实际感知、复核、正式决策或独立研究原件后才完成，失败/未知锁止，`task.reconcile` 只核对原尝试，不重放外部动作。节点 writer 锁和 operation_id 包住全部变更。新版本不能在原任务身份下偷偷替换固定发布版本；历史与 UNKNOWN 状态不随升级重写。

节点启动许可分成两类。`task.start`/`schedule.claim` 在同一 writer 事务内复查任务到期、原依赖与受影响故障；直接 `model.deep.claim` 检查原问题身份、作用域缺陷和共享东京日闸门，不把任务依赖检查冒充为该入口已有能力。`DEEP` 是高等级研究，跨日 CLAIMED/UNKNOWN 仍锁止另一项新深评；其他任务是普通执行（含正式与隔离复测），同一时间只准一个普通 `RUNNING`。高等级研究与普通执行可以各有一个在途：先领取 `model.deep.claim` 再启动 Sol 嗅探，或 Sol 先在途再领取唯一深评，均沿各自入口的准入条件。已有 `DEEP RUNNING` 也不阻断另一项普通嗅探；两项普通任务或两项新深评不能并行。已完成的同日同问题、阶段、输入和模型的 `model.deep.claim` 返回 `REUSE`，只读原结果，不算新启动、不补发名额，即使普通任务正在运行也可读取；不同输入、FAILED、UNKNOWN、CANCELED 仍不能复用。报告实际投递仍由原生回执与 UNKNOWN 去重协议拥有，任务并行许可不授予重发权。

上述是**节点服务准入矩阵**。当前宿主 `node_scheduler_worker.py` 的单个 `flock` 覆盖一次 CLI 执行全过程，因此本适配器尚未证明能在其自身 DEEP 进程运行时同时拉起 Sol；通过手工原生研究入口取得的深评状态则不锁住节点普通任务。真实双进程执行能力与原目的地投递仍须单独隔离验收，不能把节点准入测试说成客户端退出后的完整闭环。

调用端重放同一个 `task.start` operation_id 只会得到 `RECONCILE_EXISTING_NO_LAUNCH`。失败任务不能因观察到新输入就自动重开；`task.repair` 需记录实际更改和验证引用，并保留旧尝试历史。研究任务沿已验证的 request、原独立输入和原问题身份投影；独立结果已存在时只标记原待办完成。此阶段仅在节点配置明确设置 `task_shadow_enabled: true` 时投影研究与组合待办；现有正式配置默认不启用。

`scheduler_mode` 默认 `OFF`；`SHADOW` 记录 reader 实际增量与原到期待办、评估依赖但绝不领取模型任务；`ACTIVE` 才能通过 `schedule.claim` 领取。显式启用 Compose `node-scheduler` profile 后，现有 `btc_node scheduler-loop` 在数据节点内按 `scheduler_poll_ms` 运行 `schedule.tick`，使用同一 MySQL writer、任务状态与故障闸门；宿主与客户端不登记时钟待办。`reader.checkpoint` 仅在真实投影插入后排队 SIGNAL，由节点时钟登记；同值重取不准入。节点时钟追回错过的原到期，保持原对象版本与期限；故障锁止后窗口不可用，必须核清原故障。DEEP 仅当原研究请求投影并明确启用 `scheduler_deep_enabled` 时进入可领取队列；REPORT 因原目的地运输未验证保持阻断。

reader 批次转成同一 MySQL 事务中的持久 SIGNAL 任务后，调度器清除该批次的待登记记录；重复输入由稳定任务 ID 查重。旧版已标记登记的批次只有在对应任务确实存在时才压缩，缺失任务直接锁止，不把待办从状态中静默抹去。

宿主 `scripts/node_scheduler_worker.py --config <现有 node.json>` 是默认关闭的模型执行适配器，节点任务日志和原领域原件仍为权威。它检查节点最近成功时钟窗口、使用同一 Docker 服务领取任务、宿主 `flock`、物理发布目录和本地 Codex CLI，**不调用 `schedule.tick`**；`schedule.tick/claim` 不向客户端 MCP 暴露，客户端可查看状态。同一任务只领取一次，CLI 的非临时 `--json` 会话须返回唯一完成的原生 thread 引用，原领域原件仍由节点核验；私有 `attempt_dir` 保存该映射和不确定结果的诊断证据，不另建模型操作权威。中断后原尝试保持 RUNNING 或 UNKNOWN，不因新轮次重放。宿主适配器的唤醒方式只负责进程存活与领取，不决定业务到期；选定前须验证桌面退出时的 CLI 登录、网络、工具及原生送达能力。`node_scheduler.enabled`、节点 profile 与容器 `scheduler_mode=ACTIVE` 均需在旧正式任务确实停用、投递可验证后才启用；不要把复测定时器当成正式触发。切换后 `runtime_schedule` 才读节点写出的新时间窗口，不再读旧 Codex 自动任务时间。

worker 在领取持久任务之前，还要核对本机 CLI 可执行、项目目录存在、当前物理发布可读取；本地执行器配置缺失时返回 `NODE_RUNTIME_UNAVAILABLE`，保留原 READY 任务及尝试额度。此检查只覆盖静态本机入口，不能证明登录、网络、业务工具或报告传输可用。

每次宿主入口取得原 `flock` 后，先从原 `task.status` 检查 formal 的 RUNNING/UNKNOWN attempt，再检查新领取所需节点窗口和本机执行器；原 release 已变化、节点没有新窗口或 CLI 不可用，都不免除消费原结果的责任。读取仅限本适配器原私有 attempt 目录，不搜索原生会话存储。原 claim 的问题、阶段、输入、证据、release 与 started_ms 必须逐一相等；原生事件须属于原调用的唯一 thread 和同一唯一 turn，按 thread.started→turn.started→turn.completed 顺序且结束后无新轮，显式 thread/turn ID 一致；缺开始、结束后新启动、交错轮次及 error/turn.failed 均不能完成。CLI未提供服务端turn ID时保留None与有序单轮的证据边界，不编造真实ID；最后结果须标识同一 task/attempt。随后调用原 `task.reconcile`，仍由原领域校验实际业务原件，不能从 JSON 自述或模型 final 签业务完成。已成功核清的一次入口不再领取新模型，下一次才按原准入继续。

新 attempt 在实际调用前先持久写入 revision2 claim 与 launch-intent，并 fsync 文件和父目录。只有本适配器锁已取得、revision2 claim 存在、launch-intent 与任何执行产物都不存在，才可记录明确未执行；旧 claim 缺 launch-intent、进程退出、超时或时间经过均不能推出未执行。已经花费的研究名额不退还。原生结果/持久效果仍未知则保留原 attempt，不启动替代模型；不影响无依赖的源码、取证和独立工作。

核清变更也先按原 attempt 以固定 `reconcile-intent.json` O_EXCL 写证据及固定 operation_id，再调用原节点；任何旧版 `reconcile-*.json` 意图也阻止自动重发。晚到事件或证据字节变化不生成第二个提交身份；响应丢失时下一入口只读原 task 状态，不能重新提交同一核清。仍未核清时使用保存的原完整请求调用原 `core.recover`，在同一 writer/operation 日志中核清；完整原请求缺失或旧版 journal 状态不明时保留待明确核清，不重放外部动作。该持久文件仅绑定原节点日志，不是另一套任务库。当前实现与隔离通过不证明原宿主接口可用、节点接管已采用或真实跨轮唤醒。

业务验收仍围绕同一原投资问题：按机制确定非日内主周期与适用方法，实际读取原结论、未决事项和新增事实；首次冻结与正式采用沿原 run_flow/forward_lifecycle/DecisionSnapshot，后续真实事实只在其实际支持范围形成后继，证伪以关联新版本修正并保留旧失败。不能把支持偏空的事实写作偏空的失效条件；此语义由原作者与领域独验核对，程序结构与测试数量不能代审。一次退出后的真实节点唤醒、后继接收和原报告消费须各有实际回执；旧客户端时钟只是迁移前事实，不能作为目标能力的最终依赖。

macOS `config/local.btc.node-scheduler.plist.example` 只是宿主适配器的一种唤醒候选，不是必选实现或合并条件。若选它，安装位置仅限该用户的 `~/Library/LaunchAgents`、域 `gui/<uid>`；当前 Docker Desktop 与 CLI 在用户会话中，退出登录时该入口不会继续执行。LaunchDaemon 不能假定获得用户会话资源；无登录运行须另行核验 Docker 与 CLI 身份，不能借用 root 或新持久凭据。模板默认 `Disabled=true`，一分钟唤醒仅轮询节点已登记的待办；睡眠或重启错过的业务窗口由节点持久任务扫描恢复。`flock` 和 MySQL writer/operation_id 防止并发领取。其他宿主唤醒方式也必须服从同一节点窗口与任务准入，不创建第二份业务时钟。

现有 `local.btc.host-watchdog` 会周期性打开 ChatGPT 桌面应用，不能把它视作独立模型/报告运输；桌面不可用验收必须在不依赖其重开的受控环境中进行。本源码不会停止该现有监控，也不会把它作为节点任务的调度器。

正式入口切换前，读取旧 `btc` Codex 自动任务的受支持状态并留存已停回执，再核验节点 profile、ACTIVE、所选宿主适配器与旧轮无 RUNNING；若旧任务仍 ACTIVE，新正式入口保持关闭。当前云端任务对 `codex_app.automation_update(view)` 的调用返回“Automations are only supported for local threads”；原主机上须由支持该工具的本地 Codex 任务或应用界面完成停用并读回。本阶段未触碰旧自动任务。

正式总体投资决策缺失时，同一 `portfolio-synthesis` 问题持续待办；revision 7 的旧决策不被伪装成 revision 8/9 完成。`Projection.refresh` 只从已验证正式结果投影完成状态；旧研究程序缺陷持续阻断受影响阶段，普通市场与报告工作按各自作用域继续。

## 迁移与验收

现有小时 Codex 自动任务在生产切换前保持原状。先用锁定真实数据做隔离回放，检查无增量跳过、实质信号进入正确阶段、研究问题身份和 blocked/ready 区分、模型预算、市场/组合隔离、前瞻验证、报告语义与身份来源、UNKNOWN 去重、故障锁止、并发与重启恢复、版本回滚和复测隔离。工程通过后仍须验证数据节点在桌面客户端不可用时具备 Sol 调用、所需工具与**原报告目的地的真实原生投递/回读**能力；仅有定时 shell 或 CLI 回答不算完整闭环。不能擅自增加付费 API、凭据或更换报告目的地。

满足以上能力后，在旧运行完成或安全锁止的窗口，通过受支持的入口停用旧 Codex `ACTIVE` 自动任务，读回停用状态，再启用节点唯一正式调度源。保留旧物理版本和状态供回滚；新旧调度不得同时推进正式业务。若投递能力或受支持的停用入口未验证，节点新入口只可 shadow/隔离运行，不能宣称切换完成。

原核清请求现在与固定operation_id一起在reconcile-intent中先持久化。响应未知时，后续入口先读task.status；仍需核清且有完整新意图时，调用原core.recover(original_request=原完整task.reconcile信封)。原服务在同一writer/事务屏障中核原ID及request digest：已COMMITTED只取原结果；没有记录则执行同一原领域核清并原子保存该ID。缺记录不被当作模型未执行；晚到原请求只能读取这一个提交，不能另改业务状态。此分支不执行core.execute、模型、报告、额度退还或另一attempt。旧per-proof、缺完整信封或身份不符的意图保留待原operation明确审阅，不自动猜参恢复。
