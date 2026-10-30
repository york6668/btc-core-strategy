# Docker 持续分析

共同定义、当时可知证据、判断生命周期与分层验收统一按[判断生命周期与术语契约](decision-lifecycle.md)；本文件承接其对应阶段，不另设相冲突术语或固定指标阈值。

本契约覆盖当前全部研究资产与个人组合；领域用于区分业务语义，不是固定研究清单。方法由本仓库维护，服务只保存方法版本，不解析 Markdown 生成交易规则。部署与恢复见仓库 `service/README.md`。

## 每轮接续

固定发布版本后仍沿原 `market → allocation → observe → report`。节点开启 Docker 时，原 CLI 通过宿主适配层提交给核心服务，服务不可用时保留原 operation_id 恢复，不能在宿主另写一份状态。进入、模型、Keychain、原生发送和助手回读留在宿主。

`run-prepare` 等返回的 `continuity.public_context` 包含全部有效公共对象及未决依赖复核。与本轮公开材料一起读取，按新增事实复核原对象，并主动发现对象之外的新问题。市场冻结后才读取 `continuity.personal_context`；其中 market_binding 绑定独立市场结果，个人材料不能反写市场判断。事实变化只复核相关配置，复用仍有效的市场结果。不存在 context 路径时查询服务，不将缺失理解成没有待办。

接续清单按固定对象版本、digest 与 content_path 引用不可变原文，未变化分析不在每轮复制。须读取有效对象和待复核条件所需的 content_path，或以清单中的 version 调用 object.get，不能仅凭清单元数据作判断。

宿主统一入口 `btc-core data` 从 stdin 接收 JSON；项目 BTC MCP 提供同名能力（点改为下划线）。例如 `{"method":"conditions.due","params":{"scope":"PUBLIC"}}`。写操作提供稳定 operation_id；重试复用相同 ID 和输入。读取分页返回 next 时继续读取，不能把第一页当作完整范围。

故障复核使用 `failure.get(document_key)` 读取当前原文与关闭版本；只有核实同一故障已恢复后，才用 `failure.resolve(document_key, expected_digest, expected_version, resolution_operation_id, reason)` 绑定成功核心恢复或 evidence-bound acceptance.repair。未确认恢复继续 OPEN，不能凭文件日期、另一次运行成功或 UNKNOWN 投递的等待状态关闭。关闭后的过程文件保留 14 天并继续接受引用与回收缓冲保护；同路径新原文不能继承旧关闭结论。

查询 PERSONAL 的 object.get/list/history 或 conditions.due 同样提供已冻结的 market_binding；不能通过直接对象查询提前读取个人材料。

`object.get` 的身份参数是 `kind`、`object_id`、`scope`，可加 `version` 或 `as_of`；例如 `{"method":"object.get","params":{"kind":"FACTOR","object_id":"已登记的对象ID","scope":"PUBLIC","version":1}}`。`product` 是 `object.list` 的筛选参数，不是 `object.get` 的身份参数；不能省略 kind 或从产品猜测对象种类。`object.history` 使用 kind/object_id，PERSONAL 历史也必须带本轮 market_binding。缺字段、类型错误和不支持的参数返回具体方法和字段；修正该次读取后继续原冻结阶段，不自动改绑、重建对象或放宽个人阶段限制。

CLI `continuity.market_binding` 仅表示本轮已冻结市场结果，与 `continuous-personal.json` 一致；尚未冻结时为 null。`formal_market_binding` 是旧正式核心基线，不能代替本轮个人阶段绑定。

1. `prices.products` 发现实际产品；`prices.query` 指定产品、序列、事件窗口和 as_of。不同来源、现货、指数、合约、代理、单位均保留身份，首次获知时间和事件时间分开。辅助采集仍是 UNTRUSTED_LEAD，需要独立当前来源核验；无产品、单位或接收时间的旧材料留原文并暴露投影缺口，不猜测补齐。
2. `object.get/history` 接续稳定对象；`object.put` 以 expected_version 提交证据绑定的观察或判断，未变内容复用。区域、价格行为、周期、前瞻、因子、配置、方案、研究、纠偏分别保存业务字段。突破与历次回踩关联原区域；触价、确认、可执行建议、用户采纳、实际成交分开，程序不推导结论。
3. 重要对象保留资产与周期、首次识别时间、证据及版本、支持和反证、重要变化、下一复核条件、依赖和替代关系。退出/撤回不能以旧锚保留为由恢复；新对象显式关联替代的退休对象。
4. `conditions.due` 同时列出到期与依赖变化。旧前瞻期限到期必须实际评价原窗口，在 `REVIEW` 保存 subject（含原 version）、outcome、explanation 与证据；只有 REALIZED / FAILED / REVISE 的实际结果复核才能改原期限。PENDING / EVIDENCE_LIMITED 仍有待办，不能滚动延期或解除对应依赖复核。`review.resolve` 只接受覆盖对应 requirement 和当前版本的有依据实际复核。`REVIEW` 保存某次复核结果，程序按历史记录处理；未决工作仍挂在原 subject，不对历史复核再创建复核。默认活跃上下文不列历史 REVIEW，按需用 `object.list` 显式指定 kind=REVIEW、active=null，或按 ID/version 读取原结果；旧 active 标志和误生成的复核关系保留可查。来源纠错仍沿历史复核关系传递到实际活跃消费者。未进报告的条件同样维护。
5. 个人阶段先核对三份确认文件的 exact fact_version，再用 `facts.project` 提交带文件、行范围和原文 quote 的持仓、操作、目标、偏好与约束视图；模型实际核对语义。派生估值、资金占用、保证金、名义敞口和风险属于 ALLOCATION，不能写成持仓事实；UNKNOWN 保持未知。建议存 PLAN，价格或模型判断不修改确认文件。正式方案按采纳决策与建议名称共同标识，重复读取同一正式方案复用对象。新正式方案替代旧对象，不替旧方案延期或推断结果；旧 ID、原期限和未决复核保留，`conditions.due` 也返回退休对象尚未解决的复核。旧的仅按建议名称标识的对象保留为历史，不能将其混合期限用于新方案。
6. 研究沿原 issue/request/progress 继续，复用完成成果及正式采纳关系。纠偏绑定受影响对象与证据版本，复核依赖项并保留有效部分。DELIVERY 只能由既有真实发送和助手回读协议投影，MCP 不能直接标为已送达。UNKNOWN 沿原请求核对，不能重放旧正文。投影的 recovery 与核心恢复分类一致；只有 WAIT_FOR_NEW_EVIDENCE 的历史项不进入默认活跃上下文，active 和原 UNKNOWN 仍保留，可用 object.get/history 或显式 kind=DELIVERY 查询。新主动尝试仍正常列出，等待项无变化不回读或重复通知。

报告消费已完成的市场判断、配置结果和重要业务变化；对象维护不依赖报告出现次数。旧 v4 字段仍由原 validator 验证，结构化服务补充跨轮次查询及未决条件，不把旧混合正文标作独立市场结论。

动量与反向趋势沿同一市场对象及版本接续：实际核验原驱动、价格反应、反向延续、反证与确认程度，记录相较上次变化和下一可区分证据。枯竭迹象不自动生成反转结论，反转失败保留原判断及修正原因，资料缺口保持具体未知；价格风险收益和个人风险仍分别核验。已有充分研究复用真实版本，关键遗漏或反证沿原研究接续，不能因方法更新重复建任务或改写历史。

卖竭沿[卖竭分析方法](seller-exhaustion.md)进入当前研究和持续对象：从冻结 raw_ref 核对日值、三路原料、获知时间与修订，结合现货需求、价格结构、原论点和反证记录采用、未采用或证据不足的理由及下一验证条件。三路原料来自同一来源族，不算三个独立核验；维持 UNTRUSTED_LEAD 与 analysis_eligible=false。以 prices.products 发现 method/来源身份后用 prices.query 的 as_of 查询当时已知版本，不以今天的历史修订替换过去判断。指标变化本身不触发深评或报告，实质影响判断时才在正常报告解释。

reader 检查点按原附件接收时间增量投影已验证的历史序列；模型只取最新附件不再决定哪些价格修订能持久保存。历史回补保留原接收时间、原文与旧冻结，不改写已有修订；同值的迟到材料按当时可见的前一版本去重，避免将每份历史附件复制成一轮新价格。该历史查询不改变 reader 的最新材料选择：最新缺口仍是缺口，不回退旧附件作为当前证据。检查点和 project 返回 reader 历史待处理数及缺口，积压未清或缺口未核对时不能宣布历史覆盖完整。

## 生命周期与验收

公开嗅探和研究输入的 `prospective_feedback.research_projection` 是原 claim/freezes 的只读派生元数据，格式为 `BTC_RESEARCH_PROJECTION_V1`。它保留原预测 hash、首次登记、原期限、来源/条件摘要及实际冻结内的复核结果；已有 claim 与注册不重写。`as_of` 使用本轮真实知识截止，未来登记/未来复核不进入视图；复核的作者时间不能替代其冻结持久时间。同一旧 claim 的新增复核沿现有 DecisionSnapshot 或 sensing observation 的身份、内容 hash、公开评估及持久时间绑定，`source_owner` 只接收这两种原身份，不接受任意文件路径。无法绑定时明确 `REVIEW_PERSISTENCE_UNVERIFIED`，不补造前瞻证明；未提供后续 Owner 时不凭输入中的成功标签授予记录。日期型预测只允许原期限内的实际字段观测提供支持，期限后取得的旧期内观测仍按真实发生/获知时间判断。

原失败不能因下一轮省略 reviews 而消失。校验按本轮相关原 claim hash，从原首次登记冻结，以及已有 versions、sensing、research 的内容身份绑定记录中读取当时已持久化的 FORWARD_OBSERVATION 失败；后续同 claim 的复核不要求再登记一次 claim。旧记录不修改，不新建索引或数据库；已保存失败只撤销对应原组件的支持资格，不凭历史标签生成成功授予，也不要求把旧原料复制进当前证据包。失败原预测不能重新成为成功的原预测或该失败组件的已验证前提。新版本、明确 FACT_CORRECTION、独立新事实及风险响应仍按原规则校验；未失败组件可在真实支持范围内授予。若直接前提已失败，prior_check 必须实际 FAILED 或 REVISE，不能通过遗漏复核将其变成 REALIZED。

实际冻结按原生产者读取：run_flow 的 payload.market_assessment 与 result_hash、research 的 market_result.outlook.market_assessment、Decision 的 assessment.outlook.market_assessment、sensing 的 review.market_assessment 使用同一读取边界。每次原 register 调用都将实际冻结原字节追加到既有 freezes，包括原 claim 不变的后续复核；首次 claims 登记及原Owner不改。run工作目录清理后仍可从该原归档恢复，不扫描任意工作目录。保留迁移 assessment=null、旧版混合正文及没有 lifecycle 的旧字段为未评估/事后历史，不补造前瞻；未知、歧义、损坏或缺少原持久时钟的公开冻结明确报缺口，不能默认为没有失败。升级前已持久化的原生版本、观察与研究结果仍按原身份读取；未归档且已不在可核清单内的旧外部run不能凭空发现，真实节点采用前必须核原完整历史与恢复集合，离线通过不证明这项完整性。

脱敏投影也从同一原Owner读取所选预测的已保存失败，保留其 review_hash、原持久时间和失败Owner摘要，并按同一复核去重；不因新输入省略而只展示成功。损坏或无法读取的历史明确 HISTORY_PERSISTENCE_UNVERIFIED；校验不能在该缺口下重新授予相关前提。失败证据的持久时间晚于查询截止时不提前进入历史视图；独立、未关联的新研究不消费该缺口。

该元数据只包含固定枚举、时间、数量及摘要身份，不复制自由叙述、原始报告、账户、节点路径或原始来源标识。`recorded_reviews` 只表示原记录内容，`RECORDED_NOT_REVERIFIED` 不证明本次重新核验市场事实；方向、归因与时间仍由原 claim/REVIEW 证据逐项解释。旧未注册记录保持 `POSTHOC_ONLY`，无本轮截止的到期状态为 `UNKNOWN`。

仅这个子对象适合作为后续最小脱敏读取契约的输入；完整 `prospective_feedback` 和其他上下文仍是本地内部资料。此实现不发布数据、不提供跨端连接器或读写权限、不替换原历史/数据库。`external_read_verified=false` 必须保留；只有真实 ChatGPT 宿主通过已授权且受支持的只读入口读到同一记录后，才能在独立验收中说明该实际读取。未完成时保持 UNKNOWN，不改变原定时研究或发送任务。

证据新鲜度、判断适用期、建议有效期、存储保留期各自独立。缓存/导出 24 小时；已完成运行 7 天；已关闭故障 14 天；普通原文及细于小时观测 30 天；小时序列 400 天；日线、正式决策、重要变化、结果复核、事实版本、最终报告和确认长期保留。活动对象和未决任务的必要证据受引用保护，回收有 48 小时缓冲。普通过程材料须先确认用途结束；未回踩长期锚不按年龄删除。

切换后 `acceptance.start` 从下一个日本完整自然日开始计七日。每日 `acceptance.window` 提取上一完整日的实际输入、状态变化和消费结果，由模型逐项审查后 `acceptance.record`；缺陷登记 `acceptance.defect` 并重验受影响窗口。没有自然发生的场景写 REPLAY_ONLY、实际回放证据与 natural_gap；没有实际 observe 的日不能通过运行链项。七天到期、容器健康及测试通过都不自动关闭验收。GitHub、本机、云端发布、全新云端任务及自然运行分别确认。

## 固定 PUBLIC 快照的模型读取视图

全量 inventory、不可变对象与每条未决要求继续保留，读取体量不能作为删除、解决或后移义务的理由。正常 PUBLIC 接续可用本项目 `scripts/continuous_public_read.py` 对已经固定的 `continuous-public.json`、对应版本的 PUBLIC `object.get` 原回执及本轮已完整读过的 PUBLIC read_set 生成有界视图；该工具只读本地原件、写读取材料，不联网、不采集、不投影新业务状态。`--reader-scripts` 可明确指定本轮已安装的读取实现，其代码散列与修正工具散列分别记录，不能把本地新工具说成旧引擎已经发布。

工具的 native 视图沿用 `material_reads.make(compact=True, base=<同一作者已读的本轮PUBLIC read_set>)`，保留全部对象 payload、生命周期、来源和依赖。新作者未读该 base 时须先完整读取，不借另一作者或上一轮的读取证明。要求视图按真实 subject 和原 reason 分组；`causes` 保留每个实际 cause_key/cause_version，`opened_ms_values` 保留每个实际开启时钟，组内 `entries` 的三列依次是原行序号、cause 索引和时钟索引。所有差异及独有业务字段仍在 required 材料；相同默认字段只在实际逐值相同时复用。仅 opaque 的 requirement_key 可放身份目录，原行号一对一绑定，完整要求能够逐值还原。分组和目录不是语义复核或解除凭据，模型仍须逐项解释实际变化及其判断影响，证据不足保留具体未知。

未决要求可能关联已经退休、未列在活跃对象索引的主体。沿既有 `scope=PUBLIC object.list(active=null)` 或 `conditions.due` 只发现稳定 kind/object_id，再用原运行的 as_of/版本取得原文；当前发现状态不替换原快照。工具显式保留 `subject_gaps`，不把缺原文当无义务，不自动 resolve。PERSONAL 查询仍须独立市场冻结，不能借读取视图提前进入。

生成后的 manifest 给出正常 `run-read --run <原run> --read-set <返回read_set> --cursor 0` 所需路径。按实际 next_cursor 读到 complete 并完成语义审阅；展开相等、分页完成和工具返回成功分别只是对应机械证据，不能替代模型已理解、市场候选或正式业务完成。原始输入、旧视图、失败与真实时钟不覆盖。
