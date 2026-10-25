"""Clawd's moods: energy follows the time of day and sleep, boredom grows while he is ignored,
and affection grows when the user pets him, plays with him and visits him."""
from __future__ import annotations

ENERGY_BY_HOUR = (
    0.25, 0.2, 0.2, 0.2, 0.25, 0.35,  # 0-5
    0.6, 0.75, 0.85, 0.9, 0.9, 0.9,  # 6-11
    0.8, 0.75, 0.85, 0.9, 0.9, 0.85,  # 12-17
    0.8, 0.75, 0.7, 0.6, 0.5, 0.4,  # 18-23
)
ENERGY_DRIFT = 1 / 600  # per second towards what the hour calls for
SLEEP_REST = 1 / 300  # per second while asleep
BOREDOM_RISE = 1 / 900  # empty for 15 minutes means bored to the bone
BOREDOM_FALL = 1 / 90  # while somebody is paying attention
IGNORED_AFTER = 20 * 60  # this long without attention and affection starts to fade
AFFECTION_FADE = 1 / 3000
AFFECTION_FLOOR = 0.15


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


class Mood:
    def __init__(self, affection: float = 0.5) -> None:
        self.energy = 0.8
        self.boredom = 0.2
        self.affection = affection
        self._label = "calm"

    def update(self, dt: float, hour: int, asleep: bool, attended: bool, ignored_for: float) -> None:
        """`attended`: the user is doing something with him right now; `ignored_for`: seconds
        since the last time they did."""
        if asleep:
            self.energy = clamp01(self.energy + SLEEP_REST * dt)
        else:
            target = ENERGY_BY_HOUR[hour % 24]
            step = ENERGY_DRIFT * dt
            self.energy = clamp01(self.energy + max(-step, min(step, target - self.energy)))
        if attended:
            self.boredom = clamp01(self.boredom - BOREDOM_FALL * dt)
        elif not asleep:
            self.boredom = clamp01(self.boredom + BOREDOM_RISE * dt)
        if ignored_for > IGNORED_AFTER:
            self.affection = max(AFFECTION_FLOOR, self.affection - AFFECTION_FADE * dt)

    def cheer(self, boredom: float = 0.0, affection: float = 0.0) -> None:
        """Something nice happened: positive numbers lessen boredom and raise affection."""
        self.boredom = clamp01(self.boredom - boredom)
        self.affection = clamp01(self.affection + affection)

    def annoy(self, affection: float = 0.1) -> None:
        self.affection = clamp01(self.affection - affection)

    @property
    def label(self) -> str:
        """One word for how he feels. Moods stick a little: leaving one takes a bigger change."""
        previous = self._label
        e, b, a = self.energy, self.boredom, self.affection
        if a < (0.3 if previous == "sulking" else 0.22):
            label = "sulking"
        elif e < (0.35 if previous == "sleepy" else 0.28):
            label = "sleepy"
        elif b > (0.6 if previous == "bored" else 0.75):
            label = "bored"
        elif a > 0.72 and b < 0.45:
            label = "happy"
        else:
            label = "calm"
        self._label = label
        return label

    def describe(self) -> str:
        """For the model, in English."""
        label = self.label
        extra = {
            "sulking": "he feels ignored and is a little sulky",
            "sleepy": "he is low on energy",
            "bored": "he is bored and wants attention",
            "happy": "he is in a great mood and fond of the user",
            "calm": "he feels fine",
        }[label]
        return f"Clawd's mood: {label}; {extra}."
