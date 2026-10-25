import random
import time

import pytest

from clawd.pet import Pet
from clawd.sprite import GRID_H, GRID_W


class Recorder:
    def __init__(self):
        self.said = []
        self.spawned = []

    def say(self, category, force=False, values=None):
        self.said.append(category)

    def say_text(self, text, force=False):
        self.said.append(text)

    def spawn(self, kind):
        self.spawned.append(kind)

    def hide_bubble(self):
        pass


def make_pet(unit=5):
    fx = Recorder()
    pet = Pet(unit, fx)
    pet.set_area(1200, 700)
    return pet, fx


def run(pet, seconds, start, dt=1 / 60):
    now = start
    for _ in range(int(seconds / dt)):
        now += dt
        pet.step(now)
        pet.frame(now)
        pet.rotation(now)
    return now


def test_greets_and_stays_on_the_floor():
    pet, fx = make_pet()
    now = run(pet, 3, time.monotonic())
    assert fx.said[0] == "hello"
    assert pet.state in ("idle", "walk")
    assert pet.y == pytest.approx(pet.bounds.max_y)
    assert 0 <= pet.x <= pet.bounds.max_x
    assert pet.w == GRID_W * 5 and pet.h == GRID_H * 5
    assert now > 1000


def test_hit_test_covers_the_body_only():
    pet, _ = make_pet()
    run(pet, 0.1, time.monotonic())
    cx, cy = pet.x + pet.w / 2, pet.y + pet.h / 2
    assert pet.hit_test(cx, cy)
    assert not pet.hit_test(pet.x - 50, cy)
    assert not pet.hit_test(cx, pet.y - 50)


def test_tap_hops_and_drag_throws():
    pet, fx = make_pet()
    now = run(pet, 1, time.monotonic())
    x, y = pet.x + pet.w / 2, pet.y + pet.h / 2
    pet.pointer_down(x, y, now)
    assert pet.held
    pet.pointer_up(now + 0.1)
    assert pet.state == "idle" and pet.hop_active

    now = run(pet, 1, now + 0.1)
    pet.pointer_down(x, y, now)
    for i in range(1, 6):
        pet.pointer_move(x + i * 40, y - i * 30, now + i * 0.01)
    assert pet.state == "drag"
    pet.pointer_up(now + 0.06)
    assert pet.state == "fly"
    assert pet.vx > 0 and pet.vy < 0
    run(pet, 30, now + 0.06)
    assert pet.state != "fly" and pet.y == pytest.approx(pet.bounds.max_y)


def test_holding_pets_and_spawns_hearts():
    pet, fx = make_pet()
    now = run(pet, 1, time.monotonic())
    pet.pointer_down(pet.x + 20, pet.y + 30, now)
    now = run(pet, 1.0, now)
    assert pet.state == "pet"
    assert "heart" in fx.spawned


def test_claude_session_makes_him_code_and_then_celebrate():
    pet, fx = make_pet()
    now = run(pet, 1, time.monotonic())
    pet.on_claude({"kind": "start", "session": "s", "project": "demo"}, now)
    assert pet.state == "code" and pet.claude_work
    pet.on_claude({"kind": "tool", "session": "s", "project": "demo", "tool": "Edit", "detail": "a.py"}, now + 1)
    assert "tool:Edit" in fx.said
    pet.on_claude({"kind": "stop", "session": "s", "project": "demo"}, now + 2)
    assert pet.state == "idle" and not pet.claude_sessions
    assert "claude_done" in fx.said
    pet.on_claude({"kind": "report", "session": "s", "project": "demo", "text": "Done it"}, now + 2.5)
    run(pet, 3, now + 2.5)
    assert "Done it" in fx.said


def test_loud_sound_wakes_a_sleeper():
    pet, fx = make_pet()
    now = run(pet, 1, time.monotonic())
    pet.go_to_sleep(now, 0)
    pet.on_audio({"kind": "loud", "db": -5}, now + 1)
    assert pet.state == "idle"
    assert "loud_wake" in fx.said


def test_music_starts_dancing_or_wearing_headphones():
    pet, fx = make_pet()
    now = run(pet, 8, time.monotonic())
    pet.on_audio({"kind": "music", "playing": True}, now)
    now = run(pet, 12, now)  # he reacts once he stops walking
    assert pet.state == "dance" or pet.phones
    pet.on_audio({"kind": "music", "playing": False}, now)
    run(pet, 1, now)
    assert pet.state != "dance" and not pet.phones


def test_sleeper_wakes_for_music_hover_and_return():
    pet, fx = make_pet()
    now = run(pet, 1, time.monotonic())
    pet.go_to_sleep(now, now + 3600)
    pet.on_audio({"kind": "music", "playing": True}, now)
    now = run(pet, 0.5, now)
    assert pet.state != "sleep" and "music_wake" in fx.said

    pet.on_audio({"kind": "music", "playing": False}, now)
    now = run(pet, 1, now)
    pet.go_to_sleep(now, now + 3600)
    pet.set_cursor(pet.x + pet.w / 2, pet.y + pet.h / 2, now)
    now = run(pet, 2, now)
    assert pet.state != "sleep"

    pet.go_to_sleep(now, now + 3600)
    pet.last_user_input = now - 400
    pet.on_typing(120, now)
    assert pet.state == "idle" and "welcome_back" in fx.said


def test_falls_asleep_when_left_alone():
    pet, _ = make_pet()
    pet.last_interaction = time.monotonic() - 1
    run(pet, 200, time.monotonic(), dt=0.05)
    assert pet.state == "sleep" or pet.state in ("read", "code", "think")


@pytest.mark.parametrize("seed", range(5))
def test_random_poking_never_crashes(seed):
    rng = random.Random(seed)
    import clawd.pet as pet_module

    pet_module.random.seed(seed)
    pet, _ = make_pet()
    now = time.monotonic()
    for _ in range(4000):
        now = run(pet, rng.random() * 0.5, now, dt=1 / 30)
        roll = rng.random()
        x, y = rng.uniform(0, 1200), rng.uniform(0, 700)
        if roll < 0.15:
            pet.pointer_down(x, y, now)
        elif roll < 0.3:
            pet.pointer_move(x, y, now)
        elif roll < 0.45:
            pet.pointer_up(now, cancelled=rng.random() < 0.2)
        elif roll < 0.5:
            pet.on_audio(rng.choice([{"kind": "loud"}, {"kind": "music", "playing": rng.random() < 0.5}, {"kind": "beat", "strength": 5}]), now)
        elif roll < 0.55:
            pet.on_claude({"kind": rng.choice(["start", "tool", "attention", "stop", "end", "thought", "report"]), "session": "s", "project": "p", "tool": "Bash", "detail": "ls", "text": "hi"}, now)
        elif roll < 0.6:
            pet.set_cursor(x, y, now)
        elif roll < 0.62:
            pet.recall(now)
        elif roll < 0.64:
            pet.on_typing(rng.choice([None, 0, 400]), now)
        assert -pet.w <= pet.x <= 1300 and -200 <= pet.y <= 800
