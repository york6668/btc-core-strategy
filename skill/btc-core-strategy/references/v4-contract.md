当前新内容使用 revision 9，个人结果继续使用 revision 7 引入的 `recommendations.portfolio.risk_assessment`；`allocation_review`绑定`portfolio_risk_hash`，报告消费`portfolio_risk`。完整新契约见[完整闭环](closed-loop.md)；以下 revision 1–6 仅描述历史兼容，不为新研究强制生成动作。

> 报告的分析依据、触发复核和日本每日反馈以[报告复核契约](report-care.md)为当前规则。新报告包含maintenance；旧格式保持兼容。

历史内容 revision 5 使用七个 section 及唯一 current 指针；新增纯市场结果、分歧区接续、market→portfolio 分阶段材料、MARKET_UPDATE 与 dispute_zone 手机块，完整字段和流程见[独立市场与分歧区](market-state.md)。下述 revision 1—4 保留历史读取规则；该节 revision 5 描述仅供历史恢复，不补造旧记录。


# v4 主策略、专项和报告契约

生产入口 `$BTC_PYTHON <Skill>/scripts/strategy_v4.py --project <项目> --root <项目>/.strategy/v4`。默认返回必要身份、差异和路径；全量实物仅按需用全局 `--full` 或读取固定文件。确定性工具验证结构、引用、金额及恢复，不代替模型的市场判断。

## 唯一当前结论与主评估

`DecisionSnapshot`仍为BTC_DECISION_SNAPSHOT_V4，保存在versions/<id>/decision.json；current是唯一正式指针。先pin一个真实版本再读取动态因子分析、解释、建议、事件和已吸收问题。状态与计划投影来自同一版本；revision 3/4/5版本不要求report.md或新的deep-current。

精简pin/commit明确返回assessment_revision、minimum_write_revision、domain_strategy_revision、has_current_conclusion和assessment_owner。domain历史版本与主评估revision含义不同；assessment_revision=3表示既有主评估，=4表示已发布动态因子格式，=5表示已发布独立市场与分歧区格式；新写入门不证明当前版本已经迁移。读取计划先用这些字段和固定decision_path，不靠全库检索猜测格式。

该历史格式的主评估仍为BTC_ASSESSMENT_DRAFT_V4，revision=5。保留draft_id、evidence_id、evidence_bindings、fact_version、temporal_scope、domain、outlook、robustness、portfolio_analysis、recommendations、maintenance_binding、stop_reason；**不含report**。沿用revision 3主任务字段并增加factor_assessment：

- 历史 revision 3–5 的 provenance 示例为 {role:MAIN,model:gpt-5.6-sol,effort:high,agent_id,run_ref}，保留原真实主任务身份；新运行按 CURRENT_HOURLY_MODEL 及真实轨迹记录；类型允许不证明真实运行，样本和自然验收须核对日志。
- conclusion={business_meaning,narrative}。business_meaning为开放的稳定键到已审阅业务陈述映射，涵盖价值判断/解释/前瞻/条件等重要含义；主任务复用仍相同的含义，不能用分类名掩盖判断变化。narrative保存当前可读说明，同义改写不自动报告。
- factor_assessment保存本次模型实际选用的因子及理由，结构见下节。它属于同一DecisionSnapshot，不建立factor-current或macro-current；conclusion与outlook拥有采纳后的核心含义，recommendations继续唯一拥有建议。
- section_provenance在revision 4/5恰好覆盖domain、outlook、robustness、portfolio_analysis、recommendations、conclusion、factor_assessment；revision 3保留原六项。每项={mode:ASSESSED|REUSED,decision_id,assessed_at,evidence_id,explanation}。ASSESSED绑定当前证据，decision_id=null；REUSED必须逐字匹配真实祖先的对应字段，保留其原始评估时间/证据，说明为何仍适用。修改的部分不伪称复用；历史无因子字段不能伪称REUSED。
- research_absorptions=[{issue_id,request_id,result_id,decision:ADOPT|PARTIAL|REJECT,rationale,assessment_refs}]。引用真实专项结果及主结论内受影响的JSON Pointer。事实变化后增加applicability={fact_version,evidence_id,explanation}，由主任务重新判断旧结果适用性。

主审阅BTC_MAIN_REVIEW_V4保留schema、draft_id、evidence_id、reviewer_role=MAIN、checks、unresolved_issues=[]、material_new_outlook。revision 3/4/5的checks沿用raw_evidence、competing_explanations、portfolio_completeness、semantic_continuity、research_absorption，每项具体说明；没有因子目录覆盖或评分检查。report_fidelity由独立报告审阅承担。material_new_outlook保留兼容；旧conclusion缺失返回unknown，不补造旧字段。报告是否值得展示另由独立report-review对照实际旧正文判断。

内部完整策略仍覆盖竞争解释、价值区生命周期、当前背离与条件风险、主解释失败、五模块及两侧四条资金路径。增量只更新受影响部分。domain沿用[历史域不变量](legacy-domain-v3.md)中的字段、数值、引用、时间和资产事实约束；revision 5 不沿用旧固定因子组合、独立因子数量和特定因果时序模式对归因或迁移的裁决，改由独立市场评估及实际审阅判断。旧A/D/E不控制报告，完整周期深评由下述周期来源校验及实际证据触发。各时间分别保留market_cutoff、knowledge_cutoff、domain_market_cutoff，原始数值与公开来源完整绑定；事实来源仅三份本地文件。

人工声明的价格与金额继续使用精确十进制字符串。公开冻结JSON本身使用数字时，报价绑定核验按同一原始字节的十进制词元读取，并保持raw_hash/value_hash校验；不得把浮点解析舍入当作原始值，也不为通过校验改报价或改写冻结证据。

已完成 K 线的收盘价用既有来源字段中的 `payload_kind=BTC_SPOT_CANDLE_CLOSE`，不标为即时 ticker。仅接受已登记的 Binance、Coinbase、Kraken 匿名 BTC K 线端点及真实请求参数；`canonical_origin` 保持场所身份，`as_of` 保持实际收盘截止，`retrieved_at` 保持真实取得时间。原始完成行、数值绑定、多所独立性与知识截止继续核验；未完成行不得充当收盘价。该真实端点也可作为自选公开观察窗口的时间来源，不能伪称官方日程。当冻结原件将请求保存为 `retrieval.endpoint` 与 `retrieval.parameters` 时，v4 对同一原件的完整请求进行等价核验：允许参数顺序和 URL 编码不同，资产、周期及全部范围参数必须一致；原始数值仍绑定该原件，不能只凭同一主机替代来源。旧 ticker 和冻结历史来源保持兼容。

正常采集的 `BTC_MARKET_EVIDENCE_V1` 将多个逻辑来源保存在同一 `datasets` / `retrievals` 容器。此时 domain 报价的 `source_id` 使用真实 `dataset_id`，RawRef 的 `source_id` 仍指向冻结容器，两者不互换。消费者复用采集契约核注册的 source/group/market/instrument/quote_unit、完整请求及取得状态，逐项绑定对应 `/datasets/<dataset_id>/rows/<index>/close`、实际数值散列和收盘/取得时间；不接受任意同容器字段或自造来源名。多所独立性仍按真实注册场所核验，不因共用文件否认不同场所，也不以改名制造独立性。

完整周期修正获同一 Astra 实际批准并由 Sol 正式采纳后，v4 兼容层允许用获批复核期限提前现有事件链的最后期限，更新其研究假设引用；周期身份及开启时间保留，不伪造原事件已完成或延后承诺。旧版可选 material_pattern 投影若已过时，可由这次完整研究整体替代，不强迫把旧解释写成已证伪或补造旧小时研究绑定；仍保留该投影时继续执行原绑定及保留校验。此权限来自实际获批的完整候选，普通观察、未批准候选和直接旧版入口均不具备。事实、原始证据、事件时间及最终审阅一致性检查继续执行。

若旧事件已到期，v4 模型评估中的 event 与真实 D/E 不变量祖先的载体逐值未变，且有完整周期 review 的复核上下文，兼容层仅核验该历史结构和来源；不从当前时钟补造旧复核完成或要求开启新链。正式消费仍须由真实完成并获批的完整周期 review 提供上下文，未批准候选的只读预检没有采纳效力。更改的事件及普通 legacy 入口继续执行原到期门。

Sol 映射候选时可用只读 `domain.validate(..., cycle_recheck_at=<独立深评提出的必要复核时间>)` 预检上述兼容语义，及时发现引用和时间错误；此调用不登记批准、不写入状态。正式 validate/commit 的适配参数由已核验的真实研究批准派生，候选中的文本或预检通过不能取得发布权限。

## MAIN 当前原始输入绑定与维护形成时钟

`main_flow.assemble` 的 components 可显式携带 `current_market_refs`，格式仍是原 RawRef 列表，不接收自报数值。MAIN 内容核验选定引用，程序在原 EvidenceBundle 上验证 PUBLIC_RAW、raw_hash/value_hash、来源 end 与当前 market.cutoff 一致及当前输入归属。完整 market 容器仍逐值匹配，只供当前 context 的独立输入身份，不因组装而提高 trust。普通真实采集的可信完整包继续支持原 `/datasets` 默认路径；FROZEN_RAW_REPLAY 归一化包必须显式引用 normalization.inputs 中同 source_id、raw_hash、原请求 URL 和取得时钟的底层原件，所引原观测须到达同一当前窗口，旧行、未完成行、其他源和不可信容器均不能回退成当前依据。`assessment.temporal_scope.current_market_refs` 逐值承接它们，最终候选不得在批准后换引用。

原 CLI `assemble-main --components` 与 `run-advance --stage main` 的 authored components/answers 可提供此可选键；`material_reads.compose` 已承接作者附加字段，不增加模板默认引用，不修改冻结模板。旧真实采集 caller 不带此键继续原校验；导入 caller 必须传入其实际核验的 RawRefs。组装不猜资产方向、价格意义或阈值。

`event.cycle.opened_at` 是观察维护计划实际形成壁钟，允许晚于旧锁定行情/资料截止，但不得晚于可信执行 clock；published_at/retrieved_at 仍不得超出原 observation/pricing 来源截止。未来官方日程仍须真实原来源，日期未知保留未知；公开自选观察窗口不冒充官方日程。新链仍须 2–4 个有据且严格顺序的 future pending，全部晚于真实 clock；旧 final 的原 due/completion/身份及原 deadline 消费门不变，不能把新计划当成价格周期延续。

复用已正式保存的 domain 时，唯一受控 historical replay clock 从真实原始祖先 DecisionSnapshot.created_at 取值，重现该次实际 publication 的壁钟，而非较早的资料截止。这是内部不可变历史校验时钟，调用者不能提供或改变；当前 REUSED final 到期门仍先用真正 now，新的 ASSESSED domain 仍用真正 now。来源截止和旧事件截止始终原样，不能用历史模式采用新安排。

## 动态因子评估

`assessment.factor_assessment`与观察review内的`factor_checks`共用以下结构：

```json
{
  "items": [
    {
      "id": "模型选择的稳定文本身份",
      "name": "因子名称",
      "assessment": "模型对周期、作用、当前含义及不确定性的说明",
      "change_reason": "选用、沿用或调整的理由",
      "refs": [],
      "counter_refs": []
    }
  ],
  "summary": "本次因子的综合说明，可包含退出因素及原因"
}
```

`items`可为空，id及名称不受固定目录约束；不要求固定数量、权重、正负方向或覆盖率。`assessment`、`change_reason`、`summary`均为模型自由文本，机器不解析文案来裁决市场意义。refs与counter_refs中的每项沿用`{source_id,pointer,value_hash}`，只引用本次EvidenceBundle中冻结的真实值；两类列表都可为空，由模型如实解释证据受限、无可用反证或当前不适用的原因，不伪造引用。工具校验结构、已提供引用的真实存在、哈希和时间/版本一致性，不判断某个因子是否必须出现或证据是否足以证明其方向。

模型可从当前`context.factor_context`及精简运行上下文读取研究指引、公开来源入口、原始 section_provenance/temporal_scope、已选因素与原分析，独立决定新增、保留、改变或退出。没有历史factor_assessment时显示HISTORY_UNASSESSED；这不是自动触发研究或补齐名单的信号。首次revision 4必须有本轮真实模型评估，后续按section_provenance复用仍有效部分。因子解释与采纳后的整体价值含义分层：实质分析补充可发布内部新版本，报告仍比较conclusion、outlook、recommendations等现有业务语义；因子名单、原始引用或格式变化不自动产生用户报告。

慢数据在原始证据中保留数据期、公布及取得时间，模型根据发布节奏、修订和机制变化决定复用适用性；没有统一小时有效期或固定领先周期。主任务的适用性说明与实际原始版本分开，不能以本次抓取时间覆盖旧数据期，也不能把历史缺失字段补成当时已评估。

## 价值与时间分析的既有字段承载

[动量枯竭与反向趋势](core-strategy.md#动量枯竭与反向趋势)沿现有字段传递实际判断：factor_assessment解释驱动，horizons的structure/change/basis/confirmation及原引用说明当前状态、变化和支持反证，reversal说明原动力衰减与反向延续各自的确认程度，conflicts/next_check保存限制及下一验证。实质含义同步到outlook.semantic、business_meaning及独立正文；cycle_structure与verification审阅真实映射，cycle_basis/deep_report_summary核对最终表达。程序保护来源、版本和同源结果，不按关键词或指标判定枯竭、反转或中性，不新增必填因子或评分。

[核心策略的价值背离与时间优势](core-strategy.md#价值背离与时间优势)是分析方法Owner。每次正式主评估按该方法完成或复用综合判断；此处沿用七节分析承载；revision 5 在 outlook 中增加独立市场及分歧区状态，详见 market-state.md，不增加必选因子、评分器或独立当前指针；周期研究来源与发布校验见下节。

| 现有字段 | 承载职责 |
|---|---|
| `factor_assessment.items[].assessment`、`summary`及原引用 | 因素的传导、持续或衰减、影响周期、定价线索和证据限制；综合结论由主任务取舍 |
| `outlook.semantic`、`narrative`、`refs` | 情景方向、分析窗口、收敛机制、形成与失效条件、下一鉴别证据；正文解释当前定价背离及证据，不要求未来涨跌路径或兑现日期 |
| `conclusion.business_meaning`、`narrative` | 采纳后的定价差异、分周期时间优势及对安排的含义；实质变化进入稳定业务陈述，不只留在正文或因子说明 |
| `robustness`、`portfolio_analysis` | 收敛失败、等待成本、不利价格路径及各模块承受能力 |
| `recommendations`、独立报告的`maintenance` | 组合评估后的建议、适用条件与有效期；报告复核依据、触发条件、未解决项和最迟复核时间 |

[美债机会成本与风险补偿](core-strategy.md#美债机会成本与风险补偿)沿用上述承载：原始报价、利率和公共成本资料进入 EvidenceBundle；选择的基准、情景、期限、公式、计算结果及不确定性保留在因素与前瞻正文并引用原始输入，不把派生年化或假设终值登记为 PUBLIC_RAW/verified_facts。补偿与等待风险进入 robustness，采纳后的实质吸引力变化进入 conclusion.business_meaning；回报门槛不写成 recommendations 的正式目标价。没有定量依据仍可保存具体受限判断，不新增必填利率、固定期限、评分或自动交易字段。

`outlook.semantic`继续使用status、baseline、alternatives与next_evidence；情景继续使用direction、horizon、mechanism、formation_conditions和invalidation_conditions。`horizon`表示情景的投资分析窗口，仍按现有unit/minimum/maximum格式，不用它伪造尚未知的因素兑现期。只有有依据的事件时滞才在现有narrative说明，不要求预测兑现日期，最迟报告复核绑定maintenance.recheck_at；三者不互相替代。同一情景中的短期与中期差异在正文及采纳后的业务含义中说明，不能仅因期限不同而伪装成相互排斥的alternatives。

复用原有开放的mechanism/conditions概念列表和business_meaning映射，不建立固定的时间优势标签表。周期、机制或采纳后的时间优势改变时更新相应语义；同义改写保持原稳定概念及业务陈述，时钟和纯行情更新不借此制造新含义。增量复用仍通过section_provenance保留实际原版本和评估时间；缺少旧分析时由本轮真实评估形成，不能伪称REUSED或补造历史。原解释未按期兑现时，在现有前瞻、反证与适用性说明中解释本次取舍，并保留不可变旧版本。

主审阅checks不新增键：competing_explanations说明周期、定价和收敛判断的依据与竞争解释；portfolio_completeness说明等待代价及五模块安排；semantic_continuity核对重要含义、真实复用和未兑现条件处理。模型完成内容审阅，确定性工具只校验既有结构、引用、时间与版本一致性。已有语义比较仍产生报告判断输入，实际是否报告继续由独立report-review决定；金额不变不能隐藏时间优势的实质变化，首次补充旧版缺失分析也不凭字段出现自动推送。

## 正常运行

正常优先使用[run-prepare / run-advance](run-flow.md)薄封装；下列原命令保留为兼容与异常定位入口，正式Schema、Owner与写入门不变。

1. `recover`检查真实当前和outbox；未发送旧报告重新判断，SENDING/UNKNOWN先核实实际消息。`pin`取得固定版本。
2. 公开collect后，主任务按问题取得需要的额外公开资料，依下条组成精简证据根，再执行`context --market --evidence`、`observe --market --evidence --review`、`sensing-gate --observation`。--evidence显式绑定本次冻结目录，并包含与--market逐值一致的完整公开行情包；普通观察与主评估使用真实一致的证据上下文；参见[连续观察](market-observation.md)。Sol 识别证据影响，实质周期变化、重大遗漏、事实影响及完整基线缺失返回 Astra 深评；到期只核验适用性，普通观察不要求发布。
3. 独立评估建精简证据根：`evidence-root --base <历史包> --market <完整当前包> --references <实际需要历史ref列表> --additional <额外冻结包> --directory <新目录> --cutoff <实际知识截止>`。没有额外包时省略--additional；该参数把模型实际选用的额外公开来源并入现有EvidenceBundle，不创建第二份策略Owner。原有历史引用闭包、当前公开包与额外来源都保持真实字节和来源绑定。或使用`freeze-evidence --spec --raw-files --directory`手动组成已验证根。只有同一次研究补证才加`--parent`；不得跨小时无条件继承整个历史包。所有历史source字节/取得时间保留。专项请求前用`archive-evidence --directory <新包>`归档到版本库；这只保存原始证据，不发布主结论或冒充研究成功。
4. 主任务撰写受影响业务内容。`assemble-main --components --evidence --market --identity --checks --output --review-output`可机械复用其余字段、绑定context/事实/哈希。components={updates,section_reviews,evidence_bindings,domain_market_cutoff,stop_reason}；revision 4/5的updates仅含七section及可选research_absorptions，section_reviews每section具体说明；旧revision 3仍按原六section读取。首次迁入revision 4时实际撰写factor_assessment，首次revision 5由独立市场阶段实际评估market_assessment/dispute_zones；不能自动填充历史未评估因素或共振判断。identity为真实provenance；checks文件含checks、unresolved_issues、material_new_outlook。需要更新domain先用`prepare-domain --at --trigger --replacements --output`机械装配；不得用工具更新方式替代因果判断。
5. `validate --draft --evidence --review`仅证明结构和绑定。主任务确认实质质量后用相同实物`commit ... --expected <pinned id>`。写锁内再次检查当前、事实、上下文、证据、建议有效期与结果适用范围。有效解释/专项吸收可发布无报告的版本；只有原始时钟/身份更新则UNCHANGED。main完成收据不推进DEEP。
6. 报告独立比较当前完成结论与最后实际验收业务语义，按下节冻结/交付。专项完成不自动入队，主结论也不必生成报告才可成功。

## 专项接口

专项不得写正式结论。以下命令仅本地存储和校验，不自动调用模型：

- `research-request --spec`：{issue_key,scope,question,decision_id,evidence_id,change,reopen_conditions}；change={kind,reason,refs,reopen_condition}。kind为INITIAL_QUESTION、NEW_KEY_EVIDENCE、MATERIAL_COUNTEREVIDENCE、REOPEN_CONDITION_MET；明确用户重评另见下节 USER_REQUESTED_REVIEW。首次重要未知问题可由当前有鉴别力的证据进入INITIAL_QUESTION，不要求已有专项基线；Sol 发现问题，完整周期的因子取舍与最终归因由 Astra 深评确定。发起专项的原始证据须为PUBLIC_RAW；factor_checks可保留UNTRUSTED_LEAD并说明限制，线索不独立取得专项资格。问题identity不因scope调整或小时后缀换新；NEW_KEY_EVIDENCE 或 MATERIAL_COUNTEREVIDENCE 使用有鉴别价值的新原始值，reopen_condition=null；只有 REOPEN_CONDITION_MET 填此前已记录的具体条件。不能改source名称绕重复，未完成请求继续恢复。
- `research.context` 的 `latest_request.progress` 从既有不可变 independent/candidate/result 派生，保留阶段、同一代理及其实际运行记录路径；最新候选有对应结果才显示 RESULT_RECORDED，历史结果仍供原基线参考，不能冒充新请求完成。阶段是完成证据，不是运行锁或存活判断；Sol 先核对实际运行/返回再恢复原阶段，已有候选不重建独立深评。精简运行上下文保留同一字段，无第二份研究状态。
- 生成不可变受控input及request。独立Astra子代理从`research-read --request <id> --phase independent`实际读入，按需回读其中原始路径。input仅问题、原始证据及带日期的历史锚，不含当前候选；事实路径保留为后续组合适配来源，Astra 先完成公开市场结论与价格风险，再由同一代理读取事实完成个人应对。
- 等真实独立回答完成，`research-independent --request --agent --rollout --turn`校验实际Astra/max、读入实物和完成turn；`research-reveal --request --independent --candidate`此后冻结候选。
- 独立公开阶段自行补证后，先归档合并的完整EvidenceBundle，再对`research-independent`增加`--additional <合并证据目录>`。登记仍核验原冻结输入的真实读取、同一作者和实际完成回答，补证时钟不得晚于该回答完成；补证不得改写同名原来源。登记保留原request/input身份，并记录实际补证身份与时钟，候选仍显式绑定同一补证，不重开研究或将补证写回初始输入。
- 同一研究中途补证时保留原 request 和独立输入，先将包含最终完整行情与新增官方原文的合并 EvidenceBundle 归档，再对 `research-reveal` 加 `--additional <合并证据目录>`。候选阶段的实际输入同时携带新增来源路径及真实证据时钟，同一 Astra 必须据此修正并审阅。最终结果沿用该证据身份、行情截止和知识截止，不把新资料写回旧请求的时间；不可改写同名旧来源。BTC_CYCLE 候选时钟必须与绑定包一致，不能省略补证参数却使用较晚资料。历史无补证的候选与结果保持原样。
- 同一Astra读取`research-read --request --phase candidate --candidate-id`并审阅候选。`research-result --spec`保存request_id、candidate_id、agent_id、rollout_path、turn_id、coverage、counterevidence、unresolved、reopen_conditions、analysis，analysis必须对应实际第二阶段回答。结果校验相同代理、顺序、输入绑定及证据截止；普通专题按 scope，BTC_CYCLE 必须覆盖完整周期与组合，均不负责报告投递。
- 主任务阅读结果并取舍，写research_absorptions和受影响结论，重新取得context绑定后发布；结果完成不要求再做一次观察才可吸收。不同问题保留独立累计窗口；历史缺结构化覆盖维持UNKNOWN，不补造记录或批量研究。

若运行环境使用collaboration编排且没有本机子任务inner-tool日志，使用`research-orchestrated-independent --request --rollout --dispatch --returned --input-review`及`research-orchestrated-result --spec`。它们从亲本真实spawn/followup、工具回执和FINAL_ANSWER校验模型、forknone、同代理、先后及实际摘要；输入由MAIN具体审阅。input_review={reviewer_role:MAIN,request_id,input_id,dispatch_call_id,material_review,phase_order_review,evidence_refs}。结果spec={request_id,candidate_id,rollout_path,dispatch_call_id,return_message_id,input_review,coverage,counterevidence,unresolved,reopen_conditions}，不手写analysis。明确记作ORCHESTRATION_DISPATCH_RETURN_AND_MAIN_INPUT_REVIEW，inner_tool_trace_verified=false、os_input_isolation_verified=false；不得伪造native task_complete或宣称完整进程隔离。

桌面同一会话的日志分段按实际 `history_base` 及其字节截止读取，来源仍是原始日志。未完成阶段的暂停后续接保留实际 followup，MAIN 审阅其输入范围；已有完成返回须先登记，不能把后续修订改称首次独立结果。后续审阅允许使用包含原始来源链的新日志段，不要求同一个文件路径。

## 周期深评与嗅探

用户明确要求完整重评时，`research-request` 另支持 `change.kind=USER_REQUESTED_REVIEW`，并在顶层增加 `user_request={reference,reason}`：reference 是实际用户消息或任务中的请求引用，reason 说明本次重评目的。该入口只用于既有 BTC_CYCLE，scope 仍覆盖全部七部分，refs 仍绑定实际已归档 PUBLIC_RAW；可以复用已核验原始值，不伪装成新的行情变化。没有该 kind 的历史请求结构和普通小时触发条件不变。

同一 issue 与 user_request.reference 幂等：重复调用返回原研究；有未采纳的在办研究时返回其真实进度并绑定这个用户请求，接续同一研究和同一 Astra。`research/user_requests` 只保存用户请求到研究的去重引用，不拥有策略或代理运行状态。调用方核对实际进度与运行，不从到期、小时任务或旧计划自动生成用户请求。

独立输入的 `cycle_assignment.method_references` 提供核心方法、建议维护和项目价值因子的阅读入口；披露候选的 `review_assignment` 提供映射契约及输出要求。新输入的 historical_anchor 只保留旧版本身份、创建时间、temporal_scope 与 section_provenance 的原始时间、模式和版本/证据身份（不含 explanation）；旧七个作者分析章节可能混入个人内容，不进入初始市场输入。历史市场依据通过公开原始 sources 回读，旧建议在市场分析后再披露。版本创建时间不冒充各部分原评估时间，历史缺项不补造。程序组织路径和材料，模型实际阅读并完成论证；文档内容不被解析为行情规则或评分。

新周期观察在review顶层增加applicability_review={basis_result_id,outcome,explanation,refs,limitations}。outcome为VALID/EVIDENCE_LIMITED/REVISE，refs使用本轮EvidenceBundle原始引用；VALID须有已采纳基线和实际引用。cycle_review.applicability_revision=3，verification_completed只在显式VALID且无待重评理由时成立，不再从没有研究触发推导。EVIDENCE_LIMITED影响摘要适用范围，不单独重复完整研究；REVISE持续返回同一BTC_CYCLE直到新报告采纳。历史revision=2及缺失字段保持可读，但不能刷新适用性或补造旧核验。

同一研究期间本地已确认事实或策略偏好变化时，继续原请求与同一Astra，不改写初始独立输入。最终cycle候选的fact_version使用实际最新文件版本；reveal校验并在同一候选的可选fact_context冻结三份文件正文供最终审阅，结果绑定实际审阅的版本。它只保存该次输入证据，不成为事实Owner。未变候选及历史结果保持原结构；披露时版本不实、批准后事实再变或未获实际Astra审阅仍不得发布。新增研究范围先由同一Astra补充分析再审阅最终映射，不能只换fact_version。

仅已确认偏好修订、账户事实未变时，domain.account_facts使用account_fact_changed_known=false、实际最新fact_owner_fingerprints，以及known_change_evidence=[{confirmation_type: USER_CONFIRMED_PREFERENCE_CHANGE, owner_id: ASSET_CONFIGURATION}]、local_record_status=PREFERENCE_RECORDED，user_input_requirement=NONE。该兼容分支只允许资产配置文件实际变更且操作/纠正记录未变；模型与最终Astra审阅负责确认内容确为偏好变化，不得隐藏成交、账户事实纠正或缺失事实。真实账户变更继续原FACT_SYNC契约；下一轮文件未变时不重复携带此次偏好变更标记。不得为满足旧校验把已记录偏好伪装成待用户同步的账户事实。

完整周期报告以币安QQQ、VOO、BTC为用户对象，SPX/NDX保留底层研究身份；加密周期、企业利润、货币与资产重估是解释机制，方法见[核心策略](core-strategy.md#三类收益与核心报告)。保留BTC_CYCLE身份、六项实际审阅与唯一DecisionSnapshot；六项覆盖按适用资产说明，不用BTC数据替代美股。正文、业务语义、重要条件及建议统一映射，factor_checks/research_findings及maintenance维护其全部重要内容，而非仅手机稿。

recommendations.lower_spot.allocation_percent承载经审阅的动态组合：非空、有效标识、比例正值且合计100，不固定成员名单。tiers缺省继承该组合；显式allocation_percent仅允许数值等同于侧级组合或{"BTC":"100"}独立机会，不接受未说明的残缺篮子。侧级仅含BTC时，缺省继承仍是组合，显式BTC分配仍保留独立BTC身份。upper_short不得带该字段。资金路径只计一次该档capital_u，有显式分配时额外派生spot_capital_by_asset_u。比较按数值规范化，展示及历史共识恢复保留资产归属；不回填旧档位或历史正文。美股与整体配置复用既有叙述及business_meaning，不新增当前指针或挪用BTC价格字段。

`write-policy.json.cycle_review_required=true` 只约束新的写入和报告；旧 DecisionSnapshot、研究、报告和收据继续按冻结实物读取，不补造历史周期深评。Sol/high 仍是 MAIN 发布者，Astra/max 是周期分析来源；唯一当前指针不变。

完整周期请求使用稳定 `issue_key=BTC_CYCLE`，scope 至少包含 `/domain`、`/outlook`、`/robustness`、`/portfolio_analysis`、`/recommendations`、`/conclusion`、`/factor_assessment`。原始输入加 cycle_assignment 覆盖提示，市场历史锚保留原始期限与出处，初始输入不携带旧个人建议章节；先独立研究，不能把 Sol 当前观察或候选先传给 Astra。其他问题可用原专项接口，但不能成为完整周期基线。

独立回答后，Sol 将结论映射到原七个 section，补齐事实、引用、组合和时间一致性。`research-reveal` 的 candidate 为 `{assessment:{domain,outlook,robustness,portfolio_analysis,recommendations,conclusion,factor_assessment,fact_version,temporal_scope}}`，可由 `v4.cycle.candidate(assembled_draft)` 机械提取。此时不发布。候选预检复用正式分析section的结构、稳定概念与去重检查，以及原建议数字/结构和证据适用时钟校验，from不得晚于候选knowledge_cutoff；完整证据与domain映射仍先对实际assembled draft做只读assessment.validate，不能只检查domain后把其余结构错误留到正式写入。正式生效以真实发布时钟为下界，详见[建议维护](recommendation-maintenance.md)。Astra 第二阶段必须实际读取对应候选，最终回答使用以下 JSON；这里的值均为填写说明，不能直接当成完成结果：

```json
{
  "candidate_hash": "research-reveal 返回的实际 candidate_hash",
  "approved": true,
  "report_narrative": "Astra直接撰写并与候选conclusion.narrative逐字一致的完整中文报告",
  "coverage": {
    "cycle_structure": "标普500、纳指100、BTC分别给出评级、周期、当前结构、原结构到当前结构、依据和确认级别；小时、波段及中期分开，未能判断不写中性或未变",
    "value_pricing": "当前定价背离、竞争解释及抵消因素如何支持价格风险分析；与当前状态区分同尺度有界未来路径、真实竞争路径及相对把握，不要求精确目标价或确定兑现日期，定量受限时说明具体未知",
    "downside_paths": "回撤、修复失败、结构重估及尾部风险的条件路径摘要",
    "price_levels": "各市场价格用途、当前适用性、单位、依据、主要风险、失效或重评条件及核验结果；个人数量未知不撤销市场价格判断，也不伪造有金额的tiers",
    "portfolio_waiting": "持续组合的增减、持有或恢复投入、动态成员及权重、独立BTC与黄金机会和保护；五模块、两侧先后占资与等待成本；下行风险时分别审阅已成交现货篮子和独立BTC阶梯批次的减仓判断、条件与减仓后保护覆盖；未记录保持未知",
    "verification": "支持、反证、未决与下一鉴别证据及必要复核期限摘要"
  },
  "counterevidence": ["实际反证或竞争解释"],
  "unresolved": ["实际未知与对方案的限制"],
  "reopen_conditions": ["可核验的机制、价格条件或事件变化"],
  "recheck_at": "深评选择的含时区最迟必要复核时间"
}
```

同一 Astra 已撰写完整报告并在本次实际读取冻结候选后，最终 JSON 可用 `report_hash` 代替 `report_narrative`，避免每次映射复核重复输出全文。值必须是 `v4.common.digest(candidate.assessment.conclusion.narrative)`，不是文件字节哈希；两字段二选一。程序保留真实最终回答，并仅从同一 `candidate_hash` 绑定的冻结候选恢复逐字相同正文用于后续发布；不从任意文件或主任务补写正文。候选、真实运行身份、批准及映射一致性校验均不省略。旧全文回执继续兼容。

`approved=false` 保留真实研究结果，修正映射后向同一代理再次揭示、审阅。保存 result 的 coverage 数组按上述六项顺序取实际回答，其他三组发现与实际回答完全一致；工具从真实第二阶段输出提取 cycle_review，拒绝由 Sol 手写批准或补造覆盖。

两类已成交现货的减仓判断复用现有组合字段、`conclusion.business_meaning` 与正文，不新增当前策略或自动卖出条件。`coverage.portfolio_waiting` 由 Astra 实际审阅其事实分类、减仓或不减仓理由、条件、数量口径和保护空单影响；代码校验来源与映射，不用关键词或价格阈值替代判断。独立研究输入的 `cycle_assignment.instructions` 及 run-prepare/report 摘要中的 `spot_risk_review_instructions` 只提供方法提示，不承载资产事实或评估结果，旧冻结输入不回填。

完整基线需要 `research_absorptions.decision=ADOPT`、assessment_refs 包含上述七个路径且实际映射一致。写入校验同时保护稳定语义和含点位/价值判断的解释文字；修改内容后不能借旧结果批准。原始证据引用、运行身份、记录时间和事实同步本身不伪装成新的周期判断。事实变化后的市场建议变更必须有基于当前已确认事实的深评；recheck_at 到期不使真实作者批准失效，但不能据此改变已批准内容；真正到期的可执行建议不得再次作为有效建议发布。

`cycle_baseline` 由当前版本实际采纳的获批研究派生，包含结果、原市场截止、真实完成时间、覆盖、反证、未决、reopen_conditions 与 recheck_at，不新建 deep-current。最新未采纳/否定研究、小时主评估和报告不推进该基线。初始缺失、事实影响或模型识别的实质变化令 gate.cycle_review.required=true。到期仅令 verification_due=true，由当轮 Sol 核验；核验完成从绑定同一结果和事实的实际观察派生 applicability_verification，不改研究正文、建议期限或完成时间。`SUPPORT_WEAKENED`、单次触价和普通事件到达只触发核验；`COUNTEREVIDENCE`、金额依据/机制改变或未覆盖重要风险进入深评。关键覆盖遗漏同样进入深评。未决状态从原研究请求与观察连续派生，后续平静不能清除；正式采纳新结果才解除。代码不识别市场方向。

`RECHECK_AT:<已采纳recheck_at>` 不再是新研究请求入口，到期完成适用性核验即可。实质变化沿用现有 NEW_KEY_EVIDENCE / MATERIAL_COUNTEREVIDENCE / 有鉴别意义的 REOPEN_CONDITION_MET。在办 BTC_CYCLE 的 request 返回原 request_id、reused=true、progress 与恢复动作；新增材料补充原研究，不创建第二份独立研究。runtime_status=NOT_CHECKED 仍须核对一次真实运行。

`run-material --run <run.json>` 默认生成 report 必要性材料，`--stage main` 生成主评估与研究接续模板，`--stage report-body` 在决定 REPORT 后生成正文模板和指标；均不重新取数、不记录观察、不发布。拿到新研究结果后使用此入口，再填写 research_absorptions 和真实 section_reviews/checks；旧模板不能只改 hash。若需要补充行情或证据，则新目录 prepare 并审阅变化。

风险报告可在深评缺失、待完成、到期或证据受限时形成；受损依据、仍有效历史锚和期限留内部，不生成新周期结论。V2 可展示旧共识价位但必须标为待复核，不能当作当前可用新方案。事实变化仍要求 information.fact_limitations 内部解释，发送前验证当前事实和上下文。完整新周期判断和方案须先经深评发布。

## 独立源稿和展示稿

当前新稿通过 report-review / run-advance 的 AUTHORED_REPORT_V1 最终正文路径生成，规则见[手机表达](mobile-report.md)。以下 `report-source --decision --layout --constraints --author-review --output` 为历史 CONCISE_V1 兼容路径：用同一已发布评估生成BTC_REPORT_SOURCE_V3，含decision_id、layout、body、body_hash、semantic_hash、constraints、author_review。layout是[历史简报表达](report-expression.md)的CONCISE_V1。constraints每项={id,kind,source_text}，kind包含direction、position、amount、unit、condition、deadline、negation、uncertainty、limitation、judgement；覆盖该旧格式实际业务断言，包括表格每档位置/金额配对、完整条件和限制。author_review={role:MAIN,agent_id,source_hash,coverage_review,faithfulness_review}，审阅当前实际源稿，不能只写通过。

`enqueue --decision --destination --source`重验来源渲染字节、语义、重要变化覆盖和当前版本。新artifact为BTC_REPORT_V5；无应报告差异返回NOT_DUE，不能为推送改变策略。目的地仍为现有项目g-p-699c8dbabddc8191a71598adfdc3cd14、任务6a95a45c-cb24-83ee-a64a-52eee69f4fe7。

1. BEGIN提供BTC_DELIVERY_ATTEMPT_V1：destination、真实发送前anchor_ref、started_at，获取attempt_id；新运行同时携带实际 run_ref，后续纠正与未知重试共享该轮每目的地一次额外发送预算。仅 ready_to_send=true 且 state=SENDING 才可真实发送；重复 BEGIN 的 READ_BACK_EXISTING_ATTEMPT 只指本次尝试。其他旧 UNKNOWN 不阻挡经当前核验的新重要稿；旧请求独立恢复。只有当前有效未发稿可开始；过时稿从当前结论重判，未送达义务仍保留。
2. AUTHORED_REPORT_V1 直接发送 BEGIN 返回的 request_body，VERBATIM 逐字展示，不整理、润色、压缩、重排或补充；历史 EDIT_ONCE 才沿原有限整理规则。若工具仅受理或明确处理中，先按 market-reporting 的 ACKNOWLEDGE 保留运输状态，有界等待实际回读，不能据回执确认送达；实际请求可见后BIND_REQUEST提供BTC_SENT_REQUEST_V1：attempt_id、destination、request_ref、request_body、anchor_ref、record_refs、request_started_at；最后一项取真实云端请求 startedAt，不能早于本次发送 started_at。从真实读取获取用户消息全文，必须包含源稿并关联发送前游标。旧冻结绑定保持原样。
3. 实际读取助手展示稿后CONFIRM提供BTC_CLOUD_READBACK_V5：destination、checked_at、attempt_id、request_ref、assistant、record_refs、absence_confirmed=false、review。assistant={record_ref,parent_request_ref,role:assistant,body,completed:true,tool_calls:[]}；必须是对应用户消息的真实完成助手内容。
4. review={reviewer_role:MAIN,agent_id,source_hash,display_hash,bindings,checks,unresolved_issues}。AUTHORED_REPORT_V1 的 bindings=[]、checks={}，程序比较整个真实正文逐字一致，内容审阅已在冻结前完成。下列逐项审阅只适用于旧格式：bindings每项{constraint_id,display_text}对应源稿断言；checks包含numbers、directions、positions、units、conditions、deadlines、negation、uncertainty、limitations、no_new_judgement、coverage，每项{passed,explanation}。主任务逐项判断语义并解释，工具验证绑定/数值/关联；无矛盾才交付成功。
5. 失真保存实际展示稿并进入CORRECTION_REQUIRED；BEGIN_CORRECTION最多一次，发送本地源稿要求原样展示，再绑定实际消息并回读。仍失败保留交付异常，不重启研究。超时或空回读用UNKNOWN；一次空结果、尚未索引或只看到发送前游标都不证明未发送。NOT_SENT仅用于尚未绑定请求且发送工具明确证明未派发，原缺席回读还须提供dispatch_evidence={outcome:NOT_DISPATCHED,record_ref,detail}，record_ref来自实际发送失败记录并列入record_refs；已绑定真实请求不能标未派发。
6. 验收保存源稿、展示稿、实际消息关联与审阅，再推进reported-current。晚确认旧稿不得倒退基线。`repair-completion --delivery-id`只重验已完成交付，不重新发送。

AUTHORED_REPORT_V1 的源稿与会话文本只允许单个文件结束 LF 差异；不改变内部换行、空格、语义或源稿哈希，presentation.text_match 记录 EXACT 或 TERMINAL_LF_ONLY，旧回执没有该字段仍按实际正文核验。末尾 LF 导致的旧 CORRECTION_REQUIRED 可重新 BIND_REQUEST 核正同一请求的实际文本，再以同一助手身份、原正文和完成时间 CONFIRM；原 journal 与尝试次数保留，不能借此替换另一请求、改稿或补造发送。其他更正沿原一次重发链。

历史错误绑定到较早请求而卡在SENDING/UNKNOWN时，使用同一delivery的`RECONCILE_REQUEST`，不手改status、不再次发送。receipt为`{schema:BTC_REQUEST_RECONCILIATION_V1,previous_request:{record_ref,started_at,body},binding:<完整BTC_SENT_REQUEST_V1>,readback:<完整BTC_CLOUD_READBACK_V5>,reason}`。previous_request取实际云端记录，必须匹配原绑定且早于当前尝试；binding取当前尝试后真实请求；readback对应该请求的完成助手正文并通过全部语义核验。接口保留原绑定和缺席记录，新增恢复证据后原子确认；失败不修改状态，相同恢复证据可幂等重放。确认旧日报不替代今日反馈；随后重新审阅过时PENDING稿并完成当前日摘要。

## 兼容、切换与恢复

`RETRY_UNKNOWN` 使用 `BTC_DELIVERY_RETRY_V1`：destination、anchor_ref、started_at、prior_attempt_id、实际 run_ref、current_context_id、readback={checked_at,record_refs,outcome:UNCONFIRMED}、applicability_review、duplicate_risk_accepted=true。必须先真实回读，再审查当前证据下正文仍适用且未被替代；每个实际运行、目的地最多一次恢复后额外发送。返回新的 attempt_id 及完整 request_body，重复调用不重复发送。原 UNKNOWN、绑定、回读均按 attempt_id 保留，旧回读晚到仍可 CONFIRM。NOT_SENT 的权威未派发证明要求不变。

未确认的实际回读用 `UNKNOWN --receipt` 保存 `BTC_UNCONFIRMED_READBACK_V1={schema,attempt_id,checked_at,record_refs,detail}`。页面异常、只能读到旧游标或尚无完整助手正文在 detail 中如实区分，不能据此声称未派发。多次回读分别保留，重复同一回读幂等；晚到回读按原 attempt_id 登记，不覆盖后续尝试。

`recover.attempt_recovery` 为完整历史，新增只读派生动作 `WAIT_FOR_NEW_EVIDENCE` 及 `recovery_evidence={unconfirmed_readback_ref,delivery_id,coverage_review_ids,receipt_ref}`。仅实际未确认回读及已验证送达的同稿/显式承接链可支持等待；新运输状态晚于该回读时恢复主动处理。`recover.delivery_recovery` 为主动清单，市场与报告材料复用同一分类，不再按 UNKNOWN 状态重新生成回读指令。不存在新的投递状态或历史迁移；原协议始终接受有效的晚到回读。无变化不重读、不重复通知的方法见 market-reporting。

`SUPERSEDE_PENDING` 的收据为 `{schema:BTC_REPORT_REPLACEMENT_V1,replacement_delivery_id,reason}`，只替代未派发稿，新稿必须通过 delivery_coverage 显式覆盖旧 review_id；UNKNOWN 不使用此动作。report_delivery_obligations 与 attempt_recovery 分别恢复应报告义务和不确定请求，后续静默不清除二者。

历史EvidenceBundle、revision 1/2/3/4评估及BTC_REPORT_V4不重写，旧V4回读依旧精确正文＋“已收录”。当前旧版保持可pin、复用与恢复，首次revision 5通过本轮实际市场及分歧区评估装配发布新版本；不自动补写过去的因子列表、覆盖情况或解释。迁移的历史深评不是新的专项结果；只有实际有结构化证据支持的问题才可迁移，本次不凭正文补造。

`recover`不晋升孤立generation或草稿。主发布先原子切current，当前版本本身即读取依据；main收据中断不要求伪造deep修复。旧域适配只可改变 control、fingerprints、previous_fingerprints、previous_projection_baseline；发生这种标准化时，main收据同时保存原 submitted_draft，原审阅继续绑定提交身份，接受内容继续精确绑定正式版本，收据文件以 completion_hash 命名。历史无此字段的收据仍按原草稿身份校验。已实际发布但旧收据因标准化身份中断，可用 `repair-completion --main-draft <原提交>` 核验原提交、实际当前发布及仅限适配的差异后新增恢复收据；保留旧收据和正式版本，不重发策略，再继续原 run-advance。历史DEEP只允许`repair-completion --completion`修复有真实发布证据的实物。outbox独立保留，不因新观察覆盖。

先以兼容旧格式的代码完成新入口、真实引用、空因子、复用、版本迁移、发布与报告独立性的隔离验证。切换新写入规则时短暂停原任务并核验当前运行状态，同步已验证的代码、Skill、项目规则和同一任务提示，当前新入口按内容能力准备材料；在办研究和未知投递保持原物理版本，不通过统一提高写入门中断旧上下文。正式迁移必须来自真实评估采纳，不能转换旧叙述。写入门只要求新格式，不要求任何具体因子或目录覆盖。新主评估与报告均兼容现有当前指针；不修改历史、资产事实、独立事件任务、全局规则或Git/Engine。

回滚必须能读取已发布的revision 5及带dispute_zone的交付；只有未生成新格式时才可恢复完整基线实现。若旧实现不兼容则暂停自动写入，保留最新结论与交付现场，不能覆盖新状态。隔离结构验证不证明模型实际评估质量或自然运行成功；自然主运行核对真实模型输入、因子选择和依据，专项与报告分别验收，未发生的标待验收，不制造触发。文件内容与结构检查不代表本会话已重新加载新规则。

### 历史域复用与当前到期

主评估复用 `domain` 时，先验证它与真实祖先版本的内容、原证据及原评估时间完全一致；旧周期切换按该版本发布前基线与原时钟复验，旧 `final_event_reached` 只是历史完成收据。当前事件排程最终到期仍按当前时钟和本次知识截止单独检查，到期由 Sol 核验并维护排程；市场周期或期限假设改变须经深评，不能用历史复用跳过。CLI validate 与 commit 使用同一条校验路径。

事件复核截止另从 `main-current` 明确指向的真实完成收据读取：校验收据、草稿、原始行情截止、当前版本祖先及事实绑定；`event_review_baseline` 只影响到期事件检查，`identity.main_completion_hash` 参与过期绑定检查。有效但属于旧版本/旧事实的收据明示不适用并回退当前核心截止；损坏或孤立绑定失败关闭，无指针不扫描孤立main文件。主评估返回UNCHANGED也能完成该事件复核，但不会推进正式Owner或其他问题窗口。

## 独立行情报告扩展

当前报告流程使用[market-reporting.md](market-reporting.md)：report-context → report-review → market-report-source → enqueue → 原交付协议。新增BTC_REPORT_SOURCE_V4/BTC_REPORT_V6，沿用BTC_CLOUD_READBACK_V5助手正文验收。行情总结独立于策略差异，观察完成后必须判断报告价值；旧report-source与V4/V5交付保持兼容。恢复按报告身份处理，只有真实送达推进覆盖基线。


手机报告新稿使用 AUTHORED_REPORT_V1.body 最终正文及来源bindings，不拆lead/blocks，不自动追加建议，不限制表格、段落或长度。研究、嗅探及报告均遵守[研判与报告的职责边界](mobile-report.md#研判与报告的职责边界)：正文呈现已完成的综合结果及必要依据，内部分析字段不自动成为展示清单，汇报不另作因子取舍或因果解释。冻结前内容审阅，交付与回读VERBATIM逐字一致；旧格式及不确定交付按原接口恢复，完整接口见[手机报告契约](mobile-report.md)。写入策略mobile_report_required仅约束新enqueue，不影响历史产物回读/恢复。用户明确重写请求可通过同一review的user_request独立绑定，不伪造新行情或推进时间。

## 完整深评报告来源与摘要绑定

`write-policy.json.deep_report_required=true` 仅约束新发布和新报告审阅。Astra 先独立撰写完整中文报告，Sol 原文映射到 `conclusion.narrative`；第二阶段实际 JSON 以 `report_narrative` 逐字返回同一正文，或按前述 `report_hash` 接口精确绑定本人已写且已读的冻结正文。返回全文时，仅末尾 LF 换行数量不同属于文本封装差异：真实回复完整保留在 `analysis`，校验后按已批准候选正文保存映射和报告哈希，不为此重新研究；内部换行、空格或正文改动均不适用此兼容。正文需修改时返回同一 Astra，重新形成候选并审阅。新发布必须具备该真实来源，旧冻结结果缺失时不补造；仍可读取，派生为 `REPORT_AUTHORSHIP_PENDING` 并恢复周期研究。

周期结果保留实际助手全文。解析只接受单个完整 JSON 对象或完整 `json` 代码块；其后仅兼容一条已确认的 `::inbox-item{title="..." summary="..."}` 通知标记。不扫描任意段落内的 JSON，不忽略其他说明、其他指令、多对象或重复字段。通知标记不作为判断、审批或工具指令执行。原代理、模型、阶段、候选哈希、覆盖与期限校验保持。

`cycle_baseline.report_narrative/report_hash` 从唯一当前策略所采纳的真实深评派生，无独立指针。报告上下文增加 `deep_report_policy_revision=1`，其身份绑定正文哈希。每份新 report-review（包括静默核对）增加：

```json
{"report_basis":{"result_id":"上下文cycle_baseline.result_id，可为null","report_hash":"上下文cycle_baseline.report_hash，可为null","mode":"CYCLE_SUMMARY","verification":"逐项说明小时证据如何验证原报告，或说明缺口和重大风险对原解释的影响"}}
```

`mode` 为 `CYCLE_SUMMARY`、`LIMITED_FEEDBACK`、`URGENT_RISK` 或 revision 5 的 `MARKET_UPDATE`。常规反馈要求当前有效且有实际 Astra 正文；待建立、到期尚未实际核验、事实影响或实质变化待复核时使用有限反馈，有旧报告则保留来源绑定和限制，无旧报告保持 null。已核验的重要背离或波段风险可及时用 URGENT_RISK 提示，不要求中期反转、仓位变化或当天未报告，不能绕过新正式价位及个人建议的深评要求。字段由模板提供，不能手算或填造来源。`report_basis.verification` 及 `quality_review.cycle_basis/deep_report_summary` 在内部对照完整分析和本轮判断，核对最终正文是否忠实表达综合方向、适用周期、依据和安排，并保留影响其适用性的必要边界。各期限、竞争解释、详细仓位、条件及未知仍完整维护，不要求逐项展示。重要波段风险按其实际期限表达，短窗方向不冒充中期结论；价位未变不能掩盖建议价值变化。程序不按关键词判断市场含义。

旧研究、策略、审阅、源稿和交付收据不回填新字段，按原冻结上下文恢复。新策略、新源稿和发送前核验使用新规则。登记旧结果仅修复历史完成证据；过期、证据变化或正文来源缺失时须恢复实际深评，不能直接成为当前方案。

## 内容 revision 6/7

外层 AssessmentDraft / DecisionSnapshot、七个 section 和唯一当前指针不变。[完整闭环](closed-loop.md)定义 revision 7 的 risk_assessment、可选风险应对及独立 strategy_advice，风险结论不要求操作清单。forward、区域证据类型、allocation_review、复核条件归属和结果绑定继续适用；recommendations 两侧保留 EXECUTION_LIMITED/UNDETERMINED，方向与档位执行资格分开。revision 6 的 allocation_decision 及其他旧内容按原版本读取，不自动改成新风险评估。

旧冻结 report_contract_revision=4 及新报告 report_contract_revision=5 保留契约3的首个实质结论为公开市场、全部实质正文有公开/个人/时间 bindings及 quality_review.market_independence，并增加 decision_delivery、evidence_priority、maintenance_separation、narrative_focus 四项实际全文阅读审阅；它们分别核对直接结论与取舍、关键证据优先、内部维护没有挤占正文、重复结论没有掩盖主线。生成入口与校验共用同一审阅键集合，模型按手机表达方法对照正文与冻结结果作实质裁决，程序不判断文风、字数或投资结论。允许忠实改写冻结判断，旧冻结契约继续原验证。新稿5在正式记录前 --check-only 导出完整候选和 review_read，模型实际回读全文与绑定来源完成审阅，填入 reviewed_candidate_hash；哈希仅绑定该候选，不证明语义通过。补填审阅说明或更新滚动时钟产生独立不可变材料，不改正文身份；任何正文、证据范围或冻结结果改动重新预检与审阅。审阅覆盖最终全文与最后收束的实际依赖，结构通过和来源标签不证明结论独立。不可用个人文件以 fact_version=null 及 availability 显式保留；公开研究继续，正式个人采用仍要求完整事实与原 domain 校验。原生独立阶段拒绝同一物理 rollout 中已知的个人/候选 research-read 记录，此守卫不覆盖普通读取、消息或未展开历史，不证明全会话盲性；有披露的原研究修正如实登记为 CONTINUED_PUBLIC_CORRECTION。

## revision 8 兼容与完整组合决定

外层schema与唯一current不变，可读1–8；新内容在 recommendations.portfolio 中保留 risk_assessment/strategy_advice，增加 investment_decision，字段与语义按[投资决策及反馈](investment-process.md)。每个市场期限新增 momentum、price_attractiveness、strategy_applicability，因素新增 decision_review；均是模型形成的业务判断，程序不计算方向或解析方法文档。allocation_review 增加 investment_decision_hash 与逐项 issue_reviews；正式判断缺失/过期不能声明VALID，旧未决不能被required=false关闭。报告绑定同时覆盖综合结果，只传完成的取舍与必要限制；旧上下文仍按原格式恢复。账本与评价只作为已有事实/决定的源绑定视图，不升级为账户事实或第二策略Owner。

新稿报告契约5通过run-advance --stage report --check-only导出实际全文候选及review_read；预检不记录正式报告、不发送，也不证明语义。实际通读来源和全文后填写具体quality_review和reviewed_candidate_hash，再记录同一正文。改写使旧绑定失效；旧冻结3/4及UNKNOWN仍按原内容恢复。完整职责、有限反馈边界及最终接口统一见[手机表达](mobile-report.md)，不在汇报阶段重新研究。

## 内容 revision 9：可验证预期与后继依赖

新入口使用 revision 9，原 revision 1–8 的不可变记录按原接口读取和恢复；它们没有原始形成及检验记录时只提供事后复核，不补填为前瞻通过。既有 DecisionSnapshot、研究与观察继续拥有结论，`market-lifecycle/claims` 仅保存首次公开记录的原条件及保守时间，不是第二份策略或结论 Owner。

升级后的小时观察可以继续复核确实来自旧存储的 `forward`：保留其原 judgement/realization 窗口、conditions 和 invalidations，只更新实际 observed_window、prior_check 与说明，结果仍为 `POSTHOC_ONLY`。新 horizon、新预期或已注册预期的继承必须使用 revision 9 lifecycle；不得为旧记录伪造 formed_at、首次登记、后验样本或支持结论。预检按原持久对象与本轮候选的身份逐项比较，不能只用新建空状态测试升级。公开 raw refs 必须从实际引用索引携带完整 `source_id/pointer/value_hash`；缺散列时退回本轮材料修正，不从相似历史引用猜测或自动补写。

`horizon.forward.lifecycle` 包含 `claim`、`reviews`、`dependencies`。`claim` 固定 `id`、`formed_at`、`data_cutoff`、`scope`、`basis_kind`、`basis_refs`、`scale`、`expectations`。实际首次登记时间另由程序记录；前瞻事实必须晚于声明形成与首次登记两者，不能借声明时间回填。已有结论及原标准不能修改；新预期使用有关联的新身份。

`basis_kind` 为 FACTS、VALIDATED_PREDECESSOR、FACT_CORRECTION、INDEPENDENT_NEW_FACTS 或 RISK_RESPONSE。后三类及独立事实初判不依赖尚未验证的前序；真正递推必须引用实际注册的前序散列、expectation_id、review_hash 和获支持的 scope。单所、产品、主体或数据维度的支持不得扩成更大范围。

`scale` 为 `process/end/end_ref`：通常按机制过程检验，end 与 end_ref 均为 null；确有事件终点时，end_ref 必须绑定实际日期原件。新的 judgement_window 从形成后开始，不能复制旧预测起点。`outlook.semantic` 的过程尺度可使用 `unit=PROCESS, minimum=null, maximum=null`，不能仅因历史存在日期就重新给出数周或数月延续。

每个 expectations 项为 `id/mechanism/scope/support/falsification`。支持和证伪条件使用可核验原始值的比较表达式：`all` 或 `any` 组合；叶项为 `left/op/right`，op 支持 EQ、NE、GT、GE、LT、LE。操作数是 `anchor` 原始引用或 `sample/path` 后续观察角色。业务模型选择条件和尺度，程序检查引用值及结果，不选择市场方向、交易阈值或构造指数。不能将字段标签、同一样本重复审阅或报告文本当观测值。

review 固定 `prior_claim_hash/kind/evaluated_at/samples/outcomes`。样本指定 `id/ref/time_pointer/scope`，时间指向同一实际观测而非抓取时钟；业务事实适配器返回真实范围、发生与获知时间及字段维度。outcome 逐项说明 expectation_id、verdict、supported_scope 和 explanation。缺证、矛盾或未触发保留 UNKNOWN；POSTHOC、ENGINEERING、BACKTEST、FROZEN_REPLAY 不提供前瞻支持。SUPPORTED/PARTIAL 要通过原条件的实际值检查，证伪后不得继续引用；获支持范围不得超出实际使用的观测字段。

公开材料中的 prospective_feedback 来自公开结论注册与实际机制复核，不含个人资料、账本或收益模拟。个人投资反馈仍在冻结后的个人阶段读取，不能替代公开周期验证。

revision 9 的 investment_decision 另带 account_constraints，每个候选带 execution_role，区分 INVENTORY_MAINTENANCE、INVENTORY_ADJUSTMENT、INCREMENTAL、POLICY 和 INDEPENDENT_RISK_RESPONSE。约束来自当前确认文件绑定的已提交 RISK_CONSTRAINTS 视图；没有记录、事实版本变化或后续成交/资金事项使其失效时保持未知。完整敞口或全仓状态未知可能改变调哪一侧，不能选成存量维持；POLICY 只表达用途，不能授权实际 HOLD。必要独立风险响应保留自身证据、能力缺口和范围，不受公开前序验证门槛阻塞。

工程回归及本轮锁定重放只验收调用、身份、依赖与表达保护；真实预测和投资效果仍待对应后续事实或真实绩效验证。完整正文按主题、结论先行及自然段表达，不输出上述内部字段和运行记录。

原节点传输同时核验客户端退出码和严格布尔响应；非零退出不能凭成功JSON认定完成。未知响应保留原operation、阶段、退出码或启动errno、输出长度与散列，不在错误中复制原始stderr、环境或凭据。客户端permission denied、程序拒绝、工具执行前审核拒绝和根因证明分别记录；诊断类别不证明任务策略来源，也不授权重试或改写入通道。原operation核清前不重发，恢复合法执行条件后仍由原报告链核新鲜输入、质量、记录和真实送达；源码或离线通过不证明运行已采用或报告已恢复。

新完整链验收必须实际经过原 `research.request/read_input/_public_result` 的市场、前景和完整 `factor_assessment={items,summary}` 合同，冻结公开阶段后才读取个人事实并完成同一原研究的候选审阅、主Owner映射与报告消费。只把已有散文填成固定 semantic、旁路 factors 或造空 legacy Owner 的消费者回放，不能代替原链完成。事实、后续反馈和成稿须分别保留原身份及实际时钟；无新形成后市场事实时说明尚无自然后验。合成支持/部分/证伪/未知及范围测试单独标记，验收模型是否维持、降级、替代或撤回对应命题，不能只验反馈进入payload。独立语义审阅对照实际主因形成、一步前瞻、反证影响、原入口/消费者和正文；失败全文与实质正例均需判定，字段、关键词、测试通过不提供语义评分。
