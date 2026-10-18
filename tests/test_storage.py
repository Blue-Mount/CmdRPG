"""Persistence contract tests: faults, retransmission, concurrency, and recovery."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from saves.storage import Repository, RepositoryError, backup_database


class SnapshotEngine:
    """Small deterministic engine isolates persistence guarantees from combat."""

    def new_run(self, name, templates, seed):
        return {"id": str(uuid.uuid4()), "name": name, "revision": 0, "phase": "combat",
                "seed": seed, "rng_cursor": 0, "pending": None,
                "content_version": "test-1", "rules_version": "test-1", "save_version": 1,
                "event_log": [{"seq": 1, "kind": "start", "text": "开始", "details": {}}]}

    def view(self, state):
        return {key: deepcopy(state[key]) for key in
                ("id", "name", "revision", "phase", "pending", "content_version")}

    def preview(self, state, command):
        # A careless engine must not alter the saved snapshot from a read.
        state["rng_cursor"] += 999
        return {"command": command, "cost": {}, "description": "预览", "warnings": [], "targets": []}

    def execute(self, state, command):
        state = deepcopy(state)
        state["revision"] += 1
        state["rng_cursor"] += 1
        if command["type"] == "wait":
            state["pending"] = {"rolled": state["rng_cursor"], "actor": "P1"}
            state["phase"] = "reaction"
        elif command["type"] == "defeat":
            state["phase"] = "defeat"
        event = {"seq": len(state["event_log"]) + 1, "kind": "command", "text": "执行", "details": {}}
        state["event_log"].append(event)
        return state, [event]

    def react(self, state, choice):
        state = deepcopy(state)
        state["pending"] = None
        state["phase"] = "combat"
        state["revision"] += 1
        event = {"seq": len(state["event_log"]) + 1, "kind": "reaction", "text": choice, "details": {}}
        state["event_log"].append(event)
        return state, [event]

    def retry(self, state):
        state = deepcopy(state)
        state["phase"] = "combat"
        state["rng_cursor"] = 0
        state["revision"] += 1
        event = {"seq": len(state["event_log"]) + 1, "kind": "retry", "text": "重试", "details": {}}
        state["event_log"].append(event)
        return state, [event]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "game.sqlite3"
        self.repository = Repository(self.path, SnapshotEngine())
        self.addCleanup(self.repository.close)
        self.created = self.repository.create_run(seed=41, command_id="new-1")
        self.run_id = self.created["run_id"]

    def saved_state(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return json.loads(connection.execute("SELECT state_json FROM runs WHERE id = ?",
                                                 (self.run_id,)).fetchone()[0])

    def test_create_is_idempotent_and_id_is_global(self):
        replay = self.repository.create_run(seed=41, command_id="new-1")
        self.assertEqual(self.created, replay)
        self.assertEqual(len(self.repository.list_runs()), 1)
        with self.assertRaises(RepositoryError) as changed:
            self.repository.create_run(seed=42, command_id="new-1")
        self.assertEqual(changed.exception.code, "command_id_conflict")
        with self.assertRaises(RepositoryError):
            self.repository.submit(self.run_id, "new-1", 0, {"type": "tick"})

    def test_original_receipt_replayed_before_revision_check(self):
        first = self.repository.submit(self.run_id, "step-1", 0, {"type": "tick"})
        self.repository.submit(self.run_id, "step-2", 1, {"type": "tick"})
        self.assertEqual(self.repository.submit(self.run_id, "step-1", 0, {"type": "tick"}), first)
        self.assertEqual(self.repository.get_view(self.run_id)["revision"], 2)
        with self.assertRaises(RepositoryError) as changed:
            self.repository.submit(self.run_id, "step-1", 0, {"type": "wait"})
        self.assertEqual(changed.exception.code, "command_id_conflict")
        with self.assertRaises(RepositoryError) as stale:
            self.repository.submit(self.run_id, "stale", 0, {"type": "tick"})
        self.assertEqual(stale.exception.revision, 2)
        self.assertEqual(stale.exception.status_code, 409)

    def test_preview_and_player_output_do_not_mutate_or_reveal_snapshot(self):
        before = self.saved_state()
        preview = self.repository.preview(self.run_id, {"type": "tick"})
        self.assertEqual(preview["revision"], 0)
        self.assertEqual(self.saved_state(), before)
        for payload in (self.created, self.repository.get_view(self.run_id), self.repository.events(self.run_id)):
            self.assertNotIn('"seed"', json.dumps(payload))
            self.assertNotIn('"rng_cursor"', json.dumps(payload))

    def test_crash_before_receipt_rolls_back_state_and_events(self):
        before = self.saved_state()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts
                                WHEN NEW.command_id = 'crash' BEGIN
                                SELECT RAISE(ABORT, 'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.submit(self.run_id, "crash", 0, {"type": "tick"})
        self.assertEqual(self.saved_state(), before)
        self.assertEqual(len(self.repository.events(self.run_id)["events"]), 1)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DROP TRIGGER fail_receipt")
        result = self.repository.submit(self.run_id, "crash", 0, {"type": "tick"})
        self.assertEqual(result["revision"], 1)

    def test_pending_reopens_without_reroll_and_receipts_survive_restart(self):
        pending = self.repository.submit(self.run_id, "pending", 0, {"type": "wait"})
        cursor = self.saved_state()["rng_cursor"]
        self.repository.close()
        with Repository(self.path, SnapshotEngine()) as restarted:
            self.assertEqual(restarted.get_view(self.run_id)["pending"], pending["view"]["pending"])
            self.assertEqual(restarted.submit(self.run_id, "pending", 0, {"type": "wait"}), pending)
            result = restarted.react(self.run_id, "answer", 1, "accept")
            self.assertIsNone(result["view"]["pending"])
            self.assertEqual(self.saved_state()["rng_cursor"], cursor)

    def test_two_connections_cannot_overwrite_same_revision(self):
        other = Repository(self.path, SnapshotEngine())
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)

        def submit(repository, command_id):
            barrier.wait(timeout=5)
            try:
                return repository.submit(self.run_id, command_id, 0, {"type": "tick"})
            except RepositoryError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, self.repository, "a")
            second = pool.submit(submit, other, "b")
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        conflict = next(result for result in results if isinstance(result, RepositoryError))
        self.assertEqual(conflict.code, "revision_conflict")
        self.assertEqual(self.saved_state()["revision"], 1)
        self.assertEqual(self.saved_state()["rng_cursor"], 1)

    def test_backup_restores_snapshot_and_receipts_with_wal_open(self):
        pending = self.repository.submit(self.run_id, "pending", 0, {"type": "wait"})
        destination = Path(self.directory.name) / "backup.sqlite3"
        self.assertEqual(backup_database(self.path, destination), destination.resolve())
        self.repository.react(self.run_id, "answer", 1, "accept")
        with Repository(destination, SnapshotEngine()) as restored:
            self.assertEqual(restored.get_view(self.run_id), pending["view"])
            self.assertEqual(restored.submit(self.run_id, "pending", 0, {"type": "wait"}), pending)
            self.assertEqual(len(restored.events(self.run_id)["events"]), 2)
        with self.assertRaises(FileExistsError):
            backup_database(self.path, destination)
        with self.assertRaises(FileExistsError):
            self.repository.backup(self.path)
        self.assertEqual(self.repository.get_view(self.run_id)["revision"], 2)

    def test_event_pagination_and_retry_receipt(self):
        self.repository.submit(self.run_id, "defeat", 0, {"type": "defeat"})
        receipt = self.repository.retry(self.run_id, "retry", 1)
        self.assertEqual(self.repository.retry(self.run_id, "retry", 1), receipt)
        first = self.repository.events(self.run_id, limit=2)
        self.assertEqual([event["seq"] for event in first["events"]], [1, 2])
        second = self.repository.events(self.run_id, after=first["next_after"])
        self.assertEqual([event["seq"] for event in second["events"]], [3])
        self.assertEqual(self.repository.events(self.run_id, after=3)["next_after"], 3)
        with self.assertRaises(RepositoryError):
            self.repository.events(self.run_id, limit=201)
        with self.assertRaises(RepositoryError) as missing:
            self.repository.get_view("does-not-exist")
        self.assertEqual(missing.exception.status_code, 404)


class CoreStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "game.sqlite3"
        self.repository = Repository(self.path)
        self.addCleanup(self.repository.close)
        self.created = self.repository.create_run(seed=17, command_id="new")
        self.run_id = self.created["run_id"]

    def test_invalid_core_command_does_not_commit_or_reserve_receipt(self):
        before = self.repository.get_view(self.run_id)
        with self.assertRaises(RepositoryError) as invalid:
            self.repository.submit(self.run_id, "attempt", 0, {"type": "end", "actor": "P99"})
        self.assertEqual(invalid.exception.status_code, 400)
        self.assertEqual(self.repository.get_view(self.run_id), before)
        self.assertEqual(self.repository.events(self.run_id)["events"], self.created["events"])
        command = {"type": "end", "actor": before["active"][0]}
        receipt = self.repository.submit(self.run_id, "attempt", 0, command)
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(self.repository.submit(self.run_id, "attempt", 0, command), receipt)

    def test_incompatible_content_is_rejected_without_rewriting_save(self):
        self.repository.engine.content["version"] = "incompatible-test-version"
        with self.assertRaises(RepositoryError) as mismatch:
            self.repository.get_view(self.run_id)
        self.assertEqual(mismatch.exception.code, "version_mismatch")
        self.assertEqual(mismatch.exception.status_code, 409)
        self.repository.close()
        with Repository(self.path) as restored:
            self.assertEqual(restored.get_view(self.run_id), self.created["view"])


class CorePendingRecoveryTests(unittest.TestCase):
    def test_real_reaction_resume_matches_uninterrupted_resolution(self):
        from core.engine import Engine

        class PendingEngine(Engine):
            def new_run(self, name, templates, seed):
                state = super().new_run(name, templates, seed)
                # A controlled mid-action fixture uses the real combat helpers:
                # a completed attack followed by movement leaving P1's reach.
                state["map"]["walls"] = []
                state["map"]["heights"] = {}
                for actor_id, position in {"P1": "B2", "P2": "A1", "P3": "C1", "P4": "D1",
                                           "E1": "B3", "E2": "P12", "E3": "D2"}.items():
                    state["actors"][actor_id]["position"] = position
                state["actors"]["P1"]["ended"] = False
                state["actors"]["P1"]["budgets"] = {"action": 1, "bonus": 1, "reaction": 1, "movement": 9}
                state["actors"]["E1"]["budgets"]["movement"] = 9
                state["active_group"] = ["P1"]
                self._weapon_attack(state, state["actors"]["E3"], state["actors"]["P4"])
                self._continue_move(state, state["actors"]["E1"], ["B4"])
                assert state["pending"] is not None
                return state

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.sqlite3"
            engine = PendingEngine()
            with Repository(path, engine) as repository:
                created = repository.create_run(seed=17, command_id="pending-create")
                run_id = created["run_id"]
                with closing(sqlite3.connect(path)) as connection:
                    pending_state = json.loads(connection.execute("SELECT state_json FROM runs WHERE id = ?",
                                                                  (run_id,)).fetchone()[0])
                expected_state, expected_events = engine.react(pending_state, "accept")
            with Repository(path) as restarted:
                self.assertEqual(restarted.get_view(run_id)["pending"], created["view"]["pending"])
                result = restarted.react(run_id, "reaction", 0, "accept")
                self.assertEqual(result["view"], engine.view(expected_state))
                self.assertEqual(result["events"], expected_events)
                self.assertEqual(restarted.react(run_id, "reaction", 0, "accept"), result)
                with closing(sqlite3.connect(path)) as connection:
                    resumed_state = json.loads(connection.execute("SELECT state_json FROM runs WHERE id = ?",
                                                                  (run_id,)).fetchone()[0])
                self.assertEqual(resumed_state["rng_state"], expected_state["rng_state"])
                self.assertEqual(resumed_state["actors"]["E1"]["position"], "B4")


if __name__ == "__main__":
    unittest.main()
