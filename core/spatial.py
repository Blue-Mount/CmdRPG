"""Shared geometry for both previews and execution (one cell is 1.5 m)."""

import heapq
import math
import re


def xy(position):
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", str(position).upper())
    if not match:
        raise ValueError(f"无效位置：{position}")
    x = 0
    for letter in match[1]:
        x = x * 26 + ord(letter) - 64
    return x - 1, int(match[2]) - 1


def label(x, y):
    x += 1
    letters = ""
    while x:
        x, remainder = divmod(x - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{y + 1}"


def walkable(board, position):
    if not isinstance(position, str) or position != position.upper():
        return False
    try:
        x, y = xy(position)
    except ValueError:
        return False
    return 0 <= x < board["width"] and 0 <= y < board["height"] and position not in board["walls"]


def distance(board, a, b):
    ax, ay = xy(a)
    bx, by = xy(b)
    size = board["cell_size"]
    dz = board.get("heights", {}).get(a, 0) - board.get("heights", {}).get(b, 0)
    return math.sqrt(((ax - bx) * size) ** 2 + ((ay - by) * size) ** 2 + dz ** 2)


def neighbors(board, position):
    x, y = xy(position)
    height = board.get("heights", {}).get(position, 0)
    for dx, dy in ((-1, 0), (0, -1), (0, 1), (1, 0)):
        dest = label(x + dx, y + dy) if x + dx >= 0 and y + dy >= 0 else ""
        if not walkable(board, dest):
            continue
        # The slice uses graded stairs, not implicit leaps up cliffs.
        if abs(board.get("heights", {}).get(dest, 0) - height) > board["cell_size"]:
            continue
        cost = board["cell_size"] * (2 if dest in board.get("difficult", []) else 1)
        yield dest, cost


def path(board, start, destination, blocked=()):
    if not walkable(board, start) or not walkable(board, destination) or destination in blocked:
        return None
    queue = [(0, start)]
    best = {start: 0}
    previous = {}
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != best[current]:
            continue
        if current == destination:
            result = [current]
            while current != start:
                current = previous[current]
                result.append(current)
            return list(reversed(result)), cost
        for adjacent, price in neighbors(board, current):
            if adjacent in blocked:
                continue
            new_cost = cost + price
            if new_cost < best.get(adjacent, math.inf):
                best[adjacent] = new_cost
                previous[adjacent] = current
                heapq.heappush(queue, (new_cost, adjacent))
    return None


def line_of_sight(board, start, end):
    """Conservative supercover line: touching a wall corner blocks a ray."""
    if not walkable(board, start) or not walkable(board, end):
        return False
    ax, ay = xy(start)
    bx, by = xy(end)
    dx, dy = bx - ax, by - ay
    size = board["cell_size"]
    eye_a = board.get("heights", {}).get(start, 0) + size
    eye_b = board.get("heights", {}).get(end, 0) + size
    # Intersect the ray with each blocking cell's closed square. Small maps
    # make this simpler and less error-prone than ad-hoc Bresenham corner rules.
    obstacles = set(board["walls"]) | set(board.get("heights", {}))
    for cell in obstacles:
        if cell in (start, end):
            continue
        cx, cy = xy(cell)
        low, high = 0.0, 1.0
        for origin, delta, c in ((ax, dx, cx), (ay, dy, cy)):
            if delta == 0:
                if abs(origin - c) > 0.5:
                    low, high = 1, 0
                    break
            else:
                t1, t2 = (c - 0.5 - origin) / delta, (c + 0.5 - origin) / delta
                low, high = max(low, min(t1, t2)), min(high, max(t1, t2))
        if low <= high and high > 0 and low < 1:
            if cell in board["walls"]:
                return False
            # Elevated solid terrain blocks rays beneath its surface.
            ray_height = min(eye_a + (eye_b - eye_a) * low, eye_a + (eye_b - eye_a) * high)
            if board["heights"][cell] >= ray_height:
                return False
    return True
