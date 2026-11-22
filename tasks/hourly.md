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

1. 先对照前次独立市场结论、当前证据及其事前条件，比较主情景、竞争路径、现价净机会与风险。从上下文的 `review_targets` 取得可核验的原条件，用 `trading.review` 核查出现相关新事实或已到期的条件。提交返回的 `target {kind,object_id,version,digest}`、原 `condition_id`、`outcome`、`explanation`、`evidence` 和实际影响 `impact=KEEP/REVISE`。结果使用 SUPPORTED、PARTIAL、REFUTED、NOT_TRIGGERED 或 UNKNOWN。没有固定日期的条件按相关新事实核查。后置事实须带确切来源，证据不足和未兑现沿原条件保留。
2. 有重要反证、缺失或明确重评要求时，用 `trading.research` 承接实际问题及来源，参数包括 `scope,subject,question,evidence,expected_version`。公开研究只交公开问题及证据。已有研究从原状态继续。新的原生研究调用通过节点 MCP 的 `model_deep_claim` 或 `btc-core data` 的 `model.deep.claim`，提交 `{claim_id,issue_id,scope,phase,input_digest}`。由 `trading.research` 的 `result.object_id` 取得 `issue_id`，由 `result.payload` 取得 `claim_scope`、`phase` 和 `input_digest`，将 `claim_scope` 传给 `scope`。为本次实际尝试固定 `claim_id`，接续同一尝试时沿用。领取的新尝试按 `CLAIMED_NEW` 启动，`REUSE` 沿原结果核对。

   取得真实完成正文后，计算其 UTF-8 字节的 `result_ref=sha256:<64 位十六进制摘要>`。用节点 `model.deep.finish`（MCP 中为 `model_deep_finish`）提交 `{claim_id,outcome:"COMPLETE",result_ref}`，再用 `trading.research` 追加 `outcome {claim_id,result_ref,conclusion,origin_run,agent_id,turn_id}`。 `origin_run` 取发起研究记录的 `source_ref`，`agent_id`、`turn_id` 取产生确切正文的原生子任务与完成回合。后续从 `trading.research_result` 读取确切原文。原生完成来源无法核实时保留原请求。主轮可在研究待办期间继续有依据的独立工作。
3. 用 `trading.market` 提交正式市场结果。新判断提供 `expected_version`、`evidence`、`result {conclusion,primary,alternatives,value,assessments,valid_until,conditions}`。assessments 按有必要单独引用的公开问题保存实质判断。条件为 `{id,criterion,review_at}`。兼容既有有效判断时传原 `market` 确切结果引用、`reason` 和本轮 `evidence`，保留原判断的期限。引用使用 `BLOB {type,digest}` 或 `OBSERVATION {type,product,series,item_key,observation_key}`，用到已完成研究时引用其确切公开实体。需要反馈的实质新决定同时提供 `notification {kind,reason,due_at}`。
4. 公开结果冻结后读 `trading.personal`，按原条件及当前价格基准核查需要复评的决定。按[交易决策主线](../skill/btc-core-strategy/references/decision-design.md)在同一公开主情景和竞争情景下比较两组投入、额外现货、现金留存与存量合约。比较保留、减持和退出从现价起对组合下行、反向修复和未来成本的净影响，再一次形成各项选择。现货取舍及合约前瞻补偿真实互相影响时，说明哪个选择依赖哪个输入。实际保护总额只计入已确认的覆盖。

   用 `trading.portfolio_decision {expected_version,market,primary,alternatives,interactions,decisions,reuse}` 登记。版本取 `personal.portfolio.version`，初次为 0；`market` 传本次评估使用的 `personal.market` 确切引用。`primary` 和按冻结顺序排列的 `alternatives` 写各情景的组合净效果。`interactions` 项为 `{subject,depends_on,reason}`，分别写受影响的决定、它实际依赖的另一项决定及依据。所有适用决定各提交一次，`decisions` 放新评估，`reuse` 按 subject 放上个组合中仍有效的确切引用 `{kind,object_id,version,digest}`。前一组合缺席时完成新评估；同一组合仅复评相关市场、事实、条件及实际联动变化所影响的决定。

   每项新决定提供 `subject,expected_version,policy_basis,facts,fact_refs,market_refs,result,evidence`，另有适用的 `notification`。facts 列实际使用文件及摘要，fact_refs 为 `[{file,quote}]`，market_refs 选择实际依赖的市场字段。来源文字实际变更时，用 `trading.policy {subject,kind,fields,refs,expected_version}` 更新受影响的范围。result 含 `decision,reason,risk,opportunity_cost,valid_until,conditions`。月度组比较足额、跳过和有依据的分批，填写 `comparisons [{amount,reason}],amount`，分批加 `amount_basis`。独立机会按净补偿评估，正额带 `value_zone {lower,upper,quote_unit,basis}`。投入真实未决时填写具体 `gap` 和 `amount:null`。

   原空单的开单价与来源从 `policies[subject].entry_reference` 读取。价格基准适用时，新决定的顶层 `entry_reference` 传该对象的 `revision` 字符串。对原单选择 KEEP、REDUCE 或 EXIT 中的一项，研判范围为 PRICE_BASELINE_DIRECTIONAL，仓位事实可以为 UNKNOWN。真实可能改变方向的市场证据缺口用 UNRESOLVED 并说明 `gap`。确认已平时给出 NO_POSITION，提交时省略顶层 `entry_reference`。当前开单价缺失且仓位未知时，沿有依据的条件判断，在 `result.conditional_actions` 以 PRESENT、ABSENT 记录各自的 `{decision,reason}`。在仓分支确实未决时注明其 `gap`。后续改判条件写入 `conditions`。

   对减持原空单的结论，比较现价先回补和等待的净得失。在 `result.reason` 写明是否在现价先减，或等哪个价带再减。可确定两端时，把本轮回补价带写入 `result.value_zone {lower,upper,quote_unit,basis}`，说明上下沿的经济依据和合约报价口径。仅在仓分支减持时，该价带只适用于 PRESENT。只能支持参考价时省略价带，写明时点、参考价和哪一侧边界缺什么依据。余仓改判另列入 `conditions`。方向相同时，仍核对价带或现价与等待之间的取舍是否发生实质变化，并按实际影响反馈。

## 报告、投递和下一轮

读 `trading.feedback` 获取最后实际展示、尚未履行义务和原在途尝试。先完成价格变化或已确认平仓所影响决定的复评及采用，再核对实质取舍或预定反馈是否改变。实质变化按现有 UPDATE 路径处理，普通重复信息保持静默。当前合约决定已有实质反馈义务时，若原尝试在途，沿该尝试核收。用 `trading.assess_feedback {outcome,reason,evidence}` 记录本轮实际结论，证据或交付受限用 EVIDENCE_LIMITED。已核实的显著风险可在受影响的研究未采纳时先作有界反馈。

个人报告的 `decisions` 引用冻结的市场、已采纳的组合 `PLAN` 和实际呈现的成员决定。完整日报引用组合的全部适用成员。按组合决定交代每组金额、理由和风险，再交付合约本轮动作、适用范围、共同下行与修复时的净作用及改判依据。开单价基准下交付已采用的单一方向，实际仓位和保护覆盖按各自的确认范围表达。逐项对照终稿与原决定。用 `trading.report {purpose,body,decisions,reason,obligation_ids}` 提交同一篇 Markdown，服务计算正文摘要并冻结。非日报提供实际原 `event_ref`。公开重大风险缺完整判断时可引用确切公共事件并提交 `purpose=RISK`、空 decisions 和有据的 valid_until，明确已核范围。历史未送达通过 `trading.inherit_feedback` 查实际义务，只承接仍有意义的项。更正带 `supersedes {report_id,reason}` 并显式覆盖原义务。目标不可用时保留待反馈事项，照常形成和保存独立结论。

日报含原空单减持建议时，按[报告表达](../skill/btc-core-strategy/references/mobile-report.md)在开头一句给本轮回补价位和动作，下一句交代余仓复评条件。若原决定只有方向和盈亏测算，回到本轮空单研判补齐价格判断。

投递沿原已授权目的地和实际可用原生入口。先核目标真实游标，再用 `trading.begin_delivery {report_id,anchor_ref}`。仅首次明确返回 ready_to_send=true 时，使用返回的原目的地及确切 request_body 发起实际调用。按[正式服务的原生投递回执接口](../service/README.md#原生投递回执)登记工具受理、绑定实际原请求并核对其完成正文。投递回执和源稿保留原身份。效果无法确认时沿原尝试保留 UNKNOWN；明确拒绝时只停止对应发送，沿原证据保留义务。失败及未知范围、回读和更正按[当前交付方法](../skill/btc-core-strategy/references/market-reporting.md#当前交付)处理。

承接仍有效的历史反馈时，用 `trading.inherit_feedback {retain:[{review_id,reason}]}` 取得义务。新正文覆盖该含义时，将返回的 `inherited` 中相应身份列入 `obligation_ids`，在 `legacy_coverage` 中按该身份说明实际覆盖方式。

历史尝试沿 `archived_attempts` 查原目标和原绑定。对仍为 SENDING 或 UNKNOWN 且已有原请求绑定的归档尝试，明确取得原请求对应的完成正文后，用 `trading.record_delivery` 的 CONFIRM_ARCHIVED 追加核收。原正文、请求和发送记录保留原身份。

本轮结束时回收确切研究、决定及实际反馈状态，由 `btc-core finish --run-id <原生运行身份>` 关闭执行。下一实际运行按各自期限续接原条件、确切结果和未决义务。研究待办、市场兑现和送达由其各自事实记录推进。
