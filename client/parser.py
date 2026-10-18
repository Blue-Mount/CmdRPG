"""Syntax validation performed before a write is sent."""

import re
import shlex


class ParseError(ValueError):
    pass


def _actor(value):
    value = value.upper()
    if not re.fullmatch(r"P[1-9][0-9]*", value):
        raise ParseError("角色编号格式为 P1 至 P4。")
    return value


def _target(value, position=False):
    value = value.upper()
    pattern = r"(?:P|E)[1-9][0-9]*"
    if position:
        pattern = r"[A-Z]+[1-9][0-9]*"
    if not re.fullmatch(pattern, value):
        raise ParseError("目标须为角色编号或合法格坐标。" if position else "目标编号格式为 P1 或 E1。")
    return value


def _ability(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ParseError("能力编号使用小写字母、数字和下划线，例如 fire_bolt。")
    return value


def parse_action(tokens):
    if not tokens:
        raise ParseError("请指定行动，例如 preview attack P1 E1。")
    kind = tokens[0].lower()
    args = tokens[1:]
    if kind == 'camp' and not args:
        return {'type': 'camp'}
    if kind == 'travel' and len(args) == 1:
        return {'type': 'travel', 'destination': args[0]}
    if kind == 'buy' and len(args) == 2:
        return {'type': 'buy', 'actor': _actor(args[0]), 'item': args[1]}
    if kind == 'practice' and not args: return {'type':'practice'}
    if kind == 'recover' and len(args)==2 and args[1] in ('1','2','3'):
        return {'type':'recover','actor':_actor(args[0]),'slot':int(args[1])}
    if kind == 'levelup' and len(args)==1:
        return {'type':'levelup','actor':_actor(args[0])}
    if kind == 'equip' and len(args)==3:
        return {'type':'equip','actor':_actor(args[0]),'slot':args[1],'item':None if args[2]=='none' else args[2]}
    if kind == 'prepare' and len(args)==2:
        return {'type':'prepare','actor':_actor(args[0]),'spells':[] if args[1]=='none' else args[1].split(',')}
    if kind in ("attack", "move", "help"):
        if len(args) != 2:
            raise ParseError("格式：%s P1 %s" % (kind, "G6" if kind == "move" else "E1/P2"))
        command = {"type": kind, "actor": _actor(args[0])}
        command["destination" if kind == "move" else "target"] = _target(args[1], kind == "move")
        return command
    if kind in ("dash", "disengage", "dodge", "potion", "end"):
        if len(args) != 1:
            raise ParseError("格式：%s P1" % kind)
        return {"type": kind, "actor": _actor(args[0])}
    if kind == "ability":
        if len(args) not in (2, 3):
            raise ParseError("格式：ability P1 second_wind 或 ability P2 unarmed_strike E1")
        command = {"type": kind, "actor": _actor(args[0]), "ability": _ability(args[1])}
        if len(args) == 3:
            command["target"] = _target(args[2])
        elif command["ability"] not in ("second_wind","action_surge","patient_defence","mass_healing_word"):
            raise ParseError("此能力命令缺少目标，例如 ability P2 unarmed_strike E1。")
        return command
    if kind == "cast":
        if len(args) < 2:
            raise ParseError("格式：cast P3 fire_bolt E1 或 cast P4 bless P1,P2,P3 slot=1")
        command = {"type": kind, "actor": _actor(args[0]), "ability": _ability(args[1])}
        remaining = args[2:]
        if remaining and remaining[-1].startswith("slot="):
            try:
                slot = int(remaining.pop()[5:])
                if slot < 1:
                    raise ValueError
            except ValueError as exc:
                raise ParseError("法术位格式为 slot=1。") from exc
            command["slot"] = slot
        if len(remaining) > 1:
            raise ParseError("多个目标用逗号连接；法术位放在最后，例如 P1,P2,P3 slot=1。")
        if remaining:
            targets = remaining[0].split(",")
            parsed = [_target(target, position=True) for target in targets]
            multiple = len(parsed) > 1 or command["ability"] == "bless"
            command["targets" if multiple else "target"] = parsed if multiple else parsed[0]
        elif command["ability"] not in ("aid","mass_healing_word","preserve_life"):
            raise ParseError("此施法命令缺少目标，例如 cast P3 fire_bolt E1。")
        return command
    if kind == "rest":
        if args != ["short"]:
            raise ParseError("当前切片只支持 rest short。")
        return {"type": "rest", "kind": "short"}
    raise ParseError("未知行动 %s；输入 help 查看可用命令。" % kind)


def parse_line(line):
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        raise ParseError("引号未闭合。") from exc
    if not tokens:
        return "empty", None
    kind = tokens[0].lower()
    args = tokens[1:]
    if kind == "preview":
        return kind, parse_action(args)
    if kind == "do":
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) < 1:
            raise ParseError("格式：do 1；先用 preview 取得预览编号。")
        return kind, int(args[0])
    if kind == "react":
        if len(args) != 1 or args[0].lower() not in ("accept", "decline"):
            raise ParseError("格式：react accept 或 react decline。")
        return kind, args[0].lower()
    if kind in ("resume", "party", "retry", "resend", "quit", "map") or (kind == "help" and not args):
        if args:
            raise ParseError("%s 不接受额外参数。" % kind)
        return kind, None
    if kind == "inspect":
        if len(args) != 1:
            raise ParseError("格式：inspect E1 或 inspect G6。")
        return kind, _target(args[0], position=True)
    if kind == "actions":
        if len(args) != 1:
            raise ParseError("格式：actions P1。")
        return kind, _actor(args[0])
    if kind == "log":
        if len(args) > 1:
            raise ParseError("格式：log 10。")
        try:
            count = int(args[0]) if args else 10
            if not 1 <= count <= 100:
                raise ValueError
        except ValueError as exc:
            raise ParseError("日志条数须为 1 至 100。") from exc
        return kind, count
    return "action", parse_action(tokens)
