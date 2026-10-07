# MARKET_COLLECTOR_EVIDENCE_V1 读取契约

此协议与 `BTC_COLLECTOR_EVIDENCE_V1` 并存。旧 BTC 数据继续由原解析器处理；市场协议按 `asset_id` 接受 QQQ、VOO。根字段 `bundle_id` 使用 `market:` 加规范 JSON 哈希，`text_sha256` 绑定原正文，正文首行标的必须匹配。所有原始更新、正文版本和附件保留，导出按当前解析器重新校验，不删除旧拒绝结果。

`instrument` 指定股票 `EQ_QQQ` / `EQ_VOO`、price_currency=USD、volume_unit=shares、calendar=US_EQUITY。衍生品分别是 QQQUSDT/direct、SPYUSDT/proxy。payment_currency=USDC 是支付币种，不替代价格单位。字段名、来源端点和实际标的须匹配；来源标识冲突不能把其他标的记录拼接进来。

支持 stock_quote、stock_1h、derivative_oi、funding、dividend_adjustments、basis、macro。导出只保留类型化数值、单位、来源、时间与限制，忽略机器人 full_report_text 和 mechanical_observation 等分析文字。报价无源时间时 as_of 保留 null、source_time_missing=true，retrieved_at 只表示抓取时间。

stock_1h 采用 ADJUSTED K 线，按 calendar_periods 明确的 UTC 时段核对闭合和缺口，分别识别 PRE、REGULAR、POST、OVERNIGHT。休市不当作缺失 K 线；夏令时取实际日历；日历覆盖外的历史保留限制。复权修订单列标识。股数成交量不是主动资金流。

funding 仅接受 Regular，dividend_adjustments 仅接受 Special，按各自域去重，即使 time 相同也不覆盖。8/24/240 小时窗口分别校验；长窗失效不抹去短窗。基差只由同一衍生品的 mark/index 计算。SPY 代理数据不代表 VOO 自身资金确认；BTC 专属指标对股票不适用。

`collector-observations --asset BTC|QQQ|VOO|all` 无参数保持 BTC 行为。all 按标的分别选择最新正文和当前附件版本，缺失、待下载、无效时返回该标的 gap，不回退旧值；其他标的独立输出。旧附件晚到、正文编辑及重新下载均不得跨标的覆盖。所有输出维持辅助证据身份和独立核验数 0，不访问正文 URL、不查询私人账户、不发送消息。

## V2 并存

`MARKET_COLLECTOR_EVIDENCE_V2` 的 `market-v2:` 哈希绑定完整正文、顶层七个原有数据域和新增 `measurements`。
股票公开流来源严格对应 `wss://nbstream.binance.com/equity`，不以旧 REST 或旧复权历史替代。
`stock_1h` 的 `adjustment_mode=UNKNOWN`、`analysis_eligible=false` 随原始价格一起传递；时段与缺口照常核验，不能据此确认趋势。
`measurements` 投影为各自数据域：公开股票小时／五分钟／报价、1/7/14 日合约净主动成交、OI、普通资金费、波动率和 CFTC 仓位。
股票 ETF 申赎与 Spot CVD 未取得来源时明确不可用。VOO 的 SPY 合约始终为 proxy。
导出保留逐行数值、来源、时间、窗口与缺口，消费端复用同一测量校验和计算；机器人叙述、判断及额外任意文本不进入模型材料。
Regular 与 Special 仍分开，缺失长窗不抹去有效短窗。缺少某旧数据域的测量时保留该域的不可用占位，不伪造值。
