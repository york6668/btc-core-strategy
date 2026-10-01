# 币安 QQQ／VOO 与底层研究

报告对象是币安 QQQ、VOO、BTC。用户采用美元近似口径，USD 与 USDT 常规微差不阻断分析和价位展示；股票 USD/股、合约 USDT 保留实际单位。QQQ 的底层研究沿 NDX，VOO 沿 SPX；指数点位、股票价格与衍生品价格保持各自身份。简称不证明具体产品版本，不默认 QQQB、QQQon 或其他产品，也不改变持仓。

官方 [Stocks API 公告](https://www.binance.com/en/support/announcement/detail/95d746cb2b5e4d179c50d0026dd7dfbe) 区分 Stocks 与将部分证券铸成 bStocks 的能力；[产品公告](https://www.binance.com/en/support/announcement/detail/8c8fb6809d9c46789306905327e7567a) 也说明不同产品形态。Spot exchangeInfo 的带后缀交易对不能自动代表 QQQ／VOO；Spot 未找到 VOO 不能推出 Stocks/Wallet 不存在 VOO。通用同名币搜索结果不是 ETF 证据。

公开来源优先从币安 [Stocks API](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api)、[交易参数](https://www.binance.com/en/trade-parameters?biz=stocks) 和实际产品公开页核验。采集样本的 EQ_QQQ、EQ_VOO 股票及 QQQUSDT、SPYUSDT 衍生品映射见[辅助采集消费](collector-evidence.md)，仍须独立核验。SPYUSDT 是 VOO 的代理参考，不能作为 VOO 自身报价或资金确认。页面空壳或接口限制不算已取得报价，只用公开资料，不读取私人持仓、不执行交易。

## 冻结和消费

沿原 freeze-evidence / --additional 保存独立取得的公开原文。EvidenceBundle 的可选 report_products 每项：

- symbol=QQQ 或 VOO，venue=BINANCE；product_id、product_type、quote_unit 取实际产品。quote_unit 支持 USD、USD/share、USDT、USDC，不能是指数点位。当前报告允许实际 USD、USD/share 和 USDT 价格；不要求另找 USDT 股票报价，也不把原始 USD 字段改名为 USDT。
- identity_refs 分别绑定 symbol/product_id/product_type/quote_unit 的原始标量，使用 source_id/pointer/value_hash，至少一个身份源为 Binance 官方，全部来自独立公开原文。
- quote 可为 null。已知源时间的兼容格式为 price、as_of、refs，refs 绑定 price/product_id/quote_unit/as_of，必须来自同一实际报价行。
- 扩展时间格式额外包含 retrieved_at、time_basis。有源时间时 time_basis=SOURCE_OBSERVATION；无源时间时 as_of=null、time_basis=RETRIEVED_AT_SOURCE_TIME_UNKNOWN，refs 只绑定 price/product_id/quote_unit。retrieved_at 必须等于冻结来源的实际抓取时间，不补造 as_of。缺失行情时间的报价可以如实展示，但不能证明精确触价时刻。

公开源未给发布时间时 source.published_at 可为 null；start/end 表示已知的数据或实际获取区间，不补造行情发生时间。这些字段属于冻结公开证据；本文不是运行配置。JSON pointer 指向实际原文字段，不能为满足接口制造“原始记录”。身份或单位缺失时保留 quote=null 或暂不加入记录，按原流程补证。支付 USDC 不证明报价 USDC；同名合约不证明产品相同。

研究、小时纯市场输入和报告上下文共享 report_subjects，包含底层标识、产品身份、实际报价单位、时间、区域及缺口。当前 report_quote_policy revision 2 采用美元近似口径；历史已冻结上下文沿其原策略验证，不改写旧报告。缺少对应报价只限制产品价位判断，继续完成方向、盈利价值与组合应对。

产品分歧区用实际 product_id 和单位独立建立，说明与底层区域的关系。SPX/NDX 区域保留为研究和历史引用，不改名、不机械折算；用户正文按[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)表达其对综合判断的实际影响，具体产品价位使用已核验产品证据。补证保留父级来源；压缩新研究根包时只有完整保留身份及报价引用闭包才继承产品记录。新增或缺失报价不自动调整仓位，也不强制完整重评。
