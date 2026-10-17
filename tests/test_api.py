"""HTTP integration checks against the actual game core and SQLite repository."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from server.app import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "Optional server dependencies are not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "game.sqlite3"
        self.client = TestClient(create_app(self.path, "test-token"))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Authorization": "Bearer test-token"}

    def create(self, command_id="new-run"):
        response = self.client.post("/runs", headers=self.headers,
                                    json={"name": "测试队伍", "seed": 17, "command_id": command_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_authentication_and_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        body = {"command_id": "new-run"}
        self.assertEqual(self.client.post("/runs", json=body).status_code, 401)
        denied = self.client.post("/runs", json=body, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers["www-authenticate"], "Bearer")
        self.assertFalse(self.path.exists(), "Denied requests must not create a game database")

    def test_unconfigured_token_denies_game_access(self):
        with patch.dict("os.environ", {}, clear=True):
            app = create_app(self.path)
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            response = client.get("/runs/anything/view", headers=self.headers)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "token_not_configured")
        self.assertFalse(self.path.exists())

    def test_create_preview_command_and_retransmission(self):
        created = self.create()
        self.assertEqual(self.create(), created)
        run_id = created["run_id"]
        view = self.client.get(f"/runs/{run_id}/view", headers=self.headers).json()
        self.assertEqual(view, created["view"])
        self.assertTrue(view["active"])
        command = {"type": "end", "actor": view["active"][0]}
        preview = self.client.post(f"/runs/{run_id}/preview", headers=self.headers,
                                   json={"command": command})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["revision"], view["revision"])
        self.assertEqual(self.client.get(f"/runs/{run_id}/view", headers=self.headers).json(), view)
        body = {"command_id": "end-1", "expected_revision": view["revision"], "command": command}
        executed = self.client.post(f"/runs/{run_id}/commands", headers=self.headers, json=body)
        self.assertEqual(executed.status_code, 200, executed.text)
        replay = self.client.post(f"/runs/{run_id}/commands", headers=self.headers, json=body)
        self.assertEqual(replay.json(), executed.json())
        self.assertEqual(executed.json()["revision"], view["revision"] + 1)
        self.assertNotIn('"seed"', executed.text)
        self.assertNotIn('"rng_state"', executed.text)
        events = self.client.get(f"/runs/{run_id}/events?after=0&limit=2", headers=self.headers)
        self.assertEqual(events.status_code, 200)
        self.assertLessEqual(len(events.json()["events"]), 2)

    def test_conflict_and_invalid_command_leave_state_unchanged(self):
        created = self.create()
        run_id = created["run_id"]
        view = created["view"]
        invalid = self.client.post(f"/runs/{run_id}/commands", headers=self.headers,
                                   json={"command_id": "bad-actor", "expected_revision": 0,
                                         "command": {"type": "end", "actor": "P99"}})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertIn("code", invalid.json()["error"])
        self.assertEqual(self.client.get(f"/runs/{run_id}/view", headers=self.headers).json(), view)
        command = {"type": "end", "actor": view["active"][0]}
        body = {"command_id": "first", "expected_revision": 0, "command": command}
        self.assertEqual(self.client.post(f"/runs/{run_id}/commands", headers=self.headers,
                                         json=body).status_code, 200)
        stale = self.client.post(f"/runs/{run_id}/commands", headers=self.headers,
                                 json={**body, "command_id": "second"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["current_revision"], 1)
        changed = self.client.post(f"/runs/{run_id}/commands", headers=self.headers,
                                   json={**body, "command": {"type": "dodge", "actor": command["actor"]}})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["error"]["code"], "command_id_conflict")

    def test_missing_run_and_request_validation(self):
        self.assertEqual(self.client.get("/runs/missing/view", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/runs", headers=self.headers,
                                         json={"command_id": "x", "templates": ["fighter"]}).status_code, 422)
        created = self.create()
        run_id = created["run_id"]
        invalid = self.client.post(f"/runs/{run_id}/commands", headers=self.headers,
                                   json={"command_id": "x", "expected_revision": True, "command": {}})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get(f"/runs/{run_id}/events?limit=201", headers=self.headers).status_code, 422)
        reaction = self.client.post(f"/runs/{run_id}/reactions", headers=self.headers,
                                    json={"command_id": "react", "expected_revision": 0, "choice": "invalid"})
        self.assertEqual(reaction.status_code, 422)

    def test_options_endpoint_is_authenticated_visible_and_read_only(self):
        created = self.create()
        run_id = created["run_id"]
        body = {"command": {"type": "move", "actor": created["view"]["active"][0]}}
        self.assertEqual(self.client.post(f"/runs/{run_id}/options", json=body).status_code, 401)
        response = self.client.post(f"/runs/{run_id}/options", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["revision"], 0)
        self.assertTrue(result["reachable"])
        self.assertTrue(result["landmarks"])
        self.assertNotIn('"rng_state"', response.text)
        self.assertNotIn('"battle_snapshot"', response.text)
        self.assertEqual(self.client.get(f"/runs/{run_id}/view", headers=self.headers).json(), created["view"])
        invalid = self.client.post(f"/runs/{run_id}/options", headers=self.headers,
                                   json={"command": {"type": "move", "actor": "P99"}})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.get(f"/runs/{run_id}/view", headers=self.headers).json(), created["view"])


if __name__ == "__main__":
    unittest.main()
