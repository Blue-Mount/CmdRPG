"""JSON-safe, versioned PRNG with unbiased die sampling.

This is gameplay randomness, never an authentication primitive. Explicit
integer state makes replay independent of Python's pickle/random internals.
"""

import re

MASK = (1 << 64) - 1


def die(state, sides):
    threshold = (1 << 64) % sides
    while True:
        state["rng_state"] = (state["rng_state"] + 0x9E3779B97F4A7C15) & MASK
        value = state["rng_state"]
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
        value ^= value >> 31
        if value >= threshold:
            return value % sides + 1


def roll(state, expression, critical=False):
    match = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", expression)
    if not match:
        raise ValueError(f"无效伤害骰：{expression}")
    count, sides = int(match[1]), int(match[2])
    results = [die(state, sides) for _ in range(count * (2 if critical else 1))]
    bonus = int(match[3] or 0)
    return max(0, sum(results) + bonus), {"dice": expression, "rolls": results, "bonus": bonus, "critical": critical}


def d20(state, advantage=False, disadvantage=False):
    rolls = [die(state, 20)]
    if advantage != disadvantage:
        rolls.append(die(state, 20))
    result = max(rolls) if advantage and not disadvantage else min(rolls)
    return result, rolls


def describe_d20(info, rolls=None, natural=None):
    """Explain a preview or an already resolved roll without rolling again."""
    adv, dis = info.get("advantage", False), info.get("disadvantage", False)
    if adv and dis:
        label, rule, selected = "优势与劣势抵消", "掷1次d20", "取"
    elif adv:
        label, rule, selected = "优势", "掷2次d20取高", "取高"
    elif dis:
        label, rule, selected = "劣势", "掷2次d20取低", "取低"
    else:
        label, rule, selected = "普通掷骰", "掷1次d20", "取"
    text = f"{label}（{rule}）" if rolls is None else f"{label}：d20 {rolls}，{selected} {natural}"
    reasons = []
    for key, name in (("advantage_reasons", "优势来源"), ("disadvantage_reasons", "劣势来源")):
        if info.get(key):
            reasons.append(name + "：" + "、".join(info[key]))
    return text + ("；" + "；".join(reasons) if reasons else "")


def hit_probability(bonus, ac, advantage=False, disadvantage=False, bless=False):
    totals = [bonus + b for b in range(1, 5)] if bless else [bonus]
    probabilities = []
    for value in totals:
        p = sum(1 for n in range(1, 21) if n == 20 or (n != 1 and n + value >= ac)) / 20
        if advantage != disadvantage:
            p = 1 - (1 - p) ** 2 if advantage else p ** 2
        probabilities.append(p)
    return round(sum(probabilities) / len(probabilities), 4)
