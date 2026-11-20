# 交易系统主任务

按当前项目、真实触发身份及原节点执行[交易系统闭环](../skill/btc-core-strategy/references/closed-loop.md)。从节点的 `btc-core status` 取得现行入口。用本次真实触发身份调用 `btc-core enter --run-id <本轮身份>`，使用回执中的物理发布目录、节点配置、解释器和 Skill。已结束的同一触发返回原结果。在途运行接续原输入。实际准入拒绝按原作用范围处理。

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
4. 公开结果冻结后读 `trading.personal`，按适用范围核查原个人决定的条件。按实际适用政策及已确认事实形成每个独立决定。用 `trading.personal_decision` 提交 `subject,expected_version,policy_basis,facts,fact_refs,market_refs,result,evidence`。facts 仅列实际使用的文件及其摘要，fact_refs 是对应 `[{file,quote}]`。market_refs 选择本决定真正依赖的 market_inputs 字段。来源文字实际变更时，从当前确认记录用 `trading.policy {subject,kind,fields,refs,expected_version}` 更新该范围，refs 提供原文件、行范围和原句。result 包含 `decision,reason,risk,opportunity_cost,valid_until,conditions`。月度组按本组预算真实比较足额与跳过，分批时再列选中金额及理由。填写 `comparisons [{amount,reason}],amount`；分批加 `amount_basis`。明确本月继续或跳过，真实待定使用 UNRESOLVED、具体 `gap` 及 `amount:null`。独立机会按价值区和净补偿评估，正额机会带 `value_zone {lower,upper,quote_unit,basis}`。持仓按用途或实际未知给出结论。新事实或条件只作用于相关决定，同一市场结论可以承接不同个人选择。

## 报告、投递和下一轮

读 `trading.feedback` 获取最后实际展示、尚未履行义务和原在途尝试。核对重大事实、主要取舍及预定反馈是否改变。普通重复信息保持静默。用 `trading.assess_feedback {outcome,reason,evidence}` 记录本轮实际结论，证据或交付受限用 EVIDENCE_LIMITED。已核实的显著风险可在受影响的研究未采纳时先作有界反馈。完整日报采用确切市场及当期每项个人决定。对照正式决定核对实际终稿，逐组讲清本月是否继续、决定性理由、主要风险、金额和改判条件。未决写明缺口。用 `trading.report {purpose,body,decisions,reason,obligation_ids}` 提交同一篇 Markdown，服务计算正文摘要并冻结。非日报提供实际原 `event_ref`。公开重大风险缺完整判断时可引用确切公共事件并提交 `purpose=RISK`、空 decisions 和有据的 valid_until，明确已核范围。历史未送达通过 `trading.inherit_feedback` 查实际义务，只承接仍有意义的项。更正带 `supersedes {report_id,reason}` 并显式覆盖原义务。目标不可用时保留待反馈事项，照常形成和保存独立结论。

投递沿原已授权目的地和实际可用原生入口。先核目标真实游标，再用 `trading.begin_delivery {report_id,anchor_ref}`。仅首次明确返回 ready_to_send=true 时，使用返回的原目的地及确切 request_body 发起实际调用。按[正式服务的原生投递回执接口](../service/README.md#原生投递回执)登记工具受理、绑定实际原请求并核对其完成正文。投递回执和源稿保留原身份。效果无法确认时沿原尝试保留 UNKNOWN；明确拒绝时只停止对应发送，沿原证据保留义务。失败及未知范围、回读和更正按[当前交付方法](../skill/btc-core-strategy/references/market-reporting.md#当前交付)处理。

承接仍有效的历史反馈时，用 `trading.inherit_feedback {retain:[{review_id,reason}]}` 取得义务。新正文覆盖该含义时，将返回的 `inherited` 中相应身份列入 `obligation_ids`，在 `legacy_coverage` 中按该身份说明实际覆盖方式。

历史尝试沿 `archived_attempts` 查原目标和原绑定。对仍为 SENDING 或 UNKNOWN 且已有原请求绑定的归档尝试，明确取得原请求对应的完成正文后，用 `trading.record_delivery` 的 CONFIRM_ARCHIVED 追加核收。原正文、请求和发送记录保留原身份。

本轮结束时回收确切研究、决定及实际反馈状态，由 `btc-core finish --run-id <原生运行身份>` 关闭执行。下一实际运行按各自期限续接原条件、确切结果和未决义务。研究待办、市场兑现和送达由其各自事实记录推进。
