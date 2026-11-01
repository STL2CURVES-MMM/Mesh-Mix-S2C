"""What a conversion is doing now, for the studio's page: a long analysis otherwise
looks hung (a threaded part spent half an hour in one step, with nothing to show for
it). The pipeline names each step as it starts; the studio's page asks for it while it
waits. Only the main process reports (workers only help within a step).
"""
import time

_now = {"step": "", "since": 0.0, "within": ""}


def step(text):
    """The conversion is now doing this (plain words, e.g. "finding screw threads")."""
    _now["step"], _now["since"] = text, time.time()


def within(text):
    """Steps from now on are part of this (e.g. "body 2 of 5"); "" for none."""
    _now["within"] = text


def current():
    """(what it is doing, seconds since that started), or ("", 0) before anything."""
    if not _now["step"]:
        return "", 0.0
    text = f"{_now['within']}: {_now['step']}" if _now["within"] else _now["step"]
    return text, time.time() - _now["since"]
