"""Consistent SQLite backup command. Never overwrites the destination."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

# Support both `python -m tools.backup` and `python tools/backup.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.storage import backup_database


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="一致性备份游戏存档；目标文件必须不存在。")
    parser.add_argument("destination", help="新备份文件路径")
    parser.add_argument("--database", default=os.environ.get("CMDRPG_DB", "data/game.sqlite3"),
                        help="源 SQLite 存档（默认 CMDRPG_DB 或 data/game.sqlite3）")
    args = parser.parse_args(argv)
    try:
        result = backup_database(args.database, args.destination)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"备份失败：{exc}\n")
    print(f"已备份并通过完整性检查：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
