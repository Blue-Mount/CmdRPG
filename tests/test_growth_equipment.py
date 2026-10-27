"""M3 acceptance: actual effects, pure previews, isolated persistence and UI."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from core.engine import Engine, GameError
from core.growth import DATA, validate_growth
from saves.storage import Repository, RepositoryError


def arena(level=5):
    engine = Engine()
    state = engine.new_run(seed=42, levels=[level] * 4)
    state["map"].update(walls=[], heights={}, hazards={})
    for index, a in enumerate(state["actors"].values()):
        a["position"] = ["B2", "C2", "D2", "E2", "C3", "F3", "G3"][index]
        a["ended"] = False
        a["budgets"] = {"action": 1, "bonus": 1, "reaction": 1, "movement": a["speed"]}
        if a["side"] == "enemy":
            a["hp"] = a["max_hp"] = 1000
            a["ac"] = 10
    state.update(
        active_group=["P1", "P2", "P3", "P4"],
        cursor=4,
        turn_order=["P1", "P2", "P3", "P4", "E1", "E2", "E3"],
    )
    return engine, state


def test_rally_wizard_temporary_hp_is_visible_and_absorbs_damage():
    from types import SimpleNamespace
    from client.menu import TerminalMenu
    from client.render import actor_line, render_actor_card, render_map

    e, s = arena(level=3)
    s, events = e.execute(s, {"type": "ability", "actor": "P1", "ability": "rally", "target": "P3"})
    wizard = s["actors"]["P3"]
    hp = wizard["hp"]
    assert wizard["temp_hp"] == 8
    assert any("P3 获得鼓舞，当前临时生命 8" in event["text"] for event in events)

    for damage, remaining in ((0, 8), (3, 5), (7, 0)):
        if damage:
            e._damage(s, wizard, damage, "bludgeoning", "测试攻击")
        view = e.view(s)
        actor = next(a for a in view["party"] if a["id"] == "P3")
        outputs = [TerminalMenu(SimpleNamespace(view=view, pending=None)).render(),
                   actor_line(actor), render_actor_card(actor), render_map(view)]
        for output in outputs:
            assert ("临时生命 %s" % remaining in output) if remaining else "临时生命" not in output
        assert wizard["temp_hp"] == remaining
    assert wizard["hp"] == hp - 2


def safe(state):
    state["phase"] = "victory"
    state["outcome"] = "victory"
    state["active_group"] = []
    return state


@pytest.mark.parametrize(
    "cid,aid,hp",
    [
        ("fighter", "P1", [12, 20, 28, 36, 44]),
        ("monk", "P2", [10, 17, 24, 31, 38]),
        ("wizard", "P3", [8, 14, 20, 26, 32]),
        ("cleric", "P4", [10, 17, 24, 31, 38]),
    ],
)
@pytest.mark.parametrize("level", range(1, 6))
def test_twenty_snapshots(cid, aid, hp, level):
    e, s = arena(level)
    a = s["actors"][aid]
    assert a["level"] == level and a["max_hp"] == hp[level - 1]
    assert a["proficiency"] == (3 if level == 5 else 2)
    assert a["extra_attack"] == int(level == 5 and cid in ("fighter", "monk"))
    stat = {"fighter": "str", "monk": "dex", "wizard": "int", "cleric": "wis"}[cid]
    assert a["stats"][stat] == (18 if level >= 4 else 16)
    assert a["abilities"] == DATA["classes"][cid][str(level)]["abilities"]
    assert all(e.content["abilities"][x]["effect"] for x in a["abilities"])
    if cid in ("wizard", "cleric"):
        assert (
            a["slots"]
            == [
                None,
                {"1": 2},
                {"1": 3},
                {"1": 4, "2": 2},
                {"1": 4, "2": 3},
                {"1": 4, "2": 3, "3": 2},
            ][level]
        )


@pytest.mark.parametrize(
    "ability,actor,target",
    [
        ("action_surge", "P1", None),
        ("rally", "P1", "P2"),
        ("flurry", "P2", "E1"),
        ("patient_defence", "P2", None),
        ("scorching_ray", "P3", "E1"),
        ("fireball", "P3", "E1"),
        ("aid", "P4", None),
        ("preserve_life", "P4", None),
        ("mass_healing_word", "P4", None),
    ],
)
def test_every_new_effect_changes_real_state_and_preview_is_pure(
    ability, actor, target
):
    e, s = arena()
    a = s["actors"][actor]
    for t in s["actors"].values():
        if t["side"] == "party":
            t["hp"] = 1
    command = {
        "type": "cast" if e.content["abilities"][ability]["level"] else "ability",
        "actor": actor,
        "ability": ability,
    }
    if target:
        command["target"] = target
    before = deepcopy(s)
    preview = e.preview(s, command)
    assert e.preview(s, command) == preview and s == before
    with patch("core.dice.die", side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(s, command)
    assert s == before and after["revision"] == s["revision"] + 1 and events
    changed = after["actors"][actor]
    for resource, amount in preview["cost"].items():
        if resource in a["budgets"]:
            assert changed["budgets"][resource] == a["budgets"][resource] - amount
        elif resource.startswith("slot_"):
            assert changed["slots"][resource[5:]] == a["slots"][resource[5:]] - amount
        else:
            assert changed["resources"][resource] == a["resources"][resource] - amount
    if ability == "action_surge":
        assert changed["budgets"]["action"] == 2 and changed["budgets"]["bonus"] == 1
    elif ability == "rally":
        assert after["actors"]["P2"]["temp_hp"] == 8
    elif ability == "patient_defence":
        assert "dodge" in changed["conditions"]
    elif ability in ("flurry", "scorching_ray", "fireball"):
        assert after["actors"]["E1"]["hp"] < 1000
    elif ability == "aid":
        assert all(
            after["actors"][p]["max_hp"] == s["actors"][p]["max_hp"] + 5
            for p in ["P1", "P2", "P3", "P4"]
        )
    else:
        assert all(after["actors"][p]["hp"] > 1 for p in ["P1", "P2", "P3", "P4"])


def test_extra_attacks_do_not_grant_spells_or_repeat_forever_and_surge_adds_pair():
    e, s = arena()
    c = {"type": "attack", "actor": "P1", "target": "E1"}
    s, _ = e.execute(s, c)
    assert s["actors"]["P1"]["budgets"]["action"] == 0
    assert e.preview(s, c)["cost"] == {}
    assert next(x for x in e.menu_actions(s, "P1") if x["command"]["type"] == "attack")[
        "available"
    ]
    s, _ = e.execute(s, c)
    with pytest.raises(GameError):
        e.preview(s, c)
    s, _ = e.execute(s, {"type": "ability", "actor": "P1", "ability": "action_surge"})
    s, _ = e.execute(s, c)
    s, _ = e.execute(s, c)
    with pytest.raises(GameError):
        e.preview(s, c)
    # Cantrip cannot spend an extra weapon attack.
    s["actors"]["P3"]["budgets"]["action"] = 0
    s["actors"]["P3"]["extra_attacks"] = 1
    with pytest.raises(GameError):
        e.preview(
            s, {"type": "cast", "actor": "P3", "ability": "fire_bolt", "target": "E1"}
        )


def test_upgrade_retains_spent_resources_hp_preparation_equipment_and_aid():
    e, s = arena(3)
    safe(s)
    a = s["actors"]["P3"]
    a["hp"] -= 4
    a["slots"]["1"] -= 2
    a["resources"]["arcane_recovery"] = 0
    a["prepared"] = ["magic_missile"]
    a["max_hp"] += 5
    a["hp"] += 5
    a["effects"]["aid"] = {"amount": 5, "source": "P4"}
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P3", "slot": "main_hand", "item": "staff"}
    )
    for level in (4, 5):
        s, _ = e.execute(s, {"type": "levelup", "actor": "P3"})
    a = s["actors"]["P3"]
    assert a["hp"] == 33 and a["max_hp"] == 37
    assert a["slots"]["1"] == 2 and a["slots"]["3"] == 2
    assert a["resources"]["arcane_recovery"] == 1
    assert a["equipment"]["main_hand"] == "staff"
    assert "mage_armor" not in a["prepared"] and "fireball" in a["prepared"]
    for level in (6, 7, 8):
        s, _ = e.execute(s, {"type": "levelup", "actor": "P3"})
    assert s['actors']['P3']['slots']['1'] == 2
    assert s['actors']['P3']['slots']['4'] == 2
    with pytest.raises(GameError):
        e.execute(s, {"type": "levelup", "actor": "P3"})


def test_equipment_legality_ac_damage_stat_proficiency_and_casting():
    e, s = arena()
    safe(s)
    assert s["actors"]["P1"]["ac"] == 19  # chain + shield + defence
    with pytest.raises(GameError):
        e.execute(
            s,
            {"type": "equip", "actor": "P1", "slot": "main_hand", "item": "greatsword"},
        )
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P1", "slot": "off_hand", "item": None}
    )
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P1", "slot": "main_hand", "item": "greatsword"}
    )
    assert s["actors"]["P1"]["ac"] == 17 and s["actors"]["P1"]["weapon"] == "greatsword"
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P3", "slot": "armor", "item": "chain_mail"}
    )
    assert s["actors"]["P3"]["ac"] == 16
    s.update(phase="combat", active_group=["P1", "P2", "P3", "P4"])
    with pytest.raises(GameError, match="施法"):
        e.preview(
            s, {"type": "cast", "actor": "P3", "ability": "fire_bolt", "target": "E1"}
        )
    assert e._weapon_proficiency(s["actors"]["P3"]) == 3  # dagger remains proficient
    assert e._attack_context(s, s["actors"]["P3"], s["actors"]["E1"])[1]
    # Weapon proficiency actually changes both preview and resolution.
    safe(s)
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P3", "slot": "main_hand", "item": "longsword"}
    )
    assert (
        e._weapon_proficiency(s["actors"]["P3"]) == 0
        and s["actors"]["P3"]["attack_stat"] == "str"
    )
    s.update(phase="combat", active_group=["P1", "P2", "P3", "P4"])
    s["actors"]["P3"]["position"] = "B3"
    with patch("core.dice.die", side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(s, {"type": "attack", "actor": "P3", "target": "E1"})
    event = next(x for x in events if x["kind"] == "attack")
    assert event["details"]["proficiency"] == 0 and event["details"]["disadvantage"]


def test_monk_armor_and_shield_disable_wis_ac_speed_and_martial_abilities():
    e, s = arena()
    safe(s)
    assert s["actors"]["P2"]["ac"] == 17 and s["actors"]["P2"]["speed"] == 12
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P2", "slot": "armor", "item": "leather"}
    )
    a = s["actors"]["P2"]
    assert a["ac"] == 15 and a["speed"] == 9 and a["attack_stat"] == "str"
    s.update(phase="combat", active_group=["P1", "P2", "P3", "P4"])
    a["attacked"] = True
    for ability in ("flurry", "patient_defence", "unarmed_strike"):
        with pytest.raises(GameError):
            e.preview(
                s,
                (
                    {
                        "type": "ability",
                        "actor": "P2",
                        "ability": ability,
                        "target": "E1",
                    }
                    if ability != "patient_defence"
                    else {"type": "ability", "actor": "P2", "ability": ability}
                ),
            )


def test_upcast_cantrip_scaling_preparation_domain_and_recovery():
    e, s = arena()
    safe(s)
    with pytest.raises(GameError):
        e.execute(s, {"type": "prepare", "actor": "P4", "spells": []})
    s, _ = e.execute(
        s, {"type": "prepare", "actor": "P3", "spells": ["fireball", "magic_missile"]}
    )
    s.update(phase="combat", active_group=["P1", "P2", "P3", "P4"])
    with pytest.raises(GameError, match="准备"):
        e.preview(
            s, {"type": "cast", "actor": "P3", "ability": "mage_armor", "target": "P3"}
        )
    with patch("core.dice.die", side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(
            s,
            {
                "type": "cast",
                "actor": "P3",
                "ability": "magic_missile",
                "slot": 3,
                "target": "E1",
            },
        )
    assert (
        after["actors"]["E1"]["hp"] == 975 and after["actors"]["P3"]["slots"]["3"] == 1
    )
    safe(after)
    after, _ = e.execute(after, {"type": "recover", "actor": "P3", "slot": 3})
    assert (
        after["actors"]["P3"]["slots"]["3"] == 2
        and after["actors"]["P3"]["resources"]["arcane_recovery"] == 0
    )
    after, _ = e.execute(after, {"type": "rest", "kind": "short"})
    assert after["actors"]["P3"]["resources"]["arcane_recovery"] == 0
    with pytest.raises(GameError):
        e.execute(after, {"type": "recover", "actor": "P3", "slot": 3})
    s["actors"]["P3"]["prepared"].append("mage_armor")
    with patch("core.dice.die", side_effect=lambda state, sides: min(10, sides)):
        _, events = e.execute(
            s, {"type": "cast", "actor": "P3", "ability": "fire_bolt", "target": "E1"}
        )
    assert next(x for x in events if x["kind"] == "damage")["details"]["dice"] == "2d10"


def test_fireball_sculpting_hidden_damage_and_safe_events():
    e, s = arena()
    s["actors"]["E2"].update(position="F4", hidden=True)
    # Hidden at >3m from every observer but within explosion radius.
    s["actors"]["E2"]["position"] = "E6"
    before = deepcopy(s)
    preview = e.preview(
        s, {"type": "cast", "actor": "P3", "ability": "fireball", "target": "E1"}
    )
    assert "E2" not in preview["targets"] and s == before
    with patch("core.dice.die", side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(s, preview["command"])
    assert after["actors"]["E2"]["hp"] < 1000
    assert all(
        after["actors"][p]["hp"] == s["actors"][p]["hp"]
        for p in ["P1", "P2", "P3", "P4"]
    )
    assert "E2" not in json.dumps(events) and "E6" not in json.dumps(events)


def test_aid_recast_cannot_heal_again_and_upcast_only_adds_difference():
    e, s = arena()
    c = {"type": "cast", "actor": "P4", "ability": "aid"}
    s, _ = e.execute(s, c)
    s["actors"]["P4"]["budgets"]["action"] = 1
    s["actors"]["P1"]["hp"] -= 10
    hp = s["actors"]["P1"]["hp"]
    s, _ = e.execute(s, c)
    assert s["actors"]["P1"]["hp"] == hp
    s["actors"]["P4"]["budgets"]["action"] = 1
    s, _ = e.execute(s, {**c, "slot": 3})
    assert s["actors"]["P1"]["hp"] == hp + 5


def test_rally_revives_without_refresh_and_defence_expires():
    e, s = arena()
    t = s["actors"]["P2"]
    t.update(hp=0, conditions=["downed"], ended=True, last_turn_round=1)
    t["budgets"]["action"] = 0
    s, _ = e.execute(
        s, {"type": "ability", "actor": "P1", "ability": "rally", "target": "P2"}
    )
    assert (
        s["actors"]["P2"]["hp"] == 1
        and not s["actors"]["P2"]["ended"]
        and s["actors"]["P2"]["budgets"]["action"] == 0
    )
    s, _ = e.execute(
        s, {"type": "ability", "actor": "P2", "ability": "patient_defence"}
    )
    assert e._attack_context(s, s["actors"]["E1"], s["actors"]["P2"])[1]
    e._start_actor(s, s["actors"]["P2"])
    assert "dodge" not in s["actors"]["P2"]["conditions"]


@pytest.mark.parametrize(
    "command",
    [
        {"type": "equip", "actor": [], "slot": "armor", "item": None},
        {"type": "equip", "actor": "P1", "slot": [], "item": None},
        {"type": "prepare", "actor": "P3", "spells": [[]]},
        {"type": "levelup", "actor": "P1", "level": 5},
    ],
)
def test_bad_management_is_game_error_and_atomic(command):
    e, s = arena()
    safe(s)
    before = deepcopy(s)
    with pytest.raises(GameError):
        e.execute(s, command)
    assert s == before


def test_content_rejects_empty_unknown_effect_snapshot_and_resource():
    for mutate in [
        lambda d: d["abilities"]["rally"].update(effect=""),
        lambda d: d["classes"]["monk"].pop("3"),
        lambda d: d["classes"]["fighter"]["5"]["resources"].update(fake=1),
        lambda d: d["classes"]["wizard"]["5"]["abilities"].append("placeholder"),
    ]:
        data = deepcopy(DATA)
        mutate(data)
        with pytest.raises(ValueError):
            validate_growth(data)


class FixtureEngine(Engine):
    initial = None

    def new_run(self, *args, **kwargs):
        return deepcopy(self.initial)


def test_old_save_read_only_adapter_and_explicit_atomic_upgrade(tmp_path):
    e, s = arena(1)
    safe(s)
    s.update(content_version="0.1.0-slice", rules_version="0.1.0-provisional")
    for a in s["actors"].values():
        for k in (
            "growth_version",
            "equipment",
            "inventory",
            "prepared",
            "known_spells",
            "known_before",
            "proficiency",
            "extra_attack",
            "always_prepared",
            "passives",
        ):
            a.pop(k, None)
    from core.content import load_content

    old = load_content()
    for a in s["actors"].values():
        if a["side"] == "party":
            template = old["classes"][a["template"]]
            a["ac"] = a["base_ac"] = template["ac"]
            a["resources"] = dict(template["resources"], potions=2)
            a["max_resources"] = deepcopy(template["resources"])
    s["map"]["walls"].append("F5")
    before = deepcopy(s)
    e.view(s)
    assert s == before and s["actors"]["P1"]["ac"] == 18
    fixture = FixtureEngine()
    fixture.initial = s
    db = tmp_path / "old.sqlite3"
    repo = Repository(db, fixture)
    r = repo.create_run(command_id="old")
    rid = r["run_id"]
    command = {"type": "levelup", "actor": "P1"}
    repo.preview(rid, command)
    assert repo._load(rid) == before
    receipt = repo.submit(rid, "upgrade", 0, command)
    assert receipt["view"]["party"][0]["level"] == 2
    assert repo._load(rid)["content_version"] == "0.6.0-m6"
    repo.close()
    repo = Repository(db)
    assert repo.submit(rid, "upgrade", 0, command) == receipt
    with pytest.raises(RepositoryError) as exc:
        repo.submit(rid, "stale", 0, command)
    assert exc.value.status_code == 409
    assert repo.get_view(rid) == receipt["view"]
    repo.close()


def test_level_five_reaction_restart_preserves_extra_attack_and_resources(tmp_path):
    e, s = arena()
    s["actors"]["P1"]["extra_attacks"] = 1
    s["actors"]["E1"]["position"] = "A2"
    e._continue_move(s, s["actors"]["E1"], ["A3", "A4"])
    assert s["pending"] and s["pending"]["reactor"] == "P1"
    fixture = FixtureEngine()
    fixture.initial = s
    db = tmp_path / "reaction.sqlite3"
    repo = Repository(db, fixture)
    r = repo.create_run(command_id="create")
    repo.close()
    repo = Repository(db)
    before = repo.get_view(r["run_id"])
    assert before["pending"]
    result = repo.react(r["run_id"], "react", 0, "accept")
    a = repo._load(r["run_id"])["actors"]["P1"]
    assert a["extra_attacks"] == 1 and a["budgets"]["reaction"] == 0
    assert repo.react(r["run_id"], "react", 0, "accept") == result
    repo.close()




def test_chinese_growth_menu_preview_cancel_confirm(tmp_path):
    from client.cli import Client
    from client.transport import LocalTransport

    e, s = arena(3)
    safe(s)
    fixture = FixtureEngine()
    fixture.initial = s
    transport = LocalTransport(tmp_path / "menu.sqlite3")
    transport.repository.close()
    transport.repository = Repository(tmp_path / "menu.sqlite3", fixture)
    output = []
    client = Client(transport, output=output.append)
    client.create()
    client.handle("7")
    assert client.ui.stage == "growth"
    assert "成长与装备" in "\n".join(output)
    client.handle("1")
    assert client.ui.stage == "confirm"
    client.handle("0")
    assert client.ui.stage == "growth" and client.view["party"][0]["level"] == 3
    client.handle("1")
    client.handle("c")
    assert client.view["party"][0]["level"] == 4
    transport.close()


def test_developer_upgrade_equip_replay_backup_and_retry(tmp_path):
    e, s = arena(3)
    safe(s)
    s["development"] = True
    s, _ = e.execute(s, {"type": "levelup", "actor": "P1"})
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P1", "slot": "off_hand", "item": None}
    )
    s, _ = e.execute(
        s, {"type": "equip", "actor": "P1", "slot": "main_hand", "item": "greatsword"}
    )
    command = {"type": "practice"}
    before = deepcopy(s)
    e.preview(s, command)
    assert s == before
    fixture = FixtureEngine()
    fixture.initial = s
    repo = Repository(tmp_path / "practice.sqlite3", fixture)
    created = repo.create_run(command_id="create")
    rid = created["run_id"]
    result = repo.submit(rid, "practice", s["revision"], command)
    assert result["view"]["party"][0]["level"] == 4
    assert result["view"]["party"][0]["equipment"]["main_hand"] == "greatsword"
    assert result["view"]["phase"] == "combat" and result["view"]["development"]
    assert repo.submit(rid, "practice", s["revision"], command) == result
    repo.backup(tmp_path / "backup.sqlite3")
    repo.close()
    restored = Repository(tmp_path / "backup.sqlite3")
    current = restored._load(rid)
    assert restored.get_view(rid) == result["view"]
    restored.close()
    current["phase"] = "defeated"
    retry, _ = e.retry(current)
    assert retry["id"] == rid and retry["actors"]["P1"]["level"] == 4
    assert retry["actors"]["P1"]["equipment"]["main_hand"] == "greatsword"
    assert retry["development"]
    ordinary = deepcopy(s)
    ordinary.pop("development")
    with pytest.raises(GameError):
        e.execute(ordinary, command)


def test_targetless_spells_parse_and_new_preview_attack_counts():
    from client.parser import parse_line

    assert parse_line("preview cast P4 aid slot=3")[1] == {
        "type": "cast",
        "actor": "P4",
        "ability": "aid",
        "slot": 3,
    }
    e, s = arena()
    p = e.preview(
        s,
        {
            "type": "cast",
            "actor": "P3",
            "ability": "scorching_ray",
            "slot": 3,
            "target": "E1",
        },
    )
    assert (
        p["attack_count"] == 4
        and p["cost"] == {"action": 1, "slot_3": 1}
        and 0 < p["hit_probability"] < 1
    )
    s["actors"]["P1"]["equipment"]["armor"] = None
    s["actors"]["P1"]["equipment"]["off_hand"] = "shield"
    from core.growth import recalculate

    recalculate(s["actors"]["P1"], e.content["items"])
    s["actors"]["P1"]["position"] = "C2"
    s["actors"]["P2"]["position"] = "B2"
    s, _ = e.execute(
        s, {"type": "cast", "actor": "P3", "ability": "mage_armor", "target": "P1"}
    )
    assert s["actors"]["P1"]["ac"] == 16  # 13 + Dexterity 1 + shield 2


def test_isolated_developer_entry_uses_real_terminal_and_temp_database():
    command = [
        sys.executable,
        "-m",
        "tools.dev_play",
        "--levels",
        "2,3,4,5",
        "--equip",
        "P1:off_hand:none",
        "--equip",
        "P1:main_hand:greatsword",
    ]
    process = subprocess.run(
        command,
        input="party\nq\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=__import__("os").environ | {"PYTHONIOENCODING": "utf-8"},
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert (
        "CmdRPG-M3-" in process.stdout
        and "双手巨剑" in process.stdout
        and "等级 5" in process.stdout
    )
