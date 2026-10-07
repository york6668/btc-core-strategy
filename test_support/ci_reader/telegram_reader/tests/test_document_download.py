import io
import json
import unittest
import urllib.error
import urllib.request

from bot_client import BotAPI, MAX_DOWNLOAD_BYTES, TelegramReaderError, _RejectRedirects


class Response(io.BytesIO):
    def __init__(self, body, *, headers=None, url=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.url = url
        self.status = status
        self.read_sizes = []

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response.url is None:
            response.url = request.full_url
        return response


def metadata(*, path="documents/evidence.json", size=2, file_id="file-1"):
    file = {"file_id": file_id, "file_path": path}
    if size is not None:
        file["file_size"] = size
    return Response(json.dumps({"ok": True, "result": file}).encode())


class DocumentDownloadTest(unittest.TestCase):
    def test_uses_getfile_then_fixed_official_host_and_shared_opener(self):
        opener = Opener(metadata(), Response(b"{}", headers={"Content-Length": "2"}))
        api = BotAPI("test-only-token", opener=opener)
        self.assertEqual(api.download_document("file-1"), b"{}")
        api_request, _ = opener.requests[0]
        file_request, _ = opener.requests[1]
        self.assertEqual(api_request.get_method(), "POST")
        self.assertEqual(json.loads(api_request.data), {"file_id": "file-1"})
        self.assertTrue(api_request.full_url.endswith("/getFile"))
        self.assertEqual(file_request.get_method(), "GET")
        self.assertEqual(
            file_request.full_url,
            "https://api.telegram.org/file/bottest-only-token/documents/evidence.json",
        )
        self.assertEqual(file_request.get_header("Accept-encoding"), "identity")

    def test_metadata_size_over_limit_stops_before_download(self):
        opener = Opener(metadata(size=MAX_DOWNLOAD_BYTES + 1))
        with self.assertRaises(TelegramReaderError):
            BotAPI("test-only-token", opener=opener).download_document("file-1")
        self.assertEqual(len(opener.requests), 1)

    def test_rejects_invalid_declared_sizes(self):
        for size in (-1, True, "2", 2.0):
            with self.subTest(size=size):
                opener = Opener(metadata(size=size))
                with self.assertRaises(TelegramReaderError):
                    BotAPI("test-only-token", opener=opener).download_document("file-1")
                self.assertEqual(len(opener.requests), 1)

    def test_rejects_untrusted_or_ambiguous_file_paths(self):
        for path in (
            "https://example.invalid/evidence.json", "//example.invalid/a",
            "/documents/a", "../a", "documents/../a", "documents/./a",
            "documents//a", "documents/%2e%2e/a", "documents/a?next=x",
            "documents/a#x", "documents\\a", "documents/a\n",
        ):
            with self.subTest(path=path):
                opener = Opener(metadata(path=path))
                with self.assertRaises(TelegramReaderError):
                    BotAPI("test-only-token", opener=opener).download_document("file-1")
                self.assertEqual(len(opener.requests), 1)

    def test_requires_matching_file_identity(self):
        opener = Opener(metadata(file_id="different-file"))
        with self.assertRaises(TelegramReaderError):
            BotAPI("test-only-token", opener=opener).download_document("file-1")
        self.assertEqual(len(opener.requests), 1)

    def test_declared_size_must_match_actual_size(self):
        opener = Opener(metadata(size=3), Response(b"{}"))
        with self.assertRaises(TelegramReaderError):
            BotAPI("test-only-token", opener=opener).download_document("file-1")

    def test_content_length_must_match_metadata_and_actual_size(self):
        cases = ((2, "3", b"{}"), (None, "3", b"{}"), (2, "-1", b"{}"))
        for declared, length, body in cases:
            with self.subTest(declared=declared, length=length):
                opener = Opener(metadata(size=declared), Response(body, headers={"Content-Length": length}))
                with self.assertRaises(TelegramReaderError):
                    BotAPI("test-only-token", opener=opener).download_document("file-1")

    def test_rejects_oversized_stream_without_length_headers(self):
        response = Response(b"x" * (MAX_DOWNLOAD_BYTES + 1))
        opener = Opener(metadata(size=None), response)
        with self.assertRaises(TelegramReaderError):
            BotAPI("test-only-token", opener=opener).download_document("file-1")
        self.assertEqual(response.read_sizes, [MAX_DOWNLOAD_BYTES + 1])

    def test_accepts_exactly_five_mib(self):
        body = b"x" * MAX_DOWNLOAD_BYTES
        opener = Opener(metadata(size=len(body)), Response(body))
        self.assertEqual(BotAPI("test-only-token", opener=opener).download_document("file-1"), body)

    def test_rejects_response_host_change_and_non_success_status(self):
        for response in (
            Response(b"{}", url="https://example.invalid/file"),
            Response(b"{}", status=206),
        ):
            opener = Opener(metadata(), response)
            with self.assertRaises(TelegramReaderError):
                BotAPI("test-only-token", opener=opener).download_document("file-1")

    def test_rejects_encoded_body(self):
        opener = Opener(metadata(), Response(b"{}", headers={"Content-Encoding": "gzip"}))
        with self.assertRaises(TelegramReaderError):
            BotAPI("test-only-token", opener=opener).download_document("file-1")

    def test_redirect_handler_rejects_before_following(self):
        request = urllib.request.Request("https://api.telegram.org/file/bottest-only-token/a")
        with self.assertRaises(TelegramReaderError) as raised:
            _RejectRedirects().redirect_request(request, None, 302, "found", {}, "https://example.invalid/a")
        self.assertNotIn("test-only-token", str(raised.exception))

    def test_errors_do_not_echo_token_or_url(self):
        errors = (
            urllib.error.HTTPError("https://api.telegram.org/file/bottest-only-token/a", 403, "test-only-token", {}, None),
            RuntimeError("https://api.telegram.org/file/bottest-only-token/a"),
        )
        for error in errors:
            with self.subTest(kind=type(error).__name__):
                opener = Opener(metadata(), error)
                with self.assertRaises(TelegramReaderError) as raised:
                    BotAPI("test-only-token", opener=opener).download_document("file-1")
                self.assertNotIn("test-only-token", str(raised.exception))
                self.assertNotIn("https://", str(raised.exception))

    def test_error_code_response_cannot_echo_untrusted_text(self):
        response = Response(json.dumps({"ok": False, "error_code": "test-only-token"}).encode())
        with self.assertRaises(TelegramReaderError) as raised:
            BotAPI("test-only-token", opener=Opener(response)).get_file("file-1")
        self.assertNotIn("test-only-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
