"""Menus must agree with legal previews and preserve private game state."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from core import dice, spatial
from core.engine import Engine, GameError
from saves.storage import Repository


@pytest.fixture
def engine():
    return Engine()


@pytest.fixture
def state(engine):
    state = engine.new_run(seed=42)
    state["map"].update(walls=[], heights={}, difficult=[], hazards={})
    positions = {"P1": "B4", "P2": "B5", "P3": "B6", "P4": "B7",
                 "E1": "C4", "E2": "L2", "E3": "N2"}
    for actor_id, actor in state["actors"].items():
        actor.update(position=positions[actor_id], ended=False, hp=actor["max_hp"],
                     budgets={"action": 1, "bonus": 1, "reaction": 1, "movement": 9})
    state.update(turn_order=["P1", "P2", "P3", "P4", "E1", "E2", "E3"],
                 active_group=["P1", "P2", "P3", "P4"], cursor=4)
    return state


def test_menu_exposes_every_owned_skill_and_resource_reason(engine, state):
    state["actors"]["P3"]["slots"]["1"] = 0
    state["actors"]["P1"]["resources"]["second_wind"] = 0
    before = deepcopy(state)
    view = engine.view(state)
    for actor in view["party"]:
        actions = actor["menu_actions"]
        assert {a["command"]["ability"] for a in actions if "ability" in a["command"]} == set(state["actors"][actor["id"]]["abilities"])
        assert {"move", "attack", "dash", "disengage", "dodge", "help", "potion", "end"} <= {a["command"]["type"] for a in actions}
        assert all(a["name"] and a["description"] and isinstance(a["cost"], dict) for a in actions)
    wizard = next(a for a in view["party"] if a["id"] == "P3")
    missile = next(a for a in wizard["menu_actions"] if a["command"].get("ability") == "magic_missile")
    assert not missile["available"] and missile["reason"] == "法术位不足"
    assert missile["remaining"]["slot_1"] == 0
    monk = next(a for a in view["party"] if a["id"] == "P2")
    strike = next(a for a in monk["menu_actions"] if a["command"].get("ability") == "unarmed_strike")
    assert not strike["available"] and "需要先攻击" in strike["reason"]
    fighter = next(a for a in view["party"] if a["id"] == "P1")
    wind = next(a for a in fighter["menu_actions"] if a["command"].get("ability") == "second_wind")
    assert not wind["available"] and wind["remaining"]["second_wind"] == 0
    assert state == before


def test_options_are_pure_and_include_only_visible_targets(engine, state):
    state["actors"]["E3"]["hidden"] = True
    before = deepcopy(state)
    options = engine.options(state, {"type": "attack", "actor": "P1"})
    assert {entry["target"] for entry in options["entries"]} == {"E1", "E2"}
    first = next(entry for entry in options["entries"] if entry["target"] == "E1")
    assert first["available"]
    assert first["preview"] == engine.preview(state, {"type": "attack", "actor": "P1", "target": "E1"})
    blocked = next(entry for entry in options["entries"] if entry["target"] == "E2")
    assert not blocked["available"] and blocked["reason_code"] == "out_of_range"
    assert "rng_state" not in json.dumps(options) and "E3" not in json.dumps(options)
    assert state == before


def test_target_options_report_cover_and_do_not_hide_unavailable_friends(engine, state):
    state["map"]["walls"] = ["C6"]
    state["actors"]["E1"]["position"] = "D6"
    state["actors"]["P4"]["position"] = "D5"  # Party-wide visibility sees the enemy.
    options = engine.options(state, {"type": "cast", "actor": "P3", "ability": "fire_bolt"})
    target = next(entry for entry in options["entries"] if entry["target"] == "E1")
    assert not target["available"] and "遮挡" in target["reason"]
    armor = engine.options(state, {"type": "cast", "actor": "P3", "ability": "mage_armor"})
    assert {entry["target"] for entry in armor["entries"]} == {"P1", "P2", "P3", "P4"}
    fighter = next(entry for entry in armor["entries"] if entry["target"] == "P1")
    assert not fighter["available"] and "穿护甲" in fighter["reason"]


def test_dead_enemies_leave_targets_but_dead_party_remains_explained(engine, state):
    state["actors"]["E1"].update(hp=0, conditions=["dead"])
    state["actors"]["P1"].update(hp=0, conditions=["dead"])
    before = deepcopy(state)
    view = engine.view(state)
    assert "E1" in {target["id"] for target in view["enemies"]}
    attacks = engine.options(state, {"type": "attack", "actor": "P2"})
    spells = engine.options(state, {"type": "cast", "actor": "P3", "ability": "fire_bolt"})
    assert "E1" not in {target["id"] for target in attacks["entries"] + spells["entries"]}
    healing = engine.options(state, {"type": "cast", "actor": "P4", "ability": "healing_word"})
    dead = next(target for target in healing["entries"] if target["id"] == "P1")
    assert not dead["available"] and "死亡" in dead["reason"] and "不会复活" in dead["reason"]
    assert state == before


@pytest.mark.parametrize("source,target,heights,description", [
    ("K4", "L4", {"K4": 1.5, "L4": 3}, "向东 1 格，目标高 1.5 米"),
    ("K4", "L5", {}, "向东 1 格、向南 1 格，同一高度"),
])
def test_distance_options_explain_diagonals_and_height_without_changing_reach(engine, state, source, target, heights, description):
    state["actors"]["P2"]["position"] = source
    state["actors"]["E3"]["position"] = target
    state["map"]["heights"] = heights
    before = deepcopy(state)
    attacks = engine.options(state, {"type": "attack", "actor": "P2"})
    enemy = next(entry for entry in attacks["entries"] if entry["id"] == "E3")
    assert enemy["distance"] == pytest.approx(2.1213203435596424)
    assert description in enemy["distance_description"]
    assert "按直线与高差计算" in enemy["distance_description"]
    if not heights:
        assert enemy["available"]
        assert state == before
        return
    assert not enemy["available"] and enemy["reason_code"] == "out_of_range"
    assert "2.12 米" in enemy["reason"] and "近身范围包含高差" in enemy["reason"]
    with pytest.raises(GameError) as blocked:
        engine.preview(state, {"type": "attack", "actor": "P2", "target": "E3"})
    assert enemy["reason"] == blocked.value.message
    assert state == before


def test_menu_and_options_choose_lowest_available_spell_slot(engine, state):
    state["actors"]["P3"]["slots"] = {"1": 0, "2": 1, "3": 1}
    menu = engine.menu_actions(state, "P3")
    missile = next(item for item in menu if item["command"].get("ability") == "magic_missile")
    assert missile["command"]["slot"] == 2 and missile["cost"]["slot_2"] == 1
    options = engine.options(state, {"type": "cast", "actor": "P3", "ability": "magic_missile"})
    assert options["command"]["slot"] == 2
    target = next(item for item in options["entries"] if item["target"] == "E1")
    assert target["preview"]["cost"] == {"action": 1, "slot_2": 1}
    assert target["hit_probability"] == target["preview"]["hit_probability"] == 1


def test_bless_options_allow_multi_selection_then_combined_preview(engine, state):
    options = engine.options(state, {"type": "cast", "actor": "P4", "ability": "bless"})
    assert options["max_targets"] == 3
    assert all(entry["available"] for entry in options["entries"])
    chosen = [entry["target"] for entry in options["entries"][:3]]
    preview = engine.preview(state, {**options["command"], "targets": chosen})
    assert preview["targets"] == chosen and preview["cost"] == {"action": 1, "slot_1": 1}
    with pytest.raises(GameError):
        engine.preview(state, {**options["command"], "targets": ["P1", "P2", "P3", "P4"]})


def test_movement_options_use_actual_paths_costs_and_occupancy(engine, state):
    state["map"]["difficult"] = ["C6"]
    state["actors"]["P3"]["budgets"]["movement"] = 3
    before = deepcopy(state)
    options = engine.options(state, {"type": "move", "actor": "P3"})
    near = next(entry for entry in options["entries"] if entry["destination"] == "C6")
    assert near["available"] and near["cost"] == 3 and near["path"] == ["B6", "C6"]
    far = next(entry for entry in options["entries"] if entry["destination"] == "E6")
    assert not far["available"] and far["cost"] > 3 and far["path"][-1] == "E6"
    assert far["name"] == "门洞西侧" and far in options["landmarks"]
    assert "剩余 3 米" in far["reason"]
    occupied = next(entry for entry in options["entries"] if entry["destination"] == "B5")
    assert not occupied["available"] and occupied["path"] == []
    assert set(options["reachable"]) == {entry["destination"] for entry in options["entries"] if entry["available"]}
    for entry in options["entries"]:
        if entry["available"]:
            assert entry["preview"]["path"] == entry["path"]
            assert entry["preview"]["cost"]["movement"] == entry["cost"]
    changed, _ = engine.execute(state, near["preview"]["command"])
    assert changed["actors"]["P3"]["position"] == "C6"
    assert changed["actors"]["P3"]["budgets"]["movement"] == 0
    assert state == before


def test_movement_preview_excludes_ranged_only_and_blocked_reactions(engine, state):
    state["actors"]["E1"]["position"] = "N11"
    state["actors"]["E3"]["position"] = "C4"
    command = {"type": "move", "actor": "P1", "destination": "A4"}
    assert not engine.preview(state, command)["warnings"]
    changed, events = engine.execute(state, command)
    assert not any(event["kind"] == "attack" for event in events)
    assert changed["actors"]["E3"]["budgets"]["reaction"] == 1
    state["actors"]["E3"]["position"] = "N2"
    state["actors"]["E1"]["position"] = "C4"
    assert engine.preview(state, command)["opportunity_attacks"] == [{"id": "E1", "name": state["actors"]["E1"]["name"]}]
    original = spatial.line_of_sight
    with patch("core.spatial.line_of_sight", side_effect=lambda board, start, end: False if (start, end) == ("C4", "B4") else original(board, start, end)):
        assert not engine.preview(state, command)["warnings"]
        _, events = engine.execute(state, command)
        assert not any(event["kind"] == "attack" for event in events)


def test_unarmed_and_save_menu_probabilities_match_legal_resolution(engine, state):
    state["actors"]["E1"]["position"] = "C5"
    state["actors"]["P2"]["attacked"] = True
    options = engine.options(state, {"type": "ability", "actor": "P2", "ability": "unarmed_strike"})
    target = next(entry for entry in options["entries"] if entry["target"] == "E1")
    assert target["hit_probability"] == dice.hit_probability(5, state["actors"]["E1"]["ac"])
    sacred = engine.options(state, {"type": "cast", "actor": "P4", "ability": "sacred_flame"})
    target = next(entry for entry in sacred["entries"] if entry["target"] == "E1")
    failures = 0
    for natural in range(1, 21):
        with patch("core.dice.die", return_value=natural):
            _, events = engine.execute(state, target["preview"]["command"])
        failures += not next(event for event in events if event["kind"] == "save")["details"]["success"]
    assert target["failure_probability"] == failures / 20


@pytest.mark.parametrize("command", [
    {"type": "attack", "actor": "P1", "target": "E1"},
    {"type": "move", "actor": "P99"},
    {"type": "cast", "actor": "P3", "ability": "bless"},
    {"type": "cast", "actor": "P3", "ability": "fire_bolt", "slot": 1},
])
def test_invalid_options_leave_state_unchanged(engine, state, command):
    before = deepcopy(state)
    with pytest.raises(GameError):
        engine.options(state, command)
    assert state == before


def test_repository_options_defensively_copy_state_and_command(tmp_path):
    class CarelessEngine(Engine):
        def options(self, state, command):
            result = super().options(state, command)
            state["rng_state"] = 999
            state["revision"] += 1
            command["actor"] = "P99"
            return result

    with Repository(tmp_path / "game.sqlite3", CarelessEngine()) as repository:
        created = repository.create_run(seed=42, command_id="create")
        command = {"type": "move", "actor": created["view"]["active"][0]}
        before = repository._load(created["run_id"])
        result = repository.options(created["run_id"], command)
        assert result["revision"] == 0
        assert repository._load(created["run_id"]) == before
        assert command["actor"] == created["view"]["active"][0]
        assert repository.get_view(created["run_id"]) == created["view"]
