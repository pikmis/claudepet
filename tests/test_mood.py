import json
from datetime import datetime, timedelta

import pytest

from clawd.memory import Memory
from clawd.mood import Mood
from tests.test_pet import Recorder, run


class Clock:
    def __init__(self, start=datetime(2026, 3, 10, 14, 0, 0)):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


# ---------------------------------------------------------------- mood


def test_boredom_grows_when_ignored_and_fades_with_attention():
    mood = Mood()
    for _ in range(900):
        mood.update(1.0, 14, False, False, 30)
    assert mood.boredom > 0.9 and mood.label == "bored"
    for _ in range(120):
        mood.update(1.0, 14, False, True, 0)
    assert mood.boredom < 0.1 and mood.label != "bored"


def test_energy_follows_the_hour_and_sleep_restores_it():
    mood = Mood()
    for _ in range(3000):
        mood.update(1.0, 3, False, True, 0)
    assert mood.energy < 0.3 and mood.label == "sleepy"
    for _ in range(600):
        mood.update(1.0, 3, True, False, 0)
    assert mood.energy > 0.9


def test_neglect_makes_him_sulk_and_petting_brings_him_back():
    mood = Mood()
    for _ in range(5000):
        mood.update(1.0, 14, False, False, 3000)
    assert mood.affection < 0.2 and mood.label == "sulking"
    for _ in range(4):
        mood.cheer(0.4, 0.05)
    assert mood.label != "sulking"


# ---------------------------------------------------------------- memory


def test_memory_counts_apps_hours_and_projects(tmp_path):
    clock = Clock()
    memory = Memory(tmp_path / "m.json", clock)
    memory.note_window("Code")
    clock.advance(minutes=9)
    memory.note_window("chrome")
    clock.advance(minutes=3)
    memory.note_window("python")  # his own windows don't count
    for _ in range(120):
        memory.note_activity()
    memory.note_project("claudepet")
    memory.note_project("claudepet")
    assert round(memory.apps["code"]) == 540 and round(memory.apps["chrome"]) == 180
    facts = " ".join(memory.summary())
    assert "claudepet" in facts and "14:00" in facts and "chrome" not in facts  # chrome < 10 minutes


def test_memory_survives_restarts_and_measures_the_gap(tmp_path):
    path = tmp_path / "m.json"
    clock = Clock()
    first = Memory(path, clock)
    first.note_pet()
    first.affection = 0.9
    first.save(force=True)
    clock.advance(hours=30)
    second = Memory(path, clock)
    assert second.launches == 2 and second.pets == 1 and second.affection == pytest.approx(0.9)
    assert second.gap_seconds == pytest.approx(30 * 3600, abs=5)
    assert "1 days" in " ".join(second.summary()) and "30 hours" in " ".join(second.summary())


def test_memory_never_stores_titles_and_can_forget(tmp_path):
    path = tmp_path / "m.json"
    memory = Memory(path)
    memory.note_window("code")
    memory.note_pet()
    memory.save(force=True)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "first_seen", "last_seen", "launches", "apps", "hours", "projects", "pets", "affection"}
    memory.forget()
    assert memory.pets == 0 and memory.apps == {} and memory.launches == 1


def test_broken_memory_file_is_ignored(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{ nope", encoding="utf-8")
    assert Memory(path).launches == 1
    path.write_text('{"hours": [1], "apps": {"a": "x"}, "affection": 7}', encoding="utf-8")
    memory = Memory(path)
    assert memory.hours == [0] * 24 and memory.apps == {} and memory.affection == 1.0


# ---------------------------------------------------------------- the pet acts on it


def make(mood=None, hour=14, memory=None):
    from clawd.pet import Pet

    fx = Recorder()
    fx.started = []
    fx.start_game = fx.started.append
    pet = Pet(5, fx, memory or Memory(), mood or Mood(), hour=lambda: hour)
    pet.set_area(1200, 700)
    return pet, fx


def test_greets_for_the_time_of_day_and_for_a_long_absence(tmp_path):
    pet, fx = make(hour=8)
    run(pet, 6, __import__("time").monotonic())
    assert any(s in fx.said for s in ("greet_morning",))

    clock = Clock()
    path = tmp_path / "m.json"
    Memory(path, clock).save(force=True)
    clock.advance(hours=20)
    pet, fx = make(memory=Memory(path, clock))
    run(pet, 6, __import__("time").monotonic())
    assert "reunion" in fx.said


def test_bored_pet_comes_to_the_cursor_and_offers_the_ball():
    mood = Mood()
    mood.boredom = 1.0
    pet, fx = make(mood)
    import time

    now = run(pet, 1, time.monotonic())
    pet.set_cursor(100, 650, now)
    pet.last_interaction = now - 100
    for _ in range(60):
        mood.boredom = 1.0
        now = run(pet, 5, now)
        if fx.started:
            break
    assert "bored_poke" in fx.said or "ball_offer" in fx.said
    assert fx.started == ["ball"]


def test_sulking_pet_stays_put_and_forgives_after_petting():
    import time

    mood = Mood(affection=0.1)
    pet, fx = make(mood)
    now = run(pet, 1, time.monotonic())
    pet.last_interaction = now
    start = pet.x
    now = run(pet, 60, now)
    assert abs(pet.x - start) < 1 and pet.mood.label == "sulking"
    assert pet.frame(now).eyes == "sleep"
    pet.pointer_down(pet.x + 20, pet.y + 30, now)
    now = run(pet, 1, now)
    for i in range(10):
        pet.pointer_move(pet.x + (30 if i % 2 else -30) + 60, pet.y + 30, now + i * 0.05)
    pet.pointer_up(now + 1)
    assert pet.mood.label != "sulking" and "forgive" in fx.said


def test_late_night_typing_gets_a_remark():
    import time

    pet, fx = make(hour=3)
    now = run(pet, 1, time.monotonic())
    pet.on_typing(120, now)
    assert "late_night" in fx.said
