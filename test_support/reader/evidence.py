from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9))
FRESHNESS_MINUTES = 90


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    category: str
    label: str
    value: str


@dataclass(frozen=True)
class EvidenceSnapshot:
    chat_id: int
    message_id: int
    sent_at_utc: str | None
    content_hash: str
    revision_count: int
    report_time_jst: str | None
    age_minutes: int | None
    freshness: str
    items: tuple[EvidenceItem, ...]
    internal_checks: tuple[str, ...]
    coverage: tuple[tuple[str, str], ...]
    decision_state: str
    asset_id: str = "BTC"


@dataclass(frozen=True)
class EvidenceRefreshResult:
    source_message_id: int | None
    item_count: int
    freshness: str
    decision_state: str


ITEM_PATTERNS = (
    ("btc_price", "价格结构", "BTC现价", r"💰\s*(\$[\d,.]+)"),
    ("momentum", "价格结构", "24H/7D变化", r"^(24H[^\n]+)$"),
    ("sentiment", "市场情绪", "情绪读数", r"^市场情绪：([^\n]+)$"),
    ("treasury", "宏观", "美债10Y", r"^(?:🔴|⚪)\s*(美债10Y[^\n]+)$"),
    ("real_rate", "宏观", "实际利率", r"美债10Y[^\n]*\|\s*(实际利率[^\n]+)$"),
    ("dxy", "宏观", "美元指数", r"^(?:🔴|⚪)\s*(美元指数[^\n]+)$"),
    ("liquidity", "宏观", "长期流动性", r"^(?:🔴|⚪)\s*(长期资金：[^\n]+)$"),
    ("fiscal", "宏观", "财政环境", r"^(财政：[^\n]+)$"),
    ("open_interest", "衍生品", "合约未平仓", r"^(合约未平仓：[^\n]+)$"),
    ("volume", "资金流", "成交额变化", r"^(成交额：[^\n]+)$"),
    ("taker", "资金流", "现货主动买卖", r"^(现货主动买卖：[^\n]+)$"),
    ("stablecoin", "资金流", "稳定币变化", r"^(稳定币(?:供给变化)?：[^\n]+)$"),
    ("etf", "资金流", "BTC现货ETF", r"^(BTC现货ETF：[^\n]+)$"),
    ("funding", "衍生品", "合约成本", r"^(合约成本：[^\n]+)$"),
    ("structure", "价格结构", "短周期结论", r"^(结论：[^\n]+)$"),
    ("levels", "价格结构", "支撑与压力", r"^([\d,]+ (?:当前)?支撑[^\n]+)$"),
    ("news", "新闻/基本面", "新闻状态", r"^(本小时[^\n]*新闻[^\n]*)$"),
    ("source_status", "来源声明", "采集机器人自报状态", r"^((?:本次数据|本标的采集)[^\n]+)$"),
)


def parse_evidence_items(text: str) -> tuple[EvidenceItem, ...]:
    from market_reader import report_asset
    asset = report_asset(text)
    if asset in ("QQQ", "VOO"):
        patterns = (
            ("stock_price", "股票价格", asset + "股票报价", r"^💰\s*([^\n]+)$"),
            ("stock_source", "股票成交量", "价格来源与股数成交量", r"^(来源 EQ_[^\n]+)$"),
            ("quote_time", "时效", "报价时间限制", r"^(报价源时间[^\n]+)$"),
            ("stock_series", "股票序列", "闭合与复权", r"^((?:公开流)?闭合K线[^\n]+)$"),
            ("derivative_identity", "衍生品", "合约参考关系", r"^((?:QQQ|SPY)USDT：[^\n]+)$"),
            ("derivative_oi", "衍生品", "合约OI", r"^(OI [^\n]+)$"),
            ("regular_funding", "衍生品", "普通资金费", r"^(普通Funding：[^\n]+)$"),
            ("dividend_adjustment", "股息调整", "Special", r"^(股息调整 Special：[^\n]+)$"),
            ("own_basis", "衍生品", "合约自身基差", r"^((?:QQQ|SPY)USDT 标记价/自身指数基差[^\n]+)$"),
            ("not_applicable", "适用性", "不适用指标", r"^(不适用：[^\n]+)$"),
        )
        return tuple(EvidenceItem(key, category, label, match.group(1).strip())
                     for key, category, label, pattern in patterns
                     for match in [re.search(pattern, text, flags=re.MULTILINE)] if match)
    items: list[EvidenceItem] = []
    for key, category, label, pattern in ITEM_PATTERNS:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match is None:
            continue
        items.append(
            EvidenceItem(
                key=key,
                category=category,
                label=label,
                value=" ".join(match.group(1).strip().split()),
            )
        )
    return tuple(items)


def _parse_report_time(text: str, sent_at_utc: str | None) -> datetime | None:
    match = re.search(r"📊\s*(?:BTC|QQQ|VOO)\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s+JST", text)
    if match is None:
        return None
    base_year = datetime.now(JST).year
    if sent_at_utc:
        try:
            base_year = datetime.fromisoformat(sent_at_utc).astimezone(JST).year
        except ValueError:
            pass
    try:
        return datetime(
            base_year,
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            tzinfo=JST,
        )
    except ValueError:
        return None


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def _internal_checks(
    text: str,
    report_time: datetime | None,
    sent_at_utc: str | None,
) -> tuple[str, ...]:
    checks: list[str] = []
    if report_time is None or sent_at_utc is None:
        checks.append("报告时间与 Telegram 时间：无法核对；不提升来源可信度。")
    else:
        try:
            sent_time = datetime.fromisoformat(sent_at_utc).astimezone(JST)
            difference = abs((sent_time - report_time).total_seconds())
            state = "一致" if difference <= 120 else "不一致"
            checks.append(
                f"报告时间与 Telegram 时间：{state}（相差 {int(difference)} 秒）；"
                "仅属内部检查，不证明市场数据真实。"
            )
        except ValueError:
            checks.append("报告时间与 Telegram 时间：格式异常；不提升来源可信度。")

    distance = re.search(
        r"现价\s+([\d,]+)\s*\|\s*距\s+([\d,]+)\s+压力\s+([\d,]+)（([\d.]+)%）",
        text,
    )
    if distance is not None:
        current = _number(distance.group(1))
        resistance = _number(distance.group(2))
        reported_distance = _number(distance.group(3))
        reported_percent = _number(distance.group(4))
        calculated_distance = resistance - current
        calculated_percent = calculated_distance / resistance * 100 if resistance else 0
        consistent = (
            abs(calculated_distance - reported_distance) <= 1
            and abs(calculated_percent - reported_percent) <= 0.05
        )
        state = "一致" if consistent else "不一致"
        checks.append(
            f"现价到压力位距离计算：{state}；仅属同一消息内部自洽检查，"
            "不是独立行情核验。"
        )
    else:
        checks.append("现价到压力位距离计算：缺少可解析字段。")
    return tuple(checks)


def _coverage(items: tuple[EvidenceItem, ...]) -> tuple[tuple[str, str], ...]:
    keys = {item.key for item in items}
    return (
        ("价格结构", "有辅助数据" if keys & {"btc_price", "structure", "levels"} else "缺失"),
        ("市场情绪", "有辅助数据" if "sentiment" in keys else "缺失"),
        ("ETH相对强弱与顶部撤退", "缺失"),
        ("资金流", "有辅助数据" if keys & {"volume", "taker", "stablecoin", "etf"} else "缺失"),
        ("链上数据", "缺失"),
        ("衍生品", "有辅助数据" if keys & {"open_interest", "funding"} else "缺失"),
        ("宏观环境", "有辅助数据" if keys & {"treasury", "dxy", "liquidity"} else "缺失"),
        ("新闻与基本面", "有来源声明" if "news" in keys else "缺失"),
        ("完整组合敞口与杠杆安全", "频道数据不覆盖；必须回读资产配置"),
    )


def build_snapshot(
    record: dict[str, Any], now: datetime | None = None
) -> EvidenceSnapshot:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    text = str(record["current_text"])
    from market_reader import report_asset
    asset = report_asset(text) or "BTC"
    sent_at_utc = record.get("sent_at_utc")
    report_time = _parse_report_time(text, sent_at_utc)
    age_minutes: int | None = None
    freshness = "时间未知"
    if report_time is not None:
        age_minutes = max(0, int((now - report_time.astimezone(timezone.utc)).total_seconds() // 60))
        freshness = "本周期内" if age_minutes <= FRESHNESS_MINUTES else "已过期"
    items = parse_evidence_items(text)
    return EvidenceSnapshot(
        chat_id=int(record["chat_id"]),
        message_id=int(record["message_id"]),
        sent_at_utc=str(sent_at_utc) if sent_at_utc else None,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        revision_count=int(record.get("revision_count", 0)),
        report_time_jst=report_time.isoformat(timespec="minutes") if report_time else None,
        age_minutes=age_minutes,
        freshness=freshness,
        items=items,
        internal_checks=_internal_checks(text, report_time, sent_at_utc),
        coverage=(_coverage(items) if asset == "BTC" else tuple(
            (item.label, "有辅助数据；独立待核验") for item in items)),
        decision_state="仅作不可信辅助证据；独立交叉核验前禁止用于决定或触发交易",
        asset_id=asset,
    )


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_snapshot(snapshot: EvidenceSnapshot, generated_at: datetime) -> str:
    age = f"{snapshot.age_minutes} 分钟" if snapshot.age_minutes is not None else "未知"
    lines = [
        "# 交易策略证据快照",
        "",
        f"更新时间：{generated_at.astimezone(JST).strftime('%Y-%m-%d %H:%M JST')}",
        "",
        "> 本文件由 Telegram 读取器自动生成。采集机器人默认是不可信来源；本文件只证明其曾发布这些内容，不证明市场事实成立，也不授权任何交易。",
        "",
        "## 当前源记录",
        "",
        f"- 频道定位：channel_id `{snapshot.chat_id}` / message_id `{snapshot.message_id}`。",
        f"- Telegram 时间：`{snapshot.sent_at_utc or '未知'}`；报告时间：`{snapshot.report_time_jst or '未知'}`。",
        f"- 数据年龄：{age}；时效状态：**{snapshot.freshness}**。",
        f"- 内容哈希：`sha256:{snapshot.content_hash}`；审计版本：{snapshot.revision_count}。",
        "- 来源可信度：**默认不可信**；独立来源数量：**0**。",
        f"- 策略状态：**{snapshot.decision_state}**。",
        "",
        "## 数据明细",
        "",
        "| 分类 | 数据项 | 采集机器人报告值 | 核验状态 |",
        "|---|---|---|---|",
    ]
    for item in snapshot.items:
        lines.append(
            f"| {_escape(item.category)} | {_escape(item.label)} | {_escape(item.value)} | 已捕获并解析；独立来源待核验 |"
        )
    if not snapshot.items:
        lines.append("| - | - | 未解析到策略数据 | 不可用 |")

    lines.extend(["", "## 内部一致性检查", ""])
    lines.extend(f"- {check}" for check in snapshot.internal_checks)
    lines.extend(
        [
            "- 采集机器人声称“数据完整”只是一项来源自报，不计为独立验证。",
            "",
            "## 策略证据覆盖",
            "",
            "| 策略维度 | 当前覆盖 |",
            "|---|---|",
        ]
    )
    lines.extend(f"| {_escape(name)} | {_escape(state)} |" for name, state in snapshot.coverage)
    lines.extend(
        [
            "",
            "## 使用门禁",
            "",
            "- 同一采集机器人提供的全部项目只算一个来源，不能以指标数量冒充多源证据。",
            "- 内部格式或计算一致，只说明消息自洽，不提升外部真实性。",
            "- 只有独立来源支持后，单项才能升级为辅助交叉证据；若冲突则标记冲突并停止引用。",
            "- 即使完成交叉核验，也必须结合完整组合敞口、现金、保证金、强平风险和市场阶段综合研判。",
            "- 本文件不生成开仓、平仓、增减现货、调整杠杆或转账指令。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_private_atomic(path: Path, content: str) -> None:
    # The project view links to the existing background runtime snapshot.
    # Replacing the link itself would leave that view permanently stale.
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def refresh_strategy_evidence(
    store: Any,
    output_path: Path,
    now: datetime | None = None,
) -> EvidenceRefreshResult:
    generated_at = now or datetime.now(timezone.utc)
    records = {asset: store.latest_strategy_report(asset) for asset in ("BTC", "QQQ", "VOO")}
    record = records['BTC'] or records['QQQ'] or records['VOO']
    if record is None:
        content = (
            "# 交易策略证据快照\n\n"
            f"更新时间：{generated_at.astimezone(JST).strftime('%Y-%m-%d %H:%M JST')}\n\n"
            "> 尚未采集到可解析的策略报告。采集机器人默认不可信，禁止据此生成交易。\n"
        )
        _write_private_atomic(output_path, content)
        return EvidenceRefreshResult(None, 0, "无报告", "不可用")
    snapshot = build_snapshot(record, generated_at)
    content = render_snapshot(snapshot, generated_at)
    for asset in ("BTC", "QQQ", "VOO"):
        if asset == snapshot.asset_id:
            continue
        equity = records[asset]
        content += "\n\n# " + asset + " 辅助观察\n\n"
        content += (render_snapshot(build_snapshot(equity, generated_at), generated_at)
                    if equity else "尚未收到该标的报告；不使用其他标的或历史报价补齐。\n")
    _write_private_atomic(output_path, content)
    return EvidenceRefreshResult(
        source_message_id=snapshot.message_id,
        item_count=len(snapshot.items),
        freshness=snapshot.freshness,
        decision_state=snapshot.decision_state,
    )
