"""Decision cards run the real persisted battle, without their own rule engine."""

from copy import deepcopy
import io
from itertools import product
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from client.cards import DecisionScreen, columns, wrap
from client.cli import Client, main
from client.render import render_preview, render_result
from client.transport import LocalTransport, TransportError
from core.engine import Engine, GameError
from saves.storage import Repository
from tools.play_slice import choose


class Arena(Engine):
    def new_run(self, *args, **kwargs):
        state = super().new_run(*args, **kwargs, levels=[5] * 4)
        state["map"].update(walls=[], heights={}, difficult=[], hazards={})
        for aid, position in {"P1": "B4", "P2": "B5", "P3": "B6", "P4": "B7",
                              "E1": "C4", "E2": "L2", "E3": "N2"}.items():
            actor = state["actors"][aid]
            actor.update(position=position, ended=False,
                         budgets=dict(action=1, bonus=1, reaction=1, movement=actor["speed"]))
            if aid.startswith("E"):
                actor.update(hp=1000, max_hp=1000)
        state.update(active_group=["P1", "P2", "P3", "P4"], cursor=4,
                     turn_order=["P1", "P2", "P3", "P4", "E1", "E2", "E3"])
        return state


def session(tmp_path, engine=None):
    transport = LocalTransport(repository=Repository(tmp_path / "cards.sqlite3", engine=engine or Arena()))
    screen = DecisionScreen(io.StringIO())
    client = Client(transport, output=screen.message, presenter=screen.present)
    screen.client = client
    client.create(seed=42)
    return client, transport, screen


def select(client, command):
    if client.ui.stage != "actions" or client.ui.selected != command["actor"]:
        while client.ui.stage != "home":
            client.handle("0")
        client.handle(command["actor"][1:])
    number = next(n for n, (kind, entry) in client.menu.items() if kind == "action"
                  and entry["command"]["type"] == command["type"]
                  and entry["command"].get("ability") == command.get("ability"))
    client.handle(number)
    if "destination" in command:
        client.handle(command["destination"])
    elif "target" in command or "targets" in command:
        targets = command.get("targets", [command.get("target")])
        client.handle(",".join(str(next(n for n, entry in enumerate(client.ui.entries, 1)
                                        if entry["target"] == target)) for target in targets))
    assert client.ui.stage == "confirm"


@pytest.mark.parametrize("size", [(110, 36), (132, 44), (160, 50)])
def test_all_owned_actions_paging_and_cjk_frame_boundaries(tmp_path, size):
    client, transport, screen = session(tmp_path)
    try:
        client.handle("3")
        before = transport.repository._load(client.run_id)
        pages = []
        for _ in range(20):
            pages.append(screen.frame(size))
            screen.navigate("n")
        text = "\n".join(pages)
        assert all(entry["name"] in text for entry in client.ui.actor()["menu_actions"])
        assert "完整行动" in text and "生命" in text and "动作" in text and "移动" in text
        for frame in pages:
            assert len(frame.splitlines()) == size[1] - 2
            assert all(columns(line) == size[0] - 1 for line in frame.splitlines())
            grid_rows = [line.split(" | ")[0] for line in frame.splitlines() if "| A |" in line]
            assert grid_rows  # The 16-column grid must fit without wrapping.
        assert transport.repository._load(client.run_id) == before
        assert "\x1b" not in "\n".join(wrap("名字\x1b[2J\r危险", 20))
    finally:
        transport.close()


def test_selected_effect_rules_preview_cancel_and_stale_confirmation(tmp_path):
    client, transport, screen = session(tmp_path)
    try:
        before = transport.repository._load(client.run_id)
        client.handle("3")
        number = next(n for n, (kind, entry) in client.menu.items()
                      if kind == "action" and entry["command"].get("ability") == "scorching_ray")
        client.handle(number)
        decision = client.ui.render()
        assert "效果：" in decision and "成本：" in decision and "法术位" in decision
        client.handle("rules")
        assert "规则：" in screen.feedback
        assert transport.repository._load(client.run_id) == before
        client.handle("1")
        assert "连续攻击：至多 3 段" in screen.preview
        assert "命中率" in screen.preview and "消耗" in screen.preview
        client.handle("0")
        assert client.ui.stage == "target" and not client.previews
        client.handle("1")
        transport.repository.submit(client.run_id, "external-change", before["revision"],
                                    {"type": "dodge", "actor": "P2"})
        client.handle("c")
        assert "过期" in screen.feedback
        assert client.view["revision"] == before["revision"] + 1
        assert not client.ui.confirmation
    finally:
        transport.close()


def test_every_multihit_event_and_actual_resource_delta_remains_accessible(tmp_path):
    client, transport, screen = session(tmp_path)
    try:
        select(client, dict(type="cast", actor="P3", ability="scorching_ray", target="E1"))
        client.handle("c")
        result = client.last_result
        assert sum(e["kind"] == "attack" for e in result["events"]) == 3
        for event in result["events"]:
            assert event["text"] in screen.feedback
        assert "真实结算" in screen.feedback
        assert "2环法术位 3 → 2" in screen.feedback
        assert "动作 1 → 0" in screen.feedback
        client.handle("details")
        assert "计算：" in screen.feedback
        assert sum("计算：" in line for line in screen.feedback.splitlines()) >= 6
        pages = []
        for _ in range(40):
            pages.append(screen.frame((110, 36)))
            screen.navigate("]")
        for event in result["events"]:
            assert "[%s]" % event["seq"] in "\n".join(pages)
        # A new unconfirmed preview must never replace the real result pane.
        original_feedback = screen.feedback
        client.handle("0")
        client.handle("1")
        assert client.ui.stage == "actions"
        assert screen.feedback == original_feedback
    finally:
        transport.close()


def test_fireball_preview_is_pure_and_matches_save_distribution_without_hidden_targets():
    engine = Arena()
    state = engine.new_run(seed=42)
    target = state["actors"]["E1"]
    target["conditions"] = ["dodge"]
    target["effects"]["bless"] = {"sources": ["P4"]}
    # The hidden actor is in the blast but must not enter the decision card.
    state["actors"]["E3"].update(position="F4", hidden=True, name="HIDDEN_SENTINEL")
    before = deepcopy(state)
    preview = engine.preview(state, dict(type="cast", actor="P3", ability="fireball", target="E1", slot=3))
    assert state == before
    assert "E3" not in preview["targets"]
    probability = next(s["failure_probability"] for s in preview["saves"] if s["target"] == "E1")
    bonus = (target["stats"]["dex"] - 10) // 2
    if "dex" in target["save_proficiencies"]:
        bonus += 2  # Arena enemies remain at level 1.
    rolls = list(product(range(1, 21), range(1, 21), range(1, 5)))
    expected = sum(max(a, b) + bless + bonus < preview["save_dc"] for a, b, bless in rolls) / len(rolls)
    assert probability == pytest.approx(expected)
    rendered = render_preview(1, preview, state["revision"], engine.view(state))
    assert "豁免失败概率" in rendered
    assert "HIDDEN_SENTINEL" not in rendered and "E3" not in rendered


@pytest.mark.parametrize("level", [1, 5])
def test_fixed_cards_complete_real_encounter_using_numeric_menu(tmp_path, level):
    class LeveledEngine(Engine):
        def new_run(self, *args, **kwargs):
            return super().new_run(*args, **kwargs, levels=[level] * 4)

    client, transport, screen = session(tmp_path, LeveledEngine())

    class VisibleAdapter:
        def view(self, _):
            return client.view

        def preview(self, _, command):
            try:
                return transport.preview(client.run_id, command)
            except TransportError as exc:
                raise GameError(exc.code, exc.message) from exc

    try:
        for _ in range(600):
            screen.frame((132, 44))
            if client.view["phase"] != "combat":
                break
            operation, command = choose(VisibleAdapter(), None)
            if operation == "reaction":
                client.handle("1")
            else:
                select(client, command)
            revision = client.view["revision"]
            screen.frame((132, 44))
            client.handle("c")
            assert client.view["revision"] == revision + 1
        assert client.view["outcome"] == "victory"
        assert "战斗结果：胜利" in client.ui.render()
        # The same repository can be recovered through the plain terminal client.
        recovered = Client(transport, client.run_id, output=lambda _: None)
        recovered.refresh()
        assert recovered.view == client.view
        assert recovered.ui.stage == "settlement"
    finally:
        transport.close()


def test_card_request_on_pipe_uses_plain_text_and_no_escape_sequences(tmp_path):
    process = subprocess.run([sys.executable, "-m", "client", "--new", "--ui", "cards",
                              "--db", str(tmp_path / "pipe.sqlite3"),
                              "--config", str(tmp_path / "pipe.json")],
                             input="q\n", capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert process.returncode == 0, process.stderr
    assert "兼容文字界面" in process.stdout and "文字地图" in process.stdout
    assert "\x1b" not in process.stdout


@pytest.mark.parametrize("resize", [False, True])
def test_main_fixed_screen_navigation_and_safe_restore(tmp_path, resize):
    sink = io.StringIO()
    inputs = iter(["3", "1", "D6", "0", "0", "n", "p", "plain", "q"] if not resize else ["3", "q"])
    def reader(_):
        return next(inputs)

    def enter(screen):
        screen.active = True
        screen.stream.write("\x1b[?1049h")
        return screen

    sizes = [os.terminal_size((132, 44))] + ([os.terminal_size((80, 24))] if resize else [])
    with patch("client.cli.input", reader, create=True), patch("sys.stdout", sink), \
            patch.object(DecisionScreen, "supported", return_value=True), \
            patch.object(DecisionScreen, "__enter__", enter), \
            patch.object(DecisionScreen, "size", side_effect=lambda: sizes.pop(0) if len(sizes) > 1 else sizes[0]):
        assert main(["--new", "--ui", "cards", "--seed", "42", "--db", str(tmp_path / "live.sqlite3"),
                     "--config", str(tmp_path / "live.json")], input_reader=reader) == 0
    transcript = sink.getvalue()
    assert "决策 1/" in transcript
    assert transcript.count("\x1b[?1049h") == transcript.count("\x1b[?1049l") == 1
    assert "文字地图" in transcript
    if resize:
        assert "终端尺寸不足" in transcript
    else:
        assert "预览 1" in transcript
    with Repository(tmp_path / "live.sqlite3") as store:
        run = store.list_runs()[0]
        assert store.get_view(run["id"])["revision"] == 0


def test_reaction_preview_cancel_recover_and_confirm_in_cards(tmp_path):
    class ReactionArena(Arena):
        def new_run(self, *args, **kwargs):
            state = super().new_run(*args, **kwargs)
            state["actors"]["P2"]["position"] = "A8"
            self._continue_move(state, state["actors"]["E1"], ["D4", "E4"])
            assert state["pending"]["reactor"] == "P1"
            return state

    client, transport, screen = session(tmp_path, ReactionArena())
    try:
        before = transport.repository._load(client.run_id)
        assert client.ui.stage == "reaction"
        client.handle("1")
        assert "命中率" in screen.preview and "反应 1" in screen.preview
        client.handle("0")
        assert client.ui.stage == "reaction"
        assert transport.repository._load(client.run_id) == before
        recovered = Client(transport, client.run_id, output=screen.message, presenter=screen.present)
        screen.client = recovered
        recovered.refresh()
        recovered.handle("2")
        assert "不消耗反应" in screen.feedback
        recovered.handle("c")
        assert recovered.view["pending"] is None
        assert next(a for a in recovered.view["party"] if a["id"] == "P1")["budgets"]["reaction"] == 1
        assert "放弃借机攻击" in screen.feedback
    finally:
        transport.close()


def test_result_retains_critical_kill_status_and_all_events_in_order():
    events = [{"seq": i, "kind": kind, "text": text} for i, (kind, text) in enumerate([
        ("attack", "暴击"), ("damage", "造成伤害"), ("defeat_enemy", "敌人被击败"),
        ("concentration_end", "专注结束"), ("downed", "倒地"), ("death", "死亡"),
        ("victory", "胜利"), ("campaign_reward", "金币奖励")], 1)]
    text = render_result(events)
    assert all(event["text"] in text for event in events)
    assert [text.index("[%s]" % e["seq"]) for e in events] == sorted(text.index("[%s]" % e["seq"]) for e in events)


def test_enemy_resolution_publishes_the_attack_without_target_selection_policy():
    engine = Arena()
    state = engine.new_run(seed=42)
    before = state["event_seq"]
    engine._enemy_finish(state, state["actors"]["E1"])
    events = [e for e in state["event_log"] if e["seq"] > before]
    assert any(e["kind"] == "attack" for e in events)
    assert not any(e["kind"] == "enemy_decision" for e in events)
    assert "生命最低" not in render_result(events)
