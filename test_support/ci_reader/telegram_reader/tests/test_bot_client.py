import unittest

from bot_client import ALLOWED_UPDATE_TYPES, discover_channels, parse_channel_post_update


CHAT_ID = -1001234567890


def update(
    *,
    update_id=1,
    message_id=10,
    text="有效数据",
    sender_id=123,
    sender_is_bot=False,
    entities=None,
    edited=False,
    channel=True,
):
    message = {
        "message_id": message_id,
        "date": 1787443200,
        "chat": {
            "id": CHAT_ID,
            "type": "channel" if channel else "supergroup",
            "title": "数据采集频道" if channel else "私有群",
        },
        "from": {"id": sender_id, "is_bot": sender_is_bot},
        "text": text,
    }
    if entities is not None:
        message["entities"] = entities
    return {
        "update_id": update_id,
        (
            "edited_channel_post"
            if channel and edited
            else "channel_post"
            if channel
            else "edited_message"
            if edited
            else "message"
        ): message,
    }


class BotClientTest(unittest.TestCase):
    def test_bot_api_requests_only_channel_content_updates(self):
        self.assertEqual(
            ALLOWED_UPDATE_TYPES,
            ["channel_post", "edited_channel_post", "my_chat_member"],
        )

    def test_parses_human_and_bot_without_sender_filter(self):
        human = parse_channel_post_update(update(sender_id=1, sender_is_bot=False))
        bot = parse_channel_post_update(update(sender_id=2, sender_is_bot=True))
        self.assertFalse(human.sender_is_bot)
        self.assertTrue(bot.sender_is_bot)
        self.assertEqual(human.chat_id, CHAT_ID)

    def test_only_telegram_bot_command_entity_is_filtered(self):
        command = parse_channel_post_update(
            update(
                text="/btc",
                entities=[{"type": "bot_command", "offset": 0, "length": 4}],
            )
        )
        plain_slash = parse_channel_post_update(update(text="价格路径 /btc 不是指令"))
        self.assertTrue(command.is_bot_command)
        self.assertFalse(plain_slash.is_bot_command)

    def test_text_command_fallback_filters_channel_command(self):
        command = parse_channel_post_update(update(text="/btc latest"))
        self.assertTrue(command.is_bot_command)

    def test_parses_channel_post(self):
        post = parse_channel_post_update(update(sender_is_bot=True))
        self.assertEqual(post.chat_id, CHAT_ID)
        self.assertEqual(post.text, "有效数据")

    def test_discovers_channel_without_exposing_message_content(self):
        candidates = discover_channels([update(text="敏感正文")])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].chat_id, CHAT_ID)
        self.assertNotIn("敏感正文", repr(candidates[0]))

    def test_rejects_group_message_even_when_id_matches(self):
        self.assertIsNone(parse_channel_post_update(update(channel=False)))
        self.assertEqual(discover_channels([update(channel=False)]), [])

    def test_discovers_channel_from_membership_update(self):
        membership = {
            "update_id": 9,
            "my_chat_member": {
                "chat": {
                    "id": CHAT_ID,
                    "type": "channel",
                    "title": "数据采集频道",
                }
            },
        }
        candidates = discover_channels([membership])
        self.assertEqual(candidates[0].chat_type, "channel")
        self.assertEqual(candidates[0].title, "数据采集频道")


if __name__ == "__main__":
    unittest.main()
