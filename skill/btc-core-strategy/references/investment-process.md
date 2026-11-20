# 完整投资决策及反馈

当前新研究和报告先读 [交易决策主线](decision-design.md)。周期论证与定价、相对机会、联合配置/纪律和时点报告由该主线及正常入口承接；本文旧模块和字段只保留仍有效的来源、接口与历史兼容约束，不作为新报告的固定生成架构。


本方法承担规划、选择、实施可行性和反馈的责任，不要求机构委员会、优化器、自动交易或固定模块清单。原 DecisionSnapshot/current 仍是唯一正式结论 Owner；三份本地确认文件仍是个人事实 Owner。参考 CFA [投资管理流程](https://www.cfainstitute.org/sites/default/files/-/media/documents/article/refresher-readings-free/rr-2018-l2v6r47.pdf)、[资产配置](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-asset-allocation)。

## 按月度组合作个人决策

用原政策入口承接当期已确认的月度预算、非数量约束及其事实来源。组合数量、成员、权重和用途取自当期有效配置。按同一冻结市场比较各组方案及其与留存、独立机会和存量的联动，候选比较结果汇入原投资决定。字段和兼容规则见[月度组合接口](v4-contract.md#月度组合决定与报告绑定)。

每组保留实际比较和事前窗口。用相同的可比价格起点和后续区间对照各组按原预算定投的方案，有多个可比组时再按等额投入比较修复、回撤和机会成本。把择组、金额及当时理由是否被实际进展支持分别说明。仅由投入更少减少的损失归为新增敞口变化。缺成交及成本时只核方案毛价格影响。实际进展后用原评价入口记录来源及结果，下一次个人评估读取原窗口和其反馈。

`short_position` 包含 `status`、`instrument`、`product`、`observed_at`、`source_refs` 和可空的 `entry_reference`。状态 `PRESENT/ABSENT` 来自用户明确确认及其时点，`UNKNOWN` 保留实际未确认。产品未知时填 null。缓存价格由用户告知，字段为 `price currency observed_at source_ref`，只在原来源与标的范围内使用。单独收到报价时保留仓位状态及其来源，仅替换该价格缓存；收到明确仓位变化时更新对应状态和时点，缓存的适用对象若已变化则在新版本清空，旧值留原版本。按原政策版本维护这两类输入。

有仓比较月度净补偿及反抽成本，无仓按无空头方案，状态未确认只给条件分支。没有缓存价可完成组级方案及空单前瞻分析。依据和内部比较留在投资决定，报告承接实际采用的组级计划和改变条件。

## 按开仓价基线的非执行方向分析

原事实/纠正 Owner 明确采用按价格分析、排除持仓数量时，在原 `investment-policy` 输入中记录可选 `position_analysis`，schema 为 `PRICE_BASELINE_DIRECTIONAL_V1`。它不是新账本、预算、账户确认或交易授权，不解析本方法 Markdown 作为规则。旧政策、原决定、历史数量和已过期限均保留原字节。

本 scope 只读正常 producer 的无量投影：独立冻结市场、最后已知产品/方向、平台**开仓价**、币种、台账时点、非数量约束和已确认的月度规划政策。持仓数量及其派生集中度、总体规模和账户绝对风险不参与方向决策。月度规划预算可用于提出投入金额；是否执行、可用资金、当月实际投入由用户自行评估。

`baseline` 精确字段为 `instrument product direction entry_price currency observed_at price_type time_basis time_precision source_refs entry_price_ref`。`price_type` 仅接受 `PLATFORM_OPENING_PRICE`；专用 `entry_price_ref` 须含确切价格标量，支持有结构的千位逗号，不允许 mark/现价替代。`observed_at` 是 `SOURCE_SNAPSHOT_TIME` 或 `SOURCE_UPLOAD_TIME`，按 `MINUTE/SECOND/DAY` 原精度保留；未知时两字段均 `UNKNOWN`、时间为 null，不将上传分钟叫成交时刻。源句因数量行移动时，只在同文件唯一逐字原句上重定位并保留原引用；改变或歧义回对应来源，不按同数字猜接。

`non_quantity_constraints` 每项为 `id kind status statement source_refs`；kind 为 `PRODUCT/PROTECTION/LIQUIDITY_USE/FINANCING/FORWARD_COST/PURPOSE`，status 为 `KNOWN/UNKNOWN`。KNOWN 须有真实源句，政策描述不自动升级为已确认约束。`source_level` 保留实际证据等级。完整 fact_version 与原政策版本仅作审计；数量/旧集中度文本变动不清空 mode/价格，不要求每次重录政策。显式后继 typed 政策优先，不回退被其替代的旧价；所选源实际冲突须准确回 Owner。

新 `investment_decision.analysis_scope` 同上，候选为真实 `execution_role=DIRECTIONAL_ANALYSIS`，产品仍是真实 `CASH_SPOT/LEVERAGED_DERIVATIVE/CROSS_MARGIN_DERIVATIVE`，不可假扮 `POLICY`。仍用市场路径、价格基线、准则和非量约束比较减、留、退并实际选择，程序不排序。新 gaps 的 `dimension` 只容上述非量类别及 `MARKET/DIRECTION`，个人源引用不得越过已披露源句。金融审阅须从输入反查自由叙述里的隐含数量依赖，不能用空 dependencies 或结构通过自证。

selected 仅为分析结论，action 执行状态仅 `NOT_APPLICABLE`（判断未决时 `UNDETERMINED`）；不得 READY、tiers、张数/金额、账户总 PnL、净/保证金收益率、杠杆安全或下单载荷。兼容的 account_constraints 仅保留同 fact_version、record_id=null 和三个 completeness=null，表示不在此分析范围，不形成未知量门槛；实现能力也不得藏进此 scope 的 ACTION gap。旧普通库存/新增资金 scope 的 exposure/cross_margin/funding 和实现能力门完整保留。允许有据的每单位**毛价差**或距离，不称账户实际/总/净收益；成本价和已盈利不是未来市场证据，不机械保本、止盈或止损。

正常个人 producer→决定→MAIN→报告都承接此 scope；新 reveal/MAIN 写入不能复制旧 policy 或省略 scope 绕回数量前置，旧存量版本仍按原合同读取。REPORT 默认消费 finished/decision_results，旧实际送达文仅 `DELIVERED_HISTORY_ONLY`；完整 context 只作程序绑定和按需技术审计。新个人结果未形成或已到期时，公开内容保留，个人回上游，不续旧建议。金融判断、批准、采用和送达各须真实完成。

正常 `investment-policy --input` 结构示意如下；均为占位值，须换实际 Owner 原句。入口生成 version/recorded_at/fact_version，不由调用方倒填：

```json
{
  "objectives": ["本轮实际目标"],
  "constraints": ["原始真实约束；不自动新增预算"],
  "baseline": "最后已知配置和原时点，不是当前账户确认",
  "unknowns": ["实际仍未知的非量事实"],
  "source_refs": [{"file":"纠正记录.md","line_start":1,"line_end":1,"quote":"实际模式要求原句"}],
  "position_analysis": {
    "schema":"PRICE_BASELINE_DIRECTIONAL_V1",
    "source_level":"实际来源等级，不是平台批准",
    "baseline": {
      "instrument":"实际合约标识", "product":"CROSS_MARGIN_DERIVATIVE", "direction":"NET_SHORT",
      "entry_price":"实际正数十进制串", "currency":"USDT", "price_type":"PLATFORM_OPENING_PRICE",
      "observed_at":null, "time_basis":"UNKNOWN", "time_precision":"UNKNOWN",
      "source_refs":[{"file":"资产配置.md","line_start":1,"line_end":1,"quote":"实际方向/产品/源时点原句"}],
      "entry_price_ref":{"file":"资产配置.md","line_start":2,"line_end":2,"quote":"含实际开仓价的专用原句"}
    },
    "non_quantity_constraints":[{"id":"用途","kind":"PROTECTION","status":"UNKNOWN","statement":"该非量关系当前未确认","source_refs":[]}]
  }
}
```

## 规划与研究的两套输入

投资者政策包含实际目标、持有用途、期限、承受能力及相关约束；资产、负债、税务、支出与现金流只按所问决定的真实相关性纳入。完整组合不是只看 BTC 两侧。先读取获准确认资料，不反复索取已存在事实，不查询私有账户。目标金额和比例不是已成交余额，保证金预留不是损失预算，分散用途不是已证明当前价格适合买入。

用 `investment-policy --input <政策JSON>` 在实际候选比较前记录政策：objectives、constraints、baseline、unknowns、source_refs。程序使用执行时点和确认文件版本生成 policy.version，不倒填事前时间；引用逐条含 file、line_start、line_end、原文 quote。它是输入记录，不是配置改变、账户事实或成交。已确认目标继续承接，未采纳修改保持建议。

独立市场研究不接个人政策或持仓。先说明主导机制、传导到相关资产前景的路径、已定价部分及竞争解释，区分事实、证据支持的推断和未验证猜测。宏观变量按机制发现和核验，不把用户举例固化为永久指标。因素 decision_review 记录 ADOPTED / OBSERVING / RETIRED、机制、判断影响、适用环境、核验和重复证据；退休有理由与历史，重新采用使用有据的新相关身份，不能自动复活。

每个已研究期限将 direction 与 momentum、price_attractiveness、strategy_applicability 分别交付。momentum.phase 是 CONTINUING / WEAKENING / REVERSAL_SIGNS / REVERSAL_CONFIRMED / UNKNOWN，由证据裁决，不由程序分类。原动力衰减、短暂反弹和相反趋势形成不是同一结论；方向与反转确认均可受不同证据限制。不固定4小时，不设ETF或宏观慢变量一票前置，不以单次价格或费率变化确定反转。技术区域不能充当可信终值或价值区。价格判断说明对应期限基准、净成本限制与改变条件；策略适用性回答明确策略问题，不能由偏多自动推出买入、由偏空自动推出追空。

## 在组合决定处汇合

本阶段须完成相关方案的实际比较和选择，不能把“条件允许才保留”“不匹配时应比较减险”当成已经完成的决定。比较继续持有的收益来源与反向风险、调整所减少和放弃的风险收益、退出或等待的代价；再说明当前证据支持哪项、哪项仍不能选择及缺少什么。以最后确认的动态整体配置作为有时点边界的决策基线，并把之后未核验范围单独标未知；未持有默认不得清除该基线中有确认依据的持仓。从结构位距离不推导减仓或兑现，也不以缺少数量免除可判定方案的取舍。

资产配置是动态的整体组合，不是一个静止目标，也不是只认最新截图的瞬时状态。个人阶段先把已确认的长期目标、最新一份用户确认的整体实际配置、其后有来源的变化与未报告字段按时间和范围合并，形成“最后确认状态截至何时、此后哪些范围尚未核验”的决策基线。不得把目标配置当作实际持仓；也不得因缺少实时回执或快照变旧而抹掉最后已确认的整体配置。明确的全组合变更替换其声明覆盖范围；局部成交或事实修订只更新对应资产/字段，未触及的已确认状态及原时点保留，未提供字段继续未知。没有平仓记录不证明仍开仓，缺少新快照也不证明已归零。

市场独立冻结后，完整消费个人事实与原消息/附件的字段、来源、时点、单位和未知。持仓纪律与资产配置强关联：将最后已知实际结构、目标、持有目的、市场适用性、风险和资金约束合成一个组合决定；持有、调整、退出或等待对组合方向、保护关系、净敞口和目标偏离的影响须放在一起比较。若方案调整实际组合，应说明这是否改变目标或只是暂时偏离；不能另外生成与总体配置冲突的持仓建议。公开市场方向仍独立形成，不由个人仓位改变。

事实变化先判断旧个人候选、约束与总体取舍哪些仍有依据、哪些需要重新比较；不能只把新字段附在报告末尾。当前状态未重新确认时，沿用最后已确认配置作为有时点边界的决策基线，不声称它是实时状态，也不把其中的已知持仓按零处理。未持有分析默认仅适用于该配置中没有肯定持仓证据的资产，不是覆盖全组合的清空操作。实际仓位规则未确认不禁止有据的条件提案，既有纪律、新建议与实施缺口分开；依赖实时数量或安全参数的操作保持未决，不编预算、收益风险比或订单。

本层完成实际相关方案、等待与机会成本、风险收益和条件比较，再把已形成的决定交报告。报告的[六项实质内容基线](mobile-report.md#决策内容基线及报告职责)不是要求报告层重新推导全部研究。既有周度研究、事件预警及其他来源按覆盖、时点、分歧和同源依赖参与核对；相关推送可触发原决策复盘，不新增跟踪调度。缺分析保留对应上游待办，不能靠成稿或工程验证补签充分性。

原在办运行继续其实际 workflow revision，个人修订可复用实际旧公开字段契约；复用部分逐字绑定原祖先、原证据及时间，不补造新研究字段；新公开研究按本轮接口提交完整论证。市场冻结后，由原研究个人阶段完成当前方案与相关候选比较，统筹收益来源、风险重叠、资本占用、流动性、净成本和机会成本，说明整体取舍及优先关系。候选由问题决定，不每次强凑持有、调整、保护、新投入、等待各一项；需区分战略配置、战术偏离、再平衡、保护目的及具体产品实施。收益机制可以重叠，资金、持仓和敞口只计一次。

既有 recommendations.portfolio 中保留 risk_assessment、strategy_advice，并用 investment_decision 汇合完成结果，不另立判断 Owner。它包含 policy、公开和事实绑定、decided_at/valid_until、status/summary、candidates、selected_ids、priorities、supported_judgements、gaps、issues、evaluation_windows、review_conditions。每个候选明确当前方案标记、scope/purpose/arrangement/verdict、实际 comparison、dependencies 与 validation。validation 明确 products、JUDGEMENT / METHOD_CHECK / RULE_EFFECTIVENESS / PORTFOLIO_EFFECTIVENESS、实际 record_ids、required_capabilities 与 limitations。comparison 的 return_sources/risk_overlap/capital/liquidity/net_cost/opportunity_cost/rationale 是比较责任，不是固定市场因子或正文栏目；无相关性时解释原因，不填口号。

候选 SUPPORTED / CONDITIONAL / REJECTED / UNDETERMINED 与总体 DECIDED / PARTIAL / UNDETERMINED 分开。净动作未决允许 selected_ids 为空，但仍交付有据判断及优先关系，不把空动作当安全持有。gaps 只保留影响决定的关键事实，说明 affected_candidates、QUANTITY_ONLY / ACTION / VALUE / FUNDING、实际后果及最小所需证据；候选与缺口的双向依赖一致。ACTION 缺口不能授权对应候选。实现缺口通过 capabilities 绑定具体保证金、跨保证金或融资能力；未验证能力不能形成无条件选中候选。数量受限候选可以保留 CONDITIONAL 方向，但不能产出 READY 建议或价格数量批次。每个已决定建议通过 investment_candidates 指向总体实际选中候选；个人DECIDED actions 另以 implementation_products 指定实际产品，必须在所选候选的 validation.products 范围内。upper_short 固定要求衍生品候选，lower_spot 固定要求 CASH_SPOT，研究政策或现金验证不能授权空单。RESEARCH_OR_POLICY动作只允许PAUSE/RESUME/ADJUST与NOT_APPLICABLE执行状态，不能承载OPEN/CLOSE等交易或READY。未支持的具体实现能力限制READY，即使误标VALUE缺口也不能生成价格数量批次。数量受限保留有依据的方向，公开价值缺口不得归咎于账户；已有保护既有下跌缓冲，也有反弹、费用和保证金风险，不能凭部分名义额宣布过度或充足。

issues 沿原问题身份逐项形成 RESOLVED / REPLACED / PENDING_FACTS / NEEDS_RESEARCH / NOT_APPLICABLE；说明理由，待事实明确下一证据，替代保留存在的 successor 问题及无环链，历史配置动作、风险动作、策略建议及其未决或受限范围也逐项接续。观察 issue_reviews 接续所有旧未决，不以复核结束、required=false或字段未变关闭旧问题。分析未完成返回原上游研究/采纳流程；已有事实缺口可保留有界复核，不强迫每小时全量研究。过期判断不由报告时钟续期，旧可执行安排不恢复；正式MAIN完成实际时点再次核对总体、风险与可执行两侧的原期限；总体决定到期同时限制两侧价量与DECIDED/READY动作，报告接口只留原判断及待复核问题，ALLOCATION/PLAN期限和到期队列均承接总体期限。evaluation_windows 在决定时记录所问问题、未来窗口、支持及证伪条件，事后反馈不移动原端点。

报告消费 investment_decision 的 summary/priorities/supported_judgements 与具体未决影响，当前同事实且总体未到期时另传递已形成的 portfolio_narrative 及哈希，供完整个人表达直接绑定；到期或事实变化则不传旧叙述，完整比较保留内部；同一接口继续传递风险、分项机会、公开结果及当前适用性。方法核验与未支持能力在上游完整保留，只有实质影响当前决定或实施的限制才通过具体limitations、unsupported_capabilities或对应缺口的consequence/required_evidence表达其后果；候选编号、依据标签或缺口编号不能充当已披露限制。自审逐句核对方向与拐点、关键量价/机制、价值与策略适用性、总体取舍及传递完整性。缺分析返回上游，不能在成稿重研；缺表达才改正文。旧建议期限等内部过程只在防止具体误用时说明对应后果和补救，不把未决清单当结尾。

## 组合账本与实际绩效

`ledger-append` 只追加三份确认文件的 source-bound views，记录原始币种、事实时点、实际获知与存档时点、来源版本及原文，保留修订历史和并发预期版本，整批预检后通过唯一index提交；读取仅沿index已提交版本链，忽略中断留下的孤立版本，不修改 position_fact。NAV、外部现金流、成交、费用、融资、持仓、现金、负债和贡献资料分别表示；缺量、成本和时点保持 null。报价、触价、建议或模拟不能成为成交；历史市值不能成为买入成本。当前读取不伪装成历史时点已知。

`portfolio-performance` 仅在完整组合期初/期末NAV、费用融资覆盖、完整外部现金流与币种成立时计量净收益；时间加权收益另要求现金流对应NAV检查点及明确时序。净收益、时间加权收益、资产/策略利润贡献和因果归因分别说明。现有最小贡献计算是相对期初资本的PnL贡献，不冒称TWR分解、配置/选择因果归因或经理技能。缺历史不补零；外币现金流无确认换算就停止。完整收益计量也不证明策略有效。见 [CFA绩效评价](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/portfolio-performance-evaluation)。

## 按策略类型验证

规则化策略先用 `strategy-rule-record` 按真实执行时点记录规则，再留目标、可得数据与成本、融资及资本约束，再作历史回测、留出/滚动及敏感性；防止未来数据、参数选择和幸存者偏差。`strategy-backtest` 的最小可运行实现为 CASH_SPOT_LONG_FLAT_MEAN，固定输入规则、仅用执行前已知的历史信号、下一期实际开盘价格、明确源值成本、资金上限、退出成本及成本敏感性；它是模拟，不是新采用策略。实际存档规则早于所声明测试窗口时才记 ACTUALLY_RECORDED_BEFORE_DECLARED_TEST_WINDOWS；仅输入中的历史 recorded_at 不证明事前归档，保持 DECLARED_INPUT_NOT_HISTORICALLY_ARCHIVED，也不冒称盲测。没有成本或当时可知历史时为 DATA_LIMITED，不按零；结果由实际程序写入不可变 investment/validations，带 CASH_SPOT、FULLY_FUNDED、METHOD_CHECK_ONLY 边界；组合只能引用已记录结果；METHOD_CHECK只能绑定实际完成的 SIMULATED_NOT_EFFECTIVENESS_VALIDATED 记录并保留 CASH_FUNDING/DECLARED_EXECUTION_COSTS 能力，DATA_LIMITED 只作为缺口反馈，不能称已完成方法核验。现金现货验证不能用于杠杆或不同产品候选，不能授权 RULE_EFFECTIVENESS / PORTFOLIO_EFFECTIVENESS 采用。未实现的保证金生存、跨保证金、资金费、产品结构等分别通过候选所需能力、具体 gap 和执行状态约束对应采用及建议。报告必须消费总体选择，并承接有影响的 validation_limits 或 gap；正文不得把方法验证写成含杠杆保护组合有效。留出窗口之外不自动证明数据集独立，滚动窗口不冒称已优化参数。见 [CFA回测与模拟](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation)。

判断研究用 `judgement-evaluate` 对原正式决定的 evaluation_windows、事前机制和反证条件形成 REALIZED / FAILED / PARTIAL / EVIDENCE_LIMITED / NOT_DUE，绑定后续实际证据；到期不能仍以NOT_DUE结案，不更新原窗口。下一个人复核读取 ledger 实际已提交视图、版本、原始数值/币种/事实与获知及归档时钟，并按需使用 portfolio-performance 的币种/起止/as_of 入口，不用条目计数代替绩效反馈；同时读取实际规则输入、方法验证记录与判断反馈；原 ALLOCATION 私有投影携带对应来源依赖，不新增判断 Owner。个人复核，定位研究、组合映射或实施的改善及退化。方向判断可独立复核，完整投资效果仍需要实际成交和收益资料；不把所有观点强制数字化回测。

工程CI、报告回放、锁定样本语义回归、未用过样本及前瞻投资效果分别验收。模拟表现、策略建议、真实投资结果有独立 result_kind；无完整数据不虚报长期有效。本次代码候选在用户报告验收前不切生产 current，不改调度，不额外真实发送。


候选区分 POLICY、INCREMENTAL、INVENTORY_MAINTENANCE、INVENTORY_ADJUSTMENT 与必要独立风险响应。长期参与/保护用途只是一项政策，不表示当前数量、覆盖或保证金安全。完整敞口和全仓状态未知可改变存量调整方向；不存在获证约束时，不把 current 方案或 HOLD 选成已决定安排。新资金是否投入还依赖真实资金及组合约束。

account_constraints 按当前确认事实版本及已提交 RISK_CONSTRAINTS 视图取用。NAV、盈亏、目标金额、过往截图和方法模拟均不证明这些约束；后续成交及资金事项使旧完整性视图失效。新评价按机制进展维护，不复制旧预测终点；确有用户确认的日期约束时引用该原文，而不是补造时间。原账本、净成本、现金流、真实绩效与回测能力继续保留，个人反馈不能替代公开周期验证。

过程评价窗口 `end=null` 时，沿原决定和原窗口提交 `process_progress={status,explanation}`：实际进展尚未发生使用 `NOT_OCCURRED`；已有可观察进展使用 `OBSERVED`；资料不能判断是否发生使用 `UNKNOWN`。只有前者可记 `NOT_DUE`；有进展但兑现证据不足记 `EVIDENCE_LIMITED`，不能凭没有日期免评。日期窗口原到期规则仍保留。模型根据来源决定进展与结果，程序只核绑定，不自动给市场方向、排序或权重。下一输入保留同政策历史反馈及每条实际原决定、原窗口，并明确是否属于当前决定；不能继承别次决定的 `REALIZED`。
