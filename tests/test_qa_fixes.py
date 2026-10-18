"""Regression coverage for the independent 2026-10-17 QA report."""
from copy import deepcopy
from unittest.mock import patch
import json
import pytest
from tests.test_engine import engine, state
from core.engine import GameError
from core import spatial
from client.cli import main
from client.render import actor_line, render_map


def test_downed_effects_and_concentration_are_visible(engine, state):
    actor = state["actors"]["P3"]
    actor.update(hp=0, conditions=["downed"], death_success=1, death_failure=2)
    state["actors"]["P1"]["effects"]["bless"] = {"source":"P4", "sources":["P4"]}
    state["actors"]["P4"]["concentration"] = {"ability":"bless", "targets":["P1"], "remaining":9}
    before = deepcopy(state)
    view = engine.view(state)
    assert "成功 1 / 失败 2" in actor_line(view["party"][2])
    assert "祝福" in actor_line(view["party"][0])
    assert "祝福术（剩余 9 回合，目标 P1）" in actor_line(view["party"][3])
    actor["conditions"].append("stable")
    assert "伤势稳定" in engine.view(state)["party"][2]["status"]
    actor["conditions"].remove("stable")
    assert state == before


def test_outcome_clears_active(engine, state):
    for actor in state["actors"].values():
        if actor["side"] == "enemy":
            actor.update(hp=0, conditions=["dead"])
    engine._outcome(state)
    assert state["active_group"] == engine.view(state)["active"] == []


def test_invalid_bless_slot_fails_before_options(engine, state):
    with pytest.raises(GameError, match="法术位"):
        engine.options(state, {"type":"cast", "actor":"P4", "ability":"bless", "slot":2})


def test_death_failures_capped(engine, state):
    actor = state["actors"]["P1"]
    actor.update(hp=0, conditions=["downed"], death_failure=4)
    engine._death_status(state, actor)
    assert actor["death_failure"] == 3 and actor["conditions"] == ["dead"]


def test_temp_hp_still_requires_concentration_save(engine, state):
    actor = state["actors"]["P4"]
    actor.update(temp_hp=30, concentration={"ability":"bless", "targets":[], "remaining":10})
    with patch.object(engine, "_save", return_value=True) as save:
        engine._damage(state, actor, 24, "force", "test")
    assert actor["hp"] == actor["max_hp"]
    assert save.call_args.args[3] == 12


def test_bg3_allows_action_and_bonus_spells(engine, state):
    changed, _ = engine.execute(state, {"type":"cast", "actor":"P4", "ability":"healing_word", "target":"P1"})
    changed, _ = engine.execute(changed, {"type":"cast", "actor":"P4", "ability":"bless", "targets":["P1"]})
    assert changed["actors"]["P4"]["slots"]["1"] == 0


def test_hazard_free_path_and_dijkstra_agree(engine, state):
    board = state["map"]
    board["hazards"] = {"B2": {"damage":2}}
    route, _ = spatial.path(board, "A2", "D2", avoid_hazards=True)
    assert "B2" not in route
    routes = spatial.paths(board, "A2", {"C2"})
    for cell, route in routes.items():
        assert spatial.path(board, "A2", cell, {"C2"}) == route


def test_last_seen_does_not_follow_hidden_enemy(engine, state):
    engine._emit(state, "test", "观察")
    previous = deepcopy(state["last_seen"]["E1"])
    state["actors"]["E1"].update(position="P12", hidden=True)
    engine._emit(state, "test", "观察结束")
    view = engine.view(state)
    assert previous in view["last_seen"]
    assert "P12" not in json.dumps(view["last_seen"])
    assert "最后目击" in render_map(view)


@pytest.mark.parametrize("last", [[], "bad", 1, None])
def test_malformed_last_has_no_traceback(tmp_path, last):
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"last":last}), encoding="utf-8")
    output = []
    assert main(["--config",str(config)], output=output.append) == 1
    assert "记录格式错误" in output[0]


def test_database_directory_has_no_traceback(tmp_path):
    output = []
    assert main(["--db",str(tmp_path),"--config",str(tmp_path/"config.json")],output=output.append) == 1
    assert output[0].startswith("启动失败")


def test_full_health_warnings_and_no_zero_heal_event(engine, state):
    preview = engine.preview(state, {"type":"potion", "actor":"P1"})
    assert any("满血" in warning for warning in preview["warnings"])
    before = state["event_seq"]
    engine._heal(state, state["actors"]["P1"], 4, "test")
    assert state["event_seq"] == before


def test_explicit_run_does_not_replay_pending_create(tmp_path):
    from saves.storage import Repository
    from client.cli import write_json
    db, config = tmp_path / "game.sqlite3", tmp_path / "config.json"
    with Repository(db) as repository:
        old = repository.create_run("old", seed=42, command_id="old-command")
    write_json(config, {"last":{"mode":"local", "db":str(db.resolve()), "run_id":old["run_id"]}})
    journal = config.with_name("config.pending.json")
    write_json(journal, {"operation":"create", "run_id":None, "scope":{"mode":"local", "db":str(db.resolve())}, "payload":{"name":"unexpected", "command_id":"pending-create"}})
    original = config.read_bytes()
    output = []
    assert main(["--run",old["run_id"],"--db",str(db),"--config",str(config)],output=output.append) == 1
    assert "冲突" in output[-1] and journal.exists()
    assert config.read_bytes() == original
    with Repository(db) as repository:
        assert len(repository.list_runs()) == 1


def test_list_runs_is_read_only_and_resume_ignores_classes(tmp_path):
    from saves.storage import Repository
    db, config = tmp_path / "game.sqlite3", tmp_path / "config.json"
    with Repository(db) as repository:
        old = repository.create_run("existing", seed=42, command_id="create")
    output = []
    assert main(["--list-runs","--db",str(db),"--config",str(config)],output=output.append) == 0
    assert old["run_id"] in output[0] and not config.exists()
    assert main(["--run",old["run_id"],"--classes","bad","--db",str(db),"--config",str(config)],input_reader=lambda _:"q",output=output.append) == 0


def test_movement_menu_uses_one_path_traversal(engine, state):
    before = deepcopy(state)
    with patch("core.spatial.paths", wraps=spatial.paths) as batch, patch("core.spatial.path", wraps=spatial.path) as single:
        options = engine.options(state, {"type":"move", "actor":"P3"})
    assert batch.call_count == 1 and single.call_count == 0
    assert options["reachable"] and state == before


@pytest.mark.parametrize("seed", range(10))
def test_random_legal_commands_preserve_invariants(seed):
    import random
    from core.engine import Engine
    rng, engine = random.Random(seed), Engine()
    state = engine.new_run(seed=seed)
    for _ in range(45):
        view = engine.view(state)
        if state["phase"] != "combat":
            break
        revision = state["revision"]
        if state["pending"]:
            state, _ = engine.react(state, rng.choice(["accept", "decline"]))
        else:
            actor = rng.choice(view["active"])
            actions = [entry for entry in engine.menu_actions(state, actor) if entry["available"]]
            entry = rng.choice(actions)
            command = deepcopy(entry["command"])
            try:
                if entry["target_mode"] in ("enemy", "party", "multi", "move"):
                    entries = [option for option in engine.options(state, command)["entries"] if option["available"]]
                    if not entries:
                        continue
                    option = rng.choice(entries)
                    if entry["target_mode"] == "move":
                        command["destination"] = option["destination"]
                    elif entry["target_mode"] == "multi":
                        command["targets"] = [option["id"]]
                    else:
                        command["target"] = option["id"]
                state, _ = engine.execute(state, command)
            except GameError as exc:
                pytest.fail(f"Legal menu command failed: {command}: {exc}")
        assert state["revision"] == revision + 1
        positions = [a["position"] for a in state["actors"].values() if "dead" not in a["conditions"]]
        assert len(positions) == len(set(positions))
        for member in state["actors"].values():
            assert 0 <= member["hp"] <= member["max_hp"]
            assert all(value >= 0 for value in member["budgets"].values())
            assert all(value >= 0 for value in member.get("slots", {}).values())


def test_setup_cancel_creates_no_run(tmp_path):
    from saves.storage import Repository
    db = tmp_path / "setup.sqlite3"
    inputs = iter(["队伍", "1", "2", "3", "4", "cancel"])
    assert main(["--setup","--db",str(db),"--config",str(tmp_path/"config.json")],input_reader=lambda _:next(inputs),output=lambda _:None) == 0
    with Repository(db) as repository:
        assert repository.list_runs() == []


def test_revival_in_current_group_preserves_remaining_budget(engine, state):
    actor = state["actors"]["P1"]
    actor.update(hp=0, conditions=["downed"], ended=True, last_turn_round=state["round"])
    actor["budgets"]["action"] = 0
    engine._heal(state, actor, 1, "救助")
    assert not actor["ended"] and actor["budgets"]["action"] == 0


def test_enemy_does_not_cross_hazard(engine, state):
    enemy = state["actors"]["E1"]
    enemy.update(position="A2", hp=1)
    state["actors"]["P1"]["position"] = "E2"
    for aid, position in [("P2","P9"),("P3","P10"),("P4","P11")]:
        state["actors"][aid]["position"] = position
    state["map"]["hazards"] = {"B2":{"damage":2,"description":"火焰"}}
    engine._enemy_turn(state, enemy)
    assert enemy["hp"] == 1
    assert not any(event["kind"] == "move" and event["details"].get("destination") == "B2" for event in state["event_log"])
