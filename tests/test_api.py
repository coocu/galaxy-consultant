import io
import json
import os
import tempfile
import unittest
from importlib import reload
from urllib.parse import urlencode


class WsgiClient:
    def __init__(self, app_module):
        self.app_module = app_module
        self.cookies = {}

    def request(self, method, path, json_body=None, headers=None):
        headers = headers or {}
        body = b""
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path.split("?", 1)[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
        }
        for name, value in headers.items():
            key = "HTTP_" + name.upper().replace("-", "_")
            if name.lower() == "content-type":
                environ["CONTENT_TYPE"] = value
            else:
                environ[key] = value
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = response_headers
            for h, v in response_headers:
                if h.lower() == "set-cookie":
                    first = v.split(";", 1)[0]
                    k, val = first.split("=", 1)
                    self.cookies[k] = val

        chunks = self.app_module.application(environ, start_response)
        content = b"".join(chunks)
        return captured["status"], dict(captured["headers"]), content

    def get_json(self, path, headers=None):
        status, headers_out, content = self.request("GET", path, headers=headers)
        return status, json.loads(content.decode("utf-8"))

    def post_json(self, path, payload, headers=None):
        status, headers_out, content = self.request("POST", path, json_body=payload, headers=headers)
        return status, json.loads(content.decode("utf-8"))


class QueueApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = os.path.join(self.tmp.name, "queue.db")
        os.environ["ADMIN_KEY"] = "test-key"
        os.environ["APP_ONLY_MODE"] = "0"
        import app
        self.app = reload(app)
        self.client = WsgiClient(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_issuing_tickets_numbers_each_queue_independently(self):
        status, stores = self.client.get_json("/api/stores")
        self.assertEqual(status.split()[0], "200")
        store_id = stores["stores"][0]["id"]

        _, first_simple = self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "simple"})
        _, second_simple = self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "simple"})
        _, first_purchase = self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "purchase"})

        self.assertEqual(first_simple["ticket"]["ticket_number"], 1)
        self.assertEqual(second_simple["ticket"]["ticket_number"], 2)
        self.assertEqual(first_purchase["ticket"]["ticket_number"], 1)

    def test_kiosk_can_register_printed_ticket_number(self):
        status, stores = self.client.get_json("/api/stores")
        self.assertEqual(status.split()[0], "200")
        store_id = stores["stores"][0]["id"]

        status, issued = self.client.post_json(
            "/api/tickets",
            {"store_id": store_id, "queue_type": "simple", "ticket_number": 7},
        )
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(issued["ticket"]["ticket_number"], 7)

        _, state = self.client.get_json(f"/api/state?{urlencode({'store_id': store_id})}")
        self.assertEqual(state["queues"]["simple"]["last_issued_number"], 7)
        self.assertEqual(state["queues"]["simple"]["waiting_count"], 1)
        self.assertEqual(state["queues"]["simple"]["waiting_list"][0]["ticket_number"], 7)

        _, next_issue = self.client.post_json(
            "/api/tickets",
            {"store_id": store_id, "queue_type": "simple"},
        )
        self.assertEqual(next_issue["ticket"]["ticket_number"], 8)

    def test_admin_call_next_sets_current_number_and_waiting_count(self):
        store_id = self.client.get_json("/api/stores")[1]["stores"][0]["id"]
        self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "simple"})
        self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "simple"})

        status, result = self.client.post_json(
            "/api/admin/call/next",
            {"store_id": store_id, "queue_type": "simple"},
            headers={"X-Admin-Key": "test-key"},
        )
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(result["call"]["ticket_number"], 1)

        _, state = self.client.get_json(f"/api/state?{urlencode({'store_id': store_id})}")
        self.assertEqual(state["queues"]["simple"]["current_call_number"], 1)
        self.assertEqual(state["queues"]["simple"]["waiting_count"], 1)

    def test_direct_call_can_call_number_without_waiting_ticket(self):
        store_id = self.client.get_json("/api/stores")[1]["stores"][0]["id"]
        status, result = self.client.post_json(
            "/api/admin/call/direct",
            {"store_id": store_id, "queue_type": "purchase", "ticket_number": 77},
            headers={"X-Admin-Key": "test-key"},
        )
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(result["call"]["ticket_number"], 77)
        _, state = self.client.get_json(f"/api/state?{urlencode({'store_id': store_id})}")
        self.assertEqual(state["queues"]["purchase"]["current_call_number"], 77)

    def test_reset_only_resets_selected_queue(self):
        store_id = self.client.get_json("/api/stores")[1]["stores"][0]["id"]
        self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "simple"})
        self.client.post_json("/api/tickets", {"store_id": store_id, "queue_type": "purchase"})
        self.client.post_json("/api/admin/call/next", {"store_id": store_id, "queue_type": "simple"}, headers={"X-Admin-Key": "test-key"})
        self.client.post_json("/api/admin/call/next", {"store_id": store_id, "queue_type": "purchase"}, headers={"X-Admin-Key": "test-key"})

        status, _ = self.client.post_json(
            "/api/admin/reset",
            {"store_id": store_id, "queue_type": "simple"},
            headers={"X-Admin-Key": "test-key"},
        )
        self.assertEqual(status.split()[0], "200")
        _, state = self.client.get_json(f"/api/state?{urlencode({'store_id': store_id})}")
        self.assertEqual(state["queues"]["simple"]["last_issued_number"], 0)
        self.assertIsNone(state["queues"]["simple"]["current_call_number"])
        self.assertEqual(state["queues"]["purchase"]["last_issued_number"], 1)
        self.assertEqual(state["queues"]["purchase"]["current_call_number"], 1)

    def test_store_management_requires_admin_key(self):
        status, result = self.client.post_json("/api/admin/stores", {"name": "무단"})
        self.assertEqual(status.split()[0], "401")
        self.assertFalse(result["ok"])

        status, result = self.client.post_json(
            "/api/admin/stores",
            {"name": "이천 서비스센터", "sort_order": 10},
            headers={"X-Admin-Key": "test-key"},
        )
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(result["store"]["name"], "이천 서비스센터")

    def test_app_only_mode_blocks_browser_and_allows_app_token(self):
        os.environ["APP_ONLY_MODE"] = "1"
        os.environ["APP_ACCESS_TOKEN"] = "secret-token"
        self.app = reload(self.app)
        client = WsgiClient(self.app)
        status, _, content = client.request("GET", "/")
        self.assertEqual(status.split()[0], "403")

        status, headers, _ = client.request("GET", "/?app_token=secret-token")
        self.assertEqual(status.split()[0], "302")
        status, _, content = client.request("GET", "/")
        self.assertEqual(status.split()[0], "200")
        self.assertIn(b"CodeNote", content)


if __name__ == "__main__":
    unittest.main()
