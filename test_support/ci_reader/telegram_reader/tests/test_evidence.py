import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evidence import build_snapshot, refresh_strategy_evidence, render_snapshot
from models import FetchedMessage, ReaderConfig
from store import MessageStore


REPORT = """📊 BTC 08-23 05:19 JST（QUERY）

💰 $77,353
24H -0.12% | 7D +22.71%
市场情绪：贪婪（71/100）

🔴 美债10Y 4.74/>4.7（30天 +0.08pp） | 实际利率 2.4/2.5（30天 +0.01pp）
⚪ 美元指数 98.8/96（30天 -2.31%）
⚪ 长期资金：货币供应量（M2）同比 +5.53% | 美联储资产负债表30天 +0.04%
财政：压力偏高 | 债务成本高

合约未平仓：$8.23B | 4H -0.08% | 24H -0.03% | 72H -3.26%
成交额：合约 4H -16.38% / 24H -63.97% | 现货 4H -29.30% / 24H -51.80%
现货主动买卖：4H 主动买入占优10.65% | 24H 主动买入占优3.48%
稳定币：24H +$977M | 7D +$2B
BTC现货ETF：1D +$307M | 5D +$2B | 30D +$2B（截至 2026-08-21）
合约成本：上涨方每8H支付0.010% | 合约价较现货 +0.005%

05:19 JST 现价 77,353 | 距 78,832 压力 1,479（1.88%）
结论：支撑测试已完成，方向尚未确认。
76,888 支撑（已测试） | 78,832 压力（未突破） | 68,357 趋势支撑
本小时无新增新闻。
本次数据完整｜Binance现货/Hyperliquid影子72/72H
"""


def record():
    return {
        "chat_id": -1001,
        "message_id": 5,
        "sent_at_utc": "2026-08-22T20:19:58+00:00",
        "current_text": REPORT,
        "revision_count": 1,
    }


class EvidenceTest(unittest.TestCase):
    def test_default_state_is_untrusted_and_not_decisive(self):
        now = datetime(2026, 8, 22, 20, 25, tzinfo=timezone.utc)
        snapshot = build_snapshot(record(), now)
        rendered = render_snapshot(snapshot, now)
        self.assertGreaterEqual(len(snapshot.items), 12)
        self.assertEqual(snapshot.freshness, "本周期内")
        self.assertIn("默认不可信", rendered)
        self.assertIn("独立来源数量：**0**", rendered)
        self.assertIn("频道定位：channel_id", rendered)
        self.assertIn("禁止用于决定或触发交易", rendered)
        self.assertIn("不是独立行情核验", rendered)

    def test_stale_report_is_marked_expired(self):
        now = datetime(2026, 8, 22, 23, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(record(), now)
        self.assertEqual(snapshot.freshness, "已过期")

    def test_refresh_uses_latest_report_not_channel_test_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            self.addCleanup(store.close)
            store.save_config(
                ReaderConfig(
                    chat_id=-1001,
                    reader_bot_id=99,
                    configured_at_utc="2026-08-22T20:00:00+00:00",
                )
            )
            for message_id, text in ((3, "频道切换测试"), (5, REPORT)):
                store.observe_message(
                    FetchedMessage(
                        update_id=message_id,
                        chat_id=-1001,
                        message_id=message_id,
                        sender_id=-1001,
                        sender_is_bot=False,
                        sent_at_utc=f"2026-08-22T20:{message_id:02d}:00+00:00",
                        edited_at_utc=None,
                        text=text,
                        content_kind="text",
                        is_bot_command=False,
                    )
                )
            output = Path(directory) / "策略证据.md"
            result = refresh_strategy_evidence(
                store,
                output,
                now=datetime(2026, 8, 22, 20, 25, tzinfo=timezone.utc),
            )
            self.assertEqual(result.source_message_id, 5)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertIn("采集机器人默认是不可信来源", output.read_text())


if __name__ == "__main__":
    unittest.main()
