from stream_monitor.window_layout import choose_window_rect


def test_windows_use_free_slots_and_reuse_closed_window_space():
    requested = (0, 0, 480, 800)
    area = (0, 0, 2560, 1400)
    occupied = []
    for _ in range(5):
        rect = choose_window_rect(requested, area, occupied)
        assert all(rect[0] + rect[2] <= other[0] or other[0] + other[2] <= rect[0]
                   for other in occupied)
        occupied.append(rect)
    occupied.pop(1)
    assert choose_window_rect(requested, area, occupied) == (488, 0, 480, 800)


def test_more_windows_than_fit_still_have_distinct_onscreen_positions():
    occupied = []
    for _ in range(15):
        rect = choose_window_rect((0, 0, 480, 800), (0, 0, 2560, 1400), occupied)
        assert rect[:2] not in [other[:2] for other in occupied]
        assert 0 <= rect[0] <= 2080 and 0 <= rect[1] <= 600
        occupied.append(rect)


def test_offscreen_coordinates_are_clamped_on_a_negative_origin_monitor():
    assert choose_window_rect((-20000, -10000, 480, 800), (-1920, -200, 1920, 1040), []) == (
        -1920, -200, 480, 800)


def test_oversized_window_stays_in_monitor_work_area():
    assert choose_window_rect((100, 100, 3000, 2000), (0, 0, 1920, 1040), []) == (
        0, 0, 1920, 1040)
