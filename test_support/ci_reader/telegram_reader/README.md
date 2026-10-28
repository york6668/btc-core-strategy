# Telegram 频道内容本地读取

本工具使用一个独立 Telegram **读取机器人**接收“数据采集频道”的新内容，并把
正文或媒体说明保存到本机 SQLite。读取入口只接受 Telegram Bot API 的
`channel_post` / `edited_channel_post`，并再次校验 `chat.type=channel`；群组、
超级群组和私聊的 `message` / `edited_message` 不会解析或入库。以 `/btc`、
`/start` 等指令形式开头的频道内容会被忽略。

工具不会发送、编辑或删除 Telegram 消息。结构化证据适配仅下载已配置频道内的
JSON 附件，大小上限为 5 MiB；不访问附件正文中的 URL，也不会把 Token 或频道内容
写入项目日志、Codex/OpenAI 记忆。

采集机器人默认是不可信来源。入库只证明机器人曾发布相应内容；解析结果只能作为
交易策略交叉验证的一环，不能成为决定证据、自动交易信号或交易授权。

## 读取范围

- 使用 Bot Token，不需要个人 Telegram 账号、`api_id` 或 `api_hash`。
- Bot API 只提供机器人收到的实时更新队列，不能读取加入频道前的历史内容。
- 程序只接受 `channel_post` 和 `edited_channel_post`，不读取任何群组消息。
- 普通正文中的 `/` 不会被过滤；只有 Telegram 指令实体或 `/名称` 形式的开头指令
  才会忽略。
- Bot API 没有可靠的频道内容删除通知，本地库不伪造删除时间。

## 1. 保存读取机器人 Token

在本机终端执行下面的指令，系统会提示输入 Token；输入内容不会显示：

```bash
security add-generic-password -U \
  -s "btc.telegram.reader" \
  -a "bot_token" \
  -w
```

Token 只保存在 macOS 钥匙串，服务名为 `btc.telegram.reader`，账户名为
`bot_token`。不要把 Token 粘贴到聊天、配置文件或命令行参数中。

把发送机器人和读取机器人都添加为“数据采集频道”的管理员。发送机器人保留发布
权限；读取机器人只需能够加入并接收频道更新，不授予发布或删除内容权限。

## 2. 安装和发现目标频道

```bash
cd /absolute/reader-source
./install.sh
```

把读取机器人添加到“数据采集频道”后运行：

```bash
.venv/bin/python reader.py configure
```

程序通过尚未确认的成员更新或频道内容发现目标频道，只显示数字 ID，不显示消息
正文，也不把 Token 保存到项目目录。如果发现多个频道，会优先选择名称严格等于
“数据采集频道”的频道，否则要求按数字 ID 选择。

## 3. 同步和检查

```bash
.venv/bin/python reader.py sync
.venv/bin/python reader.py status
.venv/bin/python reader.py evidence
```

`full-sync` 为兼容入口，但 Bot API 不支持历史回溯，因此与 `sync` 一样只处理当前
可用更新队列。同步游标是 Bot API 的 `update_id`；数据库事务完成后才推进游标，
失败时下次会重试。

数据库位置：

```text
<本机 reader 数据目录>/messages.sqlite3
```

核心表和视图：

- `messages` / `current_channel_posts`：当前频道内容；
- `message_revisions` / `channel_post_audit`：频道内容首次版本与后续编辑版本；
- `sync_state`：最后 `update_id`、最近成功时间和脱敏错误类型。

除上述 JSON 证据附件外，程序不下载媒体文件，只保存媒体说明和类型。查看内容时请直接在本机查询数据库，
不要把完整资产内容复制到聊天或长期记忆。

每次 `sync` 会刷新项目根目录的`策略证据.md`。该快照记录来源消息ID、时间、内容
哈希、解析明细、时效、内部一致性和策略维度覆盖；所有采集项默认标记为“独立来源
待核验”。同一机器人提供的多个指标仍只算一个来源，内部自洽不会提升外部真实性。

### 结构化辅助观察

共享协议见 `docs/BTC_COLLECTOR_EVIDENCE_V1.md` 和 `docs/MARKET_COLLECTOR_EVIDENCE_V1.md`。原始同频道更新先持久化至
消息库同目录的 `collector_evidence.sqlite3`，之后提交原正文、审计及同步游标。
无 caption 的 JSON 附件也会入队。下载失败保留队列；后续空更新同步仍会处理到期重试。
文字编辑、乱序、重复和未知版本由共同模块校验，不会替代原文字解析流程。

同时兼容 `BTC_COLLECTOR_EVIDENCE_V2` 和 `MARKET_COLLECTOR_EVIDENCE_V2`。BTC V2 从
`base_evidence` 保留旧数据域；股票 V2 保留股票小时线、报价、衍生品 OI、普通 Funding、
Special 股息调整、基差和宏观七个数据域，再加入日／周测量。数据缺失保留明确限制。
公开流股票复权为 `UNKNOWN`，原始价格可读取，`analysis_eligible=false`，不确认趋势或关键位。
日／周资金费、稳定币供应量、OI、波动率、CFTC 和 Bybit 爆仓估算分别保留单位、窗口及来源。
Bybit 数据不代表全市场爆仓，覆盖不足不能将零记录解释为零爆仓。

```bash
.venv/bin/python reader.py collector-observations
.venv/bin/python reader.py collector-observations --asset all
```

此命令只读本地配置和证据 sidecar。无参数保留 BTC 兼容行为，也可指定 BTC、QQQ、VOO 或 all；
all 按三标的分别输出最新辅助观察或明确缺口，v4 主流程读取 all。先按各标的正文消息顺序及附件当前版本选择最新，再校验质量。最新缺口、待下载、待正文、
无效或未知版本不会回退到历史有效值；旧正文附件晚到也不会覆盖更新报告。
单标的模式在 sidecar 不存在或最新附件尚不可用时返回 `[]`。已保存原始附件每次按当前解析器重验，旧拒绝状态不阻止新版协议恢复，原记录保持不变。UTF-8 JSON 连同末尾换行上限为
4 MiB，超限以非零退出且不输出部分 JSON。无参 `EvidenceReader.observations()`
仅保留历史诊断兼容，不能作为当前策略快照。
不会打开凭据、连接 Telegram、取得同步锁，或创建、迁移原数据表。WAL 仅在公共模块的
私有临时副本中恢复，导出不改动原库及其 WAL/SHM；持续并发变动时明确报错供下次重试。所有导出项仍须
独立核验及当前来源检查，`valid_auxiliary` 仅表示协议校验通过，不是市场事实确认，
也不赋予策略决定或交易权限。

安装脚本同步九个共享模块及 `market_reader.py`、`collector_v2_reader.py` 接收适配模块至现有后台运行目录。
共享采集实现和接收适配分别维护；适配保留本机 `asset='all'`、只读导出、最新缺口、原始附件重验及消费校验行为。
`EvidenceReader.observations` 同时接受共享端的 `asset_id` 参数。文字快照分别展示 BTC、QQQ、VOO，不把股票数值解释为 BTC。股票源时间、交易日历覆盖、复权与 Funding 窗口缺口单独保留。

部署时必须将 `btc_evidence.py`、`btc_evidence_reader.py` 与读取入口一起发布；
单独更新入口或只复制旧模块清单不足以启用结构化证据。

## 4. 节点部署与调度

现行部署入口是[分析部署说明](../roles/ANALYSIS_DEPLOYMENT.md)。由节点现有配置提供源码、数据、解释器、服务标签、日志路径和 `reader_minute`，通过 `roles/analysis_role.py --config <本机配置> render-reader <候选 plist>` 生成候选。生成候选不安装或启动服务。

旧独立安装脚本的操作说明已退出；不再从本 README 发起固定机器布局和固定分钟数的安装。节点已有服务、数据、Keychain、迁移备份及调度保持原样，只有明确的部署任务才能沿现行入口切换。日志只记录数量、时间和脱敏错误类型，具体文件位置取自本机配置。
