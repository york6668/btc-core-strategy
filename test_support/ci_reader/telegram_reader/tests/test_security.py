import ast
import unittest
from pathlib import Path

from bot_client import CredentialVault, KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE
from models import unix_utc_iso


class FakeKeyring:
    def __init__(self, token=None):
        self.token = token

    def get_password(self, service, account):
        if (service, account) == (KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT):
            return self.token
        return None


class SecurityTest(unittest.TestCase):
    def test_token_is_read_only_from_keychain_backend(self):
        vault = CredentialVault(FakeKeyring("test-token"))
        self.assertEqual(vault.bot_token(), "test-token")

    def test_reader_code_contains_no_telegram_mutation_methods(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = {
            "send_message",
            "delete_messages",
            "edit_message",
            "send_read_acknowledge",
            "delete_webhook",
            "set_webhook",
        }
        found = set()
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in forbidden:
                        found.add(node.name)
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    found.add(node.attr)
        self.assertEqual(found, set())

    def test_unix_time_conversion(self):
        self.assertEqual(unix_utc_iso(0), "1970-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
