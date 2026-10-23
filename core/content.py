"""Load the deliberately small, versioned content package without executing it."""

from collections import deque
import json
import math
from pathlib import Path
import re


STATS = frozenset({"str", "dex", "con", "int", "wis", "cha"})
DAMAGE_TYPES = frozenset({"acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic", "piercing", "poison", "psychic", "radiant", "slashing", "thunder"})
EFFECTS = {
    "fire_bolt": ("action", 0, 18),
    "magic_missile": ("action", 1, 18),
    "mage_armor": ("action", 1, 1.5),
    "sacred_flame": ("action", 0, 18),
    "healing_word": ("bonus", 1, 18),
    "cure_wounds": ("action", 1, 1.5),
    "bless": ("action", 1, 9),
    "second_wind": ("bonus", 0, 0),
    "unarmed_strike": ("bonus", 0, 1.5),
}
_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_DICE = re.compile(r"([1-9][0-9]?)d(4|6|8|10|12|20)(?:[+-][0-9]+)?\Z")
_CELL = re.compile(r"([A-P])([1-9]|1[0-2])\Z")


def _fail(location, message):
    raise ValueError(f"{location}: {message}")


def _object(value, location, required=None, optional=()):
    if not isinstance(value, dict):
        _fail(location, "expected object")
    if required is not None:
        missing = set(required) - value.keys()
        extra = value.keys() - set(required) - set(optional)
        if missing:
            _fail(location, f"missing fields {sorted(missing)}")
        if extra:
            _fail(location, f"unknown fields {sorted(extra)}")
    return value


def _number(value, location, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(location, "expected number")
    if not minimum <= value <= maximum or not math.isfinite(value):
        _fail(location, f"must be finite and in [{minimum}, {maximum}]")
    if integer and not isinstance(value, int):
        _fail(location, "expected integer")
    return value


def _text(value, location):
    if not isinstance(value, str) or not value.strip():
        _fail(location, "expected nonempty string")
    return value


def _enum(value, location, allowed):
    if not isinstance(value, str) or value not in allowed:
        _fail(location, f"expected one of {sorted(allowed)}")
    return value


def _list(value, location):
    if not isinstance(value, list):
        _fail(location, "expected array")
    return value


def _ids(value, location):
    _object(value, location)
    if not value:
        _fail(location, "must not be empty")
    for ident in value:
        if not _ID.fullmatch(ident):
            _fail(location, f"invalid id {ident!r}")


def _stats(value, location, minimum=1, maximum=30):
    _object(value, location, STATS)
    for stat, score in value.items():
        _number(score, f"{location}.{stat}", minimum, maximum, integer=True)


def _save_proficiencies(value, location, required=False):
    _list(value, location)
    if (required and len(value) != 2) or len(value) > 2:
        _fail(location, "class requires two saving throw proficiencies; enemies allow zero to two")
    seen = set()
    for index, stat in enumerate(value):
        _enum(stat, f"{location}[{index}]", STATS)
        if stat in seen:
            _fail(location, "duplicate saving throw proficiency")
        seen.add(stat)


def _dice(value, location):
    if not isinstance(value, str) or not _DICE.fullmatch(value):
        _fail(location, "expected dice such as 1d8 or 2d4+2 (d4/d6/d8/d10/d12/d20)")


def _cell(value, location):
    if not isinstance(value, str) or not _CELL.fullmatch(value):
        _fail(location, "expected coordinate A1 through P12")
    match = _CELL.fullmatch(value)
    return ord(match[1]) - ord("A"), int(match[2]) - 1


def _cells(value, location):
    _list(value, location)
    seen = set()
    for index, coordinate in enumerate(value):
        _cell(coordinate, f"{location}[{index}]")
        if coordinate in seen:
            _fail(location, f"duplicate coordinate {coordinate}")
        seen.add(coordinate)
    return seen


class _JSONObjectPairs(list):
    """Distinguish JSON objects from arrays until duplicate paths are known."""


def _duplicate_checked(value, location="content"):
    if isinstance(value, _JSONObjectPairs):
        result = {}
        for key, child in value:
            child_location = f"{location}.{key}"
            if key in result:
                _fail(child_location, f"duplicate key {key!r}")
            result[key] = _duplicate_checked(child, child_location)
        return result
    if isinstance(value, list):
        return [_duplicate_checked(child, f"{location}[{index}]") for index, child in enumerate(value)]
    return value


def _invalid_constant(value):
    raise ValueError(f"JSON: non-finite number {value}")


def _validate(pack):
    _object(pack, "content", {"version", "rules_version", "save_version", "classes", "abilities", "items", "enemies", "map", "encounter"})
    _text(pack["version"], "version")
    _text(pack["rules_version"], "rules_version")
    _number(pack["save_version"], "save_version", 1, 1, integer=True)
    for category in ("classes", "abilities", "items", "enemies"):
        _ids(pack[category], category)
    if set(pack["classes"]) != {"fighter", "monk", "wizard", "cleric"}:
        _fail("classes", "slice requires exactly fighter, monk, wizard and cleric")

    for ident, ability in pack["abilities"].items():
        loc = f"abilities.{ident}"
        _object(ability, loc, {"name", "effect", "cost", "range", "level", "description"}, {"concentration"})
        _text(ability["name"], f"{loc}.name")
        _text(ability["description"], f"{loc}.description")
        effect = _enum(ability["effect"], f"{loc}.effect", EFFECTS)
        if ident != effect:
            _fail(loc, "slice ability id must match its implemented effect; aliases are unsupported")
        cost, level, maximum = EFFECTS[effect]
        _enum(ability["cost"], f"{loc}.cost", {cost})
        _number(ability["level"], f"{loc}.level", level, level, integer=True)
        _number(ability["range"], f"{loc}.range", 0, maximum)
        if ability["range"] != maximum:
            _fail(f"{loc}.range", f"slice effect {effect} requires {maximum} metres")
        concentration = ability.get("concentration", False)
        if not isinstance(concentration, bool):
            _fail(f"{loc}.concentration", "expected boolean")
        if concentration != (effect == "bless"):
            _fail(f"{loc}.concentration", "only bless requires concentration in this slice")

    for ident, item in pack["items"].items():
        loc = f"items.{ident}"
        _object(item, loc, {"name", "slot", "kind", "dice", "damage_type", "description"})
        _text(item["name"], f"{loc}.name")
        _text(item["description"], f"{loc}.description")
        kind = _enum(item["kind"], f"{loc}.kind", {"weapon", "potion", "armor", "shield"})
        expected_slot = {"weapon": "main_hand", "potion": "consumable", "armor": "armor", "shield": "off_hand"}[kind]
        _enum(item["slot"], f"{loc}.slot", {expected_slot})
        if kind in {"weapon", "potion"}:
            _dice(item["dice"], f"{loc}.dice")
            _enum(item["damage_type"], f"{loc}.damage_type", {"healing"} if kind == "potion" else DAMAGE_TYPES)
            if kind == "potion" and (ident != "healing_potion" or item["dice"] != "2d4+2"):
                _fail(loc, "slice supports only healing_potion with 2d4+2 healing")
        elif item["dice"] is not None or item["damage_type"] is not None:
            _fail(loc, "armor/shield dice and damage_type must be null")

    for ident, template in pack["classes"].items():
        loc = f"classes.{ident}"
        _object(template, loc, {"name", "subclass", "level", "stats", "hp", "ac", "speed", "attack_stat", "save_proficiencies", "weapon", "abilities", "spell_stat", "slots", "resources"})
        _text(template["name"], f"{loc}.name")
        _text(template["subclass"], f"{loc}.subclass")
        _number(template["level"], f"{loc}.level", 1, 1, integer=True)
        _stats(template["stats"], f"{loc}.stats", 3, 20)
        _save_proficiencies(template["save_proficiencies"], f"{loc}.save_proficiencies", required=True)
        _number(template["hp"], f"{loc}.hp", 1, 100, integer=True)
        _number(template["ac"], f"{loc}.ac", 1, 30, integer=True)
        _number(template["speed"], f"{loc}.speed", 1.5, 30)
        _enum(template["attack_stat"], f"{loc}.attack_stat", STATS)
        weapon = _text(template["weapon"], f"{loc}.weapon")
        if weapon not in pack["items"] or pack["items"][weapon]["kind"] != "weapon":
            _fail(f"{loc}.weapon", f"missing weapon reference {weapon!r}")
        ability_ids = _list(template["abilities"], f"{loc}.abilities")
        seen = set()
        for index, ability_id in enumerate(ability_ids):
            _text(ability_id, f"{loc}.abilities[{index}]")
            if ability_id not in pack["abilities"]:
                _fail(f"{loc}.abilities[{index}]", f"missing ability reference {ability_id!r}")
            if ability_id in seen:
                _fail(f"{loc}.abilities", f"duplicate ability {ability_id}")
            seen.add(ability_id)
        if template["spell_stat"] is not None:
            _enum(template["spell_stat"], f"{loc}.spell_stat", {"int", "wis", "cha"})
        _object(template["slots"], f"{loc}.slots")
        for slot, count in template["slots"].items():
            _enum(slot, f"{loc}.slots", {"1"})
            _number(count, f"{loc}.slots.{slot}", 0, 2, integer=True)
        _object(template["resources"], f"{loc}.resources")
        for resource, count in template["resources"].items():
            _enum(resource, f"{loc}.resources", {"second_wind"})
            _number(count, f"{loc}.resources.{resource}", 0, 1, integer=True)
        spells = [pack["abilities"][ability] for ability in ability_ids if pack["abilities"][ability]["effect"] not in {"second_wind", "unarmed_strike"}]
        if spells and template["spell_stat"] is None:
            _fail(f"{loc}.spell_stat", "spell abilities require a spell stat")
        if template["slots"] and template["spell_stat"] is None:
            _fail(f"{loc}.slots", "spell slots require a spell stat")

    for ident, enemy in pack["enemies"].items():
        loc = f"enemies.{ident}"
        _object(enemy, loc, {"name", "hp", "ac", "stats", "speed", "range", "dice", "damage_type", "role"}, {"attack_stat", "save_proficiencies", "resistances", "spell"})
        if "resistances" in enemy and (not isinstance(enemy['resistances'], list) or any(not isinstance(x, str) or x not in DAMAGE_TYPES for x in enemy['resistances']) or len(set(enemy['resistances'])) != len(enemy['resistances'])):
            _fail(loc, "invalid resistance types")
        if "spell" in enemy:
            _object(enemy['spell'], loc + '.spell', {'save', 'dc'})
            _enum(enemy['spell']['save'], loc + '.spell.save', STATS)
            _number(enemy['spell']['dc'], loc + '.spell.dc', 8, 20, integer=True)
            if enemy['role'] != 'ranged':
                _fail(loc, 'spell requires ranged role')
        _text(enemy["name"], f"{loc}.name")
        _number(enemy["hp"], f"{loc}.hp", 1, 1000, integer=True)
        _number(enemy["ac"], f"{loc}.ac", 1, 30, integer=True)
        _stats(enemy["stats"], f"{loc}.stats")
        _save_proficiencies(enemy.get("save_proficiencies", []), f"{loc}.save_proficiencies")
        _number(enemy["speed"], f"{loc}.speed", 1.5, 30)
        _number(enemy["range"], f"{loc}.range", 1.5, 36)
        _enum(enemy["role"], f"{loc}.role", {"melee", "ranged"})
        _enum(enemy.get("attack_stat", "str"), f"{loc}.attack_stat", STATS)
        if enemy["role"] == "melee" and enemy["range"] != 1.5:
            _fail(f"{loc}.range", "slice melee range must be 1.5 metres")
        _dice(enemy["dice"], f"{loc}.dice")
        _enum(enemy["damage_type"], f"{loc}.damage_type", DAMAGE_TYPES)

    board = pack["map"]
    _object(board, "map", {"width", "height", "cell_size", "walls", "heights", "difficult", "hazards", "landmarks"})
    for field, fixed in (("width", 16), ("height", 12)):
        _number(board[field], f"map.{field}", fixed, fixed, integer=True)
    _number(board["cell_size"], "map.cell_size", 1.5, 1.5)
    walls = _cells(board["walls"], "map.walls")
    difficult = _cells(board["difficult"], "map.difficult")
    if walls & difficult:
        _fail("map.difficult", "difficult terrain overlaps a wall")
    _object(board["heights"], "map.heights")
    for coordinate, height in board["heights"].items():
        _cell(coordinate, f"map.heights.{coordinate}")
        _number(height, f"map.heights.{coordinate}", 0, 30)
        if coordinate in walls:
            _fail(f"map.heights.{coordinate}", "standable height overlaps a wall")
    _object(board["hazards"], "map.hazards")
    for coordinate, hazard in board["hazards"].items():
        _cell(coordinate, f"map.hazards.{coordinate}")
        _object(hazard, f"map.hazards.{coordinate}", {"damage", "description"})
        _number(hazard["damage"], f"map.hazards.{coordinate}.damage", 1, 100, integer=True)
        _text(hazard["description"], f"map.hazards.{coordinate}.description")
        if coordinate in walls:
            _fail(f"map.hazards.{coordinate}", "hazard overlaps a wall")
    landmark_cells = []
    for index, landmark in enumerate(_list(board["landmarks"], "map.landmarks")):
        loc = f"map.landmarks[{index}]"
        _object(landmark, loc, {"position", "name", "description"})
        _cell(landmark["position"], f"{loc}.position")
        _text(landmark["name"], f"{loc}.name")
        _text(landmark["description"], f"{loc}.description")
        landmark_cells.append(landmark["position"])
    if len(set(landmark_cells)) != len(landmark_cells):
        _fail("map.landmarks", "duplicate landmark position")

    encounter = pack["encounter"]
    _object(encounter, "encounter", {"name", "description", "party_spawns", "enemies"})
    _text(encounter["name"], "encounter.name")
    _text(encounter["description"], "encounter.description")
    spawns = _cells(encounter["party_spawns"], "encounter.party_spawns")
    if len(spawns) != 4:
        _fail("encounter.party_spawns", "exactly four unique party spawns required")
    instances = _list(encounter["enemies"], "encounter.enemies")
    if not 1 <= len(instances) <= 8:
        _fail("encounter.enemies", "expected 1 to 8 enemy instances")
    enemy_ids = set()
    occupied = set(spawns)
    enemy_cells = []
    for index, instance in enumerate(instances):
        loc = f"encounter.enemies[{index}]"
        _object(instance, loc, {"id", "prototype", "position"})
        if not isinstance(instance["id"], str) or not re.fullmatch(r"E[1-9][0-9]*", instance["id"]):
            _fail(f"{loc}.id", "expected enemy instance id such as E1")
        if instance["id"] in enemy_ids:
            _fail(f"{loc}.id", "duplicate enemy instance id")
        enemy_ids.add(instance["id"])
        _text(instance["prototype"], f"{loc}.prototype")
        if instance["prototype"] not in pack["enemies"]:
            _fail(f"{loc}.prototype", "missing enemy prototype reference")
        _cell(instance["position"], f"{loc}.position")
        if instance["position"] in occupied:
            _fail(f"{loc}.position", "spawn position is already occupied")
        occupied.add(instance["position"])
        enemy_cells.append(instance["position"])
    blocked = walls | set(board["hazards"])
    required_cells = spawns | set(enemy_cells) | set(landmark_cells)
    if required_cells & blocked:
        _fail("map", f"spawn or landmark on wall/hazard: {sorted(required_cells & blocked)}")

    # A conservative safe path, using four neighbours and traversable height steps.
    # This verifies topology, not an enemy-free route or tactical balance.
    start = encounter["party_spawns"][0]
    reachable = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        x, y = _cell(current, "map")
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= nx < 16 and 0 <= ny < 12):
                continue
            neighbour = f"{chr(ord('A') + nx)}{ny + 1}"
            if neighbour in blocked or neighbour in reachable:
                continue
            if abs(board["heights"].get(neighbour, 0) - board["heights"].get(current, 0)) > 1.5:
                continue
            reachable.add(neighbour)
            queue.append(neighbour)
    unreachable = required_cells - reachable
    if unreachable:
        _fail("map", f"spawn/critical path unreachable without hazards or climbing: {sorted(unreachable)}")


def load_content(path=None):
    """Return validated JSON content, or raise ValueError with record context.

    The default resolves from this module, so callers need not run in the repo.
    Each call returns a new object; no mutable content cache is shared with runs.
    """
    source = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "content" / "pack.json"
    try:
        with source.open("r", encoding="utf-8-sig") as stream:
            raw = json.load(stream, object_pairs_hook=_JSONObjectPairs, parse_constant=_invalid_constant)
            pack = _duplicate_checked(raw)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"content {source}: {exc}") from exc
    _validate(pack)
    return pack
