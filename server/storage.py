"""Transactional SQLite saves, event history, and idempotent command receipts.

The repository only exposes the core's player view. Complete snapshots, random
state, and pending actions are private database records. Every successful write
commits the snapshot, events, and receipt together before returning to callers.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator


class RepositoryError(Exception):
    """An expected storage/protocol failure suitable for client display."""

    def __init__(self, code: str, message: str, status_code: int = 400,
                 revision: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.revision = revision


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RepositoryError("invalid_request", "请求必须包含合法的 JSON 数据。") from exc


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_id(command_id: str | None) -> str:
    if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
        raise RepositoryError("invalid_command_id", "command_id 必须是 1 至 128 字符的非空字符串。")
    return command_id


class Repository:
    """Thread-safe repository; separate instances also serialize through SQLite.

    Command IDs are unique across all runs and all write types in this database.
    Reusing an ID with an identical request returns its original receipt, even
    when the run has since advanced. Reusing it with different data is an error.
    """

    def __init__(self, database_path: str | Path = "data/game.sqlite3", engine=None):
        if engine is None:
            from core.engine import Engine
            engine = Engine()
        self.engine = engine
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(self.database_path, timeout=30,
                                           isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            self._closed = True
            raise

    def _initialize(self) -> None:
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RepositoryError("save_version_mismatch", "数据库格式版本不受当前程序支持。", 409)
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL REFERENCES runs(id),
                seq INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS receipts (
                command_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id),
                request_json TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            connection.execute("PRAGMA user_version = 1")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RepositoryError("repository_closed", "存档连接已关闭。", 503)

    def _load(self, run_id: str) -> dict:
        row = self._connection.execute("SELECT state_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RepositoryError("run_not_found", "找不到该周目。", 404)
        return json.loads(row["state_json"])

    def _call(self, method: str, *args):
        try:
            return getattr(self.engine, method)(*args)
        except Exception as exc:
            # GameError is exported by core.engine. Delayed import keeps fake
            # engines usable and local persistence independent of the HTTP stack.
            from core.engine import GameError
            if not isinstance(exc, GameError):
                raise
            conflict_codes = {"version_mismatch", "pending_reaction", "reaction_pending",
                              "no_pending_reaction", "invalid_phase", "not_defeated",
                              "not_in_combat"}
            status = 409 if exc.code in conflict_codes else 400
            raise RepositoryError(exc.code, exc.message, status) from exc

    def _replay(self, command_id: str, request: dict) -> dict | None:
        row = self._connection.execute(
            "SELECT request_json, receipt_json FROM receipts WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        if row["request_json"] != _json(request):
            raise RepositoryError("command_id_conflict", "此 command_id 已用于不同请求，请使用新的编号。", 409)
        return json.loads(row["receipt_json"])

    def _save_events(self, run_id: str, events: list[dict]) -> None:
        self._connection.executemany(
            "INSERT INTO events (run_id, seq, event_json) VALUES (?, ?, ?)",
            [(run_id, event["seq"], _json(event)) for event in events],
        )

    def _save_receipt(self, command_id: str, run_id: str, request: dict, receipt: dict) -> None:
        self._connection.execute(
            "INSERT INTO receipts (command_id, run_id, request_json, receipt_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (command_id, run_id, _json(request), _json(receipt), _timestamp()),
        )

    def create_run(self, name: str = "远征队", templates: list[str] | None = None,
                   seed: int | None = None, command_id: str | None = None) -> dict:
        command_id = _command_id(command_id)
        request = {"operation": "create", "name": name, "templates": templates, "seed": seed}
        _json(request)
        with self._transaction():
            receipt = self._replay(command_id, request)
            if receipt is not None:
                return receipt
            state = self._call("new_run", name, templates, seed)
            view = self._call("view", state)
            run_id = state["id"]
            events = state.get("event_log", [])
            now = _timestamp()
            self._connection.execute(
                "INSERT INTO runs (id, state_json, revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, _json(state), state["revision"], now, now),
            )
            self._save_events(run_id, events)
            receipt = {"command_id": command_id, "run_id": run_id, "revision": state["revision"],
                       "view": view, "events": events}
            self._save_receipt(command_id, run_id, request, receipt)
            return deepcopy(receipt)

    def get_view(self, run_id: str) -> dict:
        with self._lock:
            self._ensure_open()
            return self._call("view", self._load(run_id))

    def preview(self, run_id: str, command: dict) -> dict:
        _json(command)
        with self._lock:
            self._ensure_open()
            state = self._load(run_id)
            result = self._call("preview", deepcopy(state), command)
            return {**result, "revision": state["revision"]}

    def options(self, run_id: str, command: dict) -> dict:
        """Read menu candidates without persisting state or consuming randomness."""
        _json(command)
        with self._lock:
            self._ensure_open()
            state = self._load(run_id)
            result = self._call("options", deepcopy(state), deepcopy(command))
            return {**result, "revision": state["revision"]}

    def _write(self, run_id: str, command_id: str, expected_revision: int,
               operation: str, payload: dict) -> dict:
        command_id = _command_id(command_id)
        request = {"operation": operation, "run_id": run_id,
                   "expected_revision": expected_revision, **payload}
        _json(request)
        with self._transaction():
            receipt = self._replay(command_id, request)
            if receipt is not None:
                return receipt
            state = self._load(run_id)
            if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
                raise RepositoryError("invalid_revision", "expected_revision 必须是非负整数。")
            if expected_revision != state["revision"]:
                raise RepositoryError("revision_conflict", "局势已变化，请刷新后重新预览。", 409,
                                      revision=state["revision"])
            args = [deepcopy(state)]
            if operation == "execute":
                args.append(payload["command"])
            elif operation == "react":
                args.append(payload["choice"])
            new_state, events = self._call(operation, *args)
            if new_state["id"] != run_id or new_state["revision"] != state["revision"] + 1:
                raise RuntimeError("Core write changed the run identity or returned an invalid revision")
            view = self._call("view", new_state)
            self._connection.execute(
                "UPDATE runs SET state_json = ?, revision = ?, updated_at = ? WHERE id = ?",
                (_json(new_state), new_state["revision"], _timestamp(), run_id),
            )
            self._save_events(run_id, events)
            receipt = {"command_id": command_id, "revision": new_state["revision"],
                       "view": view, "events": events}
            self._save_receipt(command_id, run_id, request, receipt)
            return deepcopy(receipt)

    def submit(self, run_id: str, command_id: str, expected_revision: int, command: dict) -> dict:
        return self._write(run_id, command_id, expected_revision, "execute", {"command": command})

    def react(self, run_id: str, command_id: str, expected_revision: int, choice: str) -> dict:
        return self._write(run_id, command_id, expected_revision, "react", {"choice": choice})

    def retry(self, run_id: str, command_id: str, expected_revision: int) -> dict:
        return self._write(run_id, command_id, expected_revision, "retry", {})

    def events(self, run_id: str, after: int = 0, limit: int = 20) -> dict:
        if (not isinstance(after, int) or isinstance(after, bool) or after < 0
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200):
            raise RepositoryError("invalid_pagination", "after 必须非负，limit 必须在 1 至 200 之间。")
        with self._lock:
            self._ensure_open()
            self._load(run_id)
            rows = self._connection.execute(
                "SELECT event_json FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
            events = [json.loads(row["event_json"]) for row in rows]
            return {"events": events, "next_after": events[-1]["seq"] if events else after}

    def list_runs(self, limit: int = 20) -> list[dict]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise RepositoryError("invalid_pagination", "limit 必须在 1 至 200 之间。")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                "SELECT id, state_json, revision, updated_at FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"id": row["id"], "name": json.loads(row["state_json"])["name"],
                     "revision": row["revision"], "updated_at": row["updated_at"]} for row in rows]

    def backup(self, destination: str | Path) -> Path:
        with self._lock:
            self._ensure_open()
            return _backup_connection(self._connection, destination)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _backup_connection(source: sqlite3.Connection, destination: str | Path) -> Path:
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively so an existing save/backup is never overwritten.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Create a consistent backup, including committed WAL data, without overwrite."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as connection:
        return _backup_connection(connection, destination)

