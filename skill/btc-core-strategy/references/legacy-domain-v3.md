# 旧 v3 不变量与回放接口参考

仅供 v4/domain.py 复用字段校验及历史回放。本文件中的 v3 写入、收据、模型路由、报告八标题和迁移命令不属于 v4 生产流程；v4 的唯一入口见 v4-contract.md。

# 状态与计划投影契约（strategy revision 3）

本文件保存历史正式策略 revision 3 的精确 Schema。当前 revision 5 通过 v4 兼容层复用字段、数值、引用、时间与资产事实校验；下面的固定因子组合、因子数量及特定因果时序模式仅用于旧版回读，不裁决 revision 5 的市场充分性、归因或周期状态，当前规则见[独立市场与分歧区](market-state.md)。历史 revision 3 接口中，独立小时观察库的 Schema 与读写接口由[连续市场观察](market-observation.md)单独承载：它拥有 PRE_WRITE、证据来源与命题、projection sidecar、状态/计划 metadata、确定性报告 renderer、FINAL/UNAVAILABLE 收据和 CLI 分支参数。`core-strategy.md`只拥有策略推理语义并路由到这里，不得复制或另行解释字段、endpoint、payload、allowlist 或命令组合。磁盘上的`strategy_contract.py`实现及各子命令当前`--help`是可执行事实；本文必须与其同步，冲突时失败关闭并先修正文档或实现。

## 单向 Owner 关系

| 层级 | Owner | 允许内容 | 禁止反向推导 |
|---|---|---|---|
| 资产事实 | `资产配置.md`、`操作记录.md`、`纠正记录.md` | 用户确认的仓位、配置、成本和未知项 | 市场或策略不得改写事实 |
| 核心方法 | `core-strategy.md` | 观察、竞争假设、生命周期、组合决定和语义比较 | 不拥有 Schema 或 CLI |
| 精确契约 | 本文件与`strategy_contract.py` | exact-key allowlist、endpoint/payload、指纹、projection、renderer、收据和命令分支 | 其他文件只能路由，不得复制第二套 Schema |
| 派生状态 | `策略状态.md` | 当前决定、证据矩阵、三层状态和组合复核 | 观察与计划不得直接触发状态迁移 |
| 计划投影 | `当前预期计划.md` | 当前决定的完整评估投影 | 计划不得反向决定状态 |
| 评估报告 | `$btc-core-strategy` | 面向用户的决定、依据、影响和下一证据 | 报告不得承载内部控制面 |
| 自动触发 | 唯一项目级 Scheduled | Sol/high 每小时连续评估与记录；必要时请求只读 Sol/max 深评；主任务验证后执行 D/E | 入口阶段不得代替深评写策略；任何阶段均不执行交易、不改变`position_fact` |

## 运行角色与动态推理分配

- 唯一 Scheduled 主任务使用`gpt-5.6-sol / high`按[连续市场观察](market-observation.md)执行多周期形态与累计变化评估，每小时保存有效观察或缺口。入口不得代替完整深评修改正式状态/计划或生成策略报告。
- 当观察形成复核候选、最终事件完成或出现重大风险候选时，主任务临时创建一个`gpt-5.6-sol / max`只读深评子代理。子代理只读取项目 Owner 文件和公开市场证据，只返回符合本契约的候选决策包与可审计推理摘要；不得写文件、写收据、投递云端或请求真实执行。
- 主任务始终是唯一写入与投递 Owner：它验证深评结果、计算指纹、执行原子更新、决定报告并完成云端回读。
- 普通复核候选无法获得有效深评时，保持最近有效锚并静默等待下一小时重试，不允许入口阶段代替。重大风险候选无法深评时，可在存在可验证最近有效锚且全部校验通过的前提下形成保守风险说明；否则失败关闭，不伪造决定。
- Scheduled 不得读取`本机 Codex 长期记忆目录`或其他 Codex 长期记忆；不得登录私有账户或调用认证账户、订单、交易 API。

## 采集辅助观察入口

辅助数据与正式 PRE_WRITE 是两条不同权限的输入。实际导出命令为：

```text
$BTC_PROJECT/telegram_reader/.venv/bin/python -B \
  $BTC_PROJECT/telegram_reader/reader.py collector-observations
```

该命令只读本地开发目录的数据，通过公共读取接口`observations(latest_only=True)`返回最新关联正文的一份辅助快照，结果为`[]`或只含一个 observation 的 JSON 数组；不轮询消息、不下载、不访问凭据。最新正文处于缺失、待验证或无效状态时不得回退到旧的有效观察。共享 API 的历史模式只用于诊断，不能作为本入口的 current 或 previous。不要将生产副本、安装或服务重启隐含在导出步骤中。

先将本轮导出 stdout 捕获到项目之外的唯一临时文件；辅助快照可能很大，先读取 validation、dataset 清单和来源覆盖，再按需读取已筛选维度，不能把截断的终端输出当完整快照。向既有`classify-zone`、`fingerprints`或`validate-bundle`追加`--collector-observations <file>`。已有真实可比的前次单份本地导出时才可追加`--previous-collector-observations <file>`，不得伪造空基线、把历史视为当前或扩大 A 收据；独立公开市场的滚动观察库按新观察契约维护，不将采集自报升级为市场事实。两个输入都必须遵守单快照边界：完整 envelope 内容相同的重复项可折叠；两个不同 envelope 即使测量相同，也必须记录`MULTIPLE_AUXILIARY_SNAPSHOTS`并拒绝作为该侧的当前覆盖，不猜最新、不合并历史。原命令参数与正式校验门保持不变，结果中的`collector_review`只属于临时观察上下文，不能进入状态、计划、FINAL 收据或云端。

`coverage_baseline_status`明确区分`NOT_PROVIDED`（未传前次导出）、`VALID_EMPTY`（真实`[]`）、`VALID`（有效单快照）、`VALID_DEGRADED`（读取端合法降级的单快照，包括零可用覆盖）和`INVALID`（边界、结构、质量载荷、投影或公共模块不可用，或混入不同快照）。这里的有效仅指辅助输入可比较，不代表已验证市场证据。`coverage_current_status`使用同一集合但不含`NOT_PROVIDED`。只有两侧都是有效快照且确实提供前次导出时，`coverage_comparison_performed=true`；否则新增候选与`lost_coverage`均为空，不把无效输入当作零覆盖。

前次拒绝记录单独保存在`previous_rejected_observations`，与本轮`rejected_observations`的序号分开。前次无效不影响本轮合法辅助数据的接纳。合法的数据集缺失、数据集质量降级、读取端已过滤的来源/未知域诊断、空数据集诊断和重复测量不使整份基线无效；仍可比较之后恢复的覆盖。非法信任标记、读取端不会导出的质量状态、直接夹带未知数据集和投影异常不能借“合法降级”通过。未提供基线或公共模块不可用不得自报已完成比较。

消费器`collector_observation.py`直接消费读取端的`validation`结果，不重算其质量算法；字段投影复用固定项目路径`telegram_reader/btc_evidence.py`的既有投影函数，不维护第二套测量/来源字段表。只有`valid_auxiliary`或`degraded_auxiliary`中没有对应`dataset_reasons`的数据集可以作为辅助测量上下文；过滤结果保留本地输入序号、数据集与原因类别，具体质量原因仍以原导出该数据集的`validation.dataset_reasons`为准。来源不匹配、投影异常或公共模块不可用均不接纳该部分，不能自报质量成功。此处复用的是辅助字段投影，不是独立证据验证；定稿公共模块变更须重跑联合离线测试。

`accepted_observations`全部保持`verification=UNVERIFIED`，独立验证来源数恒为`0`，必须重新核验当前公开来源。同一 venue 的主源/影子源按测量族合并覆盖，完全重复测量去重；`review_candidate`只表示相对真实比较输入增加了可用的“维度上下文/测量族/来源族”，且`deep_review_authorized=false`。数值、时间、到达顺序、报告身份以及同源重复不改变覆盖候选。质量损失单独记录`lost_coverage`，不自动报风险或重算策略。该覆盖比较不是市场驱动状态机，也不是唯一的复核条件：主任务还应结合筛选后的实际时序与既有假设的证伪标准，判断是否需要独立取证；不能仅凭覆盖新增、数据集名称或一根变化的 K 线决定深评。

辅助上下文不注入正式`observation`或三类指纹，也不直接改变`evidence_availability_by_dimension`。正式状态/生命周期/组合/报告变更仍须由深评取得独立、当前证据后，通过原生产`validate-bundle`完整门禁；采集端的质量合格、ETF/稳定币等数据集名称均不等于已验证资金来源或因果归因。

## revision 3 状态模型

`策略状态.md`顶部使用 YAML frontmatter，`strategy_revision`固定为`3`。状态侧 canonical metadata 的 exact-key 集合如下，每个键在去除注释与代码围栏后的正文中必须恰好出现一次；内嵌 sidecar 的`metadata`必须且只能包含这组键：

```yaml
strategy_id: BTC-CORE
strategy_revision: 3
state_id: BTC-STATE-YYYYMMDD-HHMM
cycle_id: BTC-CYCLE-YYYYMMDD-HHMM
derived_at: YYYY-MM-DD HH:MM JST
state_recorded_at: YYYY-MM-DD HH:MM JST
trigger_type: PLAN_GENERATION | SCHEDULED_STRATEGY_BOUNDARY | MANUAL_REEVALUATION | IMPLEMENTATION_MIGRATION
pricing_as_of: YYYY-MM-DD HH:MM JST
account_fact_source: LOCAL_CONFIRMED_RECORDS
account_fact_as_of: YYYY-MM-DD HH:MM JST
derivation_status: READY
quote_verification_status: VERIFIED | CONFLICTED | UNAVAILABLE
decision_evidence_status: RESOLVED | EVIDENCE_LIMITED | UNRESOLVED
driver_regime: CRYPTO_NATIVE_SPOT | MACRO_BETA | DERIVATIVES_LEVERAGE | MIXED | UNRESOLVED
path_decision: MAINTAIN | MOVE_UP | MOVE_DOWN | WIDEN
projection_mode: CURRENT_DERIVATION | LAST_VALID_ANCHOR
created_by_transition: D | E | MIGRATION
user_input_requirement: NONE | FACT_SYNC
observation_fingerprint: sha256:...
decision_fingerprint: sha256:...
report_fingerprint: sha256:...
projection_plan_id: BTC-YYYYMMDD-HHMM
```

正文必须完整包含：

- `observation`：即时关系与数据健康，不含生命周期语义；
- `evidence_matrix`：分维状态、来源、时序、冲突和缺失；
- `hypothesis_competition`与`reasoning_summary`；
- `anchor_map`及每个价值区的四枚举生命周期；
- `forward_value_band`、`path_decision`与不迁移理由；
- `trade_candidates`及每个阶梯的资格和`amount_basis`；
- 五模块`portfolio_decision`；
- 已确认`position_fact`摘要、未知账户字段与权限限制；
- 事件链、下一鉴别证据和当前投影身份。

`strategy_revision`只在方法与投影语义变更时递增。观察、报价、时间、计划周期或同一决定内的数值刷新不得递增。全文 renderer 将 PRE_WRITE 的`runtime.trigger_type`逐字投影到 STATE metadata：非迁移只允许`PLAN_GENERATION|SCHEDULED_STRATEGY_BOUNDARY|MANUAL_REEVALUATION`，MIGRATION 必须且只能使用`IMPLEMENTATION_MIGRATION`；调用方不得在渲染后覆盖。`derived_at,state_recorded_at,pricing_as_of,account_fact_as_of`均由`runtime.pricing_as_of`投影；账户事实的真实内容版本只以`account_facts.fact_owner_fingerprints`为准，不能把`account_fact_as_of`误当事实版本。

## 确定性证据 Schema Owner

PRE_WRITE 及以下每个已声明 Mapping 节点都执行递归 exact-key allowlist；不是“包含若干字段”，而是未列入当前节点 allowlist 的任何键都失败。进入指纹与 Schema 校验前，金额和价格使用确定精度十进制，交易所/来源用稳定规范名，同源镜像合并；价值区关系只用`BELOW_ZONE|INSIDE_ZONE|ABOVE_ZONE|CROSS_VENUE_DIVERGENCE`，边界为闭区间。K 线必须明确`CLOSED|OPEN`并分开记录 OHLC；未完成 K 线不得进入已完成时段留存断言。

### evidence_matrix 的固定维度与 payload

`evidence_matrix`及`observation.evidence_availability_by_dimension`都必须且只能包含以下十个键；每个来源的`payload_kind`必须与父维度逐项相等：

| 维度键 | 精确`payload_kind` |
|---|---|
| `price_quotes` | `BTC_SPOT_TICKER` 或 `BTC_SPOT_CANDLE_CLOSE` |
| `cross_exchange_acceptance` | `BTC_CROSS_VENUE_EXECUTION_SEQUENCE` |
| `spot_flow` | `BTC_NATIVE_SPOT_FLOW_SEQUENCE` |
| `derivatives_flow` | `BTC_DERIVATIVES_FLOW_SEQUENCE` |
| `oi_timeline` | `BTC_OPEN_INTEREST_SEQUENCE` |
| `funding_basis` | `BTC_FUNDING_BASIS_SEQUENCE` |
| `capital_source` | `BTC_CAPITAL_SOURCE_SEQUENCE` |
| `macro` | `MACRO_EVENT_SEQUENCE` |
| `relative_strength` | `BTC_RELATIVE_STRENGTH_SEQUENCE` |
| `account_impact` | `LOCAL_ACCOUNT_FACT_RECORD` |

每个 evidence item 的必需 exact-key 集合为`status,sources,time_range,supporting,conflicts,missing`。`price_quotes`只额外允许`independence_groups,access_scope`；其他维度禁止这两个键。`evidence_claims`仅在实际提供结构化命题时额外允许，且父 item 必须`status=VERIFIED`、数组非空。`status`只允许`VERIFIED|CONTRADICTED|UNRESOLVED|UNAVAILABLE`；非`VERIFIED`必须在`missing`或`conflicts`解释原因，不能用空对象、`MIXED`或全局`VERIFIED`代替。

### 来源对象的 exact-key 集合

所有公开来源对象必须且只能包含`source_id,endpoint_url,payload_kind,canonical_origin,venue,access_scope,retrieved_at,as_of`八个键；八键缺一或出现额外键都失败。`source_id`必须非空且在父维度内规范化唯一，`endpoint_url`必须是无用户名/密码、无 fragment、无非默认端口的公开 HTTPS URL。`PUBLIC_UNAUTHENTICATED` endpoint 的 hostname 只接受规范化后的多标签 ASCII public DNS 形状名称，不接受任何 IP literal，无论其表面是否全局可路由；IPv4、IPv6、IPvFuture、IPv4-mapped/compatible、6to4、Teredo、ISATAP/`5efe`及其他映射、过渡、隧道、scoped、十六进制或 dotted-tail literal 一律拒绝。单标签名称、替代数字 IP 拼法、`localhost`、后缀`.localhost|.local|.localdomain|.internal|.lan|.home|.home.arpa|.test|.example|.invalid|.onion`、DNS-to-address 域`nip.io|sslip.io|xip.io|localtest.me|lvh.me|vcap.me`及其子域，以及纯数字/点号、`0x`起始或任意连续四个 numeric/hex-like 标签等编码地址形状同样拒绝。该检查不执行 DNS 解析；域名所有权、解析结果真实性与解析后实际地址约束由外层取证边界负责。`access_scope`必须为`PUBLIC_UNAUTHENTICATED`。除`price_quotes`外，`canonical_origin`必须等于 endpoint hostname 的规范化小写值；`venue`必须是非空交易场所或受控的`NOT_APPLICABLE`范围。`as_of`与`retrieved_at`必须是带时区 ISO-8601，均不得晚于`observation.observed_at`，且公开来源必须满足`as_of <= retrieved_at`。

`account_impact`不是公开来源：其本地来源对象必须且只能包含`source_id,payload_kind,access_scope,retrieved_at,as_of`五个键，并固定`payload_kind=LOCAL_ACCOUNT_FACT_RECORD`、`access_scope=LOCAL_CONFIRMED_RECORD`；不得添加 URL、origin 或 venue。两个时间字段同样必须是带时区 ISO-8601，并严格满足`as_of <= retrieved_at <= observation.observed_at`。

即时 ticker 的 `price_quotes` 保留下表三种精确映射。product 由精确 path/query 反推，不是来源对象的额外键；scheme、host、默认端口、path、query 名称和值均必须吻合，不得添加 query：

| 完整`endpoint_url` | product | `canonical_origin` | `venue` | `payload_kind` |
|---|---|---|---|---|
| `https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT` | `BTCUSDT` | `BINANCE` | `binance` | `BTC_SPOT_TICKER` |
| `https://api.kraken.com/0/public/Ticker?pair=XBTUSD` | `XBTUSD` | `KRAKEN` | `kraken` | `BTC_SPOT_TICKER` |
| `https://api.exchange.coinbase.com/products/BTC-USD/ticker` | `BTC-USD` | `COINBASE` | `coinbase` | `BTC_SPOT_TICKER` |

完成 K 线收盘另用 `BTC_SPOT_CANDLE_CLOSE`，沿用八个来源字段。允许的真实请求为 `data-api.binance.vision/api/v3/klines`（BTCUSDT 与 interval）、`api.kraken.com/0/public/OHLC`（XBTUSD 与 interval）、`api.exchange.coinbase.com/products/BTC-USD/candles`（granularity）；只附实际取得时的时间范围和条数参数，具体允许值由 `event_contract.PUBLIC_CANDLE_REGISTRY` 校验。origin/venue 与上表相同。收盘时点和获取时点分开，不能把当前未完成 K 线或历史收盘称为即时 ticker；原始完成行的证据绑定仍由 v4 校验与实际研究复核。

`quote_verification_status=VERIFIED`要求两个或更多去重后的 registry origin；`independence_groups`规范化后必须逐项等于这些 origin，来源推导出的 venue、`observation.raw_prices`键和`quote_relation_by_venue`键必须完全相同。多所同一时点 ticker 只能证明报价关系，不能证明跨所接受。`cross_exchange_acceptance=VERIFIED`要求两个或更多独立 origin 与 venue，并使用`MULTI_PERIOD`。

### time_range、父命题与 metric 的精确绑定

`time_range`只有三种 exact shape：

- 未建立：恰好`{"kind":"NOT_ESTABLISHED"}`，禁止`start/end`。
- 单点：恰好`kind,start,end`三个键，`kind=POINT_IN_TIME`，`start/end`均为带时区 ISO-8601 且`start=end`。
- 多期：恰好`kind,start,end`三个键，`kind=MULTI_PERIOD`，`start/end`均为带时区 ISO-8601 且`end>start`。

所有已建立范围的`end`不得晚于`observation.observed_at`；父 item 的`time_range.end`还不得晚于任一父来源的`as_of`。公开维度为`VERIFIED`时必须使用已建立时间范围；`NOT_ESTABLISHED`不成立。`evidence_claims`只能出现在`VERIFIED`父 item 下，每个 claim 的`status`固定`VERIFIED`，`source_ids`规范化集合必须与父 item 的全部`source_id`集合相等，claim 的`time_range`必须逐键等于父 item。每个 claim 都有全矩阵规范化唯一的非空`claim_id`。claim-parent allowlist 恰好如下；未列出的三个维度`price_quotes,relative_strength,account_impact`不得承载 claim：

| 父维度 | 允许的`claim_type` |
|---|---|
| `cross_exchange_acceptance` | `CROSS_PERIOD_ACCEPTANCE`, `RETENTION`, `TWO_WAY_EXECUTION`, `EFFECTIVE_RETEST`, `DISTRIBUTION_EXPANSION`, `VALUE_ZONE_VALIDATED`, `VALUE_ZONE_RETEST_PENDING`, `VALUE_ZONE_RETEST_CONFIRMED`, `VALUE_ZONE_INVALIDATED`, `SPOT_VALUE_NON_ACCEPTANCE` |
| `spot_flow` | `NATIVE_SPOT_FLOW`, `NATIVE_SPOT_BUY_FLOW`, `NATIVE_SPOT_BUY`, `NATIVE_SPOT_SELL_FLOW`, `NATIVE_SPOT_SELL` |
| `derivatives_flow` | `DERIVATIVES_ACTIVE_FLOW`, `DERIVATIVES_LEVERAGED_BUY_DOMINANCE`, `DERIVATIVES_DELEVERAGING_FLOW` |
| `oi_timeline` | `OI_TIMELINE`, `OI_DELEVERAGING` |
| `funding_basis` | `LEVERAGE_COST_EXPANSION` |
| `capital_source` | `CAPITAL_SOURCE` |
| `macro` | `MACRO_EVENT_TIMELINE` |

普通 claim 必须且只能包含`claim_id,claim_type,status,source_ids,time_range,metric`；`spot_flow`表内五种 claim 必须且只能再增加`direction`，其值为`BUY|SELL`，并与带`BUY`或`SELL`的`claim_type`一致。四种`VALUE_ZONE_*`生命周期 claim 与`SPOT_VALUE_NON_ACCEPTANCE`必须且只能在普通集合上增加非空`subject_zone_id`；前四者按`CURRENT_CYCLE_VALIDATED→VALUE_ZONE_VALIDATED`、`RETEST_PENDING→VALUE_ZONE_RETEST_PENDING`、`RETEST_CONFIRMED→VALUE_ZONE_RETEST_CONFIRMED`、`INVALIDATED→VALUE_ZONE_INVALIDATED`绑定实际`zone_id`，后者绑定未完成现货价值接受的实际 zone。`DERIVATIVES_LEVERAGED_BUY_DOMINANCE`的方向语义固定为杠杆主动买入主导，`DERIVATIVES_DELEVERAGING_FLOW`与`OI_DELEVERAGING`的方向语义固定为去杠杆/持仓量收缩，`LEVERAGE_COST_EXPANSION`固定表示杠杆资金成本或基差扩张，`SPOT_VALUE_NON_ACCEPTANCE`固定表示目标 zone 未建立现货价值接受；这五种类型都使用普通 claim 形状（仅`SPOT_VALUE_NON_ACCEPTANCE`额外带`subject_zone_id`），不得添加`direction`键。任何非 MIGRATION 的新增 zone 或生命周期变化都必须由相应的已验证多期结构 claim 证明，旧 zone 不得直接消失，只能保留并转为有证据的生命周期。`metric`必须且只能包含`metric_id,unit,aggregation,periods`；前三项为非空字符串，`periods`为正整数。`POINT_IN_TIME`绑定`periods=1`，`MULTI_PERIOD`绑定`periods>=2`。自由文本、来源数量或父维度`VERIFIED`本身都不能替代这些 claim。

### 因果时间线的 exact role 与证据引用

每个假设的`causal_timeline`必须是非空有序数组；每个事件必须且只能包含`sequence,occurred_at,evidence_dimension,evidence_ref,event_role`。`sequence`从 1 连续递增，`occurred_at`为带时区 ISO-8601、按时间非递减、不得晚于`observation.observed_at`，并必须落在其同维度引用 source 的`as_of`点或 claim 的已建立`time_range`内。`evidence_ref`只能是该`evidence_dimension`父 item 中的`source_id`或`claim_id`，禁止跨维度借证。

| `event_role` | 允许维度 | 必须引用 |
|---|---|---|
| `CAUSE_OBSERVED` | `spot_flow`, `derivatives_flow`, `oi_timeline`, `funding_basis`, `capital_source`, `macro` | 同维度`VERIFIED` typed claim |
| `PRICE_RESPONSE_OBSERVED` | `price_quotes` | 同维度报价 source |
| `RETENTION_OBSERVED` | `cross_exchange_acceptance` | 同维度`VERIFIED`且类型为`CROSS_PERIOD_ACCEPTANCE|RETENTION|EFFECTIVE_RETEST|SPOT_VALUE_NON_ACCEPTANCE`的 claim |
| `UNRESOLVED_PRICE_OBSERVATION` | `price_quotes` | 同维度报价 source |

只要 selected driver 不是`UNRESOLVED`，被选假设的 typed timeline 绑定恰好为：`CRYPTO_NATIVE_SPOT`需要`spot_flow:NATIVE_SPOT_FLOW|NATIVE_SPOT_BUY_FLOW|NATIVE_SPOT_BUY`与`capital_source:CAPITAL_SOURCE`的`CAUSE_OBSERVED`，并需要`cross_exchange_acceptance:CROSS_PERIOD_ACCEPTANCE`的`RETENTION_OBSERVED`；`DERIVATIVES_LEVERAGE`需要`derivatives_flow:DERIVATIVES_ACTIVE_FLOW|DERIVATIVES_LEVERAGED_BUY_DOMINANCE`与`oi_timeline:OI_TIMELINE`的`CAUSE_OBSERVED`；`MACRO_BETA`需要`macro:MACRO_EVENT_TIMELINE`的`CAUSE_OBSERVED`；`MIXED`合并其每个 component 的全部要求，不能用一个 component 覆盖整体。每组必需 cause 都必须各有至少一个绑定同维度`VERIFIED` typed claim 的事件，并在数组 sequence 和`occurred_at`上共同严格先于同一个已验证`PRICE_RESPONSE_OBSERVED`或`RETENTION_OBSERVED`；必需 retention 仍须独立存在并按真实时间排序，不能冒充 cause。该门禁同样适用于`decision_evidence_status=EVIDENCE_LIMITED`，证据受限不得绕过。只记录“先涨后找原因”、只引用 source 充当 cause 或只写自然语言领先关系均失败。

## 三类指纹

所有指纹均使用同一规范序列化：UTF-8 JSON、对象键按字典序排列、无多余空白；无序数组按其稳定业务身份排序，有序事件链保持顺序；十进制值使用归一化字符串；SHA-256 输出为`sha256:<64 lowercase hex>`。禁止对渲染后的 Markdown 直接散列。

### `observation_fingerprint`

用途：判断是否打开深评门，不决定写文件或报告。规范输入只包含：

```text
normalization_revision
source_health_class
quote_relation_by_venue
closed_bar_relation_sequence
cross_venue_divergence_class
evidence_availability_by_dimension
event_discriminator_status
final_expiry_status
```

它不得包含抓取时间、运行 ID、原始精确现价、未完成 K 线的暂态数值或账户未知项。新指纹与上次观察不同仅表示“需要判断”，不表示策略变化。

### `decision_fingerprint`

主因果字段包含已采纳假设和其 typed claim 规范化机制（含 BUY/SELL 方向、角色及必要目标），不是只有 H_SPOT 等类别身份。claim 别名归一，原始数值、来源 ID 与时点不进入决定指纹。

用途：唯一判断策略决定是否发生实质变化。规范输入只包含：

```text
primary_causal_hypothesis
driver_regime
driver_components
value_zone_lifecycle_by_zone_id
anchor_map_semantics
path_decision
forward_value_band_semantics
portfolio_stance_by_module
trade_candidate_decisions
account_fact_semantics
major_risk_semantics
```

它不得包含状态/计划/cycle ID、推导或定价时间、原始报价快照、运行状态、事件游标、报告措辞、证据抓取健康或`observation_fingerprint`。价格进出价值区、窗口到达或观察指纹变化都不能单独形成 D。既有 revision 3 原始指纹含一次性事实同步字段，其验真算法保持不变；日常 D 判断须采用下述绑定基线的同域兼容比较，不能把原始哈希不同直接当作实质变化。

上述字段必须是稳定业务 ID、枚举或结构化用户决定语义，不得直接散列自由文本理由；措辞变化不能制造 D。`value_zone_lifecycle_by_zone_id`实际逐 zone 包含`zone_id,role,band,lifecycle`；`portfolio_stance_by_module`逐模块包含`decision,amount_u,net_exposure_impact,capital_pool_impact`；`trade_candidate_decisions`包含全部候选的稳定 identity、kind、候选资格、报告相关性以及每个 entry 的价位或区间、金额、`amount_basis`、`value_role`、资格、形成/失效条件、证据置信度、账户前置与`carried_forward`；`account_fact_semantics`包含真实本地事实 Owner 版本及已知变化分支。因此候选金额、方向承诺、角色、资格或事实版本变化都能形成 D，不能以“未激活”隐藏。`strategy_revision`、运行身份和纯报告措辞不进入该指纹。

### 已消费 FACT_SYNC 的同域兼容比较

旧状态、计划、sidecar、三类原始指纹和`previous_fingerprints`继续按原算法逐字验真，不能修改历史 Owner 或把调用方自报的比较哈希冒充旧指纹。只有实际 revision 3 `previous_baseline`及当前事实版本/确认关系均通过校验后，日常控制派生才从该基线的实际 sidecar 和本轮 bundle 双侧提取`decision_semantics`，仅移除`account_fact_semantics`中的`account_fact_changed_known,known_change_evidence,local_record_status`。完整`fact_owner_fingerprints`及其他所有决定语义继续参与同一规范散列；不得扩大屏蔽范围或原地修改输入。

两侧同域比较哈希是否不同才决定`control.decision_changed`。因此仅把已消费的 FACT_SYNC 清为 NONE/false 时，即使兼容保留的原始 decision 哈希不同，也可为 A：不改 Owner、不报告。真实事实版本变化仍为 D，且必须通过原有真实版本、确认类型/Owner 精确绑定与报告语义门；伪造 FACT_SYNC 或隐藏真实变化一律失败。公共`derive_control`也执行共享的事实形状和真实版本/确认门，不能把只校验快照自身当成当前输入合法。

`validate-bundle`成功输出的`decision_comparison`只供审计，包含`mode,previous_decision_comparison_fingerprint,current_decision_comparison_fingerprint,decision_changed`；日常模式为`BOUND_REVISION3_FACT_SYNC_COMPAT`。该诊断不是第四个持久 Owner，不进入 PRE_WRITE、状态、计划、FINAL 或云端，不能由调用方覆盖控制结果。MIGRATION 和无基线私有回放仍使用`RAW_FINGERPRINT`；后者从不构成生产验收。基线验真失败不得退回原始比较后宣称成功。

### 候选 identity、方向与报告可见性

候选 kind 仍分为 `PREMIUM_SHORT`与`DISCOUNT_SPOT`，分别绑定 Alpha 名义敞口与共享现货资金池；每阶用稳定的 `S1…Sn`或`D1…Dn`身份，数量不固定。方向、价值角色和必需证据按核心策略核验，不能靠换 ID 改变同一承诺。价格必须位于其有效结构锚相应一侧，不从现价固定百分比反推。

`CONDITIONAL_ADD`允许新增现货和新增空头使用任意有依据的正建议金额；金额必须逐模块等于同方向已获得评估资格阶梯的建议合计。现货是资金池占用，空头是名义敞口，不乘杠杆；程序不再固定 600U，也不把未激活档位当作当前建议。已有仓位的事实不受这些建议影响。

完整 `amount_basis`保存预算来源、净敞口约束、价值差理由、风险假设和沿用理由；这些必须是真实评估依据，而非用“未变”代替思考。稳定业务语义用可扩展的语义键承载，具体说明进入假设摘要及自然语言报告。数字、形成/失效条件与角色是决定的一部分，不能藏在不参与比较的正文中。

正式报告使用[语义与自然表达契约](report-expression.md)，其八类语义直接绑定本轮决定。变化、新增或取得评估资格的候选必须披露；已经披露的承诺在语义中保持连续，未变且从未披露的未激活阶梯只保存在内部计划。完整候选承诺绑定当前 entry，不再通过固定中文标签或写死的 80,000U 失效条件表达。

### `report_fingerprint`

用途：判断用户可见决定是否变化和云端投递去重。规范输入只包含：

```text
overall_user_decision
primary_causal_explanation
rejected_competing_explanations
portfolio_action_by_module
decision_relevant_levels_and_sizes
material_uncertainties
next_decision_changing_evidence
user_visible_major_risk
```

它不得包含报告时间、价格快照、内部 ID/revision、A/D/E、运行/投递状态、状态或计划元数据、任何其他指纹。未发送报告也必须从当前用户可见决定投影出非空`report_fingerprint`，供以后比较。

竞争解释、因果方向、五模块决定和下一鉴别问题使用可扩展的稳定结构语义；自然解释、来源抓取时间、标题与措辞不参与指纹。主假设身份相同但已采纳因果机制或方向变化，必须改变 decision/report 指纹，不能仍判 A。

任一规范输入提取为空、字段类型错误、哈希格式错误，或状态与计划的`observation_fingerprint`、`decision_fingerprint`、`report_fingerprint`、revision、state/plan/cycle 投影身份任一不一致，都属于最高优先级的硬失败：禁止修改正式状态/计划、禁止写 Scheduled 收据、禁止生成策略报告或发送云端消息。此类一致性失败不得降格为“重大风险说明”；由任务失败通知暴露。独立观察库可在自身契约验证通过后记录缺口，不推进成功深评或已报告基线。

## 语义变化、A/D/E 与报告门

每次运行在通过 Schema 校验后形成内部写前决策包；该对象只描述计划执行的决定，不描述尚未发生的写入、报告或云端完成事实：

```yaml
schema: BTC_STRATEGY_DECISION_V3
bundle_phase: PRE_WRITE
decision_evidence_status: RESOLVED | EVIDENCE_LIMITED | UNRESOLVED
projection_mode: CURRENT_DERIVATION | LAST_VALID_ANCHOR
user_input_requirement: NONE | FACT_SYNC
control:
  write_transition: NONE | D | E | MIGRATION
  decision_class: OBSERVATION_ONLY | STRATEGY_SEMANTIC_CHANGE | FINAL_EVENT | REVISION_MIGRATION
  monitor_state: A | D | E | NONE
  project_patch_required: true | false
  report_required: true | false
  attention_planned: true | false
  attention_level: NONE | CHANGE_NOTICE | MAJOR_RISK
  decision_changed: true | false
  report_changed: true | false
```

PRE_WRITE 的`runtime`必须且只能包含`run_id,state_id,plan_id,pricing_as_of,trigger_type`。前三个 ID 必须分别符合`BTC-CYCLE-*`、`BTC-STATE-*`、`BTC-*`稳定格式；`pricing_as_of`使用项目 JST 时间格式，必须不早于`observation.observed_at`且不得超过本地时钟加 5 分钟。`trigger_type`必须是本轮真实入口：非 MIGRATION 只允许`PLAN_GENERATION|SCHEDULED_STRATEGY_BOUNDARY|MANUAL_REEVALUATION`，MIGRATION 必须且只能为`IMPLEMENTATION_MIGRATION`。它进入完整 projection/bundle identity 以供审计，但不进入`observation_fingerprint`、`decision_fingerprint`或`report_fingerprint`，因此入口变化本身不能制造 D 或报告。

`control`必须完全等于确定性派生结果；日常`decision_changed`必须逐字等于上述真实 revision 3 基线双侧同域兼容比较结果，原始当前/previous decision 指纹仍各自完整验真，日常`report_changed`必须逐字等于当前`report_fingerprint`相对受绑定 previous report fingerprint 的比较结果；首次MIGRATION唯一使用下述历史语义桥接比较，不接受调用方覆盖。普通无报告运行的`attention_level=NONE`，产生报告的正常变化为`CHANGE_NOTICE`，产生报告且有新重大风险时为`MAJOR_RISK`。`attention_planned`必须严格等于`report_required`。`PRE_WRITE`从顶层到每一个已知嵌套 Mapping 均执行逐层 exact-key allowlist，而不是只扫描黑名单；未知键一律失败。任何层级都不得夹带`attention_emitted`、当前运行的云端交付状态、状态/计划回读结果、报告校验结果、报告正文哈希、云端可见正文哈希、云端回执、可见性或“没有二次分析”等未来事实；历史交付只能隔离在具有独立严格 Schema 的`previous_delivery`中。

`monitor_state=NONE`只允许一次性迁移，且必须同时满足`write_transition=MIGRATION`与`decision_class=REVISION_MIGRATION`；日常 Scheduled 运行只允许 A/D/E。

- A：没有最终事件，且无需深评或有效深评未发现绑定基线同域比较中的实质决定变化，也不存在新的重大风险语义。即使报价关系、`observation_fingerprint`或仅消费标志造成的原始 decision 哈希变化，也保持`attention_level=NONE`、`project_patch_required=false`、`report_required=false`。
- D：非最终事件的有效深评在绑定基线同域比较中产生实质决定变化。主任务原子更新状态与计划；正常变化使用`CHANGE_NOTICE`。仅当`report_fingerprint`也变化时，`report_required=true`；新重大风险必须进入两类语义指纹，已报告且未变化的重大风险不得重复投递。
- E：`final_expiry_event`已完成。必须深评并建立下一周期，因而原子更新状态与计划；E只终止旧投影，不改变真实仓位。若新周期的用户可见决定未变，则`report_required=false`，不得机械报告。

revision 2到revision 3的首次激活使用一次性`MIGRATION`，不伪装成 D 或 E。迁移决策包使用`write_transition=MIGRATION`、`decision_class=REVISION_MIGRATION`、`monitor_state=NONE`、`project_patch_required=true`。`validate-bundle`的四个`--previous-*`参数必须 all-or-none：`--previous-state`与`--previous-plan`必须是写前固定 Owner 路径，`--previous-report`与`--previous-report-projection`必须是下文列出的不可变 fixture；MIGRATION 禁止并传`--current-state/--current-plan`，非 MIGRATION 禁止传任一迁移参数。旧报告由`revision2_report_bridge.py`以固定实物和来源见证重投影，生成非空旧完整指纹；`report_changed`唯一使用`migration_compare`在固定历史未知路径双侧屏蔽后的同域比较指纹，不直接比较跨格式哈希，也不将比较指纹覆盖正式报告指纹。来源、屏蔽范围和编码差异审查按[自然表达契约](report-expression.md#迁移比较)。无法完成真实基线校验时迁移失败关闭。迁移必须由 Sol/max 按切换时最新公开证据重新推导，主任务原子生成一组 revision 3 状态与计划；只有用户可见决定实质变化才投递报告。迁移完成后，Scheduled 只使用 A/D/E与完整报告指纹。

重大风险与 D/E 正交，但 A 不得与新的`MAJOR_RISK`语义并存。重大风险只包括：既定硬失效或安全边界、替代核验后决定性来源仍严重冲突，或会实质改变风险判断的本地已确认事实矛盾。首次确认或语义变化的重大风险必须改变`major_risk_semantics`并形成 D/E；已报告且未变化的风险由报告指纹去重。普通波动、账户实时字段未知、单一来源异常和观察关系切换均不是重大风险。

`user_input_requirement=FACT_SYNC`仅在真实本地事实 Owner 相对当前策略基线已有可信内容版本变化、但尚未同步进派生状态时使用。实时权益、可用现金、委托、累计资金费、强平距离等未知，或泛化的“如有变化”提醒，一律保持`NONE`。核心策略不得请求用户选择内部状态或真实执行动作。

### 真实本地事实版本与 FACT_SYNC

确定性工具每次从以下三条固定路径完整读取 UTF-8 实物，不做 trim、换行归一或调用方路径替换，并计算内容 SHA-256：

| `owner_id` | 固定事实 Owner |
|---|---|
| `ASSET_CONFIGURATION` | `$BTC_PROJECT/资产配置.md` |
| `OPERATION_LOG` | `$BTC_PROJECT/操作记录.md` |
| `CORRECTION_LOG` | `$BTC_PROJECT/纠正记录.md` |

`account_facts.fact_owner_fingerprints`必须且只能含上述三个`owner_id`，值逐项等于本轮真实实物哈希；它属于`decision_fingerprint`和两份 Owner 的 canonical semantics。未确认变化分支必须且只能包含`account_fact_changed_known=false,fact_owner_fingerprints`。已确认变化分支必须且只能再增加非空`known_change_evidence`与`local_record_status=FACT_SYNC_PENDING`；每个 evidence 必须且只能含`confirmation_type,owner_id`，其中`confirmation_type`只允许`USER_CONFIRMED_POSITION_CHANGE|USER_CONFIRMED_EXECUTION|USER_CONFIRMED_ACCOUNT_FACT_CORRECTION`。相对 write-before，实际发生变化的 owner ID 集合必须与 evidence 声明的 owner ID 集合完全相等，并同时要求`account_fact_changed_known=true`与`user_input_requirement=FACT_SYNC`；没有真实版本变化不得自报 FACT_SYNC，版本已变也不得隐藏为`NONE`。MIGRATION 不得同时宣称新的账户事实变化。

## 可持续的事件周期

生产 `event`必须恰好包含`final_event_reached,cycle,trigger`。精确字段、时区和日期绑定由`scripts/event_contract.py`校验，不能只提供布尔值。每个周期保存稳定身份、建立时点、2–4个顺序事件和唯一末尾最终事件；每项都有官方日历或公开观察时段依据、完成条件、待鉴别假设、反证结果与下一观察内容。现金开盘不是必选事件。

`final_event_reached`只能由已绑定的旧周期最终事件与实际截止时点派生。E 消费旧最终事件，原子建立新身份及尚未完成的新事件链；下一轮检查新周期，不得重复消费旧最终事件，也不得把旧周期重新标成 PENDING。中间事件已到不等于 E，单独日历换页不产生报告。历史 baseline 校验使用该基线的历史时点，不能因为今天已晚于它的观察窗口就损坏回读；当前运行仍必须处理真正已经到达的最终事件。

迁移使用独立 MIGRATION trigger 建立新链，不假称旧报告发生一次有效策略迁移。状态 metadata 的 cycle 身份必须与 event.cycle.cycle_id 一致。事件完成不意味着任何真实仓位结束。

## 原子状态/计划更新

固定脚本是`$BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py`，固定 Owner 是`$BTC_PROJECT/策略状态.md`和`$BTC_PROJECT/当前预期计划.md`。下文`RUN/...`表示本次运行的受限临时实物路径；两个`RUN/previous-*.md`必须在任何写入前从固定 Owner 同时捕获，整个事务不可变且内容身份写入 PRE_WRITE 的`previous_projection_baseline`。

深评先在内存中形成完整 PRE_WRITE 决策包；状态与计划全文不得由调用方手写。严格按以下顺序关闭门禁：

1. 日常 revision 3 首先对当前固定 Owner 执行：

   ```text
   python3 $BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py validate-bundle \
     --input RUN/bundle.json \
     --current-state $BTC_PROJECT/策略状态.md \
     --current-plan $BTC_PROJECT/当前预期计划.md
   ```

   该分支要求`--current-state/--current-plan`成对出现并且就是固定 Owner，禁止所有迁移`--previous-*`参数。

2. 一次性 MIGRATION 改用以下 exact invocation，四个`--previous-*`必须同时出现，且禁止`--current-state/--current-plan`：

   ```text
   python3 $BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py validate-bundle \
     --input RUN/bundle.json \
     --previous-state $BTC_PROJECT/策略状态.md \
     --previous-plan $BTC_PROJECT/当前预期计划.md \
     --previous-report $BTC_LEGACY_REVISION2_DIR/revision2_report_1346.md \
     --previous-report-projection $BTC_LEGACY_REVISION2_DIR/revision2_report_1346_semantics.json
   ```

   工具还会逐字比对写前 Owner 与不可变`revision2_state_1346.md`、`revision2_plan_1346.md`。`$BTC_LEGACY_REVISION2_DIR` 取自当前节点配置的 `legacy_revision2_dir`，仅使用本机已保存的历史基线，不能用测试生成材料补齐。四份本机历史文件为：

   - `$BTC_LEGACY_REVISION2_DIR/revision2_state_1346.md`
   - `$BTC_LEGACY_REVISION2_DIR/revision2_plan_1346.md`
   - `$BTC_LEGACY_REVISION2_DIR/revision2_report_1346.md`
   - `$BTC_LEGACY_REVISION2_DIR/revision2_report_1346_semantics.json`

   它们由节点 `profile.json` 中已审阅的 SHA-256 绑定保护；调用方自报哈希、替代路径或事后新建副本不能作为迁移基线。

3. 仅在派生控制要求 D/E/MIGRATION patch 时，以唯一全文 renderer 分别生成两份候选 Owner。非 MIGRATION 的两个 invocation 都必须携带同一对 write-before snapshot：

   ```text
   python3 $BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py render-projection-document \
     --bundle RUN/bundle.json --role STATE \
     --previous-state-snapshot RUN/previous-state.md \
     --previous-plan-snapshot RUN/previous-plan.md
   python3 $BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py render-projection-document \
     --bundle RUN/bundle.json --role PLAN \
     --previous-state-snapshot RUN/previous-state.md \
     --previous-plan-snapshot RUN/previous-plan.md
   ```

   MIGRATION 只使用`--bundle`与`--role`，禁止两个 snapshot 参数；工具改为加载上述四份 immutable fixture。成功 stdout JSON envelope 必须且只能包含`ok,hard_failure,errors,document,document_content_fingerprint`；只将`document`字符串按 UTF-8 原样保存为`RUN/candidate-state.md`或`RUN/candidate-plan.md`。不得手写/改写正文、trim、转换 CRLF/LF、追加内容、嵌入整个 envelope，或改用低层`render-projection-sidecar`拼装 Owner。

4. 全文渲染完成后、写入前执行候选投影校验：

   ```text
   python3 $BTC_RELEASE/skill/btc-core-strategy/scripts/strategy_contract.py validate-projection \
     --state RUN/candidate-state.md --plan RUN/candidate-plan.md \
     --bundle RUN/bundle.json \
     --previous-state-snapshot RUN/previous-state.md \
     --previous-plan-snapshot RUN/previous-plan.md
   ```

   非 MIGRATION 两个 snapshot 缺一即失败；MIGRATION 禁止传 snapshot，并以内置 immutable fixture 重算基线。

5. 写入前再次用固定 Owner 执行日常`validate-bundle`或 MIGRATION 四参校验，证明它们未偏离 write-before 基线。任何变化都中止，不覆盖并发新状态。
6. 仅在 D/E/MIGRATION 下以一个完整原子补丁同时把两个 renderer `document`写入固定 Owner；任何失败都保留旧文件，不得先删除旧计划或留下半份投影。
7. 回读固定 Owner，执行 post-write`validate-projection`：`--state/--plan`改为两条固定 Owner 路径，`--bundle`仍是同一个 PRE_WRITE。非 MIGRATION 继续传原 write-before snapshot；MIGRATION 继续禁止 snapshot，工具仍以 immutable fixture 为 previous baseline。只有固定 Owner 全文逐字等于同一 bundle 的确定性全文 renderer，且 post-write 校验成功，才允许报告、云端和 FINAL 收据。

不得先删除旧计划，不得留下半份新投影。A 允许写入独立小时观察及已验证的深评完成记录，不修改正式状态/计划或资产事实。状态/计划不一致不能通过重写收据掩盖，也不能以旧报告对外宣布成功。

## 计划投影

`当前预期计划.md`的 canonical metadata exact-key 集合如下，每个键必须恰好出现一次；内嵌 sidecar 的`metadata`必须且只能包含这组键：

```yaml
source_strategy_id: BTC-CORE
source_strategy_revision: 3
source_state_id: BTC-STATE-YYYYMMDD-HHMM
plan_id: BTC-YYYYMMDD-HHMM
cycle_id: BTC-CYCLE-YYYYMMDD-HHMM
state_as_of: YYYY-MM-DD HH:MM JST
observation_fingerprint: sha256:...
decision_fingerprint: sha256:...
report_fingerprint: sha256:...
```

`render-projection-document`是两份 Owner 全文的唯一 renderer：STATE 使用固定 YAML metadata 顺序，PLAN 使用固定标题与 metadata 顺序；随后按固定章节顺序写入`- canonical: <compact sorted JSON>`。状态正文必须恰好投影`observation,evidence_matrix,hypothesis_competition,anchor_map,forward_value_band,trade_candidates,portfolio_decision,event`八个章节；计划正文必须恰好投影`evidence,hypothesis,lifecycle,path,portfolio,candidates,event`七个章节。调用方不得在 renderer 全文中插入可读摘要、持仓说明或其他任意 Markdown；完整语义已由 canonical payload 承载。

每份文档必须且只能内嵌一段`BTC_STRATEGY_PROJECTION_SIDECAR_V3` marker。sidecar 顶层 exact-key 集合为`schema,document_role,bundle_identity,document_body_fingerprint,metadata,canonical_semantics`：

- `schema=BTC_STRATEGY_PROJECTION_SIDECAR_V3`，`document_role=STATE|PLAN`。
- `document_body_fingerprint`绑定移除 sidecar 后所得的 renderer 正文字节；不 trim、不归一化尾部空白或 CRLF/LF。
- `metadata`必须逐键等于实际 Markdown 的相应 STATE 或 PLAN canonical metadata exact-key 集合。
- `canonical_semantics`必须且只能包含`strategy_revision,observation,quote_verification_status,decision_evidence_status,evidence_matrix,causal_attribution,value_zones,anchor_map_semantics,path_decision,forward_value_band_semantics,portfolio_decision,trade_candidates,major_risk,account_facts,user_input_requirement,projection_mode,event,runtime,report_semantics,fingerprints,control`。
- `bundle_identity`由`schema`与完整`canonical_semantics`确定性重算；STATE/PLAN 必须相同。三类指纹和`control`也从 canonical semantics 重算，不接受 Markdown 或调用方自报值。

`validate-projection`从两份实际 Markdown 抽取 sidecar，逐键与同一个 PRE_WRITE 重算，并将两份完整实物逐字与`render-projection-document`重算结果比较，同时核对 revision、state/plan/cycle 身份与三类指纹。sidecar 属于同一个两文件原子事务，不新增第三或第四个持久化 Owner；完整计划和 sidecar 均不得原样投递为用户报告。低层`render-projection-sidecar`只保留为兼容接口：其`--input`本身必须已经逐字等于确定性 body，stdout envelope 中只有`sidecar_block`可追加；正常写入流程禁止使用它替代全文 renderer。

## Scheduled 收据、阶段隔离与严格静默

- Scheduled 平台历史只用于运行审计，不是策略或资产事实。
- 任务目录`节点配置的 automation_memory`只保留一份最新受限最终收据，不得保存账户明细或逐小时行情叙述。正常完成使用`schema=BTC_STRATEGY_RUN_RECEIPT_V3`和`receipt_phase=FINAL`；深评不可用的严格降级使用独立`BTC_DEEP_EVAL_UNAVAILABLE_V3`，不得合成新决定。
- `FINAL`收据的公共 exact-key 集合为`schema,receipt_phase,bundle_identity,decision_class,attention_level,monitor_state,write_transition,project_patch_required,report_required,attention_planned,local_readback_state,report_validation_state,cloud_delivery_state,report_visible,observation_fingerprint,decision_fingerprint,report_fingerprint`。PRE_WRITE 含`previous_delivery`时 FINAL 必须且只能额外包含同名严格对象；否则禁止该键。`report_required=true`时再精确增加`report_body_fingerprint`；只有`cloud_delivery_state=DELIVERED`再精确增加`cloud_visible_body_fingerprint,cloud_readback_fingerprint`。任何其他键或分支缺键均失败。
- 无报告分支固定`report_validation_state=NOT_REQUIRED`、`cloud_delivery_state=NOT_REQUIRED`、`report_visible=false`，不允许报告或云端实物。报告分支固定`report_validation_state=VALIDATED`并必须提供实际 renderer `body`；`DELIVERED`、`PENDING_RETRY`、`DELIVERY_DEGRADED`字段集互斥，后两者`report_visible=false`且禁止`--cloud-readback`。
- `validate-receipt`始终要求`--input`与`--bundle`，后者必须是本轮实际已验证 PRE_WRITE。A、D/E、MIGRATION 的基线和实物参数严格按下表组合，不能混用：

  日常 CLI 先加载实际提供的一对 current 或 snapshot 并校验绑定，再执行同域决定比较，最后按派生结果核对 A 必须是固定 current Owner、D/E 必须是 write-before snapshot；不能先按原始哈希判 D 再要求 snapshot。两对同时出现、缺半对、基线被篡改或角色错误均失败。

| 分支 | 必须提供 | 必须省略 |
|---|---|---|
| A 静默 | 固定 Owner 的`--current-state`与`--current-plan` | `--previous-state-snapshot`, `--previous-plan-snapshot`, `--state`, `--plan`, `--report`及全部 cloud 参数 |
| D/E patch、无报告 | write-before 的`--previous-state-snapshot`与`--previous-plan-snapshot`；写后固定 Owner 的`--state`与`--plan` | `--current-state`, `--current-plan`, `--report`及全部 cloud 参数 |
| D/E patch、`PENDING_RETRY|DELIVERY_DEGRADED` | 上一行全部参数，再加实际`--report` | `--current-state`, `--current-plan`, `--cloud-readback` |
| D/E patch、`DELIVERED` | 上一行全部参数，再加实际`--report`与`--cloud-readback` | `--current-state`, `--current-plan` |
| MIGRATION patch | 写后固定 Owner 的`--state`与`--plan`；按报告状态添加与 D/E 相同的 report/cloud 实物 | 两个`--current-*`和两个`--previous-*-snapshot`；迁移 previous baseline 由 immutable fixtures 内置加载 |

  `--current-state/--current-plan`与两个 snapshot 参数都必须各自成对；两对不能同时存在。只要`project_patch_required=true`，`--state/--plan`必须成对且只能是两条固定 Owner 路径。`report_required=false`禁止`--report`；`report_required=true`必须传`--report`。只有 FINAL 声明`DELIVERED`时才允许且必须传唯一`--cloud-readback`实物。
- Sol/max 不可用不走`validate-receipt`，只能执行`validate-unavailable-receipt --input RUN/unavailable-receipt.json --current-state $BTC_PROJECT/策略状态.md --current-plan $BTC_PROJECT/当前预期计划.md --observation RUN/unavailable-observation.json`。两个 current 参数必须是固定 revision 3 Owner；observation 必须是实际小时观察并绑定 Owner 中原价值区/锚和本轮报价证据。该分支不接受 bundle、patch、report 或 cloud 参数。
- `BTC_DEEP_EVAL_UNAVAILABLE_V3`收据必须且只能包含`schema,deep_eval_status,retry_required,receipt_phase,monitor_state,attention_planned,project_patch_required,report_required,local_readback_state,report_validation_state,cloud_delivery_state,report_visible,observation_fingerprint,reused_decision_fingerprint,reused_report_fingerprint`；固定值由工具校验。其`--observation`顶层必须且只能包含`schema,observation,value_zones,anchor_map_semantics,price_quote_evidence,fact_owner_fingerprints,evaluation_as_of`，其中`schema=BTC_UNAVAILABLE_OBSERVATION_V3`。`value_zones`与`anchor_map_semantics`必须在结构化值上等于当前 Owner baseline；`fact_owner_fingerprints`必须逐项等于三份实际本地事实 Owner 且等于 baseline，事实版本变化不能被该分支隐藏；`price_quote_evidence`必须是本轮两个或更多 registry 来源的已验证报价证据。
- UNAVAILABLE 只允许真实普通复核候选：`event_discriminator_status=DEEP_REVIEW_CANDIDATE`、最终事件仍`PENDING`、来源健康不是`DECISIVE_SOURCE_CONFLICT`，且去除事件判别/最终状态暂态后的规范化 observation 必须相对当前 Owner baseline 实际变化，或者绑定观察库中的真实小时深评候选；任意模型自报状态或哈希不成立。实际证据截止不得晚于 observation.observed_at，时间不得早于 baseline。`evaluation_as_of`必须是带时区 ISO-8601，不得超过本地调度器 UTC 时钟加 5 分钟；`observation.observed_at <= evaluation_as_of`，报价来源和范围仍受`observed_at`截止门约束。`observation_fingerprint`同时绑定规范化 observation 与`evaluation_as_of`。任一门失败都不得写降级收据，也不得合成组合决定或候选阶梯。
- `cloud_delivery_state=DELIVERED`只接受一个有序可信回读 JSON：`--cloud-readback`必须且只能包含`schema,visible_report_text,assistant_responses`，其中`schema=BTC_CLOUD_READBACK_V3`、`visible_report_text`逐字等于实际`--report`、`assistant_responses`逐项且完整地恰好等于`["已收录"]`。工具将云端正文哈希绑定`cloud_visible_body_fingerprint`，将整个回读对象的规范指纹绑定`cloud_readback_fingerprint`，并要求`report_visible=true`。本地报告、云端正文和回执均不得 trim、折叠空白或转换 CRLF/LF；数组存在第二条响应即代表第二份输出并失败。最终收据通过上述实物绑定后才能整体替换`memory.md`。
- 日常 A/D/E 使用`monitor_state`；迁移收据固定使用`monitor_state=NONE`、`write_transition=MIGRATION`和`decision_class=REVISION_MIGRATION`。A/D/E、`attention_level`、`project_patch_required`和`report_required`只属于逐次运行收据；持久状态只用`created_by_transition`说明它由 D、E 或 MIGRATION 哪一种写入事务建立，不得把旧 D/E 当作最近运行结果。
- A 可以在所有校验通过后整体替换受限收据，并保留最近报告的交付身份；只允许维护独立观察库、禁止修改正式状态/计划与资产事实，不得输出 final、占位文字、`::inbox-item`、云端消息或其他用户注意信号。
- 若运行环境强制每次产生 final 或`::inbox-item`，必须如实记录`selective_notification_status=UNSUPPORTED_BY_RETURN_CONTRACT`，不得虚报 A 静默已经验收。
- 普通候选深评不可用时静默保留最近有效锚。只有上一组已验证、非空的`decision_fingerprint`和`report_fingerprint`可复用，且本轮已生成有效、非空的`observation_fingerprint`时，才可在受限收据记录`deep_eval_status=UNAVAILABLE`与`retry_required=true`；缺少这三个有效输入中的任一个即失败关闭且不写收据。入口阶段不得生成替代决定。

## 决策优先的评估报告

生产报告必须使用[语义与自然表达契约](report-expression.md)。深评先完成可审计判断，再自由撰写中文解释；八类稳定语义与自然文案分开，文案措辞不改变报告指纹。金额、方向、角色及条件必须在语义内绑定真实决定，不得靠固定句式模拟分析。

`render-report --projection RUN/report-semantics.json`返回唯一冻结正文 `body`和内部 `validation_projection`；用后者执行`validate-report --input RUN/report.md --projection RUN/validation-projection.json`，通过后逐字投递正文。这里“确定性”只约束同一已完成稿的渲染、数值绑定与发送一致性，不约束模型只能选择固定句子，也不证明因果真实性或自然语言语义等价。

旧的单代码报告格式、固定金额映射和仅含到期布尔值的事件格式仅保留为历史回放兼容接口。`validate-bundle`与`validate-receipt`生产入口拒绝它们；新生产基线不得降级回旧格式。内部 replay helper、低层 renderer 或旧测试通过不构成写入正式 Owner 的许可。

## 云端不可变完成稿与回读

- 云端会话只是固定展示副本，不是策略或资产事实 Owner。主任务只发送本地已经完成、回读校验通过且`report_required=true`的报告。
- A或任何`report_required=false`的运行只继承最近交付身份，不回读云端、不重试也不发送消息。
- 消息必须明确：`这是本地 Owner 的完成稿，只收录、不重算、不摘要。请仅回复“已收录”。` 报告正文随其后发送；接收方不得浏览市场或生成第二份策略解释。
- 发送后必须等待接收完成并按原有顺序回读完整消息。将实际可见报告和接收方全部完整响应写入唯一`BTC_CLOUD_READBACK_V3` JSON，并作为`validate-receipt --cloud-readback`输入；工具要求可见报告逐字等于本地 renderer `body`且完整响应数组恰好为`["已收录"]`。正文和响应均禁止 trim、空白折叠或 CRLF/LF 转换；任何第二条响应、第二份分析或缺失消息都会令`DELIVERED`失败。
- 超时、正文不可见、产生第二份策略解释或回读失败时记录`PENDING_RETRY`或`DELIVERY_DEGRADED`。云端失败不回滚已经验证的本地状态/计划，也不得在同一运行或后续小时重复发送相同`report_fingerprint`造成刷屏；先恢复或人工核对既有投递状态。

## 项目级 Scheduled 固定边界

- 标题：`BTC核心策略自动重评`
- 频率：每 1 小时
- 项目：`$BTC_PROJECT`
- 执行环境：本地项目 Cron，`ACTIVE`，失败通知策略不变。
- 只允许原位更新唯一现有任务，不得创建第二个用户可见任务；主任务是唯一项目写入与云端投递 Owner。
- 本契约定义行为，不复制或维护 Automation 的完整提示词；具体提示词只需调用当前 Skill 并遵守本契约。


## 小时观察与重要形态的精确集成契约

### 可选观察绑定

`observation` 增加可选 `hourly_review_binding`，恰好包含：

```json
{"evidence_id":"sha256:<64 hex>","review_id":"sha256:<64 hex>","observed_hour":"2030-03-01T13:00:00Z","context_id":"sha256:<64 hex>"}
```

四字段来自 `market_observation.record` 返回的实际 binding，不由模型臆造。`observed_hour` 是最后一个完整小时的起点；实际证据 `cutoff` 是小时终点，必须不晚于正式 `observation.observed_at`。

当前 bundle 的绑定通过固定观察库真实读取，并重新校验冻结 evidence、review 和写入时 context。旧 baseline 校验只审查持久化绑定形状，不依赖当前数据库或已推进的 context。生产调用额外要求实际 review 的 `model` 恰为 `{"name":"gpt-5.6-sol","reasoning_effort":"high"}`；Terra/high 与 Sol/medium 只属于隔离对照。

绑定只进入 observation 指纹，不直接进入 decision/report 指纹。没有新字段的旧 revision 3 路径不填默认值、不读观察库，旧 Owner 正文、sidecar 与三指纹算法保持原行为。

### 深评接受的重要形态

`causal_attribution` 可增加 `material_pattern`，无重要结论时不加字段，也不补 `null`、空列表或空对象。

```json
{
  "schema":"BTC_MATERIAL_PATTERN_V1",
  "review_binding":{"evidence_id":"sha256:<64 hex>","review_id":"sha256:<64 hex>","observed_hour":"2030-03-01T13:00:00Z","context_id":"sha256:<64 hex>"},
  "hypotheses":[{
    "kind":"ABSORPTION",
    "stage":"FORMING",
    "confidence":"LOW",
    "support_refs":[{"dimension":"spot_flow","ref_id":"ACTUAL_SOURCE_OR_CLAIM_ID"},{"dimension":"cross_exchange_acceptance","ref_id":"ACTUAL_OTHER_SOURCE_OR_CLAIM_ID"}],
    "counter_refs":[],
    "uncertainties":["PARTICIPANT_INTENT_UNKNOWN","CROSS_VENUE_FLOW_UNRESOLVED"],
    "next_evidence":["SELL_PRESSURE_RETEST_RETENTION"]
  }],
  "impact":["CAUSAL_EXPLANATION"]
}
```

- 外层及各 hypothesis 恰好使用示例键集合。`kind` 唯一，为 `ABSORPTION|POSSIBLE_DISTRIBUTION|ACTIVE_SELLING|DELEVERAGING|LEVERAGED_RISE_OR_SHORT_COVER`；可并存，列表非空。
- `stage=FORMING|ACCUMULATING|STRENGTHENING|WEAKENING|REVERSED|FALSIFIED|UNRESOLVED`。正式置信度为 `LOW|MEDIUM|HIGH`；小时 review 另允许的 `confidence=UNRESOLVED` 不能直接升级成已确认结论。
- `impact` 是非空、不重复的 `CAUSAL_EXPLANATION|EXPECTATION_PATH|RISK_ASSESSMENT` 集合。`uncertainties`、`next_evidence` 为稳定大写语义码列表，后者非空；同一含义沿用已有语义码，不用同义换码制造报告；`stage=UNRESOLVED` 必须保留非空不确定性。
- 每个支持引用必须对应当前 evidence matrix 中真实 `VERIFIED` 维度下的 source 或 `VERIFIED` claim。source 使用父维度的实际多期覆盖，claim 使用自身多期覆盖；不能把单点 quote 改称多期。
- 支持至少包含价格结构维度（`price_quotes|cross_exchange_acceptance`）与流/OI/资金成本维度（`spot_flow|derivatives_flow|oi_timeline|funding_basis`）。通常多期价格结构来自 `cross_exchange_acceptance`，即时 `price_quotes` 不能充数。反证同样引用实际可核验来源；缺失数据进入不确定性。
- 形态观察不要求先拥有 BUY typed cause，不改变 selected driver、价值锚、阶梯资格或建议金额的原有证据门。数据支持“卖压被吸收”不证明吸筹意图或买入资格。
- 新增/变化的重要形态必须绑定本轮真实小时深评 review；沿用未变结论可保留原来源绑定。旧重要 kind 不能直接删除：撤回应保留并明确改为 `FALSIFIED`。
- 稳定 decision 语义只包含 schema、kind/stage/confidence、uncertainties、next_evidence、impact；不包含 review 身份、证据 ID、测量数值、时间或自然文字。已确认的重要变化即使所有模块 MAINTAIN 仍可形成 D 和报告。首次启用不自动注入此字段；不得仅因格式升级制造“重要变化”。

### 八类报告保持八类

`primary_causal_explanation.semantic` 可增加同名 `material_pattern`，形状恰为上述重要形态的稳定语义（去掉 review_binding/support_refs/counter_refs）。`material_uncertainties.semantic` 仍为列表，允许新增 `{pattern_kind,conditions}` 项；`next_decision_changing_evidence.semantic` 可增加 `material_pattern:[{kind,conditions}]`。这些项必须与主解释的 uncertainties/next_evidence 精确一致，不能独立省略或伪造。

在主要解释、关键不确定性（该 kind 有 uncertainties 时）、下一证据的 `narrative` 中，每个重要 kind 都必须有一个带隐藏 `subject=kind` 的自然中文段落。允许同时有无 subject 的整体说明。主要解释对应段落必须 `verification=EVIDENCE_LINKED`，引用该形态自己的 support/counter 实物。subject 及稳定语义码不进入可见正文；仍输出 renderer 冻结的完整 body，保持八类、逐字校验与去重。

这些校验只证明绑定、覆盖与表达结构，不证明自然语言含义或市场因果。Sol/max 必须实际给出形态变化、反证、预期影响及为何暂不调整规模；主任务仍须审阅。

### 成功/不可用分支与基线推进

| 分支 | 校验与项目写入 | 基线推进 |
|---|---|---|
| 普通小时观察，无深评 | `market_observation record` 完整保存小时 review；不构造正式决策包 | 仅 HOUR |
| 成功深评，决定未变 | 原有 A 分支完整 PRE_WRITE + FINAL 实物校验，正式 Owner 不写 | DEEP；REPORT 不动 |
| 成功深评，D/E 本地完成、报告待重试 | 原有 Owner 原子一致性与本地报告校验完成；FINAL 明确 PENDING_RETRY/DELIVERY_DEGRADED | DEEP；REPORT 不动 |
| 报告实际 DELIVERED | 上述完整实物加逐字云端正文和恰好一个“已收录”的可信回读 | REPORT |
| 深评不可用 | 原有 `validate-unavailable-receipt` 加实际小时 binding；报价位置/覆盖未变也允许真实深评候选，禁止任意布尔自报 | 不推进 DEEP/REPORT；观察和候选继续保留 |
| 验证失败、超时、假回读 | 不接受成功凭据；原有完整性硬门继续生效 | 不推进 DEEP/REPORT |

既有 `validate-receipt` 新增一个**显式写入开关**：

```text
--record-hourly-completion DEEP|REPORT|BOTH
```

其余参数与 A / D / E / MIGRATION 的既有 FINAL 实物矩阵完全相同。只在完整 FINAL 校验无错后，再次在写入边界校验全部实物，派生绑定 bundle + receipt 的 SHA256 proof，调用观察库事务。REPORT/BOTH 还要求 `cloud_delivery_state=DELIVERED` 及真实回读；BOTH 按 DEEP→REPORT 顺序记录。待投递先传 DEEP，稍后同一完成稿 DELIVERED 后传 REPORT。同一 review 的成功重放保留首次完成时间，不重置 24 小时深评时钟。

该开关不自动触发报告投递、不产生交易授权，不接受模型自报 `success=true`。底层 `record_completion` 仅供已验证契约适配器调用，不向观察 CLI 暴露。


### 成功深评与已报告结论使用正式接受结果

成功凭据不能只保存 hash 后继续把入口候选当作深评结论。`record_validated_hourly_completion` 在完整 FINAL 实物验证之后，从**正式 bundle** 确定性提取：

```json
{
  "schema":"BTC_ACCEPTED_MARKET_ASSESSMENT_V1",
  "causal_semantics":{
    "driver_regime":"UNRESOLVED",
    "driver_components":[],
    "selected_hypothesis_id":"H_UNRESOLVED",
    "mechanism":[],
    "decision_evidence_status":"EVIDENCE_LIMITED",
    "next_evidence":["STABLE_MARKET_CONDITION_CODE"],
    "falsification_conditions":["STABLE_MARKET_FALSIFICATION_CODE"]
  },
  "material_pattern":null,
  "evidence_refs":{
    "price_quotes":{"status":"VERIFIED","references":[]},
    "cross_exchange_acceptance":{"status":"UNRESOLVED","references":[]},
    "spot_flow":{"status":"UNRESOLVED","references":[]},
    "derivatives_flow":{"status":"UNRESOLVED","references":[]},
    "oi_timeline":{"status":"UNRESOLVED","references":[]},
    "funding_basis":{"status":"UNRESOLVED","references":[]},
    "capital_source":{"status":"UNRESOLVED","references":[]},
    "macro":{"status":"UNRESOLVED","references":[]},
    "relative_strength":{"status":"UNRESOLVED","references":[]}
  }
}
```

- 外层恰好四键。`causal_semantics` 恰好上述七键，机制使用已有 `causal_mechanism_semantics` 稳定形状；`material_pattern` 是已接受的重要形态稳定投影，或正式 bundle 没有重要结论时的 `null`。这份 completion 投影的 `null` 不会向正式 revision 3 文件补字段。
- `evidence_refs` 恰好九个市场维度，明确排除 `account_impact`。引用仅提取 selected hypothesis 时间线及正式 material_pattern 的支持/反证实际用到的 source/claim。每条引用恰为 `{ref_id,reference_type,source_ids,source_urls,time_range}`，`reference_type=SOURCE|CLAIM`，`time_range` 恰为 `{kind,start,end}`；无实际引用时保留维度状态和空列表。URL 必须匿名公开 HTTPS。
- 不保存 portfolio、account_facts、持仓或建议金额、完整报告正文、`reasoning_summary` 或原始思维链。Shape 校验不是原正式 bundle 校验的替代品；两个门必须共同满足。
- `record_completion` 的成功分支必须由适配器传 `accepted_market_assessment=...`；REPORT 另传已验证的 `report_fingerprint`，DEEP 不携带报告指纹。失败分支不得携带接受结论。
- `read_context.successful_deep` 与 `reported_conclusion` 返回 `accepted_market_assessment`；原入口 review 明确改名 `original_hourly_candidate`。下一小时比较深评/报告时必须读已接受结果，不能拿入口候选代替。两者的 evidence 仍可用于累计新证据比较。
- 入口判断“承接形成”而 Sol/max 正式结论为 `FALSIFIED` 时，成功深评基线保存 FALSIFIED；小时候选只作为原始审计材料。REPORT 必须与同 review 已成功 DEEP 的接受结果一致，并绑定真实报告指纹。
- 对同一 review 的成功重放若接受结论或报告指纹不同则拒绝，完全相同则保留首次完成时间。旧库中没有接受结果的历史成功项明确返回 `LEGACY_COMPLETION_PAYLOAD_MISSING`，不得把原入口候选冒充接受结果。
