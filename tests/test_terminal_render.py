"""Terminal output stays readable without colors or a CJK-width grid."""

import re
import unicodedata
import unittest

from client.render import (inspect, render_actions, render_map,
                           render_menu_actions, render_options, render_preview,
                           render_view)
from core.engine import Engine


def visible_view():
    return {
        "name": "晨光小队", "revision": 7, "phase": "combat", "round": 2,
        "active": ["P3"], "party": [
            {"id": "P1", "name": "战士", "position": "A1", "hp": 4,
             "max_hp": 12, "conditions": [], "ended": True},
            {"id": "P3", "name": "法师", "position": "B2", "hp": 8,
             "max_hp": 8, "conditions": [], "budgets": {"action": 1, "movement": 9},
             "abilities": [{"id": "fire_bolt", "name": "火焰箭", "cost": "action",
                            "available": True, "description": "命中造成1d10火焰伤害。"}],
             "menu_actions": [
                 {"name": "移动", "description": "选择目的地。", "target_mode": "move",
                  "cost": {}, "available": True},
                 {"name": "火焰箭", "cost": {"action": 1}, "available": True,
                  "description": "命中造成1d10火焰伤害。", "remaining": {"action": 1}},
                 {"name": "魔法飞弹", "cost": {"action": 1, "slot_1": 1},
                  "remaining": {"slot_1": 0}, "available": False, "reason": "法术位不足",
                  "description": "三枚飞弹自动命中。"},
             ]},
        ],
        "enemies": [{"id": "E1", "name": "前哨守卫", "position": "D2",
                     "hp": 13, "max_hp": 13}],
        "map": {"width": 4, "height": 3, "cell_size": 1.5,
                "walls": ["C1"], "hazards": {"A3": {"damage": 2}},
                "difficult": ["B3"], "heights": {"C3": 1.5},
                "landmarks": [{"position": "B1", "name": "断墙门洞", "description": "东西通路。"}]},
        "recent_events": [{"seq": 9, "text": "唯一的详细战斗日志", "details": {"roll": 17}}],
    }


class TerminalRenderTests(unittest.TestCase):
    def test_fixed_ascii_cells_with_chinese_names_outside_grid(self):
        text = render_map(visible_view(), selected="P3")
        rows = [line for line in text.splitlines() if re.match(r"\s*\d+ \|", line)]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(line) == 5 + 4 * 4 for line in rows))
        self.assertTrue(all(line.isascii() for line in rows))
        self.assertIn("|P3*|", rows[1])
        self.assertTrue(all(len(cell) == 3 for row in rows for cell in row.split("|")[1:-1]))
        header = next(line for line in text.splitlines() if line.startswith("    |"))
        self.assertEqual(len(header), len(rows[0]))
        for column, label in enumerate("ABCD"):
            self.assertEqual(header[6 + 4 * column], label)
        self.assertEqual(rows[0][6 + 4 * 2], "#")
        self.assertEqual(rows[2][6 + 4 * 3], ".")
        self.assertEqual(rows[1].index("3"), header.index("B"))
        self.assertIn("法师（已选）", text)
        self.assertIn("上北下南", text)
        self.assertIn("左西右东", text)
        self.assertIn("E1 前哨守卫 生命13/13", text)

    def test_reachability_keeps_danger_and_height_visible(self):
        text = render_map(visible_view(), "P3", ["A3", "B3", "C3", "D3"])
        row = next(line for line in text.splitlines() if line.startswith("  3 |"))
        self.assertEqual(row.split("|")[1:-1], [" +!", " +:", " +^", " + "])
        self.assertIn("真实路径可达", text)
        view = visible_view()
        view["enemies"][0].update(position="D3", hp=0, conditions=["dead"])
        corpse_map = render_map(view, "P3", ["D3"])
        corpse_row = next(line for line in corpse_map.splitlines() if line.startswith("  3 |"))
        self.assertEqual(corpse_row.split("|")[-2], " + ")
        self.assertIn("阵亡敌人：E1 前哨守卫 D3", corpse_map)
        self.assertIn("遗体不阻挡移动", corpse_map)
        self.assertIn("没有尸体掉落或搜刮", corpse_map)

    def test_dead_enemies_do_not_cover_party_or_terrain(self):
        view = visible_view()
        view["enemies"][0].update(position="B2", hp=0, conditions=["dead"])
        view["enemies"].append({"id": "E2", "name": "阵亡射手", "position": "A3",
                                "hp": 0, "max_hp": 9, "conditions": ["dead"]})
        text = render_map(view, "P3")
        rows = [line for line in text.splitlines() if re.match(r"\s*\d+ \|", line)]
        self.assertEqual(rows[1].split("|")[2], "P3*")
        self.assertEqual(rows[2].split("|")[1], " ! ")
        self.assertTrue(all("E1" not in row and "E2" not in row for row in rows))
        self.assertIn("可见敌人：当前无可见敌人", text)
        self.assertIn("阵亡敌人：E1 前哨守卫 B2；E2 阵亡射手 A3", text)
        self.assertEqual(view["enemies"][0]["conditions"], ["dead"])

    def test_compound_terrain_and_actor_covered_height_remain_explained(self):
        view = visible_view()
        view["map"]["landmarks"].append({"position": "C3", "name": "瞭望台石阶"})
        view["map"]["heights"]["B2"] = 1.5
        view["map"]["heights"]["A3"] = 1.5
        view["map"]["difficult"].append("A3")
        text = render_map(view, "P3", ["C3"])
        row = next(line for line in text.splitlines() if line.startswith("  3 |"))
        self.assertEqual(row.split("|")[1:-1], ["!:^", " : ", "+/^", " . "])
        self.assertIn("B2 高度1.5米", text)
        self.assertIn("A3 危险、困难地面、高度1.5米", text)
        self.assertIn("C3 台阶、高度1.5米", text)

    def test_sixteen_column_map_and_chinese_legend_fit_terminal(self):
        view = visible_view()
        view["map"]["width"] = 16
        view["map"]["height"] = 12
        text = render_map(view, "P3")
        rows = [line for line in text.splitlines() if re.match(r"\s*\d+ \|", line)]
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(len(row) == 69 for row in rows))
        for line in text.splitlines():
            display_width = sum(0 if unicodedata.combining(character) else
                                2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
                                for character in line)
            self.assertLessEqual(display_width, 78)

    def test_reported_positions_and_saved_dead_enemy_overlap_on_real_map(self):
        engine = Engine()
        state = engine.new_run(seed=42)
        for actor_id, position in {"P1": "H4", "P2": "K4", "P3": "C6", "P4": "I6",
                                   "E1": "E6", "E2": "K5", "E3": "L4"}.items():
            state["actors"][actor_id]["position"] = position
        text = render_map(engine.view(state), "P2")
        rows = [line for line in text.splitlines() if re.match(r"\s*\d+ \|", line)]
        self.assertEqual(rows[3].split("|")[11], "P2*")
        self.assertEqual(rows[3].split("|")[12], " E3")
        self.assertIn("K4 台阶、高度1.5米", text)
        self.assertIn("L4 高度3米", text)
        state["actors"]["P1"]["position"] = "K5"
        state["actors"]["E2"].update(hp=0, conditions=["dead"])
        text = render_map(engine.view(state), "P1")
        rows = [line for line in text.splitlines() if re.match(r"\s*\d+ \|", line)]
        self.assertEqual(rows[4].split("|")[11], "P1*")
        self.assertNotIn("E2", "\n".join(rows))
        self.assertIn("阵亡敌人：E2", text)
        self.assertIn("E2", state["actors"])

    def test_view_defaults_to_map_and_does_not_repeat_event_log(self):
        text = render_view(visible_view(), "P3")
        self.assertIn("文字地图", text)
        self.assertIn("当前可操作：P3 法师", text)
        self.assertIn("断墙门洞", text)
        self.assertIn("P3*", text)
        self.assertNotIn("唯一的详细战斗日志", text)
        self.assertNotIn("roll", text)

    def test_menu_shows_every_owned_skill_and_localized_resource_reason(self):
        text = render_menu_actions(visible_view(), "P3")
        self.assertIn("1. 移动", text)
        self.assertIn("2. 火焰箭", text)
        self.assertIn("3. 魔法飞弹", text)
        self.assertIn("1环法术位 1", text)
        self.assertIn("剩余：1环法术位 0", text)
        self.assertIn("不可用：法术位不足", text)
        self.assertNotIn("fire_bolt", text)
        self.assertNotIn("slot_1", text)
        self.assertNotIn("preview cast", text)
        view = visible_view()
        del view["party"][1]["menu_actions"]
        self.assertIn("火焰箭", render_actions(view, "P3"))

    def test_preview_uses_names_and_exact_probability_path_cost_warning(self):
        preview = {"command": {"type": "move", "actor": "P3"},
                   "description": "P3 法师移动。", "path": ["B2", "B1"],
                   "targets": ["E1"], "cost": {"movement": 1.5, "slot_1": 1,
                                              "second_wind": 1, "potions": 1, "short_rests": 1},
                   "hit_probability": 0.625, "failure_probability": 0.4,
                   "save_dc": 13, "warnings": ["可能遭到 E1 前哨守卫借机攻击。"]}
        text = render_preview(1, preview, 7, visible_view())
        for expected in ("P3 法师 | 移动 | 确认执行", "E1 前哨守卫", "B2 → B1",
                         "移动 1.5米", "1环法术位 1", "回气次数 1", "治疗药水 1",
                         "短休次数 1", "命中率：62.5%", "豁免失败概率：40.0%", "借机攻击",
                         "c 确认执行", "0 返回", "q 退出"):
            self.assertIn(expected, text)
        self.assertNotIn("前哨守卫 前哨守卫", text)
        self.assertNotIn("数字 1 确认", text)

    def test_team_rest_preview_lists_each_recovery_with_one_confirmation(self):
        preview = {"command": {"type": "rest"}, "description": "全队短休。",
                   "cost": {"short_rests": 1}, "rest_effects": [
                       {"actor": "P1", "name": "战士", "hp_before": 4, "hp_after": 12,
                        "healing": 8, "resources_restore": {"second_wind": 1}},
                       {"actor": "P3", "name": "法师", "hp_before": 8, "hp_after": 8,
                        "healing": 0, "resources_restore": {}},
                   ]}
        text = render_preview(1, preview, 7, visible_view())
        self.assertIn("全队恢复结果（一起确认）", text)
        self.assertIn("P1 战士：生命 4 → 12（恢复 8）", text)
        self.assertIn("恢复资源：回气次数 1", text)
        self.assertIn("P3 法师：生命 8 → 8（恢复 0）", text)
        self.assertEqual(text.count("c 确认执行"), 1)

    def test_options_explain_invalid_targets_and_late_landmarks(self):
        targets = render_options([
            {"target": "E1", "name": "前哨守卫", "hp": 13, "max_hp": 13,
             "distance": 3, "available": False, "reason": "超出1.5米射程",
             "distance_description": "向东 2 格，同一高度；按直线与高差计算，每格 1.5 米。"},
            {"target": "E2", "name": "瞭望手", "hp": 9, "max_hp": 9,
             "distance": 9, "available": True,
             "preview": {"failure_probability": 0.5}},
        ])
        self.assertIn("1. E1 前哨守卫", targets)
        self.assertIn("生命 13/13", targets)
        self.assertIn("不可用：超出1.5米射程", targets)
        self.assertIn("距离说明：向东 2 格，同一高度", targets)
        self.assertIn("豁免失败 50.0%", targets)
        destinations = render_options([
            {"destination": "B1", "name": "门洞西侧", "cost": 1.5,
             "available": True, "description": "对门洞后的敌人建立射线。"},
            {"destination": "K4", "name": "瞭望台台阶", "cost": 18,
             "available": False, "reason": "移动只剩9米", "description": "接近高台射手。"},
        ])
        self.assertIn("路径成本 1.5米 | 本回合可达", destinations)
        self.assertIn("路径成本 18米 | 本回合不可达：移动只剩9米", destinations)
        self.assertIn("对门洞后的敌人建立射线", destinations)
        with self.assertRaisesRegex(ValueError, "格坐标"):
            inspect(visible_view(), "bad")


if __name__ == "__main__":
    unittest.main()
