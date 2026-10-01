> 报告的分析依据、触发复核和日本每日反馈以[报告复核契约](report-care.md)为当前规则。新报告包含maintenance；旧格式保持兼容。

> 新报告使用[最终正文接口](mobile-report.md) AUTHORED_REPORT_V1，遵守其中研判与报告的职责边界，承接已经形成的综合结果并 VERBATIM 交付。用户对象为币安QQQ、VOO、BTC，指数继续作底层研究。下文 MARKET_CONCISE_V1 的段落与插值示例仅用于历史兼容。

# 独立行情汇报与交付

核心报告以币安QQQ、VOO和BTC为用户对象，美股指数保留底层研究身份，三类收益解释机制，不替代市场分类。组合是持续评估单位，成分和权重可调整；新增、减少、持有及恢复投入先针对组合，独立BTC／黄金和保护分别表达。每次审阅核验各市场的重要结论、结构变化、建议和限制；评级不变但结构实质变化也参与报告价值判断。maintenance维护全部条件，手机仍须保留影响判断与执行的条件。修订后嗅探和报告绑定同一正式版本；旧稿按原来源恢复。实际回读核对市场与组合归属，美股、黄金价格使用自身单位，不放入BTC价位字段。

主任务每次完成有效观察后都围绕已采纳深评完整报告执行摘要价值判断，即使sensing-gate不要求发布或专项。策略是否变化和周期报告是否需要每日总结或实质更新是两个问题。小时行情须先验证其意义；已核验的重要背离或波段风险可以通过 URGENT_RISK 反馈，注明本轮依据及期限，新正式价位和个人建议仍须深评采纳。模型核对这些观察是否改变原周期结论或共识价位适用性，再对照最后实际展示判断；内部分析不等于已收到。中期未反转、仓位未变或当天已送达不能屏蔽重要新变化；每天至少一份实际送达的有效报告，模型选择具体时间；不设置固定推送时刻、冷却期或价格/金额机械阈值。报告价值判断不成为第二份策略 Owner，不为摘要措辞或格式本身启动 Astra；完整报告的核心结论缺项则交原研究补评。

下行风险对仓位的影响按[已成交现货的减仓评估](core-strategy.md#已成交现货的减仓评估)在内部完整处理；verification 验证减仓依据和保护规模，不能用暂停新增代替持仓评估。完整报告遗漏时交回 Astra 修正；手机不展示比较过程，忠实提取周期形态、建议价值与必要限制和价位有效性。

## 正常入口

以下命令沿用strategy_v4.py的--project与--root；使用$BTC_PYTHON。

1. 完成当前observe和必要的主评估后，`report-context`取得BTC_REPORT_CONTEXT_V1。包含当前策略、最新已验证观察、原始指标、最后真实展示正文、累计未报告摘要及缺口、静默时长、上一静默理由与条件、独立交付队列。metrics严格按dataset保留SPOT/PERPETUAL、交易所、instrument和USD/USDT；today_utc与since_report为不同窗口，不能相加。最新行latest与累计测量分开，测量缺失保持null。上下文含未报告条数与截断标记，超出240项时按引用补读，不以截断证明无变化。
2. 主任务实际阅读证据和展示稿后，用`report-review --spec <JSON>`保存BTC_REPORT_REVIEW_V1，字段如下：

```json
{
  "schema":"BTC_REPORT_REVIEW_V1",
  "context_id":"实际上下文身份",
  "action":"REPORT",
  "purposes":["MARKET_SUMMARY"],
  "reason":"具体汇报价值；静默则说明为什么没有新增展示价值",
  "next_observation":"下一项值得汇报的观察条件，不设固定推送时刻",
  "baseline_review":"逐项对照最后实际展示内容，说明新含义、仍重复的内容和旧字段未知",
  "applicability_review":"当前观察如何支持正式策略仍适用；变化时先完成主评估，说明来源、口径、风险和限制",
  "information":{"稳定主题":"本轮实际要传达的稳定业务含义，不放时间戳、模板版本或同义改写来绕过去重"},
  "layout":{
    "format":"MARKET_CONCISE_V1",
    "overview":"当前安排",
    "market":"Binance现货收于{{/metrics/binance_spot_1h/latest/close}}USDT。",
    "explanation":"主要解释及策略保持或调整的原因",
    "risk":"风险与必要不确定性",
    "next_observation":"模型继续观察什么",
    "include_recommendations":true
  },
  "provenance":{"role":"MAIN","model":"gpt-6.1-sol","effort":"high","agent_id":"真实主任务身份","run_ref":"真实运行引用"}
}
```

action支持REPORT、SILENT、EVIDENCE_LIMITED。后两者layout=null，保存理由和下一观察条件但不产生源稿、不推进已报告基线。purposes支持MARKET_SUMMARY、STRATEGY_UPDATE或两者；主策略有重要变化时使用STRATEGY_UPDATE并说明改变、理由及影响。旧报告缺少conclusion意味着未知，不自动视为相同或重要变化；对照旧实际正文完成baseline_review。纯格式迁移可静默，不能让之后新增解释长期跳过审阅。

layout每段最多240字符，不嵌套额外表格或多段全文。数字仅用/metrics或/assessment JSON Pointer绑定；数值显示四舍五入至两位小数。net_taker_quote_wan为主动买卖金额除以一万，单位仍由dataset.quote_unit决定；负数表示净卖。observed_return_percent为窗口首开至末收收益百分比。recommendations表机械来自当前策略，区分空单名义额和现货投入，不重算或另填建议。行情窗口不足时必须正文说明所用部分窗口，不能称完整日行情。不得把不同货币或合约/标记/指数价混成三所现货范围。

3. `market-report-source --review <review_id> --output <源稿JSON>`冻结BTC_REPORT_SOURCE_V4。所有段落和建议表逐行成为断言；源稿中的报告审阅及上下文不可变。检查实际正文是否忠实呈现已审阅信息。已有CONCISE_V1与旧report-source只供兼容，不跳过独立报告判断。
4. `enqueue --decision <当前策略ID> --source <源稿> --destination <原目的地JSON>`产生BTC_REPORT_V6。入队与BEGIN重验当前策略、观察、真实事实和最后已送达基线。相同冻结产物幂等；同一策略版本的新行情报告使用各自report-review身份。NOT_DUE表示需要核对当前上下文或信息已展示，不自行改hash重试。

## 交付与恢复

发送工具返回受理但尚无可绑定请求／正文时，使用 `ACKNOWLEDGE` 保存实际运输回执：`{schema:BTC_TRANSPORT_RECEIPT_V1,attempt_id,recorded_at,status,record_refs,detail}`。status 为 ACCEPTED、PROCESSING 或 FAILED，只按工具实际结果填写；成功回执仅能证明 ACCEPTED。PROCESSING 必须有真实处理中状态，不能从正文尚未出现推断。它们不确认送达，也不清除义务。明确仍处理时先有界等待完成信号并回读，不能立即重试；无完成信号时保留阶段，由原主任务接续，不反复空读。晚到回执不能倒退状态。

实际请求出现后按原 BIND_REQUEST 绑定，助手正文完成后才 CONFIRM。已送达的相同绑定可幂等回放；新回读只用于核对，不能改写已确认历史。实际处理中状态消失或失败而仍无法确认正文时，保存较新的 UNKNOWN 回读再按既有限额恢复。同一运行不因重启或更换临时目录重置额外发送额度；根运行引用沿用原值。

报告必要性、深评必要性和实际交付分别判断。重要市场变化、深评结果、重新定价、重大风险和重要纠错只是事件示例，不是封闭清单。评级、金额或中期方向未变、当天已报告均不能取消重要含义。研究使旧判断、价位失效或待复核时，先完成当前影响报告；正式采纳后报告结果，普通进度不冒充完成稿。新审阅的 `important_events` 每项为 `{id,kind,phase,impact}`；id 标识真实事件及阶段，kind 自然说明类型，phase 为 IMPACT_PENDING、COMPLETED 或 CORRECTION，impact 必须逐字出现在报告中。已采纳而未实际反馈的深评通过 `required_report_events` 提醒，即使评级不变也须说明结果。

未送达 REPORT 持续保留交付义务，SILENT、EVIDENCE_LIMITED、入队及工具回执均不消除。新稿承接旧稿时填写 `delivery_coverage=[{review_id,explanation}]`，逐项说明仍重要含义怎样覆盖或纠正；义务转到新 REPORT，直到真实展示才完成。错误且未派发旧稿可用 SUPERSEDE_PENDING 显式关联覆盖它的新 delivery；旧 UNKNOWN 保留独立恢复事项，不冒充未发送。

BEGIN 只核验本稿当前有效性。其他稿的 SENDING/UNKNOWN 不再锁住目的地；新重要报告经当前证据复核后继续发送，旧请求独立回读。同一 BEGIN 幂等调用返回 ready_to_send=false 时只回读本次尝试，不再次发送；只有 ready_to_send=true 且 state=SENDING 才真实发送。必须发送 BEGIN 返回的完整 request_body，不能从 artifact 猜取不存在的字段。参数拒绝先修正参数；派发前拒绝可据实际工具记录 NOT_SENT，页面错误、DOM 标识、请求派发及助手完成展示分别判断。无完成正文的工具成功回执不能确认送达。

重要报告按至少一次送达恢复：先实际回读；仍不确定、当前证据确认正文适用且义务未被新稿承接时，使用 RETRY_UNKNOWN，保留可能重复以及原未知尝试。每个实际运行、同一目的地最多一次恢复后的额外发送；正文更正也占用本轮额外发送机会，不与重试循环叠加。继续失败则明确保留交付异常，由原主任务后续接续，不新建任务、不轮询。过时或错误正文应生成当前纠正稿承接义务，不能为补发而原样重试。

新 V6 的 AUTHORED_REPORT_V1 要求实际助手正文逐字一致，回读 bindings=[]、checks={}；只有真实关联、完成正文与逐字核验通过才推进基线。旧格式沿原有限语义审阅恢复。一次空回读不证明未发送；NOT_SENT 仍仅接受实际未派发证据，不能抹掉已经绑定的请求。接口字段见 v4-contract。

recover 分别返回 report_delivery_obligations（包括已入队但未送达）和 attempt_recovery（按 attempt_id 保留旧请求）；report_reviews_not_enqueued 仅是未入队兼容视图。CONFIRM/repair-completion 可修复本地确认中断。每次尝试、绑定和实际回读独立保存，晚到旧回读核验自己的 attempt_id，不替换新尝试的请求绑定，也不倒退已报告覆盖或展示顺序。

历史revision 1/2/3/4评估及V4/V5报告不改写。旧V4正文加已收录只按旧契约认可；缺失用途、覆盖起点、information与conclusion均未知，不伪造迁移值。动态因子仍由当前DecisionSnapshot.factor_assessment唯一承载，小时观察验证已采纳周期基线，实质变化返回 Astra 完整深评。

用户明确要求当次总结时，取得新鲜证据后应形成报告，不以策略未变取消；另有每日反馈兜底，具体时点由模型选择。自然运行验收与手动补发、隔离测试分别记录，未发生不宣称通过。

同一行情截止时点仍可能有尚未展示的当日或其他窗口信息。新上下文的coverage_display_revision=2将这类标题标明为同一截止时点的补充，正文明确实际指标窗口；coverage保留已报告进度，不伪造时间推进。旧冻结上下文没有该标记时继续按原格式验证，不能重渲染后替换历史已发送正文。

最后实际展示与已报告覆盖分别保存：last-display-current按串行发送尝试顺序关联真实已验收助手正文，reported-current的覆盖不倒退。晚到的旧稿更正可改变最后展示但不能覆盖新行情进度，context明确显示两者是否相同，主任务据此纠正用户当前看到的过时内容。重复确认不会改变顺序。

已验收基线存在时，旧布局的重要策略语义变化仍必须用 STRATEGY_UPDATE 报告。新报告由 Sol 对照最后真实正文判断新增市场结论、重要背离/波段风险、价格适用性及个人应对。普通研究进度、同义因子维护或时钟更新不制造报告；应报告的深评结果、重定价待审影响和正式完成则独立送达，不因评级或金额未变而静默。内部分析过但未展示的风险不能当作重复；相反，仅换措辞和 information 名称不能制造新信息。无实质变化可静默，但中期反转未确认或当天已报告本身不是静默理由。程序在入队与发送前拦截相同实际正文，每日反馈到期与用户明确重发保留；语义由模型实际核验，不由字节相等或质量说明自证。

周期修正启用后，report-context 和 run 返回的精简报告上下文都包含完整周期前瞻、下行风险、组合、建议及真实 cycle_baseline。小时统计保留内部，日/周统计提供原始期限与口径；报告围绕周期及其风险演变选取数字。重大反证已经触发深评时，cycle_baseline 显示 REASSESSMENT_PENDING；新深评真正采纳后才解除，旧观察不能把新基线再次标为待评。

## 完整报告来源

新审阅沿用 BTC_REPORT_REVIEW_V1 和 report_basis，绑定 context.cycle_baseline 的真实结果与正文哈希；正式正文由 Astra 直接撰写并保存在当前策略的 conclusion.narrative。Sol 在内部结合嗅探完成依据适用性与风险影响的判断，报告按[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)表达同一研究及本轮已形成的结果；不能在汇报时重新排序因子、补充因果解释或改变周期、点位、金额和建议。CYCLE_SUMMARY、LIMITED_FEEDBACK、URGENT_RISK、MARKET_UPDATE 的条件见 v4-contract.md。每日反馈可以结论不变，但必须完成真实复核与实际送达，不能用日内波动凑更新；纯格式重写仍不计每日有效反馈。
