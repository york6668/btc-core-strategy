# Docker 持续分析

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

证据新鲜度、判断适用期、建议有效期、存储保留期各自独立。缓存/导出 24 小时；已完成运行 7 天；已关闭故障 14 天；普通原文及细于小时观测 30 天；小时序列 400 天；日线、正式决策、重要变化、结果复核、事实版本、最终报告和确认长期保留。活动对象和未决任务的必要证据受引用保护，回收有 48 小时缓冲。普通过程材料须先确认用途结束；未回踩长期锚不按年龄删除。

切换后 `acceptance.start` 从下一个日本完整自然日开始计七日。每日 `acceptance.window` 提取上一完整日的实际输入、状态变化和消费结果，由模型逐项审查后 `acceptance.record`；缺陷登记 `acceptance.defect` 并重验受影响窗口。没有自然发生的场景写 REPLAY_ONLY、实际回放证据与 natural_gap；没有实际 observe 的日不能通过运行链项。七天到期、容器健康及测试通过都不自动关闭验收。GitHub、本机、云端发布、全新云端任务及自然运行分别确认。
