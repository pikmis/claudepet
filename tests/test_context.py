from clawd.context import MAX_TITLE, count_new_presses, shorten


def test_counts_only_new_presses():
    a, b = 0, 0b0110 << 8
    assert count_new_presses(a, b) == 2
    a = 0b0010 << 8
    assert count_new_presses(a, b) == 1
    assert count_new_presses(b, a) == 0


def test_long_titles_are_shortened():
    short = shorten("я" * 200)
    assert len(short) == MAX_TITLE + 1
    assert shorten("  Firefox  ") == "Firefox"
