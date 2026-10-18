import io
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from client.cli import Client, main, read_json
from client.parser import ParseError, parse_line
from client.render import inspect, render_actions, render_map, render_view
from client.transport import LocalTransport, TransportError


def make_view(revision=0):
    return {
        "id": "test-run", "name": "远征队", "revision": revision,
        "phase": "combat", "round": 1, "active": ["P1", "P2", "P3", "P4"],
        "party": [{"id": "P1", "name": "战士", "position": "A1", "hp": 12,
                   "max_hp": 12, "ac": 16, "speed": 9, "level": 1,
                   "resources": {"second_wind": 1, "potions": 1}, "slots": {},
                   "budgets": {"action": 1, "bonus": 1, "reaction": 1, "movement": 9},
                   "abilities": [{"id": "second_wind", "name": "回气", "cost": "bonus",
                                  "range": 0, "level": 0, "available": True, "description": "恢复生命"}],
                   "conditions": [], "concentration": None}],
        "enemies": [{"id": "E1", "name": "卫兵", "position": "B1", "hp": 8,
                     "max_hp": 8, "ac": 12, "conditions": []}],
        "map": {"width": 16, "height": 12, "cell_size": 1.5, "walls": ["F5"],
                "heights": {"G6": 3}, "difficult": ["C2"],
                "hazards": {"D2": {"damage": "1d4", "description": "火焰"}},
                "landmarks": [{"position": "G6", "name": "高台", "description": "石阶"}]},
        "pending": None, "recent_events": [], "outcome": None,
    }


class FakeTransport:
    def __init__(self):
        self.view = make_view()
        self.writes = []
        self.receipts = {}
        self.fail_after_commit = False
        self.closed = False

    def get_view(self, run_id):
        return deepcopy(self.view)

    def preview(self, run_id, command):
        return {"command": deepcopy(command), "revision": self.view["revision"],
                "description": "攻击卫兵", "cost": {"action": 1}, "warnings": [], "targets": ["E1"]}

    def create_run(self, payload):
        receipt = {"command_id": payload["command_id"], "run_id": "test-run", "revision": self.view["revision"],
                   "view": deepcopy(self.view), "events": []}
        self.receipts[payload["command_id"]] = receipt
        return deepcopy(receipt)

    def write(self, run_id, operation, payload):
        self.writes.append((run_id, operation, deepcopy(payload)))
        command_id = payload["command_id"]
        if command_id in self.receipts:
            return deepcopy(self.receipts[command_id])
        if payload["expected_revision"] != self.view["revision"]:
            raise TransportError("revision_conflict", "局势已改变", status_code=409, revision=self.view["revision"])
        self.view["revision"] += 1
        event = {"seq": self.view["revision"], "kind": "attack", "text": "攻击完成", "details": {"roll": 15}}
        self.view["recent_events"].append(event)
        receipt = {"command_id": command_id, "revision": self.view["revision"], "view": deepcopy(self.view), "events": [event]}
        self.receipts[command_id] = receipt
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise TransportError("connection_lost", "连接中断", retryable=True)
        return deepcopy(receipt)

    def events(self, run_id, after=0, limit=20):
        values = [event for event in self.view["recent_events"] if event["seq"] > after][:limit]
        return {"events": deepcopy(values), "next_after": values[-1]["seq"] if values else after}

    def close(self):
        self.closed = True


class ParserTests(unittest.TestCase):
    def test_supported_commands_and_spell_targets(self):
        kind, command = parse_line("preview move p2 g6")
        self.assertEqual(kind, "preview")
        self.assertEqual(command, {"type": "move", "actor": "P2", "destination": "G6"})
        self.assertEqual(parse_line("cast P4 bless P1,P2,P3 slot=1")[1],
                         {"type": "cast", "actor": "P4", "ability": "bless", "targets": ["P1", "P2", "P3"], "slot": 1})
        self.assertEqual(parse_line("rest short"), ("action", {"type": "rest", "kind": "short"}))
        self.assertEqual(parse_line("help"), ("help", None))
        self.assertEqual(parse_line("help P1 P2")[1], {"type": "help", "actor": "P1", "target": "P2"})

    def test_malformed_commands_are_rejected(self):
        for line in ("attack P1", "attack P1 E1 extra", "move P2 G0", "do zero", "react maybe",
                     "cast P3 fire_bolt", "cast P4 bless P1, slot=1", "cast P4 bless P1 slot=0",
                     "log -1", "retry P1", "preview unknown P1", 'inspect "G6'):
            with self.subTest(line=line), self.assertRaises(ParseError):
                parse_line(line)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.output = []
        self.client = Client(self.transport, "test-run", make_view(), output=self.output.append)

    def test_invalid_input_and_read_commands_never_write(self):
        for line in ("attack P1", "cast P3 fire_bolt", "do invalid", "react maybe", "rest long",
                     "party", "actions P1", "inspect G6", "log 10", "map", "resume"):
            self.client.handle(line)
        self.assertEqual(self.transport.writes, [])

    def test_confirm_preview_and_numeric_menu(self):
        self.client.handle("preview attack P1 E1")
        self.assertEqual(self.transport.writes, [])
        self.client.handle("1")
        self.assertEqual(self.transport.writes, [])
        self.client.handle("c")
        self.assertEqual(len(self.transport.writes), 1)
        self.assertEqual(self.transport.writes[0][2]["command"], {"type": "attack", "actor": "P1", "target": "E1"})
        self.assertEqual(self.client.view["revision"], 1)
        self.assertFalse(self.client.previews)

    def test_stale_preview_refuses_write(self):
        self.client.handle("preview attack P1 E1")
        self.transport.view["revision"] += 1
        self.client.handle("do 1")
        self.assertEqual(self.transport.writes, [])
        self.assertTrue(any("过期" in line for line in self.output))

    def test_timeout_replays_exact_payload_without_double_action(self):
        self.client.handle("preview attack P1 E1")
        self.transport.fail_after_commit = True
        self.client.handle("do 1")
        self.assertIsNotNone(self.client.pending)
        self.client.handle("end P1")
        self.assertEqual(len(self.transport.writes), 1)
        self.client.handle("do 1")
        self.assertEqual(len(self.transport.writes), 2)
        self.assertEqual(self.transport.writes[0], self.transport.writes[1])
        self.assertEqual(self.transport.view["revision"], 1)
        self.assertIsNone(self.client.pending)

    def test_pending_survives_restart_and_keeps_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "client.pending.json"
            scope = {"mode": "local", "db": "game.sqlite3"}
            first = Client(self.transport, "test-run", make_view(), output=self.output.append, journal_path=journal, scope=scope)
            self.transport.fail_after_commit = True
            first.handle("end P1")
            self.assertTrue(journal.exists())
            original_payload = read_json(journal)["payload"]
            second = Client(self.transport, output=self.output.append, journal_path=journal, scope=scope)
            second.load_pending()
            second.handle("resend")
            self.assertEqual(self.transport.writes[-1][2], original_payload)
            self.assertEqual(self.transport.view["revision"], 1)
            self.assertFalse(journal.exists())

    def test_pending_refuses_different_database_or_run(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "pending.json"
            scope = {"mode": "local", "db": "game.sqlite3"}
            first = Client(self.transport, "test-run", make_view(), output=self.output.append, journal_path=journal, scope=scope)
            self.transport.fail_after_commit = True
            first.handle("end P1")
            for different_scope, run_id in (({"mode": "local", "db": "other.sqlite3"}, None), (scope, "other-run")):
                client = Client(self.transport, run_id, output=self.output.append, journal_path=journal, scope=different_scope)
                with self.assertRaises(ValueError):
                    client.load_pending()

    def test_revision_conflict_clears_confirmations(self):
        self.client.handle("preview attack P1 E1")
        self.transport.view["revision"] = 2
        self.client.handle("end P1")
        self.assertIsNone(self.client.pending)
        self.assertEqual(self.client.view["revision"], 2)
        self.assertFalse(self.client.previews)

    def test_pending_reaction_menu(self):
        self.transport.view["pending"] = {"kind": "opportunity", "description": "是否借机攻击？", "reactor": "P1", "mover": "E1"}
        self.client.refresh(display=False)
        self.client.handle("2")
        self.assertEqual(self.transport.writes, [])
        self.client.handle("c")
        self.assertEqual(self.transport.writes[-1][1], "reactions")
        self.assertEqual(self.transport.writes[-1][2]["choice"], "decline")



class TransportTests(unittest.TestCase):






    def test_local_adapter_calls_repository_without_http(self):
        class Repository:
            def submit(self, run_id, **payload):
                return {"run_id": run_id, "payload": payload}

        transport = LocalTransport(repository=Repository())
        result = transport.write("abc", "commands", {"command_id": "fixed", "expected_revision": 1, "command": {"type": "end", "actor": "P1"}})
        self.assertEqual(result["payload"]["command_id"], "fixed")


class RenderTests(unittest.TestCase):
    def test_render_uses_only_visible_actors_and_known_terrain(self):
        view = make_view()
        text = render_view(view) + "\n" + render_map(view)
        self.assertIn("可操作", text)
        self.assertIn("P1", text)
        self.assertIn("E1", text)
        self.assertNotIn("E2", text)
        self.assertIn("高台", inspect(view, "G6"))
        self.assertIn("回气", render_actions(view, "P1"))
        with self.assertRaises(ValueError):
            inspect(view, "Q13")


class LocalIntegrationTests(unittest.TestCase):
    def test_actual_cli_saves_move_spell_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "game.sqlite3")
            config = str(Path(directory) / "client.json")
            commands = iter(["preview cast P3 fire_bolt E1", "preview move P3 D6", "do 1",
                             "preview cast P3 fire_bolt E1", "do 2", "end P3", "quit"])
            output = []
            self.assertEqual(main(["--new", "--seed", "42", "--db", db, "--config", config],
                                  input_reader=lambda prompt: next(commands), output=output.append), 0)
            saved = read_json(config)
            transport = LocalTransport(db)
            try:
                view = transport.get_view(saved["last"]["run_id"])
                self.assertEqual(view["revision"], 3)
                self.assertEqual(next(actor for actor in view["party"] if actor["id"] == "P3")["position"], "D6")
                events = transport.events(view["id"], limit=100)["events"]
                self.assertTrue(any(event["kind"] == "move" for event in events))
                self.assertTrue(any("火焰箭" in event["text"] for event in events))
            finally:
                transport.close()
            self.assertTrue(any("遮挡" in line or "视线" in line for line in output))
            self.assertFalse(Path(directory, "client.pending.json").exists())

    def test_actual_repository_recovers_committed_write_after_disconnect(self):
        class LostReceiptTransport(LocalTransport):
            def write(self, run_id, operation, payload):
                super().write(run_id, operation, payload)
                raise TransportError("connection_lost", "响应丢失", retryable=True)

        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "game.sqlite3")
            journal = Path(directory) / "client.pending.json"
            scope = {"mode": "local", "db": db}
            output = []
            transport = LostReceiptTransport(db)
            first = Client(transport, output=output.append, journal_path=journal, scope=scope)
            first.create(seed=42)
            first.handle("end P2")
            run_id = first.run_id
            payload = read_json(journal)["payload"]
            transport.close()
            recovered_transport = LocalTransport(db)
            try:
                second = Client(recovered_transport, run_id, output=output.append, journal_path=journal, scope=scope)
                second.load_pending()
                second.handle("resend")
                self.assertEqual(second.view["revision"], 1)
                self.assertFalse(journal.exists())
                receipt = recovered_transport.write(run_id, "commands", payload)
                self.assertEqual(receipt["revision"], 1)
                self.assertEqual(recovered_transport.get_view(run_id)["revision"], 1)
            finally:
                recovered_transport.close()

    def test_actual_text_client_plays_a_complete_encounter(self):
        """A deterministic player uses only filtered views and legal previews."""
        from core.engine import GameError
        from tools.play_slice import choose

        with tempfile.TemporaryDirectory() as directory:
            transport = LocalTransport(str(Path(directory) / "game.sqlite3"))
            output = []
            client = Client(transport, output=output.append)
            try:
                client.create(templates=["fighter", "fighter", "fighter", "fighter"], seed=713)

                class VisibleAdapter:
                    def view(self, _state):
                        return deepcopy(client.view)

                    def preview(self, _state, command):
                        try:
                            return transport.preview(client.run_id, command)
                        except TransportError as exc:
                            raise GameError(exc.code, exc.message) from exc

                for _ in range(600):
                    if client.view["phase"] != "combat":
                        break
                    operation, command = choose(VisibleAdapter(), None)
                    old_revision = client.view["revision"]
                    if operation == "reaction":
                        client.handle("react " + command)
                    else:
                        parts = [command["type"], command["actor"]]
                        for field in ("ability", "target", "destination"):
                            if field in command:
                                parts.append(command[field])
                        line = " ".join(parts)
                        number = client.next_preview
                        client.handle("preview " + line)
                        client.handle("do %s" % number)
                    self.assertEqual(client.view["revision"], old_revision + 1, "\n".join(output[-3:]))
                self.assertEqual(client.view["phase"], "victory", "\n".join(output[-4:]))
                self.assertGreater(client.view["revision"], 5)
                client.handle("rest short")
                self.assertEqual(client.view["short_rests"], 1)
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
