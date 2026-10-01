# 建议与持续维护（v4 周期深评）

先依据公开证据形成指数/BTC的评级、结构及变化、依据与价格风险，再结合本地资产和偏好形成组合方案及数量建议。同一市场证据与期限下，持仓不能改变市场结论。美股组合与加密现货组合是持续建议单位，成分和权重引用当前已确认版本；成分调整须明确是建议、用户已采纳还是实际执行。独立BTC／黄金属于额外机会，保护另评。综合方案覆盖原五模块；有金额的新增档位须满足既有数值契约，无数量的市场价格风险由正文及business_meaning承载，未知数量不抹掉价格判断，也不编造额度。Codex负责条件核验与后续复核，不把内部判断退回用户。

建议承接[核心策略的价值背离与时间优势判断](core-strategy.md#价值背离与时间优势)，在既有rationale、impact、amount_basis与validity中解释其对位置、规模、等待代价和有效性的影响。沿用旧建议也须确认当前前景与等待安排仍适用，不能仅以结构锚未迁移续期；不要求预测兑现周期；分析期限、建议有效期和报告复核期限各有用途。时间优势暂无法判断时仍给出经过组合评估的保守安排及理由，不机械归零或把某一方向判断当作入场资格。

## 正式契约

AssessmentDraft保持BTC_ASSESSMENT_DRAFT_V4；revision 3沿用recommendations与maintenance_binding，新增主结论与专项吸收。旧revision 1/2保持可读，生产write-policy.json要求minimum_assessment_revision=4，cycle_review_required=true启用真实周期来源校验。历史文件与报告不转换、不重写。新的portfolio_analysis.semantic仅含protection_purpose、amount_basis，禁止继续写scenario_budget；旧domain.trade_candidates仅供历史资格不变量兼容，不能覆盖建议金额。未来建议不声称已获得当前入场资格，不改变position_fact。

recommendations恰好包含upper_short、lower_spot、portfolio。每侧字段如下：

| 字段 | 内容 |
|---|---|
| status | RECOMMENDED或NOT_RECOMMENDED。前者必须有有效档位，后者tiers=[]且有理由 |
| unit | upper_short为SHORT_NOTIONAL_U，lower_spot为SPOT_CAPITAL_U |
| tiers | 每档id、price_low_usd、price_high_usd、amount_u、capital_u；现货可选allocation_percent，缺省继承侧级篮子分配，独立BTC用{"BTC":"100"}；金额为正值精确十进制字符串；空单capital_u可为null；价格为有依据的点或窄范围 |
| validity | from、until为有时区时间，conditions和invalidations为稳定概念列表；rationale必须解释其业务含义及期限依据 |
| purpose / amount_basis | 保护目的与规模依据的稳定概念列表 |
| allocation_percent | 空单为{}；现货侧为经审阅的动态组合，标识有效、非空、比例均为正且合计100，以当前已确认版本或明确待采纳建议为准；不固定成员名单 |
| rationale / impact / refs | 依据及无建议理由、有效性分析、组合影响、冻结公开原始引用 |

`validity.from/until` 约束冻结证据所支持的适用窗口，现有校验要求 `from <= temporal_scope.knowledge_cutoff < until`。它不是预计的提交时间；不要把实际候选形成时间或未来发布时刻写入 from 而超出知识截止。正式建议只从真实版本发布后生效，嗅探按 `max(validity.from, recommendations_issued_at)` 起算，因此保留证据下界不会回填此前策略或成交。正文区分证据适用窗口与正式采纳时钟；复核期限仍须来自实际分析。候选提交 Astra 前复用 recommendations.validate 校验该时间关系及数字/结构，原始引用另在受控证据中解析。

空单amount_u是建议名义额，capital_u是这份建议的假设资金占用，二者不得混为同一个U口径；无法支持具体预留时允许null，不阻断明确的名义金额建议。现货amount_u=capital_u为该档投入。档位缺省继承侧级完整组合；显式覆盖只能与该组合数值一致，或为独立BTC的{"BTC":"100"}，不接受未说明的残缺篮子。即使组合以后仅含BTC，继承组合与显式独立BTC也保留不同身份，不因分配数值相同而合并。空单不接受覆盖字段。BTC价位只作相应加密现货参考，美股及黄金使用自身正文、business_meaning与组合叙述，不塞入BTC价位字段。正式建议仍由Astra/max形成并审阅，Sol/high核验写入；工具不由目标缺口自动计算建议。

三类收益与新配置比例统一由核心研判形成，美股定投/减仓/回补及配置建议使用recommendations.portfolio.narrative与conclusion.business_meaning/正文承载，并绑定自身证据、条件和期限。BTC两侧资金路径只覆盖这些档位，不能冒充全资产占用核算；内部完整分析须核对美股投入、篮子、独立BTC、对冲保证金/缓冲和储备的共同资金安排，正文按[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)呈现综合安排及必要依据，不要求逐模块展开。目标比例、当期建议占用与已确认事实分别表达。

portfolio={capital_envelope_u,sequence_policy=REEVALUATE_AFTER_FIRST,existing_exposure,uncertainty,narrative}。envelope为方案建议新增资金占用预算，不是账户可用现金，资金占用均可明确时必须覆盖两侧合计；空单保证金/缓冲无法合理确定时capital_u和capital_envelope_u为null，投影显示已知现货最低计划占用加未知空单占用，不伪造现金总额。模型评估仅现货、仅空单、现货后空单、空单后现货四种路径，以及每个中间阶段的风险和剩余选择。脚本从同一档位计算逐步累计资金、现货投入和空头名义额；不把两者简单相减冒充完整净Delta。首项真实成交同步后重新评估另一侧，共用资金不自动跨模块划拨。

watch_targets由当前DecisionSnapshot派生，只保存decision_id、side及recommendation_ref；使用时固定该版本再解引用位置、依据和有效期，不维护第二份金额。NOT_RECOMMENDED侧也继续观察新机会及其理由是否失效。

建议价位绑定实际对象；BTC沿用币安BTC/USDT参考，QQQ/VOO股票保留USD/股。USD与USDT按美元近似口径比较，常规微差不阻断分析；原始单位不改名，合约或代理价格不能冒充股票价位。条件与档位必须时间上可同时适用：若先收复较高位置确认、再回踩较低档位，明确先后和回踩失效条件，不能把已回升后的确认伪装成原低价即时可采用。组合净多及两侧资金判断由主任务完成；账户未知只保留不确定性，必要时同步用户独占的实际事实，不把整体安全或市场分析任务退回用户。

## 持续维护与吸收

Astra/max 深评拥有两侧方案与组合推导；Sol/high 按已采纳深评持续验证。到价进入条件核验，金额依据、市场机制或事实影响实质变化返回完整周期深评。建议有效期到达只表示需要核验适用性；重新定价、改变金额或延长期限假设必须由深评批准。开放发现不受旧档位或五类形态限制。

主评估revision 3沿用上述recommendations字段，增加当前conclusion、section_provenance与research_absorptions；具体接口见[v4契约](v4-contract.md)。section_provenance说明本轮评估或复用的真实原版本、证据、时间和适用理由，不能将原判断伪装成最新全量分析。所有引用仍必须落在当前精简包内，实际需要的历史锚保留原source。

research_absorptions将专项补充纳入当前结论。Sol 明确采纳/部分采纳/否定；涉及定价、金额或风险的完整映射须由 Astra 复核，补齐相关五模块与四种资金路径后才能 ADOPT 为周期基线。事实变化后，旧专项结果须重新说明适用性；不因全局时钟或另一个问题研究完成清除当前问题的积累。

报价、记录时间和同义改写不自动报告。重要解释、条件、有效期、金额口径、保护目的和风险的稳定含义进入报告比较；当前conclusion.business_meaning由主任务维护已审阅含义，不能只换标签或利用旧标签隐藏新判断。可读narrative补充可发布为当前结论而不报告。报告基线来自真实验收，详见[简报表达](report-expression.md)。

恢复时读取同一pinned DecisionSnapshot；无需新的deep-current。观察保存在独立sensing，专项保存在research，主发布保存在versions，main完成收据不冒充专项完成。旧v1/v2与deep仅用于历史阅读和按旧版本恢复。
