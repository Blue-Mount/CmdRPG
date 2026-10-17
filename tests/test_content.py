import copy
import json
from pathlib import Path
import tempfile
import unittest

from core.content import load_content


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.pack = load_content()

    def load_pack(self, pack):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            return load_content(path)

    def reject(self, pack, message):
        with self.assertRaisesRegex(ValueError, message):
            self.load_pack(pack)

    def test_default_package_is_only_four_level_one_templates(self):
        self.assertEqual(set(self.pack["classes"]), {"fighter", "monk", "wizard", "cleric"})
        self.assertTrue(all(value["level"] == 1 for value in self.pack["classes"].values()))
        self.assertEqual(self.pack["classes"]["monk"]["resources"], {})
        self.assertEqual(len(self.pack["encounter"]["enemies"]), 3)

    def test_load_returns_independent_objects(self):
        self.pack["classes"]["fighter"]["hp"] = 1
        self.assertEqual(load_content()["classes"]["fighter"]["hp"], 12)

    def test_rejects_duplicate_json_keys_including_nested_keys(self):
        texts = ['{"version": "first", "version": "second"}', '{"classes": {"fighter": {"hp": 12, "hp": 99}}}']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            for value in texts:
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "duplicate key"):
                        load_content(path)

    def test_duplicate_key_error_identifies_the_record_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            path.write_text('{"classes": {"fighter": {"hp": 12, "hp": 99}}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"content.classes.fighter.hp: duplicate key"):
                load_content(path)

    def test_rejects_unknown_or_missing_fields(self):
        self.pack["classes"]["fighter"]["unimplemented_upgrade"] = True
        self.reject(self.pack, "unknown fields")
        del self.pack["classes"]["fighter"]["unimplemented_upgrade"]
        del self.pack["classes"]["fighter"]["hp"]
        self.reject(self.pack, "missing fields")

    def test_rejects_missing_weapon_ability_and_enemy_references(self):
        mutations = (
            ("weapon", lambda p: p["classes"]["fighter"].update(weapon="missing")),
            ("ability", lambda p: p["classes"]["wizard"]["abilities"].append("missing")),
            ("prototype", lambda p: p["encounter"]["enemies"][0].update(prototype="missing")),
        )
        for label, mutate in mutations:
            with self.subTest(reference=label):
                pack = copy.deepcopy(self.pack)
                mutate(pack)
                self.reject(pack, "reference")

    def test_rejects_boolean_string_float_and_out_of_range_integer_stats(self):
        for value in (True, "16", 16.5, 31, 0, 10 ** 400):
            with self.subTest(value=value):
                self.pack["classes"]["fighter"]["stats"]["str"] = value
                self.reject(self.pack, "classes.fighter.stats.str")

    def test_rejects_invalid_and_duplicate_saving_throw_proficiencies(self):
        for proficiencies in ("str,con", ["str", "str"], ["str", "luck"], ["str", True], ["str"], ["str", "dex", "con"]):
            with self.subTest(proficiencies=proficiencies):
                pack = copy.deepcopy(self.pack)
                pack["classes"]["fighter"]["save_proficiencies"] = proficiencies
                self.reject(pack, "classes.fighter.save_proficiencies")
        self.pack["enemies"]["sentry"]["save_proficiencies"] = ["con", "con"]
        self.reject(self.pack, "duplicate saving throw proficiency")

    def test_enemy_saving_throw_proficiencies_are_optional(self):
        self.pack["enemies"]["sentry"]["save_proficiencies"] = ["con"]
        loaded = self.load_pack(self.pack)
        self.assertEqual(loaded["enemies"]["sentry"]["save_proficiencies"], ["con"])
        self.assertNotIn("save_proficiencies", loaded["enemies"]["lookout"])

    def test_rejects_future_growth_and_ki_resource(self):
        self.pack["classes"]["monk"]["level"] = 2
        self.reject(self.pack, "classes.monk.level")
        self.pack["classes"]["monk"]["level"] = 1
        self.pack["classes"]["monk"]["resources"] = {"ki": 2}
        self.reject(self.pack, "classes.monk.resources")

    def test_rejects_future_class_and_ability_alias(self):
        self.pack["classes"]["rogue"] = copy.deepcopy(self.pack["classes"]["fighter"])
        self.reject(self.pack, "slice requires exactly")
        del self.pack["classes"]["rogue"]
        self.pack["abilities"]["fake_fire_bolt"] = copy.deepcopy(self.pack["abilities"]["fire_bolt"])
        self.reject(self.pack, "aliases are unsupported")

    def test_rejects_potion_data_the_engine_cannot_interpret(self):
        self.pack["items"]["healing_potion"]["dice"] = "4d4+4"
        self.reject(self.pack, "only healing_potion")

    def test_rejects_invalid_slot_and_spell_stat(self):
        self.pack["classes"]["wizard"]["slots"] = {"2": 1}
        self.reject(self.pack, "classes.wizard.slots")
        self.pack["classes"]["wizard"]["slots"] = {"1": 2}
        self.pack["classes"]["wizard"]["spell_stat"] = None
        self.reject(self.pack, "spell abilities require")

    def test_rejects_unimplemented_effect_and_invalid_effect_metadata(self):
        for field, value in (("effect", "fireball"), ("cost", "bonus"), ("level", 2), ("range", 19), ("concentration", True)):
            with self.subTest(field=field):
                pack = copy.deepcopy(self.pack)
                pack["abilities"]["fire_bolt"][field] = value
                self.reject(pack, f"abilities.fire_bolt.{field}")
        self.pack["abilities"]["bless"]["concentration"] = "yes"
        self.reject(self.pack, "expected boolean")

    def test_rejects_duplicate_ability_and_enemy_instance_id(self):
        self.pack["classes"]["wizard"]["abilities"].append("fire_bolt")
        self.reject(self.pack, "duplicate ability")
        self.pack["classes"]["wizard"]["abilities"].pop()
        self.pack["encounter"]["enemies"][1]["id"] = "E1"
        self.reject(self.pack, "duplicate enemy instance id")

    def test_rejects_invalid_dice(self):
        for dice in ("0d6", "1d3", "eval(1)", "-1d6", "1d8 ", None):
            with self.subTest(dice=dice):
                self.pack["items"]["longsword"]["dice"] = dice
                self.reject(self.pack, "items.longsword.dice")

    def test_rejects_illegal_item_slot_and_damage_type(self):
        self.pack["items"]["longsword"]["slot"] = "ring"
        self.reject(self.pack, "items.longsword.slot")
        self.pack["items"]["longsword"]["slot"] = "main_hand"
        self.pack["items"]["longsword"]["damage_type"] = "healing"
        self.reject(self.pack, "items.longsword.damage_type")

    def test_rejects_wrong_dimensions_and_non_finite_numbers(self):
        for value in (15, 16.0, True):
            with self.subTest(width=value):
                self.pack["map"]["width"] = value
                self.reject(self.pack, "map.width")
        self.pack["map"]["width"] = 16
        self.pack["map"]["heights"]["L4"] = float("nan")
        self.reject(self.pack, "non-finite number")

    def test_rejects_out_of_bounds_and_duplicate_coordinates(self):
        self.pack["map"]["walls"].append("Q5")
        self.reject(self.pack, "coordinate A1 through P12")
        self.pack["map"]["walls"][-1] = "F2"
        self.reject(self.pack, "duplicate coordinate")

    def test_rejects_terrain_on_walls(self):
        self.pack["map"]["difficult"].append("F2")
        self.reject(self.pack, "overlaps a wall")
        self.pack["map"]["difficult"].pop()
        self.pack["map"]["heights"]["F2"] = 1.5
        self.reject(self.pack, "overlaps a wall")

    def test_rejects_spawn_count_overlap_walls_and_hazards(self):
        cases = (
            ["B6", "B7", "B8"],
            ["B6", "B7", "B8", "B8"],
            ["B6", "B7", "B8", "H6"],
            ["B6", "B7", "B8", "F2"],
            ["B6", "B7", "B8", "H9"],
        )
        for spawns in cases:
            with self.subTest(spawns=spawns):
                self.pack["encounter"]["party_spawns"] = spawns
                self.reject(self.pack, "spawns|duplicate coordinate|occupied|wall/hazard")

    def test_rejects_unreachable_landmark_and_enemy(self):
        self.pack["map"]["walls"].extend(["L9", "L11", "K10", "M10"])
        self.pack["map"]["landmarks"][-1]["position"] = "L10"
        self.reject(self.pack, "critical path unreachable")

    def test_rejects_platform_without_traversable_height_step(self):
        self.pack["map"]["heights"]["K3"] = 0
        self.pack["map"]["heights"]["K4"] = 0
        self.reject(self.pack, "critical path unreachable")

    def test_rejects_unsafe_only_route_to_critical_point(self):
        self.pack["map"]["walls"].extend(["L9", "L11", "K10"])
        self.pack["map"]["hazards"]["M10"] = {"damage": 2, "description": "测试危险地面"}
        self.pack["map"]["landmarks"][-1]["position"] = "L10"
        self.reject(self.pack, "critical path unreachable")

    def test_load_errors_are_value_errors(self):
        with self.assertRaises(ValueError):
            load_content(Path("does-not-exist.json"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            path.write_text("{ malformed", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_content(path)


if __name__ == "__main__":
    unittest.main()
