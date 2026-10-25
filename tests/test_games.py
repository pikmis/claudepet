import pytest

from clawd.games import Ball
from tests.test_pet import make_pet, run


def test_ball_falls_bounces_and_settles():
    ball = Ball(100, 20, 12)
    now = 0.0
    for _ in range(600):
        now += 1 / 60
        ball.step(1 / 60, 800, 500)
    assert ball.y == pytest.approx(500 - 12)
    assert ball.on_floor and ball.vy == 0


def test_ball_can_be_thrown_with_the_mouse():
    ball = Ball(100, 400, 12)
    ball.grab(100, 400, 0.0)
    for i in range(1, 6):
        ball.drag(100 + i * 20, 400 - i * 10, i * 0.01, 800, 500)
    ball.release(0.055)
    assert not ball.held and ball.vx > 1000 and ball.vy < 0


def test_ball_stays_in_the_window():
    ball = Ball(790, 300, 12)
    ball.kick(3000, -500, 0.0)
    now = 0.0
    for _ in range(300):
        now += 1 / 60
        ball.step(1 / 60, 800, 500)
        assert 12 <= ball.x <= 788 and 12 <= ball.y <= 488


def test_clawd_chases_and_kicks_the_ball():
    pet, fx = make_pet()
    now = run(pet, 1, 1000.0)
    pet.start_ball(now, 12)
    pet.ball.x = pet.x + 400 if pet.x < 500 else pet.x - 400
    kicks = []
    original = pet.kick_ball
    pet.kick_ball = lambda t: (kicks.append(t), original(t))
    run(pet, 15, now)
    assert kicks, "he never kicked"
    assert "ball_start" in fx.said


def test_ball_game_ends_when_nobody_plays():
    pet, fx = make_pet()
    ended = []
    pet.on_game_end = ended.append
    now = run(pet, 1, 1000.0)
    pet.start_ball(now, 12)
    run(pet, 200, now, dt=0.1)
    assert pet.ball is None and ended == ["ball"] and "ball_end" in fx.said


def test_tag_flees_from_the_cursor_and_counts_catches():
    pet, fx = make_pet()
    now = run(pet, 1, 1000.0)
    pet.start_tag(now)
    now = run(pet, 1.5, now)
    start = pet.x
    pet.set_cursor(pet.x + pet.w / 2 - 80, pet.y + pet.h / 2, now)
    now = run(pet, 0.5, now)
    assert pet.x > start, "he should run away from a cursor on his left"
    pet.tag_grace = 0
    pet.pointer_down(pet.x + 20, pet.y + 30, now)
    pet.pointer_up(now + 0.05)
    assert pet.tag_score == 1 and "tag_caught" in fx.said
    pet.stop_tag("tag_end")
    assert not pet.tag
