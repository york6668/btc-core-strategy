# 完整投资决策及反馈

本方法承担规划、选择、实施可行性和反馈的责任，不要求机构委员会、优化器、自动交易或固定模块清单。原 DecisionSnapshot/current 仍是唯一正式结论 Owner；三份本地确认文件仍是个人事实 Owner。参考 CFA [投资管理流程](https://www.cfainstitute.org/sites/default/files/-/media/documents/article/refresher-readings-free/rr-2018-l2v6r47.pdf)、[资产配置](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-asset-allocation)。

## 规划与研究的两套输入

投资者政策包含实际目标、持有用途、期限、承受能力及相关约束；资产、负债、税务、支出与现金流只按所问决定的真实相关性纳入。完整组合不是只看 BTC 两侧。先读取获准确认资料，不反复索取已存在事实，不查询私有账户。目标金额和比例不是已成交余额，保证金预留不是损失预算，分散用途不是已证明当前价格适合买入。

用 `investment-policy --input <政策JSON>` 在实际候选比较前记录政策：objectives、constraints、baseline、unknowns、source_refs。程序使用执行时点和确认文件版本生成 policy.version，不倒填事前时间；引用逐条含 file、line_start、line_end、原文 quote。它是输入记录，不是配置改变、账户事实或成交。已确认目标继续承接，未采纳修改保持建议。

独立市场研究不接个人政策或持仓。先说明主导机制、传导到相关资产前景的路径、已定价部分及竞争解释，区分事实、证据支持的推断和未验证猜测。宏观变量按机制发现和核验，不把用户举例固化为永久指标。因素 decision_review 记录 ADOPTED / OBSERVING / RETIRED、机制、判断影响、适用环境、核验和重复证据；退休有理由与历史，重新采用使用有据的新相关身份，不能自动复活。

revision 8 每个已研究期限将 direction 与 momentum、price_attractiveness、strategy_applicability 分别交付。momentum.phase 是 CONTINUING / WEAKENING / REVERSAL_SIGNS / REVERSAL_CONFIRMED / UNKNOWN，由证据裁决，不由程序分类。原动力衰减、短暂反弹和相反趋势形成不是同一结论；方向与反转确认均可受不同证据限制。不固定4小时，不设ETF或宏观慢变量一票前置，不以单次价格或费率变化确定反转。技术区域不能充当可信终值或价值区。价格判断说明对应期限基准、净成本限制与改变条件；策略适用性回答明确策略问题，不能由偏多自动推出买入、由偏空自动推出追空。

## 在组合决定处汇合

原在办运行继续其实际 workflow revision，个人修订可复用实际旧公开字段契约；复用部分逐字绑定原祖先、原证据及时间，不补造新研究字段；新公开研究仍完整填写 revision 8。市场冻结后，由原研究个人阶段完成当前方案与相关候选比较，统筹收益来源、风险重叠、资本占用、流动性、净成本和机会成本，说明整体取舍及优先关系。候选由问题决定，不每次强凑持有、调整、保护、新投入、等待各一项；需区分战略配置、战术偏离、再平衡、保护目的及具体产品实施。收益机制可以重叠，资金、持仓和敞口只计一次。

revision 8 在既有 recommendations.portfolio 中保留 risk_assessment、strategy_advice，并用 investment_decision 汇合完成结果，不另立判断 Owner。它包含 policy、公开和事实绑定、decided_at/valid_until、status/summary、candidates、selected_ids、priorities、supported_judgements、gaps、issues、evaluation_windows、review_conditions。每个候选明确当前方案标记、scope/purpose/arrangement/verdict、实际 comparison、dependencies 与 validation。validation 明确 products、JUDGEMENT / METHOD_CHECK / RULE_EFFECTIVENESS / PORTFOLIO_EFFECTIVENESS、实际 record_ids、required_capabilities 与 limitations。comparison 的 return_sources/risk_overlap/capital/liquidity/net_cost/opportunity_cost/rationale 是比较责任，不是固定市场因子或正文栏目；无相关性时解释原因，不填口号。

候选 SUPPORTED / CONDITIONAL / REJECTED / UNDETERMINED 与总体 DECIDED / PARTIAL / UNDETERMINED 分开。净动作未决允许 selected_ids 为空，但仍交付有据判断及优先关系，不把空动作当安全持有。gaps 只保留影响决定的关键事实，说明 affected_candidates、QUANTITY_ONLY / ACTION / VALUE / FUNDING、实际后果及最小所需证据；候选与缺口的双向依赖一致。ACTION 缺口不能授权对应候选。实现缺口通过 capabilities 绑定具体保证金、跨保证金或融资能力；未验证能力不能形成无条件选中候选。数量受限候选可以保留 CONDITIONAL 方向，但不能产出 READY 建议或价格数量批次。每个已决定建议通过 investment_candidates 指向总体实际选中候选；个人DECIDED actions 另以 implementation_products 指定实际产品，必须在所选候选的 validation.products 范围内。upper_short 固定要求衍生品候选，lower_spot 固定要求 CASH_SPOT，研究政策或现金验证不能授权空单。RESEARCH_OR_POLICY动作只允许PAUSE/RESUME/ADJUST与NOT_APPLICABLE执行状态，不能承载OPEN/CLOSE等交易或READY。未支持的具体实现能力限制READY，即使误标VALUE缺口也不能生成价格数量批次。数量受限保留有依据的方向，公开价值缺口不得归咎于账户；已有保护既有下跌缓冲，也有反弹、费用和保证金风险，不能凭部分名义额宣布过度或充足。

issues 沿原问题身份逐项形成 RESOLVED / REPLACED / PENDING_FACTS / NEEDS_RESEARCH / NOT_APPLICABLE；说明理由，待事实明确下一证据，替代保留存在的 successor 问题及无环链，旧revision 6未决或数量受限的 allocation actions、revision 7风险动作/策略建议及未决或受限两侧也逐项接续。观察 issue_reviews 接续所有旧未决，不以复核结束、required=false或字段未变关闭旧问题。分析未完成返回原上游研究/采纳流程；已有事实缺口可保留有界复核，不强迫每小时全量研究。过期判断不由报告时钟续期，旧可执行安排不恢复；正式MAIN完成实际时点再次核对总体、风险与可执行两侧的原期限；总体决定到期同时限制两侧价量与DECIDED/READY动作，报告接口只留原判断及待复核问题，ALLOCATION/PLAN期限和到期队列均承接总体期限。evaluation_windows 在决定时记录所问问题、未来窗口、支持及证伪条件，事后反馈不移动原端点。

报告消费 investment_decision 的 summary/priorities/supported_judgements 与具体未决影响，当前同事实且总体未到期时另传递已形成的 portfolio_narrative 及哈希，供完整个人表达直接绑定；到期或事实变化则不传旧叙述，完整比较保留内部；同一接口继续传递风险、分项机会、公开结果及当前适用性。方法核验与未支持能力必须通过具体limitations、unsupported_capabilities或对应缺口的consequence/required_evidence进入正文；候选编号、依据标签或缺口编号不能充当已披露限制。自审逐句核对方向与拐点、关键量价/机制、价值与策略适用性、总体取舍及传递完整性。缺分析返回上游，不能在成稿重研；缺表达才改正文。旧建议期限等内部过程只在防止具体误用时说明对应后果和补救，不把未决清单当结尾。

## 组合账本与实际绩效

`ledger-append` 只追加三份确认文件的 source-bound views，记录原始币种、事实时点、实际获知与存档时点、来源版本及原文，保留修订历史和并发预期版本，整批预检后通过唯一index提交；读取仅沿index已提交版本链，忽略中断留下的孤立版本，不修改 position_fact。NAV、外部现金流、成交、费用、融资、持仓、现金、负债和贡献资料分别表示；缺量、成本和时点保持 null。报价、触价、建议或模拟不能成为成交；历史市值不能成为买入成本。当前读取不伪装成历史时点已知。

`portfolio-performance` 仅在完整组合期初/期末NAV、费用融资覆盖、完整外部现金流与币种成立时计量净收益；时间加权收益另要求现金流对应NAV检查点及明确时序。净收益、时间加权收益、资产/策略利润贡献和因果归因分别说明。现有最小贡献计算是相对期初资本的PnL贡献，不冒称TWR分解、配置/选择因果归因或经理技能。缺历史不补零；外币现金流无确认换算就停止。完整收益计量也不证明策略有效。见 [CFA绩效评价](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/portfolio-performance-evaluation)。

## 按策略类型验证

规则化策略先用 `strategy-rule-record` 按真实执行时点记录规则，再留目标、可得数据与成本、融资及资本约束，再作历史回测、留出/滚动及敏感性；防止未来数据、参数选择和幸存者偏差。`strategy-backtest` 的最小可运行实现为 CASH_SPOT_LONG_FLAT_MEAN，固定输入规则、仅用执行前已知的历史信号、下一期实际开盘价格、明确源值成本、资金上限、退出成本及成本敏感性；它是模拟，不是新采用策略。实际存档规则早于所声明测试窗口时才记 ACTUALLY_RECORDED_BEFORE_DECLARED_TEST_WINDOWS；仅输入中的历史 recorded_at 不证明事前归档，保持 DECLARED_INPUT_NOT_HISTORICALLY_ARCHIVED，也不冒称盲测。没有成本或当时可知历史时为 DATA_LIMITED，不按零；结果由实际程序写入不可变 investment/validations，带 CASH_SPOT、FULLY_FUNDED、METHOD_CHECK_ONLY 边界；组合只能引用已记录结果；METHOD_CHECK只能绑定实际完成的 SIMULATED_NOT_EFFECTIVENESS_VALIDATED 记录并保留 CASH_FUNDING/DECLARED_EXECUTION_COSTS 能力，DATA_LIMITED 只作为缺口反馈，不能称已完成方法核验。现金现货验证不能用于杠杆或不同产品候选，不能授权 RULE_EFFECTIVENESS / PORTFOLIO_EFFECTIVENESS 采用。未实现的保证金生存、跨保证金、资金费、产品结构等分别通过候选所需能力、具体 gap 和执行状态约束对应采用及建议。报告必须消费总体选择，并承接有影响的 validation_limits 或 gap；正文不得把方法验证写成含杠杆保护组合有效。留出窗口之外不自动证明数据集独立，滚动窗口不冒称已优化参数。见 [CFA回测与模拟](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation)。

判断研究用 `judgement-evaluate` 对原正式决定的 evaluation_windows、事前机制和反证条件形成 REALIZED / FAILED / PARTIAL / EVIDENCE_LIMITED / NOT_DUE，绑定后续实际证据；到期不能仍以NOT_DUE结案，不更新原窗口。下一个人复核读取 ledger 实际已提交视图、版本、原始数值/币种/事实与获知及归档时钟，并按需使用 portfolio-performance 的币种/起止/as_of 入口，不用条目计数代替绩效反馈；同时读取实际规则输入、方法验证记录与判断反馈；原 ALLOCATION 私有投影携带对应来源依赖，不新增判断 Owner。个人复核，定位研究、组合映射或实施的改善及退化。方向判断可独立复核，完整投资效果仍需要实际成交和收益资料；不把所有观点强制数字化回测。

工程CI、报告回放、锁定样本语义回归、未用过样本及前瞻投资效果分别验收。模拟表现、策略建议、真实投资结果有独立 result_kind；无完整数据不虚报长期有效。本次代码候选在用户报告验收前不切生产 current，不改调度，不额外真实发送。


revision 9 的候选区分 POLICY、INCREMENTAL、INVENTORY_MAINTENANCE、INVENTORY_ADJUSTMENT 与必要独立风险响应。长期参与/保护用途只是一项政策，不表示当前数量、覆盖或保证金安全。完整敞口和全仓状态未知可改变存量调整方向；不存在获证约束时，不把 current 方案或 HOLD 选成已决定安排。新资金是否投入还依赖真实资金及组合约束。

account_constraints 按当前确认事实版本及已提交 RISK_CONSTRAINTS 视图取用。NAV、盈亏、目标金额、过往截图和方法模拟均不证明这些约束；后续成交及资金事项使旧完整性视图失效。新评价按机制进展维护，不复制旧预测终点；确有用户确认的日期约束时引用该原文，而不是补造时间。原账本、净成本、现金流、真实绩效与回测能力继续保留，个人反馈不能替代公开周期验证。

过程评价窗口 `end=null` 时，沿原决定和原窗口提交 `process_progress={status,explanation}`：实际进展尚未发生使用 `NOT_OCCURRED`；已有可观察进展使用 `OBSERVED`；资料不能判断是否发生使用 `UNKNOWN`。只有前者可记 `NOT_DUE`；有进展但兑现证据不足记 `EVIDENCE_LIMITED`，不能凭没有日期免评。日期窗口原到期规则仍保留。模型根据来源决定进展与结果，程序只核绑定，不自动给市场方向、排序或权重。下一输入保留同政策历史反馈及每条实际原决定、原窗口，并明确是否属于当前决定；不能继承别次决定的 `REALIZED`。
