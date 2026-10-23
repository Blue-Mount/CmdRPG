"""Dependency-free fixed-region terminal presentation of the existing client.

Only filtered views, menu metadata and acknowledged receipts enter this module.
Paging is local: it never submits actions or changes the revision/RNG.
"""

from copy import deepcopy
import os
import shutil
import sys
import unicodedata

from .render import PHASES, render_map


MIN_COLUMNS, MIN_LINES = 110, 36


def columns(text):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def wrap(text, width):
    """Wrap Chinese by terminal columns and neutralize control sequences."""
    lines = []
    for original in str(text).split("\n"):
        line, used = "", 0
        for character in original:
            # Content/actor names must not be able to reposition the terminal.
            if unicodedata.category(character).startswith("C"):
                character = " "
            size = columns(character)
            if used + size > width:
                lines.append(line)
                line, used = "", 0
            line += character
            used += size
        lines.append(line)
    return lines


def pad(text, width):
    return text + " " * max(0, width - columns(text))


def page(text, width, height, index):
    lines = wrap(text, width)
    pages = max(1, (len(lines) + height - 1) // height)
    index = max(0, min(index, pages - 1))
    visible = lines[index * height:(index + 1) * height]
    return visible + [""] * (height - len(visible)), index, pages


class DecisionScreen:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.client = None
        self.preview = None
        self.preview_key = None
        self.feedback = "尚无本次结算；选择角色开始。"
        self.feedback_title = "结果 / 详情"
        self.notices = []
        self.decision_page = self.map_page = self.feedback_page = 0
        self.decision_key = None
        self.map_revision = None
        self.active = False
        self.console_mode = None

    def supported(self):
        size = self.size()
        return (self.stream.isatty() and sys.stdin.isatty()
                and os.environ.get("TERM") != "dumb"
                and size.columns >= MIN_COLUMNS and size.lines >= MIN_LINES)

    def size(self):
        try:
            return os.get_terminal_size(self.stream.fileno())
        except (OSError, ValueError, AttributeError):
            return shutil.get_terminal_size()

    def __enter__(self):
        if os.name == "nt":
            # Windows Terminal/conhost require VT processing; restore on exit.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.windll.kernel32
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            handle = kernel.GetStdHandle(-11)
            mode = wintypes.DWORD()
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                raise OSError("无法读取终端模式")
            if not kernel.SetConsoleMode(handle, mode.value | 4):
                raise OSError("终端不支持 VT 重绘")
            self.console_mode = (handle, mode.value)
        self.active = True
        self.stream.write("\x1b[?1049h\x1b[2J\x1b[H")
        self.stream.flush()
        return self

    def __exit__(self, *_):
        if self.active:
            self.stream.write("\x1b[?1049l")
            self.stream.flush()
            self.active = False
        if self.console_mode:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(*self.console_mode)
            self.console_mode = None

    def message(self, text):
        self.notices.append(str(text))
        self.feedback = "\n".join(self.notices)
        self.feedback_title = "详情 / 提示（只读）"
        self.feedback_page = 0

    def present(self, kind, text):
        if kind == "preview":
            self.preview = text
            self.preview_key = deepcopy(self.client.ui.confirmation)
        elif kind == "result":
            self.feedback, self.feedback_title = text, "真实结算（已保存）"
            self.feedback_page = 0
        # View/menu/status are derived from the current filtered client state.

    def navigate(self, line):
        key = line.strip().lower()
        controls = {"n": ("decision_page", 1), "p": ("decision_page", -1),
                    "]": ("feedback_page", 1), "[": ("feedback_page", -1),
                    "mn": ("map_page", 1), "mp": ("map_page", -1)}
        if key not in controls:
            self.notices = []
            return False
        attribute, step = controls[key]
        setattr(self, attribute, max(0, getattr(self, attribute) + step))
        return True

    def frame(self, size):
        client, ui = self.client, self.client.ui
        width, height = size
        width -= 1  # Leave the rightmost cell unused to avoid terminal autowrap.
        left_width = max(69, width // 2)
        right_width = width - left_width - 3
        feedback_height = min(10, max(7, height // 4))
        upper_height = height - feedback_height - 7
        decision = ui.render()
        if ui.stage == "confirm" and ui.confirmation == self.preview_key and self.preview:
            decision = self.preview
        decision_key = (ui.stage, ui.selected, decision)
        if decision_key != self.decision_key:
            self.decision_page = 0
            self.decision_key = decision_key
        revision = (client.view.get("id"), client.view.get("revision"), ui.selected, ui.stage)
        if revision != self.map_revision:
            self.map_page = 0
            self.map_revision = revision
        reachable = ui.options.get("reachable", []) if ui.stage == "move" else None
        map_text = render_map(client.view, ui.selected, reachable, detailed=False)
        left, self.map_page, map_pages = page(map_text, left_width, upper_height, self.map_page)
        right, self.decision_page, decision_pages = page(decision, right_width, upper_height, self.decision_page)
        bottom, self.feedback_page, feedback_pages = page(
            self.feedback, width, feedback_height, self.feedback_page)
        view = client.view
        heading = "%s · %s · 第%s回合 · 版本%s · 可操作 %s" % (
            view.get("name", "队伍"), PHASES.get(view.get("phase"), "战斗"), view.get("round", "?"),
            view.get("revision", "?"), "/".join(view.get("active", [])) or "无")
        lines = [pad(wrap(heading, width)[0], width),
                 pad("地图 %s/%s（mn/mp）" % (self.map_page + 1, map_pages), left_width) + " | " +
                 pad("决策 %s/%s（n/p 完整行动）" % (self.decision_page + 1, decision_pages), right_width)]
        lines += [pad(a, left_width) + " | " + pad(b, right_width) for a, b in zip(left, right)]
        lines.append(pad("%s · %s/%s（]/[ 翻页）" % (
            self.feedback_title, self.feedback_page + 1, feedback_pages), width))
        lines += [pad(line, width) for line in bottom]
        footer = "n/p 决策翻页  ]/[ 结果翻页  rules 规则  result 结算  details 计算"
        lines.append(pad(wrap(footer, width)[0], width))
        lines.append(pad("0 返回  c 确认  q 退出  plain 文字模式；编号以当前决策列表为准。", width))
        return "\n".join(lines)

    def draw(self):
        size = self.size()
        if size.columns < MIN_COLUMNS or size.lines < MIN_LINES:
            return False
        self.stream.write("\x1b[H" + self.frame(size) + "\n\x1b[J")
        self.stream.flush()
        return True
