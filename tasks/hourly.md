# 交易系统主任务

按当前项目、真实触发身份及原节点执行[交易系统闭环](../skill/btc-core-strategy/references/closed-loop.md)。从节点的 `btc-core status` 取得现行入口。市场研判用本次真实触发身份调用 `btc-core enter --run-id <本轮身份>`，使用回执中的物理发布目录、节点配置、解释器和 Skill。已结束的同一触发返回原结果。在途运行接续原输入。实际准入拒绝按原作用范围处理。

## 用户明确更新开单价

本轮原生任务直接收到用户更新原 BTCUSDT 空单开单价的明确指令时，使用本节点运行 `btc-core status` 的同一宿主可执行路径，以 `data` 子命令从标准输入提交请求。`price` 取用户确认的 USDT 十进制价格，`confirmed_at` 取真实确认事件的时间，`confirmation_ref` 取原生 `userMessage` 标识或确切确认来源引用，必要时用原生 `read_thread` 核对事件。`operation_id` 固定为同一确认事件的写入身份。只以本轮直接收到的明确用户指令为更新依据。定时触发照常读取当前价。

```json
{"method":"trading.set_entry_reference","params":{"price":"<确认的 USDT 价格>","confirmed_at":"<确认事件的 ISO 8601 时间>","confirmation_ref":"<真实确认来源>"},"operation_id":"<这次确认的稳定标识>"}
```

该操作独立于行情冻结及研判运行身份。纯改价请求以该次保存并回读的结果作为反馈。同一确认重试沿用原回执，后续研判读取当时的最新值。仓位事实由独立确认更新。

## 使用统一入口

将以下 JSON 写到标准输入。命令使用 `enter` 返回的物理发布目录。读取完整结构化回执和实际退出码。每次写入使用与同一动作及同一输入绑定的 operation_id。传输结果不明时核原状态或重用同一身份，同一已占用发送尝试只读回。

```text
<python> <release>/scripts/release.py --node-config <node_config> trading --run-id <原生运行身份>
{"method":"trading.open"}
```

`trading.open` 收集及冻结公开输入，重入读取原材料。以当轮 `trading.public` 返回的正式状态确定已采纳市场结果、应检查的原条件和公开研究，历史摘要仅用于定位原件。返回同时包含完整窗口、来源缺口和辅助附件。通过 `trading.source {digest}` 查实际冻结原文。通过节点现有 `prices.products` 和 `prices.query` 查询正式结构化市场序列，使用实际交易产品和当时可知截止。辅助采集、转述和同源观察只作线索，缺失保留未知。其他公开数据从真实公开来源获取，沿 `trading.evidence {source,url,observed_at,retrieved_at,content}` 保存实际观察、获知时间和来源正文。

使用 `{"method":"trading.<操作>","params":{...},"operation_id":"<原身份>"}` 提交写操作。读操作只需 method 及必要 params。服务端从原运行解析发布与权限绑定。角色提示、报告和业务方法采用实际职责描述。

## 研判与个人决定

1. 先对照前次独立市场结论、当前证据及其事前条件，判断原主情景的驱动和反证是否兑现。说明本轮证据影响的是日内观察、中期判断还是现价参与价值，以及参与相对等待的净补偿。从上下文的 `review_targets` 取得可核验的原条件，用 `trading.review` 核查出现相关新事实或已到期的条件。提交返回的 `target {kind,object_id,version,digest}`、原 `condition_id`、`outcome`、`explanation`、`evidence` 和实际影响 `impact=KEEP/REVISE`。结果使用 SUPPORTED、PARTIAL、REFUTED、NOT_TRIGGERED 或 UNKNOWN。没有固定日期的条件按相关新事实核查。后置事实须带确切来源，证据不足和未兑现沿原条件保留。
2. 有重要反证、缺失或明确重评要求时，用 `trading.research` 承接实际问题及来源，参数包括 `scope,subject,question,evidence,expected_version`。公开研究只交公开问题及证据。已有研究从原状态继续。新的原生研究调用通过节点 MCP 的 `model_deep_claim` 或 `btc-core data` 的 `model.deep.claim`，提交 `{claim_id,issue_id,scope,phase,input_digest}`。由 `trading.research` 的 `result.object_id` 取得 `issue_id`，由 `result.payload` 取得 `claim_scope`、`phase` 和 `input_digest`，将 `claim_scope` 传给 `scope`。为本次实际尝试固定 `claim_id`，接续同一尝试时沿用。领取的新尝试按 `CLAIMED_NEW` 启动，`REUSE` 沿原结果核对。

   取得真实完成正文后，计算其 UTF-8 字节的 `result_ref=sha256:<64 位十六进制摘要>`。用节点 `model.deep.finish`（MCP 中为 `model_deep_finish`）提交 `{claim_id,outcome:"COMPLETE",result_ref}`，再用 `trading.research` 追加 `outcome {claim_id,result_ref,conclusion,origin_run,agent_id,turn_id}`。 `origin_run` 取发起研究记录的 `source_ref`，`agent_id`、`turn_id` 取产生确切正文的原生子任务与完成回合。后续从 `trading.research_result` 读取确切原文。原生完成来源无法核实时保留原请求。主轮可在研究待办期间继续有依据的独立工作。
3. 用 `trading.market` 提交正式市场结果。新判断提供 `expected_version`、`evidence`、`result {conclusion,primary,alternatives,value,assessments,valid_until,conditions}`。assessments 按有必要单独引用的公开问题保存实质判断。若某个原条件的复核只改变特定资产的定价或参与价值，更新对应的具名判断，即使总的主情景维持。周期依据与现价参与价值分别登记。对现货在 assessments 中按独立产品或适用组合写出判断，用 result.valuations[同名项] 标明现价估值。定性估值可以用空对象；有依据时附 value_zone 和相关公开条件的 condition_ids。现货成员在 market_refs 引用本组实际使用的 valuation/<名称> 和所需的周期依据。全市场 value 确实涵盖本组定价时也可单独作为现价依据。若旧市场缺少本次取舍需要的产品估值，先补入公开结果再冻结。现价、成本或同一条件两次部分兑现之间的实质进展足以改变净补偿时，即使主情景沿用，也要更新对应的具名判断。

   每个条件给出 `{id,criterion,review_at}`。仅在预期有明确兑现截止时加 `observation_end`，用事实发生时间判断是否按时兑现，用实际获知时间决定哪轮能评价。改写或拆分尚未完成的条件时，在新条件上给 `predecessor:{target,condition_id,reason}`，说明承接哪一部分。明确结束的旧条件可列入 `result.retired_conditions`，使用同样的原条件引用和理由。按 `review_targets` 返回的目标复核。相关前项的 `prior_check` 用于读到先前已确认的范围，新条件从实际提出时独立检验。需查更早一代时，用同一阶段的 `trading.public` 或 `trading.personal` 传 `{condition:{target,condition_id}}`。既有记录只用文字写了承接关系时，核对历史版本中的原标准和复核，确认仍适用后给当前条件补确切前项。沿用原标准的条件保留其形成时点和已有复核。

   判断区间位置是否变化时，先将前次可比报价、本轮报价及期间已核实的触价记录放回当时适用的同一区间，区分价格变化、跨界与边界调整。行情只支持两个时点均在区间内时，保留原位置判断，只更新报价、时点及其实际影响。

   沿用既有有效判断时传原 `market` 确切结果引用、`reason` 和本轮 `evidence`，保留原判断的期限。引用使用 `BLOB {type,digest}` 或 `OBSERVATION {type,product,series,item_key,observation_key}`，用到已完成研究时引用其确切公开实体。需要反馈的实质新决定同时提供 `notification {kind,reason,due_at}`。
4. 公开结果冻结后读 `trading.personal`，按原条件及当前价格基准核查需要复评的决定。依照[交易决策主线](../skill/btc-core-strategy/references/decision-design.md)，以当期已确认的组合、预算、成分及用途，在同一公开主情景和竞争情景下比较各组投入、独立机会、现金留存与存量仓位。按产品用途决定取舍。现货衡量现价是否已值得积累，定投与额外投入各用自身预算；原保护性空单比较减持、不变和增加对下行补偿、反弹损失与未来成本的净影响。现货取舍及合约前瞻补偿真实互相影响时，说明哪个选择依赖哪个输入。实际保护总额只计入已确认的覆盖。首次按三向评估仍适用的原空单，重新比较该成员；其他成员按其实际变化处理。

   用 `trading.portfolio_decision {expected_version,market,primary,alternatives,interactions,decisions,reuse,member_reviews}` 登记。版本取 `personal.portfolio.version`，初次为 0；`market` 传本次评估使用的 `personal.market` 确切引用。`primary` 和按冻结顺序排列的 `alternatives` 写各情景的组合净效果。`interactions` 项为 `{subject,depends_on,reason}`，分别写受影响的决定、它实际依赖的另一项决定及依据。所有适用决定各提交一次，`decisions` 放新评估，`reuse` 按 subject 放上个组合中仍有效的确切引用 `{kind,object_id,version,digest}`。前一组合缺席时完成新评估；同一组合仅复评相关市场、事实、条件及实际联动变化所影响的决定。

   重新判断会影响选择的依据和联动，包括本轮新出现的因素。对跨轮含义影响行动或排序的成员，在 `member_reviews[subject]` 给出 `{relation,reason}`：CONTINUE 沿用、REPRICE 对原安排重定价、REVISE 调整、NEW 新安排、UNRESOLVED 关系待明。说明为什么选择或沿用这个方案。相同方向可以对应不同安排，已完成数量只取已确认的操作。范围有缺口时交付可判断的组合，保留其他成员的缺口和原决定来源。先核原始确认措辞是否明确结束该配置，再按 policy_status.ended 查已登记的结束范围。既有组合的最后一项适用配置确认结束时，沿同一组合入口提交空的 decisions、reuse 和 interactions；仅仅缺少资料时保留原成员待查。

   每项新决定提供 `subject,expected_version,policy_basis,facts,fact_refs,market_refs,result,evidence`，另有适用的 `notification`。facts 列实际使用文件及摘要，fact_refs 为 `[{file,quote}]`，market_refs 选择实际依赖的市场字段。来源文字实际变更时，用 `trading.policy {subject,kind,fields,refs,expected_version}` 更新受影响的范围。result 含 `decision,reason,risk,opportunity_cost,valid_until,conditions`。

   月度组逐组比较足额、跳过和有依据的分批。说明在主要与竞争情景下，哪项收益、成本、共同风险或等待代价决定了排序。历史波动测算说明风险规模，分批金额要有独立的配置理由。区分本月安排和当前费用、价格能否支持实施。填写 `comparisons [{amount,reason}],amount`，分批加 `amount_basis`。独立机会按净补偿评估，正额带 `value_zone {lower,upper,quote_unit,basis}`。投入真实未决时填写具体 `gap` 和 `amount:null`。

   原空单成员的普通 UPDATE 采用 DIRECTION，附实际理由。定时到期、重要风险和更正按自身含义登记，正文展示该成员正式方向。

   原空单的开单价与来源从 `policies[subject].entry_reference` 读取。价格基准适用时，新决定的顶层 `entry_reference` 传该对象的 `revision` 字符串。在 result.direction_comparison 中分别以 REDUCE、KEEP、INCREASE 为键，记录同一市场情景下三个选择的实质依据。result.decision 采用 REDUCE、KEEP、INCREASE 中的一项，减至零可选 EXIT；研判范围为 PRICE_BASELINE_DIRECTIONAL，仓位事实可以为 UNKNOWN。真实可能改变方向的市场证据缺口用 UNRESOLVED 并说明 `gap`。确认已平时给出 NO_POSITION 并省略三向比较，提交时省略顶层 `entry_reference`。当前开单价缺失且仓位未知时，沿有依据的条件判断，在 `result.conditional_actions` 以 PRESENT、ABSENT 记录各自的 `{decision,reason}`。在仓分支确实未决时注明其 `gap`。在仓分支的三向比较也写在顶层 result.direction_comparison。后续改判条件写入 `conditions`。

   对减持原空单的结论，比较现价先回补和等待的净得失。在 `result.reason` 写明是否在现价先减，或等哪个价带再减。可确定两端时，把本轮回补价带写入 `result.value_zone {lower,upper,quote_unit,basis}`，说明上下沿的经济依据和合约报价口径。仅在仓分支减持时，该价带只适用于 PRESENT。只能支持参考价时省略价带，写明时点、参考价和哪一侧边界缺什么依据。余仓改判另列入 `conditions`。价带、首批与余仓依据供后续核验和针对性追问使用。

## 报告、投递和下一轮

读 `trading.feedback` 获取应反馈事项、在途尝试及同一目的地最后实际确认的结构化底稿身份 `base_report_id`。独立市场冻结后才取 `report_base` 的模块、评估时点及待复评范围。没有可用底稿时，`base_report_id` 为 null。用 `trading.feedback {decision_id}` 查同一正式对象的 `previous_display`，按其正文、确切决定和本次组合的 `member_reviews` 核对上次判断及安排。需要查更早展示时传前次结果的 `report_id` 作为 `before_report_id`。

决定整轮静默前，核对冻结市场的到期条件、当期配置的适用范围，以及哪些已采用的成员受期限、反证、个人事实或联动变化影响。先复核并采用受影响的决定，再核对各对象的重要待反馈事项。已核为部分兑现或待证的条件按其下一适用期限续查。实质变化按现有 UPDATE 路径处理，普通重复信息保持静默。已开始的投递沿原尝试核收。

`trading.personal` 返回的 `scenario_review` 列出当前市场与已采纳组合原市场的主情景、竞争路径及其绑定。路径的驱动、相对地位、风险补偿或组合净影响确有变化，用 `trading.portfolio_decision` 采纳当前组合比较，适用成员可继续 reuse。仅描述变化且核实经济含义与组合取舍仍适用时，静默提交可附 `scenario_continuity:{portfolio,market,reason}`，引用该比较返回的精确组合与市场绑定，解释两种情景对原安排的影响。原依据缺失或竞争路径数量改变时通过组合入口重评。用 `trading.assess_feedback {outcome,reason,evidence}` 记录本轮实际结论，证据或交付受限用 EVIDENCE_LIMITED。已核实的显著风险可在受影响的研究未采纳时先作有界反馈。

日报、盘中更新、更正和风险反馈都交付完整模块。市场研判负责市场、展望、机会、交叉验证及市场风险这五个公开模块。需要成稿时用 `trading.feedback {public_writing_inputs:true}` 取公开判断、已认证的公开段落、`base_report_id` 和 `previous_copy_id`。在本轮首次 `trading.market` 冻结新判断或复用原判断时，可随附 `public_copy {base_report_id,changes,market_sources,reviewed_unchanged}`。首次完整公开稿填全五模块，其后只改受影响的子项。若该市场与底稿已有公开候选，附 `previous_copy_id` 并从该候选继续修改。只在确有反馈理由时成稿，沉默轮照常研判。沿用相同市场、公开依据与已认证底稿时，组合可直接复用公开段落。

若目标底稿缺少公开归属，先冻结本轮独立市场。获准读取原实收稿的组合侧在 `trading.feedback {writing_inputs:true}` 中读原段和 `legacy_public_digests`，对照实际原件一次提交 `trading.review_public_baseline {base_report_id,items}`。items 以八个公开子项路径为键。整项已核实可公开时交 `{digest}`。混有个人内容或原判断缺失时交 `{digest,keep:[逐段核准、按原序保留的原文],reason}`，没有安全可用原文时 keep 为空。公开作者从 `public_writing_inputs.previous_public` 读回已核正文，继续以原句为底稿补丁更新。被排除的原文只由获权的组合侧读取。发现筛选错误时，在同一实收稿上带回原 `previous_review_id` 纠正，基于旧筛选的未发稿重新核对。当前公开作者尚未取得合格底稿时，该次市场研判照常冻结，实际报告沿原反馈义务接续。

若组合研判后才发现应出稿，沿 `trading.market` 复用本轮确切市场并补交公开补丁。旧段的 `from` 仍取上述公开底稿，新的 `to` 使用 `{"extracts":[{"source":"condition/<确切id>","quote":"该条件中的连续原句"}]}`。也可按 `conclusion`、`primary`、`value`、`alternative/<从0计数的序号>` 或 `assessment/<正式名称>` 摘录。本轮独立冻结的市场沿用理由用 `source:"market_review"`，本轮在冻结前完成的公开条件复核用 `source:"review/<condition_reviews 中 review_ref 的 digest>"`，从相应理由或解释中摘录。省略 `quote` 取整条，多段按顺序展示。删去已失效的旧片段时 `to` 可用空字符串。只有真正缺少原文的子项直接给撑得住的新段，后补时该段使用公开原文 extracts。

组合采纳后用 `trading.feedback {writing_inputs:true}` 取配置各组、独立机会和现金的正式来源。常规 `trading.preview_report` 的 `changes` 只传 `allocation` 修改，无配置改动时给空对象。各成员依据自身的已采纳安排撰写，现金取各月度组的留存。如果真实联动改变了某组的选择，只解释对该组取舍的影响。原空单的参考价、首批、余仓与内部执行比较保存在对应成员的正式决定中，其他配置组及现金行也维持各自用途。原空单方向行由服务生成。实际已有配置行时，以一个 `{from,to}` 或多个 `{edits:[{from,to},…]}` 定位原稿中的变更处，保留仍有效的金额、单位、选择和理由原句。两组留存的组成、预算、计划额、月份和币种都沿用时，现金行直接沿用。需要纠正已展示的具体错误或更新确已到期的原句时，对该项补丁附 `correction:{source,reason}`，`source` 使用准确的成员绑定，现金使用组合绑定。该项原判断整体失效时提交 `{replace_all,reason,source}`；只改一条理由时继续使用局部补丁。

预览和终稿都给 `purpose,base_report_id,assessed_at,changes,decisions,reason,obligation_ids`。原样传回底稿身份，`assessed_at` 取本轮实际形成判断的时刻。`decisions` 引用冻结市场、已采纳组合 `PLAN` 和全部适用成员。非日报另给真实 `event_ref`。预览返回完整 `body` 和 `public_copy_id`；审读通过后用相同参数和该 `public_copy_id` 提交 `trading.report`。公开稿或实收底稿有变化时，读回并在最新稿上重核。成稿的各子项如下。

| 模块 | 子项与正文内容 |
| --- | --- |
| `market` | `BTC`、`SPX`、`NDX` 各自的方向、当前定价及结构变化。区分底层市场与目标 VOO、QQQ 产品的身份、价格和单位。 |
| `outlook` | `primary`、`alternative` 分别说明中期主情景和竞争路径、机制、把握及改变排序的证据。 |
| `opportunities` | `comparison` 比较现价参与或等待的风险收益与成本，说明为何现在值得参与、等待或回避。 |
| `allocation` | 以适用成员的正式 `subject` 标识各组月度决定及独立机会，`cash` 说明规划留存。按月度组、留存、独立机会、存量仓位呈现。`existing-position` 的方向行由服务从正式成员生成。 |
| `crosschecks` | `evidence` 给出独立来源、分歧及其对判断的影响。 |
| `risks` | `conditions` 给出主判断失效或安排需要调整的条件、相应影响和待复核范围。 |

公开作者在 `public_copy.market_sources` 中把 `market.BTC`、`market.SPX`、`market.NDX`、`crosschecks.evidence` 和 `opportunities.comparison` 分别对应到该段实际使用的冻结公开依据。例如本轮确实采用相应判断时，可传 `{"market.BTC":["assessment/BTC","valuation/BTC_VALUE"],"market.SPX":["assessment/VOO"],"crosschecks.evidence":["evidence"]}`。只有 VOO 判断里确实有支持标普底层结论的证据才能这样引用；`evidence` 对应冻结市场的原始证据引用。某路径首次未指定时，服务比对完整公开依据。已有归属跨轮沿用，只有该段实际来源改变时才更新。`outlook.primary`、`outlook.alternative` 分别直接绑定本轮中期主路径、竞争路径。

已有正文时，公开作者给 `public_copy.changes.<模块>.<子项>` 提交原位补丁。单处用 `{from,to}`；多处不相邻变化用 `{"edits":[{"from":"底稿中的原句或带上下文的旧数字","to":"已核的新说法"},{"from":"另一处原文","to":"对应更新"}]}`。所有原片段都在同一份底稿上唯一定位且互不重合。更新报价、时点或情景条件时，逐项保留仍有效的长期判断、原比较基准与风险证据。正式来源变化而原文仍准确时，用 `reviewed_unchanged` 指明该子项。正式依据未变、但原报告实质写错时，在公开补丁附 `correction:{source:"assessment/<名称>",quote:"直接支持纠错的正式原句"}`。只有原子项判断整体失效时，给 `{replace_all,reason,source:{source,quote}}`，说明为何整段需要更新并引用确切公开依据；正式依据未变时也要明确上述纠错来源。组合按正式成员变化更新所属子项。冻结前按[报告表达](../skill/btc-core-strategy/references/mobile-report.md)核对全文、参考时点、产品身份与配置。确认只动受影响内容后，用预览返回的公开稿身份冻结。

可核实的重大风险急需提示、相应判断仍待复评时，走受限风险反馈。提交 `purpose=RISK`、确切公共事件、空 `decisions`、有依据的 `valid_until`，在独立市场冻结前可在 `changes.risks.conditions` 撰写本次公开事件的已核影响。市场已冻结时，可复用同一事件先前保留的原短讯，或引用该事件的正式公开原句。要结合市场判断时传 `{"extracts":[{"source":"event","quote":"事件来源的原句"},{"source":"assessment/BTC","quote":"冻结市场的原句"}]}`，至少一条来自原事件。服务将短讯形成时点及该事件自己的有效期一同附在原风险条件之后。到期的原文以历史提示显示，旧记录缺有效期时标为待核。新事件使用自己的证据与期限，不能替此前短讯续期。`pending_review` 是该受限入口的子项路径数组，例如 `["market.BTC"]`。标明哪些已有判断受该风险影响、当前证据尚不足以复评。服务按同一目的地的完整底稿组装报告，标明沿用判断的时点及待复评范围。服务根据原决定的 `valid_until` 自动标记到期子项。尚未复评的子项继续带标记，并分别展示该项所据正式判断的采纳时点与标为待复评的时点。旧稿无法核实该项的采纳时间时明确写待核；原始行情观测时间沿用该段实际引用。确实完成复评后，才在常规稿的 `changes` 中显式提交该子项的结论，核准沿用时提交同文也可解除标记。整理风险短讯时，正式复核已有提示，按公开写作输入中的原短讯，把仍有效的影响写入当前 `public_copy.changes.risks.conditions`，说明失效或引起实际调整的部分，再显式提交该项，由服务收起单列短讯。首份常规稿同时补齐缺口。原空单行由服务生成，只有风险出现后正式采纳该成员的新决定，才清除其待复评标记。受限风险的 `trading.preview_report` 仅返回本轮公开短讯，公开阶段据此核验来源、时点、范围和待复评判断。含个人安排的完整结果由服务组装，沿既有授权投递通道交付。历史未送达沿 `trading.inherit_feedback` 查实际义务，只承接仍有意义的项。

首次风险先用 `trading.feedback {risk_seed_inputs:true}` 读取同一目的地的 `base_report_id`、`needs_public_seed`、独立采纳的公开市场原件 `public` 和原待反馈风险。若缺完整实收底稿，公开作者以 `public` 的判断、数据时点和证据引用撰写市场、中期情景、机会、交叉验证及市场风险五个模块。已冻结适用公开报告稿时可直接用其五个模块。否则，在原风险提交中另附 `public_seed {market,sections}`，market 取 `public.decision`，sections 按上述五个模块的全部子项给出原判断；本次新风险仍放在 `changes.risks.conditions`。若市场早已冻结而需后补，sections 的改动使用上述公开原文补丁。旧实收缺公开归属时，先在原组合侧核准旧段，取 risk_seed_inputs 的原市场版本和 previous_public；sections 只修改确实有变化的片段，缺项才补整段。服务从已采纳组合及其确切成员版本生成配置、金额、留存和原空单方向，然后叠加这次风险。过期、已变或缺当前采纳的成员保留具体范围及其原始时点；新风险可以先于新组合复评送达。首次重建及预览均不推进实收基线。

已核实的风险暂缺完整公开作者稿、正式市场或可用组合时，`trading.report` 返回 `AWAITING_REPORT_BASIS` 和原义务身份，并保留风险原文、事件与有效期。预览仅返回缺项。原任务沿 `pending_public_risks` 读回，依据补齐后用原事件、原短讯、原有效期、本次成稿时间与新的写操作身份再次提交。原有效期已过时，先复核其实际影响，在当前有效报告的 `obligation_ids` 纳入原义务，并在 `decision_coverage[原义务ID].excerpt` 给出本稿交代原风险结果的原句。公开阶段只读取并提交公开内容。

同一对象的新决定要履行较早决定的待反馈义务时，在 `decision_coverage[原义务ID].excerpt` 放入本稿说明怎样承接或纠正旧决定的原句。已确认结束的个人配置另附 `policy`，取该对象反馈查询的 `recorded_scope_end.policy`。原空单成员的后继决定只引用本稿正式方向行；当时的执行依据仍绑定在相应成员版本。其他对象的义务各依自身决定处理。更换未送达稿时用 `supersedes {report_id,reason}`，承接该稿仍待履行的义务。已确认送达的正文如有实质错误（例如风险栏给出个人回补批次），在原定时轮核实受影响的段落，按更正反馈。提交 `purpose=CORRECTION`，`event_ref` 指向实际出错的报告 ID，`base_report_id` 指向最新实收底稿，用 `supersedes` 说明更正哪份报告及原因。已核收的义务保留原回执。若更正草稿在发送前需要重组，`supersedes` 改指该草稿，`event_ref` 仍指实收原稿。目标不可用时保留待反馈事项，照常形成和保存独立结论。

公开成稿从 public_writing_inputs 的 `market_review`、`condition_reviews` 核本轮沿用或调整的公开判断、原条件及后置证据；其 `previous_public` 是可继续编辑的完整底稿，`previous_display` 是上次在这个目的地实际展示的内容。组合成稿对照 writing_inputs 内 allocation 的 scenarios 和 interactions，说明影响本次取舍的联合效果。各成员的方向、金额及已知实际操作来自相应正式记录。以当前采纳的判断为依据，对照实收稿写出原条件状态、新证据的适用窗口和接下来足以改判的检验。

为常规稿选择对应模块里能直接读懂的首句或连续完整句，连贯说明本轮最重要的事实、影响与安排。预览和冻结提交同一 `presentation:{lead:{path,quote},shorten:{模块.子项:[原句,…]}}`。lead 的 path 由本次重点决定，quote 取该项确切原文。目的地首份报告、每日稿及作为完整日报交付的稿件使用 lead 并展示完整模块；盘中按重要性选取需缩写的子项，句序与原文一致，选择能同时交代当前判断和必要限制的完整句。没有变化的依据只需简述。重要风险、配置改判或待兑现条件要在手机正文中实际可见。受限公开风险首屏由服务使用当次已核风险生成。简写覆盖待反馈的具体决定时，在该义务的 `decision_coverage` 附可见正文中交代该决定的原句 `excerpt`。以目标实际展示内容判断所欠反馈是否已履行。市场变化而动作沿用时，说明沿用理由。原安排重定价或部分完成时，分别引用已采纳判断与已确认的执行范围。

默认空单栏只呈现已采用的方向或真实适用状态。“沿用上轮减持”仍显示建议减持。用户追问某轮理由或执行安排时，从 trading.feedback 的 previous_display.decision 取得当次展示确切成员，沿 object.get 的个人历史读取接口传该版本及冻结市场绑定，读取内部比较、参考价和条件；追问某份公开风险稿中沿用的方向时，从该次确认报告的 `document.base_report_id` 找到作出判断的当期底稿。首次风险稿从 `document.seed_sources.portfolio` 回查精确的组合和成员版本。询问现在如何处理时，按当前适用证据复评。

投递沿原已授权目的地和实际可用原生入口。先核目标真实游标，再用 `trading.begin_delivery {report_id,anchor_ref}`。仅首次明确返回 ready_to_send=true 时，使用返回的原目的地及确切 request_body 发起实际调用。公开风险先行投递时，在同一获准工具编排内解析回执、把原请求字节直接交给已核目的地，回读正文在该编排内核对并登记。公开研判上下文只接收投递身份、状态和核对结果，不打印 request_body、个人段落或完整回读。按[正式服务的原生投递回执接口](../service/README.md#原生投递回执)登记工具受理、绑定实际原请求并核对其完成正文。投递回执和源稿保留原身份。效果无法确认时沿原尝试保留 UNKNOWN；明确拒绝时只停止对应发送，沿原证据保留义务。失败及未知范围、回读和更正按[当前交付方法](../skill/btc-core-strategy/references/market-reporting.md#当前交付)处理。

承接仍有效的历史反馈时，用 `trading.inherit_feedback {retain:[{review_id,reason}]}` 取得义务。新正文覆盖该含义时，将返回的 `inherited` 中相应身份列入 `obligation_ids`，在 `legacy_coverage` 中按该身份说明实际覆盖方式。

历史尝试沿 `archived_attempts` 查原目标和原绑定。对仍为 SENDING 或 UNKNOWN 且已有原请求绑定的归档尝试，明确取得原请求对应的完成正文后，用 `trading.record_delivery` 的 CONFIRM_ARCHIVED 追加核收。原正文、请求和发送记录保留原身份。

本轮结束时回收确切研究、决定及实际反馈状态，由 `btc-core finish --run-id <原生运行身份>` 关闭执行。下一实际运行按各自期限续接原条件、确切结果和未决义务。研究待办、市场兑现和送达由其各自事实记录推进。
