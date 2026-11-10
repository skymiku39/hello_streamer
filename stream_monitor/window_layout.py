"""Keep managed playback windows on a monitor with separate coordinates."""

from __future__ import annotations

Rect = tuple[int, int, int, int]  # left, top, width, height


def choose_window_rect(requested: Rect, work_area: Rect, occupied: list[Rect]) -> Rect:
    left, top, area_width, area_height = work_area
    x, y, width, height = requested
    width, height = min(width, area_width), min(height, area_height)
    right, bottom = left + area_width - width, top + area_height - height
    x, y = max(left, min(x, right)), max(top, min(y, bottom))
    candidates = [(x, y)]
    candidates.extend((px, py) for py in range(top, bottom + 1, height + 8)
                      for px in range(left, right + 1, width + 8))
    # When fixed-size windows cannot all fit, keep separate visible title bars
    # inside the work area. No fixed geometry can guarantee non-overlap then.
    candidates.extend((px, py) for py in range(top, bottom + 1, 40)
                      for px in range(left, right + 1, 40))
    candidates = list(dict.fromkeys(candidates))

    def score(position: tuple[int, int]) -> tuple[bool, int]:
        px, py = position
        overlap = sum(max(0, min(px + width, ox + ow) - max(px, ox)) *
                      max(0, min(py + height, oy + oh) - max(py, oy))
                      for ox, oy, ow, oh in occupied)
        return any(px == ox and py == oy for ox, oy, _, _ in occupied), overlap

    x, y = min(candidates, key=score)
    return x, y, width, height
