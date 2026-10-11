"""Absolute capture cadence, compensating for rounded Windows timer wakes."""


def next_deadline(previous, completed, fps):
    period = 1 / fps
    deadline = previous + period
    return completed if deadline < completed - period else deadline
