"""Interactive terminal client with revision-bound previews and durable retries."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import uuid

from .parser import ParseError, parse_line
from .render import (inspect, render_actions, render_events, render_map,
                     render_party, render_preview, render_view)
from .transport import HTTPTransport, LocalTransport, TransportError
from .menu import TerminalMenu


HELP = """中文游玩：选角色编号 → 选行动/技能编号 → 选目标编号或目的地 → 自动预览 → c 确认。
0 返回并取消当前预览；q 退出。数字只选择当前列表条目；多目标可输入 1,2,3。
当前可玩内容：一场原创前哨战，四人 1 级队伍；战士、武僧、法师、牧师。
胜利后进入战后结算：1 全队短休（一次确认），3 同阵容新周目；当前没有下一关或尸体掉落。
以下命令保留为快捷入口：
resume                   恢复并显示局势
party / map              队伍资源 / 可见格图
inspect E1 / inspect G6   可见角色 / 静态地形
actions P1               角色能力、消耗和不可用原因
preview move P2 G6       预览移动
preview attack P1 E1     预览攻击
preview cast P3 fire_bolt E1
preview cast P4 healing_word P1 slot=1
preview cast P4 bless P1,P2,P3 slot=1
preview ability P1 second_wind
preview ability P2 unarmed_strike E1
preview cast P3 mage_armor P3 slot=1
preview dash P2 / preview disengage P2 / preview dodge P2
preview help P1 P2 / preview potion P1
do 1                     执行指定预览；局势变更后必须重新预览
end P1                   结束该队员操作
react accept / react decline  接受或拒绝待选反应
rest short               战斗结束后短休
retry                    战败后回到战前状态
resend                   用原请求 ID 恢复网络中断的写入回执
log 10                   最近十条事件及计算信息
help / quit              帮助 / 离开；已提交动作自动保存
完整行动也可直接输入，例如 attack P1 E1；合法性由核心验证。
支持 readline 的终端可按 Tab 补全快捷命令。"""


def read_json(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError) as exc:
        raise ValueError("无法读取客户端记录 %s；请保留原文件并检查 JSON。" % path) from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Client:
    def __init__(self, transport, run_id=None, view=None, *, output=print,
                 journal_path=None, scope=None, on_run=None):
        self.transport = transport
        self.run_id = run_id
        self.view = view or {}
        self.output = output
        self.journal_path = Path(journal_path) if journal_path else None
        self.scope = scope or {}
        self.on_run = on_run
        self.previews = {}
        self.next_preview = 1
        self.pending = None
        self.menu = {}
        self.ui = TerminalMenu(self)
        self.preview_result = None
        self._default_menu()

    def _default_menu(self):
        self.ui.build()

    def show_menu(self):
        self.ui.show()

    def load_pending(self):
        saved = read_json(self.journal_path) if self.journal_path else None
        if saved:
            if saved.get("scope") != self.scope:
                raise ValueError("有另一连接的未确认请求；请用原连接执行 resend 后再切换。")
            if (saved.get("operation") not in ("create", "commands", "reactions", "retry")
                    or not isinstance(saved.get("payload"), dict)
                    or not saved["payload"].get("command_id")):
                raise ValueError("未确认请求记录格式无效；请保留原文件。")
            if self.run_id and saved.get("run_id") and self.run_id != saved["run_id"]:
                raise ValueError("有另一周目的未确认请求；请先用原周目恢复回执。")
            self.pending = saved
            if saved.get("run_id"):
                self.run_id = saved["run_id"]
            self._default_menu()
        return self.pending

    def _record_pending(self, value):
        if self.journal_path:
            if self.journal_path.exists() and not self.pending:
                raise ParseError("另一个终端留下了未确认请求；先关闭并用原连接恢复，或使用独立 --config。")
            write_json(self.journal_path, value)
        self.pending = value
        self._default_menu()

    def _clear_pending(self):
        if self.journal_path:
            self.journal_path.unlink(missing_ok=True)
        self.pending = None
        self._default_menu()

    def _send_pending(self):
        request = self.pending
        if not request:
            raise ParseError("没有未确认请求。retry 用于战败重试，resend 用于网络回执恢复。")
        try:
            if request["operation"] == "create":
                receipt = self.transport.create_run(deepcopy(request["payload"]))
            else:
                receipt = self.transport.write(request["run_id"], request["operation"], deepcopy(request["payload"]))
        except TransportError as exc:
            if exc.code == 'client_upgrade_required':
                # An earlier attempt may already have committed. A compatibility
                # rejection must not discard the original receipt journal.
                raise
            if exc.retryable:
                self.output("%s 请求 %s 已保留；输入 resend 或数字 9 重发同一请求。" % (
                    exc.message, request["payload"]["command_id"]))
                self._default_menu()
                return False
            self._clear_pending()
            if exc.status_code == 409 and self.run_id:
                self.refresh(display=False)
                self.previews.clear()
            raise
        # Validate a receipt before dropping a potentially committed request.
        if not isinstance(receipt.get("view"), dict) or "revision" not in receipt:
            self.output("回执格式不完整；保留原请求，输入 resend 恢复。")
            return False
        receipt_run = receipt.get("run_id", receipt["view"].get("id", self.run_id))
        if not receipt_run or receipt.get("command_id") != request["payload"]["command_id"]:
            self.output("回执与请求不匹配；保留原请求，输入 resend 恢复。")
            return False
        self.run_id = receipt_run
        if self.on_run:
            self.on_run(self.run_id)
        self.view = receipt["view"]
        self.previews.clear()
        self._clear_pending()
        # A replay can be older than the current state after another session acts.
        # Show its original result, then refresh the latest filtered view.
        if receipt.get("events"):
            events = receipt["events"]
            self.output(render_events(events[-6:]))
            if len(events) > 6:
                self.output("还有 %s 条事件，可从菜单查看详细日志。" % (len(events) - 6))
        try:
            self.refresh(display=False)
        except TransportError:
            self.output("写入已确认并保存；局势刷新暂不可用，resume 可重新读取。")
        self.ui.after_write(request)
        if request["operation"] in ("create", "retry") or self.ui.stage == "home":
            self.output(render_view(self.view, self.ui.selected))
        elif self.ui.stage == "settlement":
            self.output("全队资源已保存；从战后菜单选择接下来的操作。")
        elif (request["payload"].get("command", {}).get("type") in ("move", "end")
              or request["operation"] == "reactions"):
            self.output(render_map(self.view, self.ui.selected))
        else:
            self.output("第 %s 回合 · 可操作：%s" % (self.view.get("round", "?"), "、".join(self.view.get("active", [])) or "无"))
        self._default_menu()
        self.show_menu()
        return True

    def _write(self, operation, extra=None, revision=None):
        if self.pending:
            raise ParseError("已有未确认请求；请先输入 resend，不能生成另一个写请求。")
        if not self.run_id:
            raise ParseError("尚未取得周目；请用 resend 恢复建队回执。")
        payload = {"command_id": str(uuid.uuid4()), "expected_revision": self.view.get("revision") if revision is None else revision}
        payload.update(extra or {})
        self._record_pending({"scope": self.scope, "run_id": self.run_id,
                              "operation": operation, "payload": payload})
        return self._send_pending()

    def create(self, name="远征队", templates=None, seed=None):
        if self.pending:
            raise ParseError("先恢复未确认的请求。")
        payload = {"name": name, "templates": templates, "command_id": str(uuid.uuid4())}
        if seed is not None:
            payload["seed"] = seed
        self._record_pending({"scope": self.scope, "run_id": None,
                              "operation": "create", "payload": payload})
        return self._send_pending()

    def refresh(self, display=True):
        if not self.run_id:
            raise ParseError("尚未取得周目；请用 resend 恢复建队回执。")
        new_view = self.transport.get_view(self.run_id)
        if new_view.get("revision") != self.view.get("revision"):
            self.previews.clear()
        self.view = new_view
        self._default_menu()
        if display:
            self.output(render_view(self.view, self.ui.selected))
            self.show_menu()
        return new_view

    def _preview(self, command, display=True):
        if self.pending:
            raise ParseError("先用 resend 恢复未确认请求，然后重新预览。")
        self.refresh(display=False)
        revision = self.view["revision"]
        result = self.transport.preview(self.run_id, command)
        if result.get("revision") is not None:
            revision = result["revision"]
        # The repository includes the exact preview revision. Older servers are
        # supported by requiring the view before/after the preview to agree.
        self.refresh(display=False)
        if self.view["revision"] != revision:
            raise ParseError("预览期间局势已改变；请重新预览。")
        number = self.next_preview
        self.next_preview += 1
        self.previews[number] = {"revision": revision, "command": deepcopy(result.get("command", command))}
        self.preview_result = result
        if display:
            if command.get("actor"):
                self.ui.selected = command["actor"]
            self.ui.action = {"name": result.get("description", "行动")}
            self.ui.return_stage = "actions" if self.ui.selected else "home"
            self.ui.stage = "confirm"
            self.ui.confirmation = {"operation": "commands", "number": number}
            self.ui.revision = revision
            self.output(self.ui.context())
            self.output(render_preview(number, result, revision, self.view))
        self._default_menu()
        return number

    def _do(self, number):
        preview = self.previews.get(number)
        if not preview:
            # An interrupted do is retried only by exact operation/command match
            # in this process. A restarted process uses explicit resend.
            raise ParseError("预览 %s 不存在或已失效；请重新预览。" % number)
        if self.pending:
            original = self.pending
            if (original["operation"] == "commands"
                    and original["payload"].get("command") == preview["command"]
                    and original["payload"].get("expected_revision") == preview["revision"]):
                return self._send_pending()
            raise ParseError("已有不同的未确认请求；请先输入 resend。")
        self.refresh(display=False)
        if self.view["revision"] != preview["revision"]:
            self.previews.pop(number, None)
            self.ui.confirmation = None
            self.ui.action = None
            self.ui.stage = "actions" if self.ui.selected else "home"
            self.show_menu()
            raise ParseError("预览已过期，未提交行动；请重新预览。")
        return self._write("commands", {"command": preview["command"]}, preview["revision"])

    def _log(self, count):
        # Fetch safe event pages, retaining only the requested tail. Event
        # sequence IDs are not assumed to be contiguous.
        after, tail = 0, []
        while True:
            page = self.transport.events(self.run_id, after=after, limit=100)
            events = page.get("events", [])
            if not events:
                break
            tail = (tail + events)[-count:]
            next_after = page.get("next_after", events[-1].get("seq", after))
            if next_after <= after:
                break
            after = next_after
            if len(events) < 100:
                break
        self.output(render_events(tail, detailed=True))

    def handle(self, line):
        try:
            handled = self.ui.handle(line)
            if handled is not None:
                return handled
            kind, value = parse_line(line)
            if kind == "quit":
                if self.pending:
                    self.output("未确认请求已保留；下次启动会用原 ID 恢复回执。")
                self.output("已离开；已提交的动作和反应窗口保存在服务端。")
                return False
            if kind == "empty":
                return True
            if kind == "help":
                self.output(HELP)
            elif kind == "resend":
                self._send_pending()
            elif kind == "resume":
                self.refresh()
            elif kind in ("party", "map", "inspect", "actions", "log"):
                self.refresh(display=False)
                if kind == "party":
                    self.output(render_party(self.view))
                elif kind == "map":
                    self.output(render_map(self.view, self.ui.selected))
                elif kind == "inspect":
                    self.output(inspect(self.view, value))
                elif kind == "actions":
                    if self.view.get("phase") == "combat":
                        self.ui.select_actor(value)
                    else:
                        self.output(render_actions(self.view, value))
                else:
                    self._log(value)
            elif kind == "preview":
                self._preview(value)
            elif kind == "do":
                self._do(value)
            elif kind == "react":
                self._write("reactions", {"choice": value})
            elif kind == "retry":
                self._write("retry")
            elif kind == "action":
                self._write("commands", {"command": value})
        except (ParseError, TransportError, ValueError) as exc:
            self.output("错误：%s" % getattr(exc, "message", str(exc)))
            if self.pending:
                self.show_menu()
        return True


def install_completion(client):
    try:
        import readline
    except ImportError:
        return
    commands = ["resume", "party", "inspect", "actions", "preview", "move", "attack", "cast",
                "ability", "dash", "disengage", "dodge", "help", "potion", "do", "end", "react",
                "accept", "decline", "rest", "short", "retry", "resend", "log", "map", "quit"]

    def complete(prefix, state):
        candidates = commands + [actor["id"] for actor in client.view.get("party", []) + client.view.get("enemies", [])]
        candidates += [ability["id"] for actor in client.view.get("party", []) for ability in actor.get("abilities", [])]
        matches = sorted(set(candidate for candidate in candidates if candidate.startswith(prefix)))
        return matches[state] + " " if state < len(matches) else None

    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")


def argument_parser():
    parser = argparse.ArgumentParser(description="四人小队文字战术切片（M1/M2）")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--new", action="store_true", help="开始新周目")
    selection.add_argument("--run", help="恢复指定周目 ID")
    parser.add_argument("--name", default="远征队", help="新队伍名称")
    parser.add_argument("--classes", default="fighter,monk,wizard,cleric", help="恰好四个职业，允许重复")
    parser.add_argument("--seed", type=int, help="新周目随机种子")
    parser.add_argument("--db", help="本地 SQLite 路径（默认 CMDRPG_DB 或 data/game.sqlite3）")
    parser.add_argument("--url", help="远程服务地址（或 CMDRPG_URL）")
    parser.add_argument("--token", help="远程 Bearer 凭据；推荐使用 CMDRPG_TOKEN 环境变量")
    parser.add_argument("--timeout", type=float, default=10, help="HTTP 超时秒数")
    parser.add_argument("--config", default=os.environ.get("CMDRPG_CLIENT_CONFIG", "data/client.json"), help="上次连接记录路径")
    return parser


def main(argv=None, *, input_reader=input, output=print):
    if output is print:
        # Windows consoles handle Unicode natively; pipes also need a known
        # encoding so Chinese summaries survive shells and saved transcripts.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argument_parser()
    args = parser.parse_args(argv)
    templates = [name.strip() for name in args.classes.split(",")]
    if len(templates) != 4 or any(name not in ("fighter", "monk", "wizard", "cleric") for name in templates):
        parser.error("--classes 须含四个 fighter/monk/wizard/cleric 职业，允许重复。")
    if args.url and args.db:
        parser.error("--url 与 --db 不能同时指定。")
    config_path = Path(args.config)
    journal_path = config_path.with_name(config_path.stem + ".pending.json")
    transport = None
    try:
        saved = read_json(config_path) or {}
        previous = saved.get("last", {})
        url = args.url or (os.environ.get("CMDRPG_URL") if not args.db else None)
        if not url and not args.db and previous.get("mode") == "remote":
            url = previous.get("url")
        if url:
            transport = HTTPTransport(url, args.token or os.environ.get("CMDRPG_TOKEN"), args.timeout)
            scope = {"mode": "remote", "url": transport.base_url}
        else:
            database_path = args.db or os.environ.get("CMDRPG_DB") or (previous.get("db") if previous.get("mode") == "local" else None) or "data/game.sqlite3"
            database_path = str(Path(database_path).resolve())
            transport = LocalTransport(database_path)
            scope = {"mode": "local", "db": database_path}
        run_id = args.run or (previous.get("run_id") if not args.new and all(previous.get(key) == value for key, value in scope.items()) else None)

        def save_run(current_id):
            # Only run ID and connection address are persisted, never a token.
            write_json(config_path, {"last": dict(scope, run_id=current_id)})

        client = Client(transport, run_id=run_id, output=output, journal_path=journal_path, scope=scope, on_run=save_run)
        client.load_pending()
        if client.pending:
            if args.new:
                raise ValueError("有未确认请求；先恢复其回执，再用 --new 新建周目。")
            output("发现未确认请求，正在用原 ID 恢复回执。")
            client._send_pending()
        elif run_id:
            client.refresh()
            save_run(run_id)
        else:
            client.create(args.name, templates, args.seed)
        install_completion(client)
        while True:
            try:
                line = input_reader(client.ui.context() + " > ")
            except (EOFError, KeyboardInterrupt):
                client.handle("quit")
                return 0
            if not client.handle(line):
                return 0
    except (TransportError, ValueError, OSError) as exc:
        output("启动失败：%s" % getattr(exc, "message", str(exc)))
        return 1
    finally:
        if transport:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
