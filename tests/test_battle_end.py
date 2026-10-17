"""The completed slice leads to a useful, safe whole-party settlement menu."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from client.cli import Client, read_json
from client.transport import LocalTransport, TransportError
from core.engine import Engine, GameError
from server.storage import Repository, RepositoryError
from tools.play_slice import choose


class CompletedScenario(Engine):
    """Start the first run at victory; reopening creates a normal new battle."""

    def __init__(self):
        super().__init__()
        self.creations = 0

    def new_run(self, *args, **kwargs):
        state = super().new_run(*args, **kwargs)
        self.creations += 1
        if self.creations > 1:
            return state
        for member in state["actors"].values():
            if member["side"] == "enemy":
                member.update(hp=0, conditions=["dead"])
            else:
                member.update(hp=1, ended=True)
                member["slots"] = {level: 0 for level in member["slots"]}
                member["resources"].update({key: 0 for key in member["max_resources"]})
        state["actors"]["P2"].update(hp=0, conditions=["dead"])
        state["actors"]["P4"].update(hp=0, conditions=["downed"])
        state.update(phase="victory", outcome="victory", active_group=[], pending=None)
        return state


@pytest.fixture
def completed(tmp_path):
    transport = LocalTransport(repository=Repository(tmp_path / "completed.sqlite3", engine=CompletedScenario()))
    output = []
    client = Client(transport, output=output.append)
    client.create("归来的队伍", seed=42)
    yield client, transport, output
    transport.close()


def test_victory_menu_explains_what_to_do_and_loot_is_read_only(completed):
    client, transport, output = completed
    assert client.ui.stage == "settlement"
    assert "战后" in client.ui.context()
    display = "\n".join(output)
    assert "全队短休" in display
    assert "新周目" in display
    assert "战利品" in display
    assert "选择角色" not in client.ui.context()
    before = transport.repository._load(client.run_id)
    output.clear()
    client.handle("6")
    assert client.ui.stage == "settlement"
    assert "未配置" in "\n".join(output) or "没有配置" in "\n".join(output)
    assert transport.repository._load(client.run_id) == before
    client.handle("0")
    assert client.ui.stage == "settlement"
    assert transport.repository._load(client.run_id) == before


def test_whole_party_rest_previews_cancels_and_applies_once(completed):
    client, transport, output = completed
    before = transport.repository._load(client.run_id)
    output.clear()
    client.handle("1")
    assert client.ui.stage == "confirm"
    assert client.preview_result["cost"] == {"short_rests": 1}
    assert client.preview_result["targets"] == ["P1", "P3", "P4"]
    effects = {effect["actor"]: effect for effect in client.preview_result["rest_effects"]}
    display = "\n".join(output)
    assert all(a["name"] in display for a in client.view["party"] if a["id"] in effects)
    assert "法术位" in display
    assert transport.repository._load(client.run_id) == before
    client.handle("1")  # Digits cannot execute this confirmation.
    assert transport.repository._load(client.run_id) == before
    client.handle("0")
    assert client.ui.stage == "settlement"
    assert not client.previews
    assert transport.repository._load(client.run_id) == before
    client.handle("1")
    client.handle("c")
    after = transport.repository._load(client.run_id)
    assert client.ui.stage == "settlement"
    assert after["revision"] == before["revision"] + 1
    assert after["short_rests"] == before["short_rests"] - 1
    for aid in ("P1", "P3", "P4"):
        original, changed = before["actors"][aid], after["actors"][aid]
        assert changed["hp"] == min(original["max_hp"], original["hp"] + (original["max_hp"] + 1) // 2)
        assert changed["hp"] == effects[aid]["hp_after"]
        assert changed["hp"] - original["hp"] == effects[aid]["healing"]
        assert changed["slots"] == original["slots"]
        for key, maximum in original["max_resources"].items():
            assert changed["resources"][key] == (original["resources"][key] if key=="arcane_recovery" else maximum)
        assert changed["resources"]["potions"] == original["resources"]["potions"]
        assert "downed" not in changed["conditions"]
    assert after["actors"]["P2"] == before["actors"]["P2"]
    # An extra confirmation cannot spend another short rest.
    client.handle("c")
    assert transport.repository._load(client.run_id) == after
    client.handle("1")
    client.handle("c")
    assert client.view["short_rests"] == 0
    revision = client.view["revision"]
    output.clear()
    client.handle("1")
    assert client.ui.stage == "settlement"
    assert not client.previews
    assert client.view["revision"] == revision
    assert "不可用" in "\n".join(output)


def test_expired_whole_party_rest_preview_does_not_spend_twice(completed):
    client, transport, output = completed
    client.handle("1")
    transport.write(client.run_id, "commands", {
        "command_id": "other-terminal-rest", "expected_revision": client.view["revision"],
        "command": {"type": "rest", "kind": "short"},
    })
    committed = transport.repository._load(client.run_id)
    client.handle("c")
    assert client.ui.stage == "settlement"
    assert client.view["short_rests"] == 1
    assert not client.previews
    assert transport.repository._load(client.run_id) == committed
    assert any("过期" in line for line in output)


def test_new_run_requires_confirmation_and_preserves_old_save_and_classes(completed):
    client, transport, output = completed
    old_id = client.run_id
    old_state = transport.repository._load(old_id)
    old_templates = [actor["template"] for actor in client.view["party"]]
    client.handle("3")
    assert client.ui.stage == "confirm"
    assert client.run_id == old_id
    client.handle("1")
    assert client.run_id == old_id
    client.handle("0")
    assert client.ui.stage == "settlement"
    assert transport.repository._load(old_id) == old_state
    client.handle("3")
    client.handle("c")
    assert client.run_id != old_id
    assert client.ui.stage == "home"
    assert client.view["phase"] == "combat"
    assert client.view["revision"] == 0
    assert client.view["name"] == old_state["name"]
    assert [actor["template"] for actor in client.view["party"]] == old_templates
    assert transport.repository._load(old_id) == old_state
    assert old_id in "\n".join(output)


def test_new_run_preserves_duplicate_classes(tmp_path):
    transport = LocalTransport(repository=Repository(tmp_path / "duplicates.sqlite3", engine=CompletedScenario()))
    try:
        client = Client(transport, output=lambda _text: None)
        templates = ["fighter", "fighter", "monk", "monk"]
        client.create(templates=templates, seed=713)
        client.handle("3")
        client.handle("c")
        assert client.view["phase"] == "combat"
        assert [member["template"] for member in client.view["party"]] == templates
    finally:
        transport.close()


def test_new_run_confirmation_expires_when_the_old_run_changes(completed):
    client, transport, output = completed
    old_id = client.run_id
    client.handle("3")
    transport.write(old_id, "commands", {
        "command_id": "other-terminal-before-new", "expected_revision": client.view["revision"],
        "command": {"type": "rest", "kind": "short"},
    })
    client.handle("c")
    assert client.run_id == old_id
    assert client.ui.stage == "settlement"
    assert client.view["short_rests"] == 1
    assert transport.repository.engine.creations == 1
    assert any("过期" in line for line in output)


def test_new_run_lost_receipt_replays_original_creation_and_updates_config(completed, tmp_path):
    first, transport, _ = completed
    old_id = first.run_id
    scope = {"mode": "local", "db": str(tmp_path / "completed.sqlite3")}
    journal = tmp_path / "pending.json"
    runs = []
    output = []
    client = Client(transport, old_id, deepcopy(first.view), output=output.append,
                    journal_path=journal, scope=scope, on_run=runs.append)
    original_create = transport.create_run
    calls, receipts = [], []

    def drop_first_creation(payload):
        calls.append(deepcopy(payload))
        receipt = original_create(payload)
        receipts.append(receipt)
        if len(calls) == 1:
            raise TransportError("connection_lost", "连接中断", retryable=True)
        return receipt

    transport.create_run = drop_first_creation
    client.handle("3")
    client.handle("c")
    assert client.pending and journal.exists()
    assert client.ui.stage == "recovery"
    original = read_json(journal)
    assert original["operation"] == "create"
    restored = Client(transport, old_id, output=output.append, journal_path=journal,
                      scope=scope, on_run=runs.append)
    restored.load_pending()
    restored.handle("9")
    assert calls[0] == calls[1]
    assert receipts[0]["run_id"] == receipts[1]["run_id"]
    assert restored.run_id != old_id
    assert runs == [restored.run_id]
    assert restored.view["phase"] == "combat"
    assert restored.ui.stage == "home"
    assert not restored.pending and not journal.exists()
    assert transport.get_view(old_id)["phase"] == "victory"


def test_stdlib_terminal_settlement_rest_and_new_run(tmp_path):
    """Resume a real completed battle through the separate standard-library CLI."""
    database, config = tmp_path / "terminal.sqlite3", tmp_path / "terminal.json"
    with Repository(database) as repository:
        old_id = repository.create_run(seed=42, command_id="settlement-create")["run_id"]

        class VisibleAdapter:
            def view(self, _state):
                return repository.get_view(old_id)

            def preview(self, _state, command):
                try:
                    return repository.preview(old_id, command)
                except RepositoryError as exc:
                    raise GameError(exc.code, exc.message) from exc

        for index in range(600):
            view = repository.get_view(old_id)
            if view["phase"] != "combat":
                break
            operation, payload = choose(VisibleAdapter(), None)
            if operation == "reaction":
                repository.react(old_id, str(index), view["revision"], payload)
            else:
                repository.submit(old_id, str(index), view["revision"], payload)
        assert repository.get_view(old_id)["outcome"] == "victory"
        old_state = repository._load(old_id)
    environment = dict(os.environ, PYTHONUTF8="1")
    environment.pop("CMDRPG_URL", None)
    result = subprocess.run(
        [sys.executable, "-S", "-u", "-m", "client", "--run", old_id,
         "--db", str(database), "--config", str(config)],
        input="6\n0\n1\n0\n1\nc\n3\n0\n3\nc\nq\n", text=True,
        encoding="utf-8", capture_output=True, env=environment,
        cwd=Path(__file__).resolve().parents[1], timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "战后" in result.stdout and "全队短休" in result.stdout
    assert "未配置" in result.stdout or "没有配置" in result.stdout
    new_id = read_json(config)["last"]["run_id"]
    assert new_id != old_id
    with Repository(database) as repository:
        old = repository._load(old_id)
        assert old["phase"] == "victory"
        assert old["short_rests"] == 1
        assert old["revision"] == old_state["revision"] + 1
        assert repository.get_view(new_id)["phase"] == "combat"
    assert not config.with_name("terminal.pending.json").exists()
    (tmp_path / "settlement-transcript.txt").write_text(result.stdout, encoding="utf-8")
