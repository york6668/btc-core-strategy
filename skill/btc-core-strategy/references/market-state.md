# 独立市场判断与分歧区接续（revision 5–6）

沿用现有七个评估部分及 `.strategy/v4/current`。本契约补齐既有分析，不预设原多空结论延续或被推翻；当前方向由本轮证据决定。程序只核验结构、来源、单位、时间和接续，不以因子数量、固定组合或关键词决定充分性、归因、周期或突破。

## 输入与判断边界

市场阶段仅接收公开证据、明确原日期的独立市场历史及其未决问题，不接收账户事实、成本、目标配置、保护规模、个人建议或混合旧正文。旧版没有纯市场字段时标为待评估，不能从混合正文猜测拆分。自动任务 memory 也只保留公开市场接续与研究/交付引用，不预先携带仓位事实和个人建议。新入口按[完整闭环](closed-loop.md)先核验公开事实再对照历史假设，冻结市场结果后，组合阶段才读取本地事实与个人条件。仓位阶段只能引用市场结果完成适配；账户变化只使对应仓位复核到期，不使市场判断失效，也不阻止市场报告。

模型按问题选择识别渠道、分析周期和证据。跨市场、同向交易是渠道示例，不是必查项目。支持与冲突信号须在研判阶段完成取舍，明确主导方向、理由及反证的实际影响，遵守[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)；不能将信号并列作为综合结果。关联证据、同源镜像或重叠窗口不重复计为独立支持。短周期转向、波段变化和较大周期反转分别判断。局部共振可以成立而较大周期仍未知；短期风险无需等待较大周期反转才报告。

每轮先从当前公开证据独立判断，再检验此前判断得到支持、削弱、否定还是仍未知，再主动发现新增重要信息。不能只读取上一条摘要；读取正式基线及其后有效观察保留的状态、反证和下一鉴别条件。市场材料同时承接已采纳研究中真正独立冻结的完整 market_report，核验未在手机显示的重要市场条件；程序先核对它与当前正式市场字段及正文前缀一致，不从混合正文猜测拆分。时间更新、评级或仓位未变不决定报告必要性。

## 正式市场结果

`outlook` 保留 `semantic/narrative/refs`，新增 `market_assessment/dispute_zones`；整个 outlook 及 factor_assessment 均为公开市场内容。独立研究输出单一 JSON：`{outlook, factor_assessment, market_report}`。`market_report` 是市场结论与价格风险正文，不含个人应对。原 semantic、因子结构见 v4 契约与 assessment.py。

`market_assessment={markets,summary}`。markets 至少覆盖 SPX、NDX、BTC；每项：

- `id/as_of/horizons/resonance/reversal/conflicts/next_check`；as_of 是该市场证据所支持的实际时点，不能用报告生成时间刷新旧资料。
- 每个 horizon 为 `id/period/direction/structure/change/basis/confirmation/refs/counter_refs`。period 为模型选择的分析期限；direction 为 BULLISH、BEARISH、NEUTRAL、UNKNOWN，confirmation 为 CONFIRMED、PARTIAL、PENDING、UNKNOWN。未知不写中性。
- resonance 和 reversal 均为 `{status,periods,assessment}`，periods 引用本市场已评估 horizon id。共振状态为 ALIGNED、CONFLICTED、NOT_ESTABLISHED、UNKNOWN；反转为 CONFIRMED、FORMING、NOT_CONFIRMED、UNKNOWN。assessment 解释判断、冲突及适用范围，不按状态名机械推导交易。

summary 给已经完成取舍的综合方向、适用周期及主导理由，说明不同期限如何影响当前判断，不以“各有支持”或冲突信号并列代替结论；basis 给决定性理由；conflicts 在内部保留可能改变判断的反证及其当前影响。确认程度如实保留，不能为语气明确而提高事实确认级别。这些字段不自动成为正文栏目。

## 分歧区

优先从近期成交、买卖力量和价格反应的累积形成上下沿，未触价也可提出有依据的候选。4 小时只是观察窗口示例，模型按证据选择窗口；数据窗口与未来适用周期分别说明。不能将窗口最高/最低价机械当作边界，也不能先选历史极值再补理由。然后检查历史区域是否仍有解释力；旧记录可被否定，不能默认有效。主要分歧区、内部短线关口、历史结构锚和建议价位分别表达，建议价位仍归 recommendations。内部关口穿越不自动成为整体越界或中期反转。没有真实公开订单簿证据，不把已成交买卖力量说成未成交挂单。

新建或修正的区域使用 `role=MAIN_DISPUTE/INTERNAL_LEVEL/HISTORICAL_ANCHOR` 和 `boundary_review={revision:2,lower,upper,prior_zone_assessment}`。每侧为 `{formation,validation,evidence_window:{start,end},identified_at,tested_at,horizon,basis,refs,next_check}`。formation 分为 RECENT_INFERENCE（近期推断待验证）、RECENT_TESTED（近期触价反应）、HISTORICAL_REFERENCE（历史参考）和 UNKNOWN；validation 为 UNTESTED、SUPPORTED、INVALIDATED 或 UNKNOWN。未验证推断的 tested_at=null，不能声称已获触价支持；已测试侧保存真实反应时间。identified_at 保存实际首次分析识别时间，后续累积更新不重置历史。分析可以在冻结证据截止后完成，识别时间不得为适配证据时钟而倒填；回放形成的新判断明确不是当时已有预测。数据窗口及触价反应时间必须不晚于 as_of，as_of 不晚于证据截止。原始窗口、未来适用周期和实际识别/登记时间各有归属。

每侧独立维护，证据不足保持该侧 area 值 null、formation/validation=UNKNOWN，不借历史价格补齐。失效、迁移或重新激活要解释新证据和下一鉴别条件；历史来源不能只因价格接近而改称近期推断。`prior_zone_assessment` 说明旧区域为何保留、否定或调整。历史冻结记录（包括旧 `{observed_at,basis,refs}`）按原格式读取，不补造当时未知的证据。85.5k、85.6k 以及其他纠偏数字均只作为案例，不成为固定边界。

`dispute_zones` 是独立项目列表，可为空；不强迫模型制造区域。每项字段：

`id, market, quote_unit, area, horizon, as_of, status, state, breakout, long_risk, short_risk, response, conflicts, change_reason, next_check, retirement_reason, supersedes, refs, counter_refs`。

- area 为 `{lower,upper,label}`，每侧独立已知或 null；quote_unit 使用该市场实际单位。稳定 id 跨轮次沿用，不能每次换名。
- status 为 ACTIVE 或 RETIRED；state 只写形成后的当前争夺状态。形成依据保留内部 refs，不进入报告叙事。
- breakout 为 `{direction,stage,assessment}`。direction 为 UP、DOWN、NONE、UNKNOWN；stage 为 CONTESTED、SIGNS、INITIAL_BREAK、CONFIRMED、FAILED、UNKNOWN。这些是模型评估状态，不是程序规定必须逐级经过的路径。
- long_risk、short_risk 分别写双方不利路径；response 给公开市场的交易风险应对，不含个人金额。价格穿越、放量、突破确认、趋势反转和可交易性不能相互自动推出；实际成交与承接可以支持，也可以削弱突破解释。
- next_check 给下一鉴别条件；change_reason 说明本轮支持、削弱、否定、延伸、失败、重回争夺或未知如何改变此前判断。重要突破迹象即可报告，不等完全确认。
- 活跃区域必须接续。修改范围说明原因；退出填 retirement_reason；被替代时新区 supersedes 引用旧 id，旧项显式 RETIRED。后续轮次可省略已明确退出项，有效基线仍按 id 承接此前退休记录；省略不擦除可供后续纠正引用的历史身份，也不使正式旧版 ACTIVE 复活。

## 小时与研究入口

revision 5 的 run-prepare 首先返回纯市场 context、market_template 和原始引用入口。`run-advance --stage market` 冻结市场结果后才返回 portfolio_context。按原 observation_template 填仓位 recommendation_checks 与 main_evaluation，再 `--stage observe`；市场字段由冻结结果承接，不重新生成。完整 sensing-context 仅供确定性校验，不作为市场阶段模型材料。

独立 Astra 通过 `research-read --phase independent` 读取公开包并先完成上述 JSON；登记真实独立结果后，通过 `--phase portfolio --independent-id <id>` 读取事实，追加个人应对。Sol 映射的 outlook、factor_assessment 和市场正文必须与冻结结果一致，再交同一 Astra 审阅完整候选。公开证据需要修正时，在同一研究中由同一 Astra 返回修正后的纯市场 JSON，用 `research-market-amendment --independent <id> --rollout <真实父运行> --dispatch <followup调用> --returned <真实返回> [--additional <冻结补证目录>]` 记录为续研修正；保留原独立来源，不能把已见过仓位的续研重标为全新独立研究。原生代理运行用 `--rollout <代理真实运行> --turn <已完成轮次>` 替代 `--dispatch/--returned`，同样校验实际 Astra/max 回复。随后重新映射并审阅。

## 报告与兼容

MARKET_UPDATE 绑定正式报告 result_id/report_hash、本轮 observation_id 和 factor evidence_id，用于本轮市场核验、分歧区状态和突破迹象。它不采纳新周期、不生成新正式交易价位。当前 observation 与补充原文必须实际进入报告材料；市场适用性与仓位适用性分别说明。

手机以最终正文AUTHORED_REPORT_V1按[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)表达币安QQQ、VOO和BTC的综合结果，不重新解释或排列因子。共振、反转、分歧区及双向风险在内部完整维护，正文保留实际影响当前判断和安排的部分，不要求独立栏目或block字段。SPX/NDX继续作为底层研究；产品报价和区域见[币安产品口径](equity-products.md)。个人建议及其有效性与市场判断保持业务归属清楚。市场结论及价格风险只绑定纯市场字段或公开证据，不能摘录混合旧正文或仓位适配结果。冻结前核对结论、依据、安排与必要限制；新稿实际回读逐字一致，历史稿按原格式恢复。

历史 revision 1—4 保持原格式回读，不补造共振或分歧区记录。首次 revision 5 实际评估缺项，已撤回价位不复活。程序测试、冻结回放、正式采纳、自然观察及真实送达分别验收；未发生的自然场景保持未验证。

revision 6 每个 horizon 增加 forward 的三个时间窗口与兑现核验，每个区域增加 evidence_kind/limitations；完整字段、期限与独立结果契约见[完整闭环](closed-loop.md)。
