> 报告的分析依据、触发复核和日本每日反馈以[报告复核契约](report-care.md)为当前规则。小时研判同样遵守[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)：先完成新证据对综合判断的影响评估，汇报承接结果，内部条件继续完整维护。新报告包含maintenance；旧格式保持兼容。

# 连续公共市场观察与价值发现

生产小时主任务为 Sol/high，负责嗅探、取证、研究编排、映射核验、正式写入和报告；完整周期形态、价值归因、下行路径与市场方案由 Astra/max 深评确定。取数、窗口和公共存储复用 market_observation.py，观察与上下文使用 v4/sensing.py。按已采纳深评累计支持与反证；首次缺少完整基线、实质变化、重要遗漏或原市场判断无法继续支持时，发起或恢复 BTC_CYCLE。recheck_at 到期仅要求 Sol 以真实新观察核验适用性，不自动完整深评或延长建议有效期。旧 v1/v2 门仅供历史兼容，不按单个因子波动或固定24小时时钟改变周期。

## 取数

`$BTC_PYTHON <Skill>/scripts/market_observation.py collect --output <唯一原始JSON> [--deep]`。

匿名来源包括 Binance 现货/永续/标记/指数/OI/资金费、Coinbase 与 Kraken 现货；正常取数包含90日日线及52周周线，deep 参数强制刷新以核验数据修订，不代表调用 Astra。普通240闭合小时有4/24/72/240小时窗口；重叠窗口不能叠加计量。按canonical origin去重，区分USD/USDT、现货/合约、量/金额、OI数量/名义额。辅助采集仅UNTRUSTED_LEAD，不当作行情事实。

collect只写独占输出，不覆盖原包或正式状态。完整性、闭合时间、缺失、同源、修订、冲突和未闭合行沿用原验证器。缺失不是零，主动买卖不证明参与者身份，OI下降不独立证明强平。

collect并不自动取得所有潜在价值因子。主任务按[价值因子研究指引](../../../project-methods/价值因子.md)发现问题并选择额外公开证据，将原始资料冻结为EvidenceBundle，再经`evidence-root --additional <额外冻结包>`并入本次精简证据根。没有固定因子目录或覆盖率门槛，不为填表抓取与当前问题无关的数据。慢数据分别保留数据期、发布与取得时间；主任务判断新发布、修订及原机制是否仍适用，不因小时滚动机械过期。

## 当前上下文

按[前瞻复核主线](core-strategy.md#前瞻复核主线)恢复原论点，检查新事实、累积变化及价格偏离，形成当前定价判断和下一验证。comparison_baselines 保留研究与最后真实送达的不同截止；pending_work 提供已采纳结果、未处理候选及交付尝试的接续入口。它们不重置累计窗口，不把档案标识当作完成或仍在运行的证明。重要条件全量维护，补证按当前问题展开，方法模块不逐轮机械取齐。

按[美债机会成本与风险补偿](core-strategy.md#美债机会成本与风险补偿)复核现价、可比收益率、情景兑现进展及下行风险对投资吸引力的净影响。未破位也须判断重要补偿变化，旧情景不随时钟续期；未变基准按真实数据日期复用，缺证说明量化限制。公共成本锚按原群体和口径核验，不当作公允价值或个人成本。是否补证、恢复研究及报告仍分别判断，不由利率或算式单独触发。

revision 5 正常入口按 [market-state](market-state.md) 先市场再仓位；下述完整 context 保留为确定性绑定及历史兼容，不直接作为市场模型材料。本轮市场复核包含共振、反转、冲突和分歧区状态，接续正式基线及后续有效观察。事实变化只影响仓位适配，不改写市场结果。

下列命令前均加 `$BTC_PYTHON <Skill>/scripts/strategy_v4.py --project <项目> --root <项目>/.strategy/v4`。

`context --market <当前公开原始JSON> --evidence <本次冻结证据目录>`固定当前版本、最近观察、事实、因子证据与问题研究记录。revision 3/4从同一DecisionSnapshot读取current_conclusion、current_recommendations、event_cycle；revision 4当前已选因子与引用入口从factor_context获取。旧版没有因子分析时保持history_unassessed，不能自动补成已评估或启动批量专项。不要求deep新于正式结论。默认精简输出，保留必要变化、引用索引和路径，原始证据按需回读。各具体问题都有独立累计起点；历史未结构化研究覆盖仍标UNKNOWN。

`--evidence`把此次实际冻结包绑定进context；后续observe与装配使用同一实际证据，不能只改context_id跨过新证据或版本变化。因子资料仍由原EvidenceBundle承载，不建立另一份宏观当前状态。旧接口保留历史读取兼容。

revision 5 可按分析需要省略旧形态或只填写相关子集；它们不决定证据充分性，开放判断由 market_assessment/dispute_zones/factor_checks 承载。旧五类形态 ABSORPTION、POSSIBLE_DISTRIBUTION、ACTIVE_SELLING、DELEVERAGING、LEVERAGED_RISE_OR_SHORT_COVER 保留为观察兼容字段，不限制价值发现，也不决定深评。支持与反证 refs 指向 datasets.<id>、windows.<window>.<id> 或 gaps.<id>。候选不能把相关性写成已确认因果。

## revision 4观察

`observe --market <原始包> --evidence <本次冻结证据目录> --review <审阅JSON>`保存实际观察与本轮选用因子的判断。review沿用revision 3字段并增加factor_checks：

- `schema=BTC_SENSING_REVIEW_V4, revision=4, context_id`。
- `market_review`保留旧 BTC_HOURLY_REVIEW_V1 结构和真实 Sol/high 身份、五形态、变化摘要、deep_review；其 required/reasons 仅兼容市场变化判断，不能单独打开专项门。
- `recommendation_checks`每侧 `{side,findings,refs,explanation}`，findings为NO_MATERIAL_CHANGE、SUPPORT_WEAKENED、COUNTEREVIDENCE、AMOUNT_BASIS_CHANGED、GAP、OTHER_MATERIAL。逐项判断当前依据与适用性，保留尚需主评估的事项。
- `extra_findings:[{summary,refs,material}]`保持分类外发现。
- `factor_checks={items:[{id,name,assessment,change_reason,refs,counter_refs}],summary}`与正式factor_assessment使用同一结构，详见[v4契约](v4-contract.md#动态因子评估)。items是本轮模型实际选用的因子，可为空；id为自由稳定文本，不校验固定名单。assessment由模型说明周期、作用与不确定性，change_reason说明沿用或变化依据，summary可说明退出因素及整体影响。refs/counter_refs使用现有原始值引用；没有引用时如实说明受限，不伪造证据或阻断完整保守判断。
- `main_evaluation:{required,reasons}`由Sol判断是否需要发布新的完整/补充结论；普通取数并不强制每小时写正式版本。
- 启用周期报告的新观察必须填写 `applicability_review={basis_result_id,outcome,explanation,refs,limitations}`。basis_result_id绑定当前cycle_baseline.result_id；outcome为VALID、EVIDENCE_LIMITED或REVISE；refs为本轮EvidenceBundle中真实原始引用。VALID须有已采纳基线及实际引用，由模型解释完整论点与累计证据为何仍适用；不能由没有深评理由自动生成。REVISE恢复同一周期研究，EVIDENCE_LIMITED保留受限及影响但不机械重跑深评。历史未填此项不补造通过，也不用于刷新适用性。
- `research_findings:[{issue_key,scope,question,material_change,change_kind,change_reason,refs,reopen_condition}]`。没有需要专项的疑问则[]。change_kind为INITIAL_QUESTION、NEW_KEY_EVIDENCE、MATERIAL_COUNTEREVIDENCE、REOPEN_CONDITION_MET、NO_NEW_KEY_EVIDENCE；已有问题的reopen_condition对应此前保存的具体重研条件，新问题为null。首次重要未知问题可用当前鉴别证据进入INITIAL_QUESTION，不要求此前研究基线。引用可使用既有市场地址或本次冻结因子原始值引用；相同机制持续不是鉴别性变化。

工具记录发布以来未检查小时的高低/收盘关系、PARTIAL_ISSUE_HOUR、缺口、事实变化及到期。到价及普通事件进入条件核验；cycle_review.verification_due 表示适用性核验到期，required 仅由事实影响、实质反证、重大遗漏等研究理由决定。普通数值变化、到期或观察完成不自动决定发布、深评或报告。`deep_eligible`兼容投影 research_eligible；风险提示不等待 Astra，新周期判断和重新定价须经深评。

`sensing-gate --observation <真实ID>`回读不可变观察，检查当前结论及研究上下文仍匹配。观察后重新取得context再装配主评估；绑定过期时先读差异并处理，而非仅改hash。新增专项结果由主任务吸收，后续普通观察消费同一结论。

公共库只追加匿名原始evidence/retrieval；建议、事实hash、问题和私有检查只在v4。原始写入中断不冒充观察成功。保留历史与尚待复核的证据，不用旧DEEP清理规则删除问题仍需的行。

## 每轮报告判断

有效观察及必要主评估完成后，必须执行[独立行情汇报](market-reporting.md)，包括OBSERVATION_ONLY和main_eligible=false。不因策略、金额或档位未变提前结束报告判断；报告不机械触发Astra。现货、永续及USD/USDT指标保持分列。

## 周期观察接续

正常 collect 始终包含已收盘日线与周线；可传入最近有效公共行情包作为 cycle_cache。同一完整日/周窗口保留原始数据与实际 retrieved_at，新收盘窗口重新获取，--deep 强制核验修订/事件影响。历史无日/周数据的包仍可读取，缺口不伪装成完整周期证据。日/周历史与慢变量不随小时主评估或报告重新计时。

新策略嗅探按当前 DecisionSnapshot 已采纳的 Astra 结果派生 cycle_baseline；累计窗口从其原市场截止开始。主评估完成、最新未采纳研究或发送报告不重置该基线。周期识别和具体下行方案由深评确定，Sol 记录支持增强/减弱、反证、事件进展和缺口，重大风险可先报告。

当前市场变化先独立判断，再分别判断是否深评、是否报告。标普500、纳指100和BTC分别检查评级、周期、当前结构、原结构→当前结构、依据与确认程度；小时承接、波段形态、中期趋势不能混为一个状态。revision 5 市场阶段只沿用独立 outlook、factor_assessment 及后续有效市场观察；完整正文与 conclusion.business_meaning 在市场冻结后的仓位阶段核验，不能作为市场输入。评级或历史锚不变不能抹掉当期结构变化。重要结构变化、背离或波段风险不以中期反转、仓位建议改变或当天未报告为前提；当前依据与适用期限由模型核验，已披露风险的重复和普通反弹不机械重发。四小时仅是缺陷回放窗口。账户与偏好只参与最后组合应对，不能改变公开市场评级、结构和价格风险；成员研究服务动态组合判断，重要例外单列。
