# 交易决策主线

这是现行新研究的设计入口，不是报告栏目表。新研究在原 research / market / investment / report 路径内完成；旧结果保留原身份及其已经证明的范围，不能仅补新字段升级为新研究。

## 先完成公开决策

从当前原始公开资料和适用的既有研究形成未来 1–3 个月的独立周期论证。解释驱动、传导、应有与实际响应、市场已定价程度，裁决最强竞争解释与反证。允许主线不明；不以原观点、个人配置或仓位盈亏为证据，也不为维持历史一致而限制新判断。

随后比较资产相对机会与等待：收益来自什么、下行风险、成本、关键限制，以及为何当前更值得参与、等待或回避。方向正确不等于现价补偿足够。没有可靠数值时作有依据的定性取舍，不把结构位距离当成收益风险比。

公开决策的主对象是 `outlook.market_assessment.cycle_case`。当前新请求使用 `BTC_CYCLE_DECISION_V2`；旧 V1 原件按原已证范围读取，不能补字段冒称完成新研究。字段及绑定由 `scripts/v4/decision_design.py` 校验，包含：

- `as_of`（真实形成）、`knowledge_cutoff`（证据截止）与 `horizon {start,end,basis}`：真实公开形成时点及 1–3 月前瞻问题，非保证持续或固定复核周期。
- `thesis {status,conclusion,confidence,drivers,priced_in,strongest_alternative,strongest_falsifier}`。status 为 ESTABLISHED / UNCLEAR；driver 的 mechanism、transmission、observed_response、refs、counter_refs 构成可核证据链。最强竞争解释和反证均有 argument、implication、refs。
- `opportunities`：每项 id、asset、stance（PARTICIPATE / WAIT / AVOID / UNRESOLVED）、return_basis、downside、net_cost、limitation、rationale、refs。至少比较两个适用备选且包括等待；`preferred_ids` 与 `comparison` 给出实际相对取舍。
- 当前 V2 的 `preferred_ids` 可为空：周期主路径可以已建立，而现价下资产净回报仍不能排序。此时仍完成相关比较，保留已成立的局部或条件偏好，不为满足字段强选等待。选择准则须明确：净回报/风险补偿、降低集中度、控制尾部风险、保留选择权是不同命题；稳健偏好须说明适用理由与所放弃的上涨机会，不能冒称回报优势或用户既有目标。费用差异不单独证明净补偿；样本公司的 OCF、OCF减现金PPE与FCF不直接构成两个重叠持仓基金的现金回报差额，必须核同口径、权重与整体覆盖。缺同步估值或仅有旧短券毛息不自动推成等待优于参与。
- `prior_review`：旧论点的 prior_id、claim、outcome、reason、refs，旧预测只作待核假设。
- `crosschecks`：source、origin_group、as_of、finding、disagreement、influence、refs。按实际来源依赖解释证据，不将同源转述计为多个独立支持。
- `events`：event、occurred_at、effect、decision_changed、reason、refs。只记录已发生事件对决策的复核，未来事件是条件，不伪装成已观察事实。
- V2 `path_comparison {paths,ranking,preferred_ids,basis,unresolved}`：至少两个真实竞争周期路径，每项有 `id,mechanism,transmission,continuation,early_turn,priced_in,invalidation,implications,refs,counter_refs`。把趋势延续证据与早期转折迹象分开，并解释如何影响机会。`ranking` 为 RANKED / UNRANKED；未决说明缺少哪个鉴别条件，不免除机制比较、不强造概率或中性方向。

原市场、factor、forward 和区域接口继续承载底层事实及历史消费者兼容信息；其含义必须与主对象一致，不得反过来强迫新研究继承旧周期或围绕旧模块组织。程序核引用、时间及身份；模型核实论证、定价与选择是否成立。字段齐全不证明业务通过。

## 再完成个人决定

公开结果冻结后才读工作站持久化资产配置、操作与纠正记录。读取最新已知整体实际结构、目标、风险约束及后续变化，不依赖聊天临时摘要。实际、目标、建议和已成交分开；部分更新保留未变化事实及原时点，冲突不静默覆盖，时间变化不清空已知配置，也不永久沿用原规模。仅无肯定持仓证据的项适用未持有分析默认。

原 `investment_decision` 的候选比较及所选决定是唯一个人决策源，其 `allocation_choice`（当前 `BTC_ALLOCATION_CHOICE_V2`）将配置与纪律连为同一个结果：

正常个人输入中的 `portfolio_result_binding.market_result_hash` 是 allocation/risk/investment 结果应填写的公开语义绑定，即 `digest({outlook,factor_assessment})`，也等于 `market_content_hash`。外层旧 `market_result_hash` 与显式 `research_phase_result_hash` 标识含 `market_report` 的研究整包，不能填入上述财务结果字段。映射预检必须调用正常 recommendations/candidate 消费者，不能用同一个误填值同时传给局部检查而宣称链路通过；两种身份都保留，语义或原稿不因绑定规范化而改写。

- public_case_hash、fact_version 绑定确切冻结论证与本轮事实版本；
- actual_configuration、target_configuration、constraints 区分事实、目标和限制；
- selected_ids 与 investment_decision 完全一致，allocation_rationale 解释整体取舍；
- holding_discipline 每项引用所选 candidate_id，给出 decision、basis、change_condition、execution_limit；change_conditions 说明何时重评。真正总体未决有明确依赖缺口时允许空选择和空动作纪律，不伪选等待。
- V2 `exposure_decisions` 按实际投资问题划分范围，每项包含 `scope,purpose,current_exposure,target_risk,opportunity_ids,criterion,comparisons,gap_effects,ranking,selected_ids,conditional_preferences,basis,change_conditions`。purpose 区分 INCREMENTAL / INCUMBENT / JOINT；current_exposure 的 state 为 FLAT / NET_LONG / NET_SHORT / MIXED / UNKNOWN，basis 保留来源与时间边界；target_risk 的 status 为 DETERMINED / CONDITIONAL / UNRESOLVED，description 和 basis 说明目标风险敞口，可定性，不能把战略比例当当前风险预算。
- `criterion {kind,origin,rule,fact_refs}` 明确本次相对选择标准及其适用依据。kind 可为 RELATIVE_VALUE / RISK_CONSTRAINT / MANDATE / ROBUSTNESS / UNRESOLVED，origin 区分 CONFIRMED / ANALYTICAL_PROPOSAL / UNRESOLVED。确认约束须引用本轮事实版本中的原文；新的稳健提案不可伪称用户既有规则。
- `comparisons` 每项绑定实际 candidate_id，按同一组周期 path_id 说明 benefit / downside / net_cost，另有 opportunity_cost 和原候选 dependencies。必须比较现有安排与适用替代；不要求所有情景必胜，按已说明且有依据的准则排序。`ranking` 为 RANKED / CONDITIONAL / UNRANKED，与本轮投资选择、目标风险状态和候选依赖一致。UNRANKED 不隐含续持、减险或等待。只有 RANKED 的本轮选择进入 selected_ids；CONDITIONAL 的选项留在独立 `conditional_preferences [{candidate_id,premise,otherwise}]`，注明未满足前提及其不成立时的含义，不进入本轮所选或持仓指令。原 ACTION 缺口不为接受条件提案改标成 VALUE，条件提案不可在报告升级为无条件动作。`gap_effects` 必须逐项承接同一比较内所有候选涉及的 gap_id，说明 effect 为 RANKING / IMPLEMENTATION_ONLY / NOT_MATERIAL 及实际依据；可能改变排序的缺口禁止无条件排序，条件政策也须在原候选 dependencies 中保留它，不因 POLICY 命名逃逸。只有业务复审能判断“仅实施”或“不影响”的证据是否成立。

缺数限制数量或可执行范围，不造精确权重，不把建议写成成交；也不能用条件罗列替代有依据的保留、调整、退出或等待取舍。仍未决的动作保留实际缺口，允许总体未决，不默认安全。个人决定不得改写公开主线。

新增风险不划算，与存量敞口应调整，是不同问题。短期反弹、方向确信下降、预算未知或不能证明续持最好，单独不能推出减险，也不能默认续持。事实未知若可能改变排序，不能仅把候选改名为 POLICY 就移除依赖；已知风险目标或损失约束确实超限，或有据的稳健准则、相对价值比较支持减险时，则允许独立减险，不要求回报可精确量化或每个情景必胜。列出得失不证明相对选择成立；选择标准、支持证据、作用范围和可推翻条件须经实际业务审阅。

接续已有问题时，保留原 Owner 的 `id`、`question` 和 `scope` 原文，包括本次沿用的历史问题；“原问题接续”等说明写入 `explanation`，不加到问题文本中。正常个人输入中的开放问题不是改写其他已保留问题身份的许可。MAIN 在候选批准前核原问题连续性，缺少批准也必须先暴露这类独立错误；改过候选后重新绑定实际审阅，不继承旧稿批准。

## 最后表达与验收

用户所说行业顶级人员的行为，是以真实数据和实际方法完成取舍，再交付此刻的市场处理，并在已有研究或事件复盘中验证。正文让读者直接辨认优先处理什么、哪些安排暂不推进、何种变化才调整，不靠专家角色提示词、长分析或新增动作标题冒充。正常报告输入已有投资决定的 `priorities` 和范围化的 `allocation_choice.exposure_decisions`；作者消费这些完成结果，保持条件及未知的实际作用，不能在报告层补出新动作。总体未决不抹去局部有据方案，数量未知不取消定性判断。完整分析留在内部，只用必要依据解释处理及代价；语义复核逐项核对正文处理与原决定，而不是检查动作词或篇幅。

正常报告入口将上述两个完成结果投影为 `decision_results`，绑定原个人决定哈希与当前事实版本。事实变化必须先重评个人决定。报告不另做决策，也不成为新的状态所有者。

报告采用评估时间、简短加粗完整结论领起的自然段，段落随本轮重要问题变化。六点仍是有效内容基线：核心结论与定价、未来1–3月前瞻、机会与风险收益、配置持仓纪律、交叉验证与分歧、风险与失效应对。新架构改变形成及组织结果的方式，不删除这些投资问题；不是固定六段、机械清单或字数评分。来源、时点、分歧及限制保留真正影响判断或执行的部分，流程、审批、维护、全套备选不进正文。

报告读取真实排序的范围、准则与限制，不从“不新增”推导存量减仓，不从未决推导续持，不把条件方案变成无条件建议。正常消费者保留六点内容基线与实际完成结果，缺少判断退回其研究或个人决策责任；不能在正文补做决定。

既有云端推送及关键事件可触发对当前决策的复盘；不另建报告持续追踪、评分或监控调度。

验收从真实输入经过公开决策、冻结、个人决定、报告消费核实。必须区分：新证据推翻旧判断；主线成立但价格透支；资产相对机会；配置过期/冲突/部分更新；事件有或无决策影响；配置和纪律的一致性。语义审阅检查确切最终结果的依据和反例，不以字段、测试数、旧作者批准或漂亮排版代签。候选预览、正式采用、安装和投递分别记录，预览不要求先正式采用。

用真实失败案例及有界反例检查：同一市场下空仓、净多、净空的不同配置判断均须由同一市场证据和各自目标敞口推导；短期反弹不自动改周期或减仓；已核风险预算超限可独立减险；未知只限制依赖它的判断；旧报告不作本期证据。程序核身份、引用、候选/路径/依赖及排序一致性，模型独立复核准则适用性和经济排序；填满 V2 字段或自述 RANKED 都不证明通过。

## 正常候选预览入口

`strategy_v4.py --project <node-project> --root <v4-root> report-preview --draft <candidate> --evidence <evidence-dir> --review <main-review>` 执行同一事实、证据、金融及实际批准检查，再归档原有不可变报告准备时钟并返回未采用的上下文。来源按[分层证据契约](v4-contract.md)区分：官方 MAIN 证明或仅供隔离验证的成功执行/节点/源码绑定，后者明确保留平台调用者归属与型号未验证，不能进入正式采用。它不更改 current、策略版本、报告指针或 outbox。

成稿由同一结果消费和正文校验执行；添加 `--spec <exact-body-spec> --prepared-context <original-preview-context>` 返回实际预览及正文哈希。调用前后重新核候选、基线、事实、证据与方法；有误先退回所属阶段。预览不能登记成正式报告，更不是策略采用或投递。原平台拒绝的持久 strategy commit 不在此入口执行。

普通后续观察保持原cycle_case字节、形成时点与窗口，只在适用性中核新增证据；若需要改变周期论证，返回正常研究。较新证据包承接原case须回查原PUBLIC不可变源索引及原件内容哈希，不能只把截止比较改成小于等于。索引由正常独立研究保存时生成，只指向原件，不持有观点、当前状态、评分或新的调度。新PUBLIC论证仍须绑定本次完整冻结证据截止。

冻结case引用用原PUBLIC原件中绑定的`cycle_case_evidence_id`对应证据包解析。该包由正常研究注册保存并核截止与原知识时点一致；后续小时的新包仅解析当前市场与适用性。即使新包不含旧源，也不能重绑旧引用、续写期限或跳过原引用核验。
