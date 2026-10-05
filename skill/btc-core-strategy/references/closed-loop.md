# 完整研究、两个结果与持续验证（content revision 7）

本文件用于新研究、组合复核、报告和运行维护。保留全部研究与资产范围、原模型分工、调度和报告目的地。完整研究先形成独立市场结论，再由模型评估个人资产风险是否值得且能够继续承担；只有风险需要应对时补充操作建议。原有机会研究继续独立进行。四小时区域、现货加减、合约开平只是问题示例，不能成为固定模块或报告清单。

## 研究与冻结

研究与冻结的主结果围绕投资周期，先确定周期阶段、方向、价值、风险及改变条件。主周期由投资问题、机制和证据确定，不固定周数；`market_assessment.summary`、对应 `horizons[].forward`、BTC 的 `outlook.semantic` 与 `market_report` 保持一致。日内只验证主结论或必要即时风险，不因小时数据更新或短线到期而抢占主情景。周期证据缺失返回研究补齐，不在组合或成稿阶段改名、延长期限；近端空间也不能代替周期价值与同期限美债比较。

公开事实先按来源、原日期、产品和单位核验。历史研究、候选价与预测置于 `historical_hypotheses`，用于检验支持、削弱、否定和落空，不能循环支持自身；个人材料在市场冻结后才提供。辅助采集仍是未独立核验的线索。公开输入中的已实现价格范围，不等于成交集中区，更不等于尚未成交的挂单。

完整研究覆盖周期、价格结构、资金与融资、盈利、未来价值、当前定价；因子按问题选择，不设固定数量或评分。内部比较竞争解释、不同期限和累计反证，完成取舍后给出方向、适用期限、主导依据、前瞻成立条件及失效条件。不能只复述已经发生的价格，或把尚未证伪当成有依据的方向。

每个 `market_assessment.markets[].horizons[]` 在原字段外增加 `forward`：

- `observed_window={start,end}`：支持判断的实际数据窗口，不能晚于证据截止。
- `judgement_window={start,end}`：该判断适用的期限。
- `realization_window={start,end}|null`、`timing_basis`：预期机制兑现窗口及依据；无法识别时保留 null 并说明时间缺口，不制造精确日期。
- `conditions[]`、`invalidations[]`：前瞻成立和失效条件。
- `prior_check={outcome,explanation,refs}`：INITIAL、PENDING、REALIZED、FAILED、REVISE 或 EVIDENCE_LIMITED。原判断或兑现期限届满必须检验实际结果，不能在新报告中把期限向后移动而继续 PENDING。历史缺少前瞻时承认缺口，不补造当时已有预测。

新建或修正的区域保留 `role` 与 `boundary_review revision=2` 的逐侧形成、识别、触价验证时间及周期，并增加 `evidence_kind`（OHLC_RANGE / TRADED_VOLUME / ORDER_BOOK / PRICE_RESPONSE / UNKNOWN）和 `limitations`。高低范围、按价成交量、未成交订单、候选边界与历史参考分别处理。选择 TRADED_VOLUME 必须有按价格归集的成交证据，OHLC 总量不能替代；ORDER_BOOK 需要对应时刻和产品的真实公开订单簿。程序只验证结构和真实引用，模型需检查证据是否真的支持所声称区域。

独立研究仍输出 `{outlook,factor_assessment,market_report}`。冻结前校验公开字段、引用、时间、适用性和前瞻承接；冻结后，组合和报告不能改写市场结果。需要修正时返回公开研究环节并保留修订原因。同一代理已读个人材料后的修订属于公开证据续研，不冒称全新盲评。

## 当前资产风险结果（revision 7）

资产配置提供分析背景与约束，不代替模型判断。结合市场、资产特点、持有目的与已确认组合事实分析风险和实际影响；总资金、占用比只是可选依据，不固定公式、评分、阈值或必填清单。风险迹象、短线偏弱、比例偏离和缺数不自动触发操作。

外层 schema、七个 section 和 current 指针不变。新内容使用 revision=7，`recommendations.portfolio.risk_assessment` 替代新内容中的 `allocation_decision`：

- `summary / basis`：资产风险结论及主要依据，说明组合影响和继续承担风险的判断。
- `response_needed / response_reason`：模型判断 true、false 或 null，以及理由；null 明确未决范围，不能默认为安全。
- `actions[]`：可选风险应对建议，可以为空；仅 response_needed=true 时允许非空。需要应对但操作不明时也可以为空，不强行凑建议。
- `limitations[]`：实际判断限制，未决时必须说明；未知资金不写成零。
- `market_result_hash / fact_version`：冻结公开结果与本地三份确认文件版本。
- `assessed_at / valid_until / review_conditions[]`：真实评估时间、有效期和复核条件，不由报告时钟续期。

实际 actions 复用下述 revision 6 action 结构及数量/动作缺口约束，但风险结论不从动作状态汇总。比例、数量、批次和先后顺序仅在有依据且必要时提供，不默认减半。敞口、资金和保护的必要检查留在内部，正文只呈现影响判断的内容。原有定投、折价买入、独立BTC/黄金等机会建议不受风险应对触发条件替代，也不把无依据的风险动作改名为机会建议。

观察保留 `allocation_review` 名称，revision 7 将其中 `allocation_decision_hash` 改为 `portfolio_risk_hash`，同时加入strategy_advice_hash绑定独立机会建议，其他字段不变。报告消费 `portfolio_risk`，result_binding 使用 `portfolio_risk_hash`；新报告不允许从旧 allocation_decision 引用个人结论。旧结果未完成真实个人重评时为 EVIDENCE_LIMITED，不能改名冒充新结果。新入口采用 revision 7，可读 revision 1–7；以下 revision 6 字段仅用于历史恢复。

原 upper_short/lower_spot 价量接口保留。其他已形成的机会建议使用 `recommendations.portfolio.strategy_advice[]`，复用 action 结构、允许为空，与风险应对分开；报告提供 `strategy_advice` 并以 `strategy_advice_hash` 绑定，同一评估的市场、事实和期限仍适用。不得将完整内部研究或无风险依据的减仓包装为机会建议。

## 历史整体配置决定（revision 6）

`DecisionSnapshot` 外层 schema、七个 section 和唯一当前指针不变；真实评估及采纳后，新内容写 `revision=6`。新增 `recommendations.portfolio.allocation_decision`：

| 字段 | 含义 |
|---|---|
| `summary` | 综合整个组合风险与机遇后形成的最终安排 |
| `status` | DECIDED / PARTIAL / UNDETERMINED，按真实已决和未决范围填写 |
| `actions[]` | 实际必要的安排，不要求逐资产或固定五模块各填一条 |
| `execution_limits[]` | 影响整体执行的限制，不能把未知资金写成零 |
| `market_result_hash` | `digest({outlook,factor_assessment})`，绑定已冻结公开结果 |
| `fact_version` | 当时本地三份确认文件版本 |
| `decided_at / valid_until` | 本次决定实际形成时间与有效期限；可晚于证据截止时间，不倒填，也不由报告时钟续期 |
| `review_conditions[]` | 使整个安排需要复核的条件 |

每个 action 包含 `id,scope,status,decision,arrangement,gap_impact,execution,reentry_conditions`。scope 描述真正需要安排的资产或组合范围，arrangement 直接说最终怎么做。decision 支持 INCREASE / REDUCE / HOLD / EXIT / OPEN / CLOSE / PAUSE / RESUME / ADJUST / UNDETERMINED；只用于明确动作，不按枚举机械产生策略。status 为 DECIDED 或 UNDETERMINED。缺口影响 `gap_impact=NONE/QUANTITY_ONLY/ACTION`；ACTION 必须保持 UNDETERMINED，不能默认为 HOLD。

`execution={status,limits[]}`：READY / LIMITED / UNDETERMINED / NOT_APPLICABLE。QUANTITY_ONLY 保留有依据的动作方向，但执行为 LIMITED；范围或保护覆盖等缺口可能使动作本身未决。REDUCE / EXIT / CLOSE / PAUSE 同时给出 `reentry_conditions[]`，包括恢复投入、回补或重新保护的条件。

内部比较参与机会、降低风险、持有、退出的实际得失，考虑等待成本、流动性、资金占用、净敞口、保护及失效情景。五模块继续作为内部覆盖，不复制成用户清单。现金、现货资本、合约名义额、保证金与保护不能重复计算；已采纳目标不等于现有余额或成交。

原 `upper_short/lower_spot` 继续管理两类价量建议，不能代替整体配置。增加 `EXECUTION_LIMITED`（方向有依据，暂无完整价量）与 `UNDETERMINED`（动作本身未决）；两者 tiers 为空，总未知占用保留 null，不自动变成 NOT_RECOMMENDED。RECOMMENDED 仍要求真实评估的可执行档位及未过期有效期，NOT_RECOMMENDED 是真实负向决定。

## 更新、报告与交付

市场单独变化可以先形成独立观察及 MARKET_UPDATE，再检验是否影响组合；账户事实或约束变化只重做对应个人适配，继续引用有效的市场冻结。组合正式改变仍由原 Astra 审阅实际候选、Sol 采纳，复用原研究请求和已完成公开结果，不因此重新跑全部公开研究。两者同时变化则先市场、后组合。重大遗漏、累计反证、前瞻未兑现回到相应研究补证；仅表达变化修稿。到期、新小时或未公布资料本身不是重开 BTC_CYCLE 的理由。

revision 6 观察增加 `allocation_review={market_result_hash,allocation_decision_hash,fact_version,outcome,explanation,limitations[],material}`。市场适用性继续归原 `applicability_review`。组合结果未知、旧格式未形成新决定、事实变化或决定过期不能声明 VALID；重要未决风险不能视为无变化。事实变化时可再次提交同一 market 阶段输入，复用冻结公开结果，返回新事实绑定的组合模板，不重采或重算市场。

revision 7 报告通过 `market_result`、`portfolio_risk`（revision 6 恢复沿原 allocation_decision）、`applicability`、必要执行档位、引用和最后真实送达正文消费已形成的结果；完整内部研究留在原记录。报告必要性及维护材料仍承接原条件，不能把这些材料逐项转述为正文。运行提示按阶段提供：完整研究指令用于研究及映射，报告的 `result_instructions` 只说明最终结果的消费与表达边界。`result_binding` 绑定市场、个人风险、独立机会建议和适用性结果；旧上下文沿原绑定。市场部分表达方向、期限和必要依据；个人资产部分写风险结论、组合影响和必要限制，仅风险需要应对时附建议，不能重新推理或逐模块展开。缺的是分析就返回研究；缺的是表达就修稿。

maintenance 的 trigger 增加 `scope=MARKET/PORTFOLIO`；checked 增加 `outcomes={MARKET,PORTFOLIO}`，逐条结果增加 scope 和 material。两类分别为 VALID / REVISE / EVIDENCE_LIMITED；总 outcome 保留最严重状态，不覆盖分项。旧未归属条件由本次实际审阅明确归属，程序不按关键词猜测。旧条件移除仍说明退休原因。个人未知不污染市场状态；已披露且无新增意义的个人缺口可以保留 EVIDENCE_LIMITED，同时市场有效并按实际比较静默。新重要未决风险或修正必须反馈，不能借分层隐藏。kind 可为 PORTFOLIO_REVIEW，真实反馈沿用日本自然日最低要求。

必要性始终对比最后真实送达的内容。新字段、哈希、时间或措辞变化不是新报告理由。冻结前完成格式、来源、时间和内容审阅；发送前检查最新完整小时、已发生的重要事件和账户版本是否影响结论。存在重要变化时只更新受影响判断和文稿，保留仍有效研究；BEGIN 再核验当前绑定、期限和重复情况。缺少新取证时如实说明适用边界，不能用发送时钟冒充市场新核验。

AUTHORED_REPORT_V1 原文交付、实际助手回读及 UNKNOWN 恢复保持。新稿不回到旧自动拼稿器。建议、触价、送达都不代表成交，只有用户确认可更新资产事实。

## 兼容与验收

代码可读 revision 1–7；旧冻结记录按原格式校验和恢复，任何安装不改历史、资产事实、当前策略或未知投递。新入口准备 revision 7 材料，正式状态等待真实评估采纳；不把旧叙述或旧动作补成 risk_assessment。在办研究通常沿固定原版本，仅程序缺陷按[版本入口](releases.md)显式登记兼容修复后由新恢复运行接续；UNKNOWN 始终沿原物理版本上下文。回退只切代码，并须匹配当前内容可读范围，不回滚数据。

临时合成测试覆盖依赖隔离、历史假设非事实、期限落空、数量/动作缺口、市场与组合独立更新及联动、最终结果输入、旧版本与过期/延迟/重复/跨日/UNKNOWN 恢复。真实历史预览只在节点读取。内容验收逐项对照实际证据、判断和正文，不以字段、关键词或自评通过代替分析质量。程序验证、真实送达、自然耗时与成本分别验收，保留未关闭的运行缺陷。
