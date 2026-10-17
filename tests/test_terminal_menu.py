"""Exercise the player menus against real rules and persisted game states."""

from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
from queue import Queue, Empty
import re
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from client.cli import Client, main, read_json
from client.transport import LocalTransport, TransportError
from core.engine import Engine, GameError
from server.storage import Repository, RepositoryError
from tools.play_slice import choose


def actor(client, actor_id):
    return next(a for a in client.view["party"] if a["id"] == actor_id)


def action_number(client, command):
    return next(str(index) for index, entry in enumerate(actor(client, command["actor"])["menu_actions"], 1)
                if entry["command"]["type"] == command["type"]
                and entry["command"].get("ability") == command.get("ability"))


def select_actor(client, actor_id):
    while client.ui.stage != "home":
        assert client.handle("0")
    index = next(i for i, a in enumerate(client.view["party"], 1) if a["id"] == actor_id)
    client.handle(str(index))
    assert client.ui.stage == "actions"
    assert client.ui.selected == actor_id


def select_action(client, command):
    if client.ui.selected != command["actor"] or client.ui.stage != "actions":
        select_actor(client, command["actor"])
    client.handle(action_number(client, command))


def preview_command(client, command):
    select_action(client, command)
    if command["type"] == "move":
        assert client.ui.stage == "move"
        client.handle(command["destination"])
    elif "target" in command or "targets" in command:
        assert client.ui.stage == "target"
        targets = command.get("targets", [command.get("target")])
        indices = [str(next(i for i, entry in enumerate(client.ui.entries, 1) if entry["target"] == target))
                   for target in targets]
        client.handle(",".join(indices))
    assert client.ui.stage == "confirm"


@pytest.fixture
def session(tmp_path):
    transport = LocalTransport(tmp_path / "game.sqlite3")
    output = []
    client = Client(transport, output=output.append)
    client.create(seed=42)
    yield client, transport, output
    transport.close()


class OpenScenario(Engine):
    """Controlled initial board; subsequent menu actions use production rules."""

    def __init__(self, *, pending=False, empty_slots=False, defeated=False):
        super().__init__()
        self.with_pending = pending
        self.empty_slots = empty_slots
        self.defeated = defeated

    def new_run(self, *args, **kwargs):
        state = super().new_run(*args, **kwargs)
        state["map"].update(walls=[], heights={}, difficult=[], hazards={})
        positions = {"P1": "B4", "P2": "B5", "P3": "B6", "P4": "B7",
                     "E1": "C4", "E2": "L2", "E3": "N2"}
        for aid, member in state["actors"].items():
            member.update(position=positions[aid], ended=False,
                          budgets={"action": 1, "bonus": 1, "reaction": 1, "movement": 9})
        state["actors"]["P1"]["hp"] = 2
        state["actors"]["E1"].update(hp=100, max_hp=100)
        state.update(turn_order=["P1", "P2", "P3", "P4", "E1", "E2", "E3"],
                     active_group=["P1", "P2", "P3", "P4"], cursor=4)
        if self.empty_slots:
            state["actors"]["P3"]["slots"] = {"1": 0}
        if self.with_pending:
            self._continue_move(state, state["actors"]["E1"], ["D4", "E4"])
            assert state["pending"]["reactor"] == "P1"
        if self.defeated:
            for member in state["actors"].values():
                if member["side"] == "party":
                    member.update(hp=0, conditions=["dead"], ended=True)
            state.update(phase="defeated", outcome="defeated")
        return state


def scenario_session(tmp_path, **scenario):
    transport = LocalTransport(repository=Repository(tmp_path / "scenario.sqlite3", engine=OpenScenario(**scenario)))
    output = []
    client = Client(transport, output=output.append)
    client.create(seed=42)
    return client, transport, output


def test_home_reveals_every_characters_full_chinese_skill_list(session):
    client, _, output = session
    assert client.ui.stage == "home"
    homepage = "\n".join(output)
    assert all(name in homepage for name in ("战士", "武僧", "法师", "牧师"))
    assert "可操作" in homepage
    for actor_id in ("P1", "P2", "P3", "P4"):
        output.clear()
        select_actor(client, actor_id)
        display = "\n".join(output)
        for entry in actor(client, actor_id)["menu_actions"]:
            assert entry["name"] in display
        assert "成本" in display
        assert "剩余" in display
        assert "选择行动或技能" in client.ui.context()
        for ability in actor(client, actor_id)["abilities"]:
            assert ability["id"] not in display


def test_back_cancel_and_numbers_are_pure_reads(session):
    client, transport, _ = session
    before = transport.repository._load(client.run_id)
    preview_command(client, {"type": "move", "actor": "P3", "destination": "D6"})
    client.handle("1")
    assert client.ui.stage == "confirm"
    assert transport.repository._load(client.run_id) == before
    client.handle("0")
    assert client.ui.stage == "move"
    assert not client.previews
    client.handle("0")
    assert client.ui.stage == "actions"
    client.handle("0")
    assert client.ui.stage == "home"
    assert client.handle("0")
    assert client.handle("q") is False
    assert transport.repository._load(client.run_id) == before


def test_blocked_target_and_unavailable_skill_remain_visible(session):
    client, transport, output = session
    before = transport.repository._load(client.run_id)
    select_action(client, {"type": "cast", "actor": "P3", "ability": "fire_bolt"})
    index, blocked = next((i, entry) for i, entry in enumerate(client.ui.entries, 1)
                          if entry["target"] == "E1")
    assert not blocked["available"]
    assert "遮挡" in blocked["reason"]
    client.handle(str(index))
    assert client.ui.stage == "target"
    assert not client.previews
    assert any("不可用" in line for line in output)
    select_actor(client, "P2")
    client.handle(action_number(client, {"type": "ability", "actor": "P2", "ability": "unarmed_strike"}))
    assert client.ui.stage == "actions"
    assert any("需要先攻击" in line for line in output)
    assert transport.repository._load(client.run_id) == before


def test_move_menu_path_cost_and_remaining_budget_match_execution(session):
    client, transport, _ = session
    before_budget = actor(client, "P3")["budgets"]["movement"]
    select_action(client, {"type": "move", "actor": "P3"})
    assert "D6" in client.ui.options["reachable"]
    assert client.ui.entries
    unavailable = next((i for i, entry in enumerate(client.ui.entries, 1) if not entry["available"]), None)
    if unavailable:
        client.handle(str(unavailable))
        assert client.ui.stage == "move"
        assert client.view["revision"] == 0
    client.handle("D6")
    preview = deepcopy(client.preview_result)
    assert preview["path"][0] == actor(client, "P3")["position"]
    assert preview["path"][-1] == "D6"
    client.handle("c")
    assert client.view["revision"] == 1
    assert actor(client, "P3")["position"] == "D6"
    assert actor(client, "P3")["budgets"]["movement"] == before_budget - preview["cost"]["movement"]
    assert client.ui.selected == "P3"
    assert transport.get_view(client.run_id) == client.view


def test_confirm_stale_preview_never_submits_another_action(session):
    client, transport, output = session
    preview_command(client, {"type": "move", "actor": "P3", "destination": "D6"})
    transport.write(client.run_id, "commands", {"command_id": "other-session", "expected_revision": 0,
                    "command": {"type": "dodge", "actor": "P2"}})
    client.handle("c")
    assert client.view["revision"] == 1
    assert actor(client, "P3")["position"] == "B8"
    assert not client.previews
    assert client.ui.stage == "actions"
    assert any("过期" in line for line in output)


def test_stale_target_list_requires_reselection(session):
    client, transport, output = session
    select_action(client, {"type": "cast", "actor": "P3", "ability": "fire_bolt"})
    transport.write(client.run_id, "commands", {"command_id": "other-target-session", "expected_revision": 0,
                    "command": {"type": "dodge", "actor": "P2"}})
    client.handle("1")
    assert client.ui.stage == "actions"
    assert client.view["revision"] == 1
    assert not client.previews
    assert any("过期" in line for line in output)


def test_actions_keep_selection_and_end_selects_next_actor(session):
    client, _, _ = session
    preview_command(client, {"type": "dodge", "actor": "P2"})
    client.handle("c")
    assert client.ui.selected == "P2"
    assert client.ui.stage == "actions"
    preview_command(client, {"type": "end", "actor": "P2"})
    assert client.view["revision"] == 1
    client.handle("c")
    assert client.view["revision"] == 2
    assert client.ui.selected == "P3"
    assert client.ui.stage == "actions"


def test_rules_menu_expands_details_after_compact_action_without_writing(session):
    client, transport, output = session
    preview_command(client, {"type": "move", "actor": "P3", "destination": "D6"})
    client.handle("c")
    assert client.ui.compact
    before = transport.repository._load(client.run_id)
    output.clear()
    number = next(n for n, value in client.menu.items() if value == ("read", "rules"))
    client.handle(number)
    assert "技能与规则说明" in "\n".join(output)
    assert actor(client, "P3")["abilities"][0]["description"] in "\n".join(output)
    assert transport.repository._load(client.run_id) == before


def test_healing_and_multi_target_bless_only_use_numbers(tmp_path):
    client, transport, output = scenario_session(tmp_path)
    try:
        preview_command(client, {"type": "cast", "actor": "P4", "ability": "healing_word", "target": "P1"})
        assert "治愈真言" in client.ui.context()
        assert "确认执行" in client.ui.context()
        client.handle("c")
        assert actor(client, "P1")["hp"] > 2
        assert actor(client, "P4")["slots"]["1"] == 1
        select_action(client, {"type": "cast", "actor": "P4", "ability": "bless"})
        client.handle("1,1")
        assert client.ui.stage == "target"
        assert client.view["revision"] == 1
        client.handle("1,2,3,4")
        assert client.ui.stage == "target"
        client.handle("1,2,3")
        assert client.ui.stage == "confirm"
        assert client.preview_result["targets"] == ["P1", "P2", "P3"]
        client.handle("c")
        assert client.view["revision"] == 2
        assert actor(client, "P4")["slots"]["1"] == 0
        select_action(client, {"type": "cast", "actor": "P4", "ability": "cure_wounds"})
        assert client.ui.stage == "actions"
        assert any("不可用" in text for text in output)
    finally:
        transport.close()


def test_no_slots_skills_are_shown_and_cannot_be_selected(tmp_path):
    client, transport, output = scenario_session(tmp_path, empty_slots=True)
    try:
        select_actor(client, "P3")
        spell = {"type": "cast", "actor": "P3", "ability": "magic_missile"}
        client.handle(action_number(client, spell))
        assert client.ui.stage == "actions"
        assert client.view["revision"] == 0
        assert any("魔法飞弹" in text and "法术位不足" in text for text in output)
    finally:
        transport.close()


def test_defeat_menu_retry_requires_confirmation_and_restores_battle(tmp_path):
    client, transport, _ = scenario_session(tmp_path, defeated=True)
    try:
        assert client.ui.stage == "home"
        client.handle("9")
        assert client.ui.stage == "confirm"
        assert client.view["phase"] == "defeated"
        client.handle("0")
        assert client.ui.stage == "home"
        assert client.view["revision"] == 0
        client.handle("9")
        client.handle("c")
        assert client.view["phase"] == "combat"
        assert client.view["revision"] == 1
        assert client.view["active"]
        assert all(a["hp"] > 0 for a in client.view["party"])
    finally:
        transport.close()


@pytest.mark.parametrize("number,choice", [("1", "accept"), ("2", "decline")])
def test_reaction_resumes_with_selection_preview_cancel_and_confirmation(tmp_path, number, choice):
    client, transport, _ = scenario_session(tmp_path, pending=True)
    try:
        saved = transport.repository._load(client.run_id)
        # A fresh client recovers the same saved reaction window.
        recovered = Client(transport, client.run_id, output=lambda _text: None)
        recovered.refresh()
        assert recovered.ui.stage == "reaction"
        assert recovered.view["pending"] == client.view["pending"]
        recovered.handle(number)
        assert recovered.ui.stage == "confirm"
        assert recovered.view["revision"] == 0
        assert "P1" in recovered.ui.context()
        recovered.handle("0")
        assert recovered.ui.stage == "reaction"
        assert transport.repository._load(client.run_id) == saved
        recovered.handle(number)
        recovered.handle("c")
        changed = transport.repository._load(client.run_id)
        assert changed["revision"] == 1
        assert changed["pending"] is None
        assert changed["actors"]["E1"]["position"] == "E4"
        assert changed["actors"]["P1"]["budgets"]["reaction"] == (0 if choice == "accept" else 1)
    finally:
        transport.close()


def test_main_completes_default_four_class_battle_with_numeric_inputs(tmp_path):
    output, prompts, inputs, clients = [], [], [], []
    queue = deque()

    class CapturedClient(Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

    class VisibleAdapter:
        def view(self, _state):
            return deepcopy(clients[0].view)

        def preview(self, _state, command):
            try:
                return clients[0].transport.preview(clients[0].run_id, command)
            except TransportError as exc:
                raise GameError(exc.code, exc.message) from exc

    def reader(prompt):
        prompts.append(prompt)
        client = clients[0]
        if not queue:
            if client.view["phase"] != "combat":
                queue.extend(["1", "c", "q"])
            else:
                operation, command = choose(VisibleAdapter(), None)
                if operation == "reaction":
                    queue.extend(["1" if command == "accept" else "2", "c"])
                else:
                    if client.ui.stage == "actions":
                        if client.ui.selected != command["actor"]:
                            queue.append("0")
                    elif client.ui.stage != "home":
                        pytest.fail("Unexpected stage: " + client.ui.stage)
                    if client.ui.stage == "home" or client.ui.selected != command["actor"]:
                        queue.append(str(next(i for i, a in enumerate(client.view["party"], 1) if a["id"] == command["actor"])))
                    queue.append(action_number(client, command))
                    if command["type"] == "move":
                        queue.append(command["destination"])
                    elif "target" in command:
                        base = next(entry["command"] for entry in actor(client, command["actor"])["menu_actions"]
                                    if entry["command"]["type"] == command["type"] and entry["command"].get("ability") == command.get("ability"))
                        entries = client.transport.options(client.run_id, base)["entries"]
                        queue.append(str(next(i for i, entry in enumerate(entries, 1) if entry["target"] == command["target"])))
                    queue.append("c")
        value = queue.popleft()
        inputs.append(value)
        assert len(inputs) < 300
        assert re.fullmatch(r"(?:[0-9,]+|[A-Z]+[1-9][0-9]*|c|q)", value)
        return value

    config = tmp_path / "client.json"
    with patch("client.cli.Client", CapturedClient):
        result = main(["--new", "--seed", "42", "--db", str(tmp_path / "full.sqlite3"), "--config", str(config)],
                      input_reader=reader, output=output.append)
    assert result == 0
    assert clients[0].view["outcome"] == "victory", "\n".join(output[-10:])
    assert clients[0].view["revision"] == 22
    assert clients[0].view["short_rests"] == 1
    assert all("【" in prompt and " > " in prompt for prompt in prompts)
    assert any("选择目的地" in prompt for prompt in prompts)
    assert any("选择目标" in prompt for prompt in prompts)
    assert any("确认执行" in prompt for prompt in prompts)
    assert read_json(config)["last"]["run_id"] == clients[0].run_id
    assert not (tmp_path / "client.pending.json").exists()


class TerminalProcess:
    """Drive the actual terminal entry point, including its prompts and pipes."""

    def __init__(self, args):
        environment = dict(os.environ, PYTHONUTF8="1")
        environment.pop("CMDRPG_URL", None)
        self.process = subprocess.Popen(
            [sys.executable, "-S", "-u", "-m", "client", *args],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.queue = Queue()
        self.transcript = []
        self.inputs = []

        def read_output():
            while True:
                character = self.process.stdout.read(1)
                self.queue.put(character)
                if not character:
                    return

        self.reader = threading.Thread(target=read_output, daemon=True)
        self.reader.start()
        self.prompt = self.read_prompt()

    def read_prompt(self):
        characters = []
        while True:
            try:
                character = self.queue.get(timeout=15)
            except Empty:
                pytest.fail("终端等待超时：" + "".join(characters)[-3000:])
            if not character:
                pytest.fail("终端意外结束：" + "".join(characters)[-3000:])
            characters.append(character)
            if characters[-3:] == [" ", ">", " "]:
                text = "".join(characters)
                self.transcript.append(text)
                return text.rsplit("\n", 1)[-1]

    def send(self, value):
        assert re.fullmatch(r"(?:[0-9,]+|[A-Z]+[1-9][0-9]*|c|q)", value)
        self.inputs.append(value)
        self.process.stdin.write(value + "\n")
        self.process.stdin.flush()
        if value == "q":
            self.process.wait(timeout=15)
            self.reader.join(timeout=2)
            self.transcript.append(self.process.stdout.read())
            assert self.process.returncode == 0
            return
        self.prompt = self.read_prompt()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def test_real_terminal_process_battle_cancel_invalid_target_and_stale_preview(tmp_path):
    database, config = tmp_path / "terminal.sqlite3", tmp_path / "terminal.json"
    terminal = TerminalProcess(["--new", "--seed", "42", "--db", str(database), "--config", str(config)])
    try:
        run_id = read_json(config)["last"]["run_id"]
        with Repository(database) as repository:
            # Invalid target, numeric confirmation rejection, back, and cancel
            # all happen in the real subprocess before its first write.
            for value in ("3", "9", "1", "0", "1", "D6", "1", "0", "0"):
                terminal.send(value)
            assert repository.get_view(run_id)["revision"] == 0
            terminal.send("1")
            terminal.send("D6")
            repository.submit(run_id, "other-terminal-dodge", 0, {"type": "dodge", "actor": "P2"})
            terminal.send("c")
            assert "过期" in terminal.transcript[-1]
            assert next(a for a in repository.get_view(run_id)["party"] if a["id"] == "P3")["position"] == "B8"

            class VisibleAdapter:
                def view(self, _state):
                    return repository.get_view(run_id)

                def preview(self, _state, command):
                    try:
                        return repository.preview(run_id, command)
                    except RepositoryError as exc:
                        raise GameError(exc.code, exc.message) from exc

            adapter = VisibleAdapter()
            actions = 0
            for _ in range(600):
                view = repository.get_view(run_id)
                if view["phase"] != "combat":
                    break
                operation, command = choose(adapter, None)
                revision = view["revision"]
                if operation == "reaction":
                    assert "选择反应" in terminal.prompt
                    terminal.send("1" if command == "accept" else "2")
                else:
                    aid = command["actor"]
                    if "选择行动或技能" in terminal.prompt and aid not in terminal.prompt:
                        terminal.send("0")
                    if "选择角色" in terminal.prompt:
                        terminal.send(str(next(i for i, a in enumerate(view["party"], 1) if a["id"] == aid)))
                    member = next(a for a in view["party"] if a["id"] == aid)
                    index, entry = next((i, a) for i, a in enumerate(member["menu_actions"], 1)
                                        if a["command"]["type"] == command["type"]
                                        and a["command"].get("ability") == command.get("ability"))
                    terminal.send(str(index))
                    if command["type"] == "move":
                        terminal.send(command["destination"])
                    elif "target" in command:
                        options = repository.options(run_id, entry["command"])
                        terminal.send(str(next(i for i, a in enumerate(options["entries"], 1) if a["target"] == command["target"])))
                assert "确认执行" in terminal.prompt
                terminal.send("c")
                assert repository.get_view(run_id)["revision"] == revision + 1
                actions += 1
            final = repository.get_view(run_id)
            assert final["outcome"] == "victory"
            assert actions > 10
            terminal.send("1")
            terminal.send("0")
            assert repository.get_view(run_id)["revision"] == final["revision"]
            terminal.send("1")
            terminal.send("c")
            assert repository.get_view(run_id)["short_rests"] == 1
        terminal.send("q")
        transcript = "\n".join(terminal.transcript)
        assert "不可用" in transcript and "遮挡" in transcript
        assert "数字只选择当前列表" in transcript and "过期" in transcript
        assert "战斗结果：胜利" in transcript
        (tmp_path / "terminal-transcript.txt").write_text(transcript, encoding="utf-8")
        (tmp_path / "terminal-inputs.json").write_text(json.dumps(terminal.inputs), encoding="utf-8")
    finally:
        terminal.close()


def test_real_terminal_process_recovers_saved_reaction(tmp_path):
    client, transport, _ = scenario_session(tmp_path, pending=True)
    run_id = client.run_id
    transport.close()
    terminal = TerminalProcess(["--run", run_id, "--db", str(tmp_path / "scenario.sqlite3"),
                                "--config", str(tmp_path / "reaction.json")])
    try:
        assert "选择反应" in terminal.prompt
        terminal.send("1")
        assert "命中率" in terminal.transcript[-1]
        terminal.send("0")
        terminal.send("2")
        terminal.send("c")
        terminal.send("q")
        with Repository(tmp_path / "scenario.sqlite3") as repository:
            changed = repository.get_view(run_id)
            assert changed["revision"] == 1
            assert changed["pending"] is None
            assert next(a for a in changed["party"] if a["id"] == "P1")["budgets"]["reaction"] == 1
    finally:
        terminal.close()
