# 节点增量入口（旧小时任务兼容）

目标任务准入和切换条件见[节点任务职责与切换验收](node-tasks.md)。本文件的小时步骤是现有正式运行及冻结旧轮的兼容方法；新节点仅在有证据支持的执行任务下调用所需阶段。`main_eligible`、`research_eligible` 与 `deep_eligible` 的旧布尔投影说明业务待办，不能单独授权新模型启动。节点 `task.assess`、开放缺陷、每日深评名额和原阶段完成记录共同决定是否可运行；同一未完成问题不因新小时重开。

完整核心报告以币安QQQ、VOO和BTC为用户报告对象，SPX/NDX沿用为底层研究及历史引用，产品身份与价格见[币安产品口径](equity-products.md)，三类收益用于解释机制；美股与加密组合是持续评估单位，成分和权重可调整，独立BTC／黄金机会及保护分别表达。正式重评先检查本次需要的公开证据是否仍足以支持各市场与期限；可取得但尚未补取、旧行情和空壳页面不能直接当作不可判断的最终依据，按具体缺口用同一研究补证。慢数据按发布节奏和实际适用性复用，不全量重复采集。每轮先按纯市场基线及后续有效观察核验市场依据、冲突、周期共振/反转与分歧区，再由仓位阶段核验完整正文中的个人条件和本金保护；美股公开原文沿用freeze-evidence与--additional，不用BTC两侧检查替代。factor_checks/research_findings承载分资产发现，maintenance核对完整报告条件及上一反馈承诺；手机未展示条件也须维护。新比例和独立BTC档位由实际深评形成并审阅映射。

周期核验按[价值背离与时间优势](core-strategy.md#价值背离与时间优势)主动识别当前定价与需求、供给、融资、盈利等因素的背离，比较竞争解释及抵消因素，并承接、核验同尺度有界未来路径与相对把握；不要求精确目标价或确定兑现日期。有效旧结果保留，实质缺失沿原研究补评，有限反馈说明完成范围。每轮先形成当前市场判断，再分别判断深评与报告必要性；重要背离或波段风险可单独报告，不受中期反转、仓位建议改变或当天已送达限制。

正常入口准备和接续 Astra 研究报告、Sol 核验发布、小时嗅探及每日反馈；程序本身不调用模型或发送消息。研究、嗅探和报告均遵守[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)，模型先完成综合判断，用户正文呈现其结果及必要依据。完整需求决定必要的接口及消费链修改，材料精简不能缩减内部分析、条件维护或交付能力。工作目录是可审计的本轮材料，不能替代 `.strategy/v4/current`。使用 `$BTC_PYTHON`。

小时主任务按 `scripts/market_observation.py` 的 `CURRENT_HOURLY_MODEL` 维护，当前为 `gpt-6.1-sol/high`；节点可用性由实际执行记录核实，新观察、主评估和报告来源均按真实运行记录。历史基线与已完成阶段保留其原型号并可回读；未完成的旧型号提交不能改标签后发布，由当前 Sol 复用原冻结证据及已有研究结果，核对当前绑定后准备本轮材料并实际完成判断。型号不可用时暴露异常，不静默降级或升级 Astra；不因模型切换重做深评或追加报告。

## 正常路径

投递恢复消费统一的 `delivery_recovery` 主动清单。完整 `attempt_recovery` 中的 `WAIT_FOR_NEW_EVIDENCE` 及其依据仅供按需历史查询，不能因原 state=UNKNOWN 在市场或报告阶段重新安排回读。没有新关联证据时，最终回复与 inbox 不重复此历史异常；原版本收到实际新运输状态、回复证据或用户明确复查时仍可恢复。未完成报告义务与新异常继续正常处理，详见[交付恢复](market-reporting.md#历史-unknown-的恢复与提醒)。

Docker 节点返回 `continuity.public_context` 与冻结后的 `continuity.personal_context`，按[持续分析契约](continuous-analysis.md)在对应阶段读取；用稳定对象 ID、版本与证据提交观察和重要变化。历史查询使用事件窗口及 as_of，价格辅助材料保留原产品和单位。报告没提及的前瞻期限、计划条件、研究和纠偏仍由同一轮复核，不以复制上一小时整套内容代替进展。

分析沿[前瞻复核主线](core-strategy.md#前瞻复核主线)：有效论点基线 → 新增及累积变化 → 因果与当前定价复核 → 下一验证。研究积累与最后真实送达是不同基线。先核对材料中的 comparison_baselines 和 pending_work，再读本阶段必要证据；它们由现有状态派生，不产生第二份结论。

`run-material --run <run.json>` 默认只生成报告必要性材料；需正式写入或接续深评时指定 `--stage main`，决定 REPORT 后才用 `--stage report-body` 获取正文模板和指标。普通观察不预先生成这些模板。各阶段保留 required 中的全部重要条件，原始证据按引用展开，不重复读取完整 context 和其精确阅读视图。

`--answers` 可用 `{source_id,pointer}` 引用本轮冻结原始值，程序计算 value_hash；已提供的错误 hash 会报错，不静默修正。长篇已写内容可用 `{"$value_ref":{"file":"绝对路径","pointer":"/真实字段","value_hash":"sha256:实际值摘要"}}` 精确承接。文件限本轮目录、既有 research 或 versions，内容变化即拒绝；数组仍整体替换，程序不补观点或认可旧结论。

用同一固定版本的 `run-value-ref --run <run.json> --file <已核对材料> --pointer <真实字段>` 取得完整 `$value_ref`，不手写哈希；核心编码包含末尾换行。生成引用不证明内容已审核。`market_review`、`recommendation_checks` 与 `extra_findings.refs` 使用本轮 `vocabulary.json` 中的 `market_refs` 字符串 ID；`factor_checks` 等原始证据引用才使用 source_id/pointer 对象，不能混填。

Docker 桥接前将路径按宿主调用目录固定；直接调用容器服务时，相对路径以该节点项目目录解释。阶段返回的物理路径可直接接续，不依赖容器工作目录。

部分 `run-prepare` 失败不重新采集：有 `preparation.json` 时显式加 `--resume-preparation`，身份、固定输入与原状态仍一致才复用 market.json、collector.json 及完整证据包。原状态已变化或旧目录没有该记录时，用新目录和 `--market <原market.json> --collector-snapshot <原collector.json>` 接续；`--collector-observations` 只接受 reader 导出数组。损坏或未完成的证据包保持原样，不删除、不覆盖。

历史 `retained evidence source collision` 返回原包 evidence_id、完整来源 source_hash、raw_hash 与获知时间。仅确认是相同市场数据的不同获取记录后，显式传 `--retained-source-bindings <JSON文件>`，内容为 `{"冲突source_id":{"evidence_id":"原包ID","source_hash":"原来源对象摘要"}}`。选择保留该原绑定，其他获取记录以独立来源身份进入新包，原字节与时间均保留；真实行情、来源契约变化和错误引用继续拒绝。没有明确选择不自动重绑，旧包、研究、运行及 UNKNOWN 投递不变。

披露前使用 `research-reveal ... --check-only` 核验实际候选及原始引用，再向同一 Astra 披露。正式写入前可用 `run-advance --stage main ... --check-only`；现有 `validate` 与提交共用校验，集中返回可独立确定的错误。预检不写策略或候选，也不证明研究批准；真实提交在锁内重新检查当前状态。参数、绑定和交付错误分别从原阶段修复，不因机械失败重新取证或重开已完成研究。

按阶段展开方法：各阶段先理解 method_references.report_boundary 指向的职责边界，同一会话已读且未变时复用；观察读 market-observation 与价值因子指引，独立研究读 core-strategy 和 recommendation-maintenance，映射读 v4 周期契约。报告判断读 report-care，决定 REPORT 后再展开 mobile-report 的正文接口与交付规则，实际交付时读 market-reporting。各阶段的 method_references 保留这些入口；read_set 为模型提供正文、阶段材料及精确复用引用，路径可达不代表已阅读或通过实质审阅。

用户明确要求完整重评时，在已归档的真实证据上提交 `USER_REQUESTED_REVIEW` 及顶层 `user_request={reference,reason}`。同一引用复用原研究，有在办研究先核对进度再接续；无需将该请求改写成价格或资金变化。普通小时仍按原实质变化条件处理，详见 v4 周期接口。

下面所有子命令之前必须带全局参数 `--project $BTC_PROJECT --root $BTC_PROJECT/.strategy/v4`；隔离验证使用对应临时项目路径。

1. `strategy_v4.py run-prepare --run-ref <真实运行引用> --agent <真实主任务ID> --directory <本轮新目录>`。一次完成恢复盘点、pin、匿名公开取数、精简证据根和模板；优先读返回 read_set.observe 的全部 required 分段，按 on_demand 的 reference_index 展开实际原始引用。较大文本已分段，按输出预算分批读，不能将截断当作读完。旧运行无 read_set 时读 context、market_summary、reference_index、observation_template、vocabulary。revision 5 市场阶段不读取账户事实或完整混合旧正文；市场冻结后，首次个人适配或事实变化再完整读三份本地事实。报告阶段材料的 report_care 保留上一报告全部条件和承诺；revision 5 市场阶段只读纯市场基线，冻结后再读取完整报告与个人条件，即使无新完整小时也完成必要复核。已有发送中或未知交付走原恢复入口；pending_work 只含公开接续标识，交付正文留到 report 阶段。 reference-index.json 的顶层是来源数组，每项用 id 标识来源、refs 给出实际引用；input_role 根据本轮冻结绑定区分 CURRENT_MARKET、CURRENT_COLLECTOR 与 SUPPORTING_EVIDENCE，不改变来源信任或历史引用有效性，数组顺序不代表当前批次；read-set 按自身键和 required 路径读取，不预设 sources 或 sections 容器。
   `run-prepare` 默认只读本地 BTC、QQQ、VOO 最新辅助导出；`--collector-observations <文件>` 可固定输入并离线回放。三标的各自缺口、时间与限制进入 `collector_observations`，`raw_ref` 指向冻结完整数据；采集值必须独立核验，不能直接支持已核验事实。详见[辅助采集消费](collector-evidence.md)。
2. revision 5 及以后先按 [market-state](market-state.md) 填 market_template，执行 `run-advance --run <run.json> --stage market --template <market_template> --answers <判断JSON> --check-only`。程序导出实际合成的 market_candidate 及 read_set.market_review，不冻结市场结果或提供个人材料。执行返回的 `review_read`，用 `run-read --run <run.json> --read-set <read_set.market_review> --cursor 0` 输出真实片段，并跟随 `next_cursor` 到 `complete`。该只读入口不修改候选、阶段或历史；默认每页至多12000字符，工具使用推荐的16000输出token预算。若输出截断，在原cursor减小 `--max-characters` 重读，不能跳过余段。精简视图保留全部内容与对象层级，pointer-only `$read_ref` 指向同一 material 中的精确原值，重复文本及引用不重复展开；它不承接原判断的正确性。完整理解 required 后，核验主证据链、实际消费边界内的分句范围、重要反证、推断限制与条件。同一对象内唯一且清楚的范围可共享；单独摘出 changes_since_hour/deep 或其他句子时，保留或明确承接其必要限定，不能让多所价格主语覆盖单所成交。文件解析、输出 complete 和预检通过均不证明模型已审阅。需修正则更新本轮判断、重新预检并回读新候选。完成内容审阅后另行执行 `run-advance --run <run.json> --stage market --input <market_candidate>`，冻结同一候选；新冻结拒绝直接提交填写稿或重新合成的判断，只有已冻结结果可沿旧输入恢复、复核事实，且不改历史字节。跨指令版本恢复核验旧文件自身的payload/hash，以及原运行公开材料或与冻结对象完全一致的既有候选产物，保留原market_context_id；新候选仍按当前公开上下文绑定，不能借恢复改写旧结果。候选有改动须重新预检并回读审阅。不能在同一次调用中写判断并直接冻结，候选存在和预检通过均不证明语义已审阅。随后读取返回的 portfolio_context，只填写仓位检查和主评估必要性。旧版直接填写原观察稿。再 `run-advance --run <run.json> --stage observe --template <observation_template> --answers <判断JSON>`；程序承接原模板确定字段。旧 --input 完整填写稿仍可使用。revision 5 的旧五类 patterns 仅兼容观察，可按本次分析选择子集或填写空数组，不作必查清单或证据充分性门槛，extra_findings、research_findings、factor_checks 承载开放发现。程序绑定身份、证据和上下文，返回真实观察收据、gate、主评估与报告模板。每轮同时按 factor_context 指引核验旧依据并主动查找未覆盖或过时因素；精简上下文保留指引路径、公开来源入口、真实 section_provenance 与原评估时间，行情包不是全部研究证据。重大遗漏补证后用 research_findings 恢复原 BTC_CYCLE，不等待价格或金额变化；不由程序填“维持”。
3. 需要启动／接续研究或正式写入时，先用 `run-material --run <run.json> --stage main` 取得实际进度、研究请求和主评估模板，再处理 gate.cycle_review：verification_due 只表示本轮必须核验，required 才表示完整研究。首次基线或实质变化时，按返回的 cycle_request_template 和[v4周期接口](v4-contract.md#周期深评与嗅探)完成或恢复真实 Astra/max 独立研究及映射审阅。恢复前读 `research.issues[].latest_request.progress`，它列出原请求已完成的独立阶段、最新候选及同一 Astra 的真实引用；先核对运行/返回，再从现有阶段继续，不因跨小时或缺少最新结果重新派生一份独立深评。`runtime_status=NOT_CHECKED` 不代表代理正在运行或已经失效。原研究仍运行时，本轮完成必要嗅探和报告判断后结束，不反复等待或催办。重大风险可先走报告。中途补证继续原请求；最终 reveal 加 `--additional <已归档合并证据目录>`，保留最初独立输入并绑定实际最终证据。研究完成后 `run-material --run <run.json> --stage main` 取得当前主评估模板；默认报告材料单独按需刷新。需要发布已获批映射或核验当前事件排程时，读 read_set.main 及返回的 main_template，写本轮判断 JSON。在 components.updates 只填写受影响的现有 section；section_reviews 逐项说明为何重评或仍适用，checks 为模型内容审阅。`run-advance --run <run.json> --stage main --template <main_template> --answers <判断JSON>` 组装并发布，返回真实正式版本及新报告模板。旧字段缺失由本轮真实完成，不能标为历史复用。domain 确需更新时按旧 `prepare-domain` 接口处理；不逐小时重算整套档位。
4. 每轮观察后读read_set.report，沿原主论点→当前关键证据→机制及识别条件→现价参与价值→个人取舍完成实质判断，再对照最后真实正文独立判断通知。内部REVISE、短涨跌、资料或风险字段变化不直接推送；主线受到真正反证、参与价值实质改变、重要纠错、实际重大危险和每日反馈分别处理。随后执行 `run-advance --run <run.json> --stage report --template <report_template> --answers <判断JSON>`。模板及其 `_binding` 不改写，由程序承接原上下文；answers 不包含 _binding。模型填写 maintenance 的分析、全部旧条件检查、新复核条件和期限；新数据或新冲突须先判断其对结论、风险和安排的实际影响。report_basis 绑定已采纳完整报告，模型填写 mode 和 verification；首次缺少正文时用 LIMITED_FEEDBACK，不伪造周期总结。REPORT 时用 AUTHORED_REPORT_V1 将已完成的综合研判写成 layout.body，汇报环节不重新排列因子或增加另一套解释；bindings 绑定正文原片段与冻结来源，价格声明对象和单位，price_status 按真实正式建议填写。表格、段落、列表与长度由内容需要决定，程序不重排、不插入时间或追加建议。information 引用实际正文原句，内部分析留既有评估、研究材料及maintenance。quality_review 在冻结前按手机表达契约核对结论、依据、安排和必要边界；旧冻结契约4及新稿契约5的 decision_delivery、evidence_priority、maintenance_separation、narrative_focus 须对照实际全文核验当前状态、未来竞争路径与取舍、关键证据、维护分离及重复删并，生成提示与 layout_template 共用该契约，不把内部检查项逐项复制到正文。必要数字、报价和期限来自本轮冻结来源；产品价缺失只限制价位结论。SILENT/EVIDENCE_LIMITED 时 layout=null。新稿契约5先对 REPORT 候选执行同一 report 的 --check-only，沿返回 review_read 回读实际全文及绑定来源，完成实质审阅后填 quality_review 与 reviewed_candidate_hash，再提交同一候选；任何缩写、改序、删条件或收束改动须重新预检、回读和审阅。正式 REPORT 返回冻结 source 与正文；SILENT 不产生云端动作。
5. 仅 REPORT 或已有不确定交付需要时，读[报告和交付契约](market-reporting.md)、[报告复核](report-care.md)、[手机表达](mobile-report.md)及[v4交付接口](v4-contract.md#独立源稿和展示稿)，按旧 enqueue、BEGIN、真实发送、BIND_REQUEST、实际助手回读、CONFIRM 完成。工具返回的 source 不是已送达；完成时间取实际助手时间，十三项核查保持。

模板中的 null、空数组和说明没有表达“通过”或“无需更新”。具体字段规则见 [v4契约](v4-contract.md)；原始引用两套口径保持：market_review 等使用 vocabulary 中的字符串引用；factor/research 等使用 reference_index 的 `{source_id,pointer,value_hash}`。程序只生成实际存在的引用；不要手算或改写 hash。

`--answers` 与 `--input` 二选一，前者必须同时提供本阶段原始 `--template`。对象按字段合并，数组由本轮完整替换；仅承接模板确定字段，不读取上轮填写稿，不自动生成市场判断或把未知补成通过。模板被修改、绑定失效或必需判断缺失时仍拒绝；程序保存合成填写稿并沿用原意图、校验和幂等恢复。同一内容通过两种入口具有同一重试身份。直接用文件工具写 UTF-8 JSON，不逐轮生成填表 Python。

周期观察模板同时携带applicability_review：绑定basis_result_id，独立填写outcome=VALID/EVIDENCE_LIMITED/REVISE、explanation、原始refs及limitations。是否研究与是否已验证分别判断；VALID说明本轮实际核验，REVISE使缺陷持续待修，证据受限不自动完整深评。旧观察模板没有此项时复用同一冻结行情及补充证据重新prepare，只补实际判断，不重做研究或改写旧观察。新手机模板携带consensus_price_revision=2；已确认撤回的价位在price_status用WITHDRAWN，不再从历史报告回填，历史冻结稿按原版本回读。已确认目标配置按本地事实承接，不重新称待采纳。

启用完整报告策略后，report_context 携带简短 cycle_report_instructions；观察 context 通过 method_references、applicability_instructions 与 factor_context 引导核验。先独立核验当前市场变化并完成影响判断，再对照已采纳正文及最后真实展示，分别判断深评和报告；提示不生成市场结论或新增策略状态。既有 cycle_basis、deep_report_summary 与 report_basis.verification 具体核验正式研究、本轮判断与最终正文的忠实性，遵守职责边界：方向和期限清楚、依据支持结果、个人安排及必要风险和限制准确，不要求所有内部期限、冲突、状态和核验结果进入正文。分析缺项回原研究，表达遗漏在冻结前补齐；短窗变化不冒充中期反转，中期未反转也不屏蔽重要波段风险。重要价格或条件仅在已批准完整正文时，按手机契约用narrative的excerpt精确摘录绑定，不因没有独立标量字段省略。精简上下文也携带 spot_risk_review_instructions 方法提示。模型在既有观察摘要/extra_findings 中沿周期主线和当前定价，结合持有目的与已确认事实持续比较参与、防守和等待，写入支持、反证及有据取舍；实质研究变化才交回原深评，不能从短线走弱直接推出减仓。必要的敞口和保护核对留在内部；账户缺口只限制依赖它的数量、覆盖或净行动，不停止公开分歧区、下行路径和条件性价值分析。风险动作仅在确需应对且动作有依据时附加，其他机会建议不受这个门槛限制。提示本身不是已完成评估；程序不从价格生成减仓决定。新研究按 cycle_assignment 的方法入口展开完整组合分析；最终六项审阅仍使用 portfolio_waiting，不另建结论字段。

映射候选送审前先完成可做的结构、精确数值、原始引用与事件身份预校验，不能用正式发布反复试探格式。新 ASSESSED domain 可用现有 `assessment.validate(draft, evidence_dir, storage.domain_baseline(root, storage.current(root)), project, review)` 做只读校验，它只创建临时兼容视图，不写当前策略；预校验不替代实际 Astra 审阅、候选哈希和发布时重验。`cycle.validate_candidate` 也复用现有 recommendations.validate 检查数字和 `from <= knowledge_cutoff < until`，并不以壁钟替换证据截止；原始引用仍需对受控证据解析。实际建议生效由真实 published_at 起算，不为匹配未知的提交秒数反复移动 from。REUSED domain 必须满足既有来源、内容一致性及到期门，已到期不能靠 LAST_VALID_ANCHOR 或复用标记跳过当前事件复核。修正影响候选内容时，重新冻结并交同一 Astra 审阅，不能沿用另一候选的批准。

## 选择材料与重试

- `--market <已有完整行情包>` 跳过网络取数，适用于本轮补证和隔离回放。正常取数总是包括已收盘日线和周线；同一完整日/周窗口沿用原取证时间，新收盘窗口刷新。`--deep` 强制刷新以核验修订或事件影响，不代表调用 Astra。慢变量保留原发布时间，按来源更新和机制变化选择补证。
- `--additional <冻结证据目录>` 纳入模型按问题选择的官方公告、日程及其他公开原始证据。factor_checks 记录覆盖判断，research_findings 保存实质遗漏与真实引用；关键来源失败保留具体缺口和期限，不能写成无变化。先按现有 freeze-evidence 冻结，保留数据期、发布时间、取得时间和修订；补证后用新工作目录重新 prepare 并审阅受影响部分。新独立运行不继承整条父包；旧策略实际引用及 evidence_bindings 保留闭包。同一小时测量未变但取得时间变化时，为新的原始字节保存独立来源身份，旧引用和公布时间不覆盖。
- 慢数据没有“每小时失效”的统一规则，也不能因再次读取就更新原分析时间。section_provenance 保留真实祖先版本；内容变了即重新评估。context 中当前结论、未决问题和原评估时间是选择下一证据的入口，原始材料按需展开。
- `run-advance`保存命令结果时使用`--receipt <本轮目录>/<stage>-stage-receipt.json`，该路径在打开前校验。`market-result.json`、run/context/template/input等路径由程序或原输入拥有，不得用stdout重定向覆盖；市场结果与阶段返回收据不是同一JSON。宿主会在派发前识别stdout与已有本轮文件冲突并报出路径，但shell可能已先截断目标，因此不能把报错当成旧内容仍完整的证明。保留原材料并按同阶段恢复，不猜写或覆盖业务结果。
- 失败后先读本轮 `*-receipt.json` 和 `*-intent.json`。相同 run/stage/填写内容重复 advance 返回原收据；若底层已完成而封装收据中断，可识别实际观察/主评估/报告审阅再完成材料。旧收据只证明旧动作完成，不保证此刻仍适用。
- 绑定变化返回 changed_bindings 和已完成 research_progress，先 run-material 刷新当前阶段并只审阅差异；需要新小时或新补证时才新目录 prepare，复用已有行情和冻结原文，不只替换身份或 hash，不因时间变化重做独立研究。事实、候选与正式版本真实冲突仍须重验。旧填写文件和未知投递身份保留。run-prepare 失败没有正式成功含义；底层完整性错误继续使用 recover 和原 CLI 定位，不反复重跑取数。
- 已完成研究的冻结行情早于最新小时观察时，main 组装、材料刷新和提交使用只读冻结上下文；后续观察单独展示并须复核适用性，不能混入冻结证据或倒写其时点。最新观察身份继续参与并发校验，小时 observe 仍拒绝旧小时覆盖。研究、候选及其映射审阅沿原阶段接续。
- AUTHORED 正文的评估时间绑定已核验模板的真实冻结时间；填写期间跨分钟不要求改写正文。提交时仍核对当前上下文身份，并以真实提交评估时间检查建议有效性和维护期限，不能借旧正文时钟续期。
- 正常运行按返回路径读取一次必要材料，批量读相互独立的小文件。无需反复查看所有说明、`--help`、源码或历史大 JSON；契约不清楚时只读相关小节。

## 分析填写示例与审阅

以下是条件性写法示例，不是当前行情结论，也不是固定必选因子：

- factor_assessment：资金传导改善可能先影响融资成本，再影响现货需求；目前只能确认前一环，后一环证据不足。下一份实际资金流或融资数据应能区分“改善已传导”和“价格仅反弹”。原数据期与公布时间照原始引用保留。
- outlook.narrative：结构锚未迁移，但若融资改善持续并出现资金承接，中期价值预期可以先改善。兑现机制尚未闭合，分析窗口不等于已知兑现日期；如果预定复核时仍无传导证据，应降低这一解释的重要性。
- portfolio_analysis / robustness：等待使现货继续承担回撤，也保留上行参与；保护空单可能减少下跌损失，但承受反弹与资金费成本，已知和未知成本分开；现金保留下一机会的选择权，也承担错过上涨的机会成本。分别评估原有与新增模块以及两侧先后占资。
- conclusion：实质时间优势改变时同步 business_meaning 与受影响前瞻；若金额保持，说明为何现有金额在新解释下仍合适，不能仅写“未到价”。旧版缺失上述分析时实际补充，不借 section_provenance 标为 REUSED。

在现有 checks 中完成内容审阅：competing_explanations 说明价值差异、收敛机制及周期证据；portfolio_completeness 说明等待成本和组合安排；semantic_continuity 核对真实复用、未按期兑现的处理及重要含义。证据不足可形成完整保守安排，说明下一次有意义的证据/事件/时间，不强造公平价格、已定价比例或兑现日期。程序不做关键词评分，也不规定方向、因子数量或研究次数。

## 周期研究与补充选题

先明确主要投资周期及其机制、价值和风险，小时数据用于检验而非决定研究主线。独立市场summary、对应forward、BTC semantic主情景和正式正文须一致；短线剩余期限、区域回收或下一复核时刻不能自动成为主周期。若发现正式结论已偏向日内交易，沿原BTC_CYCLE核对并修正研究、映射及成稿，保留旧记录和仍有效结果，不只改报告标题或延长小时期限。当前用户纠偏立即用于在办研究，同一代理据公开证据续研，保留原物理代码绑定，不为方法修订登记程序兼容迁移。

主任务先读当前纯市场基线及后续有效观察；仓位阶段再读完整周期正文与个人下行方案，再从 research、next_evidence、factor_assessment、outlook、report_care.unresolved 识别反证和未覆盖问题；评估重要性、可解决性和策略影响。请求的 question 自然写清具体问题、竞争解释和不同答案如何改变策略；change.refs 给出能够区分解释的实际证据，reopen_conditions 保存下一鉴别条件。缺少完整周期基线必须建立 BTC_CYCLE 研究；证据不可获取时说明限制和下一有意义的复核条件，不用每个小时新行重开。沿用[v4专项接口](v4-contract.md#专项接口)的独立阶段与候选审阅，主任务明确 ADOPT/PARTIAL/REJECT、反证、未决与新增发现的实际影响。

## 运行量与自然验收

普通静默轮次不展开 main_template、cycle_request_template、layout_template、report_metrics 或交付文档；只填写实际需要的观察与报告审阅。read_set 的完整正文逐段读取；后续阶段通过 $read_ref 和 $report_ref 引用同一冻结材料。已在本会话完整读过的相同内容不重复读取；新会话先读 base_read_set，新正文与改变部分仍完整读取。原 context 和完整校验材料保留兼容，不同时重复展开两套视图。额外发现严格用 vocabulary.extra_finding_fields；引用按 vocabulary.reference_formats 选择。新报告直接填写最终正文；数字通过 bindings 绑定真实原句及冻结来源，程序不插值或自动生成价位。

`run_usage.py --since <含时区时间> --until <含时区时间> --output <本地文件>` 分别统计原任务及子代理、失败和未完成的真实消耗；同类比较仅使用窗口内主任务及其子代理均已完成的运行，每轮消耗包含子代理，有子代理则归入研究组。响应按 response_id 去重并按时间窗口裁切，分别记录模型响应、工具调用、输入、缓存输入和输出；按有无专项/报告分组比较，完成中的运行不计成功。缓存包含在输入内，不能再相加，计量缺失保持未知，不换算额度比例。`--baseline` 比较已冻结基线，`--defect` 只向既有缺陷补充用量证据并保留失败，不能自动代替质量和实际送达验收或关闭缺陷。

切换后三个完整日本自然日利用原任务观察：实际分析回答了什么、每日真实送达、同类普通运行模型往返中位数是否比冻结基线下降至少50%。这是验收目标，不限制研究、不截断运行。工程测试、冻结场景及自然验收分别记录；未发生的专项只报告隔离测试。审阅以价值、时间和组合解释的证据为准，不以调仓次数或报告篇数计分。

周期修正与既有降耗验收分别保留；本次需求优先修正的日检查和三日窗口见[运行维护](runtime-maintenance.md)。先用 run_usage.py --status 只读检查待办，已完成的日检查不重复，不能用工程测试或新研究收据代替自然运行证据。

revision 5 深评独立阶段先交付纯市场 JSON，冻结后在同一研究中追加个人应对，Sol 将完整正文原文写入 conclusion.narrative；第二阶段 Astra 在结构化结果中用 report_narrative 返回同一正文，或按 v4 契约用 report_hash 绑定本人已写且本次已读的冻结正文，避免重复输出。结果登记保留真实原回复及绑定全文，并支持已确认的末尾通知标记；不能手工删改真实回复或补造批准。研究来源、模型或实际输入无法核实时，保留现场并暴露交付异常。

## revision 7 风险结果与建议

新 CLI run-prepare 使用 revision 7 市场→组合→观察→报告材料，细节见[完整闭环](closed-loop.md)。market 阶段复用观察校验；按正常路径预检、回读并审阅实际公开候选，再冻结同一内容。结构、引用、时间和上下文绑定由程序重验，语义范围由模型核验；组合材料含事实版本及已冻结结果，不附原始采集或混合结论。仅事实变化可用同一公开输入再次执行 market，获得新组合模板，原 market-result 不变。observe 的 allocation_review 绑定 portfolio_risk_hash 和 strategy_advice_hash；主评估由真实 risk_assessment 进入 revision 7，风险应对可为空，原有机会建议独立保留。旧 allocation_decision 仅按原版本恢复，不自动转换为新风险结论。

report 材料只含最终两项结果、适用性及必要来源，result_binding 已由模板绑定。完整内部研究不通过报告 read_set 再次提供。旧 run.json 未含 workflow_revision 时按旧上下文接续，不为升级重建输入或重发报告。

公开上下文使用 MARKET_INSTRUCTIONS；市场冻结后才提供本轮可读 fact_files 及 fact_availability。不可用个人文件的 fact_version 值为 null 并保留原因，公开阶段继续，个人阶段具体限制依赖该资料的判断，不能推为空仓/安全或复用另一事实版本的动作。正式个人采用继续原完整事实校验，不以空文件绕过。reported.display 标为 DELIVERED_HISTORY_ONLY，只用于通知比较。新报告契约 3 的 market_independence 对照公开命题、个人映射及完整正文，末尾个人选择也不得回写公共总裁决；全部实质正文 bindings 只证明来源归属，语义由模型核验。

## revision 8 投资决策接续

按[投资决策及反馈](investment-process.md)维护事前 policy 输入。公开研究先独立冻结 direction/momentum/price_attractiveness/strategy_applicability；个人阶段消费原风险、机会、所有 open_investment_issues 及 investment_feedback，逐项完成 issue_reviews 和是否需上游更新。正式改动沿原研究身份与 main 采纳；investment_decision 是同一 recommendations section 的综合责任，不新增正式 Owner。报告从 finished-result 接口消费已完成总体取舍。新增 investment-policy、ledger-append、portfolio-performance、strategy-backtest、judgement-evaluate 沿原 strategy_v4 CLI，仅写本地源绑定输入/反馈视图或返回计算，不交易、不发送、不改调度。

新稿报告契约5通过run-advance --stage report --check-only导出实际全文候选及review_read；预检不记录正式报告、不发送，也不证明语义。实际通读来源和全文后填写具体quality_review和reviewed_candidate_hash，再记录同一正文。改写使旧绑定失效；旧冻结3/4及UNKNOWN仍按原内容恢复。完整职责、有限反馈边界及最终接口统一见[手机表达](mobile-report.md)，不在汇报阶段重新研究。
