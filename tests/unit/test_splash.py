import io
import os

import pytest

from grippo.cli import splash


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def big_terminal(monkeypatch):
    monkeypatch.setattr(splash.shutil, "get_terminal_size",
                        lambda fallback=(0, 0): os.terminal_size((120, 40)))


def test_frames_are_consistent():
    assert len(splash.RAW_FRAMES) == 4
    for frame in splash.RAW_FRAMES:
        assert len(frame) == splash.HEIGHT
        assert all(len(line) == splash.ART_WIDTH for line in frame)
    # every consecutive blink frame differs, so the animation is visible
    for a, b in zip(splash.RAW_FRAMES, splash.RAW_FRAMES[1:] + splash.RAW_FRAMES[:1], strict=True):
        assert a != b


def test_animation_on_terminal(big_terminal):
    out, slept = FakeTTY(), []
    splash.show(out, "9.9.9", cycles=2, env={"TERM": "xterm"}, sleep=slept.append)
    text = out.getvalue()
    assert len(slept) == 8 and abs(sum(slept) - 8 / splash.FPS) < 1e-9
    assert text.count(f"\x1b[{splash.HEIGHT}A") == 8
    assert text.startswith("\x1b[?25l") and "\x1b[?25h" in text  # cursor restored
    assert "GRIPPO" in text and "9.9.9" in text and "\x1b[38;5;214m" in text


def test_ctrl_c_skips_and_restores_cursor(big_terminal):
    out = FakeTTY()

    def interrupt(_):
        raise KeyboardInterrupt

    splash.show(out, "1", env={"TERM": "xterm"}, sleep=interrupt)
    assert "GRIPPO" in out.getvalue()
    assert out.getvalue().endswith("\x1b[?25h")  # cursor restored after Ctrl-C


@pytest.mark.parametrize("env,kwargs", [
    ({"TERM": "xterm", "GRIPPO_NO_ANIMATION": "1"}, {}),
    ({"TERM": "dumb"}, {}),
    ({"TERM": "xterm"}, {"animate": False}),
    ({"TERM": "xterm"}, {"cycles": 0}),
])
def test_disabled_draws_static_without_sleep(big_terminal, env, kwargs):
    out, slept = FakeTTY(), []
    splash.show(out, "1", env=env, sleep=slept.append, **kwargs)
    assert slept == [] and "\x1b[?25l" not in out.getvalue() and "GRIPPO" in out.getvalue()


def test_pipe_gets_title_only(big_terminal):
    out = io.StringIO()
    splash.show(out, "1", env={"TERM": "xterm"}, sleep=lambda _: None)
    assert out.getvalue() == "  GRIPPO  v1  assistente Git local\n"


def test_no_color(big_terminal):
    out = FakeTTY()
    splash.show(out, "1", env={"TERM": "xterm", "NO_COLOR": "1"}, sleep=lambda _: None)
    assert "\x1b[38;5" not in out.getvalue() and "\u28ff" in out.getvalue()


def test_small_terminal_does_not_animate(monkeypatch):
    monkeypatch.setattr(splash.shutil, "get_terminal_size",
                        lambda fallback=(0, 0): os.terminal_size((30, 10)))
    out, slept = FakeTTY(), []
    splash.show(out, "1", env={"TERM": "xterm"}, sleep=slept.append)
    assert slept == [] and splash.RAW_FRAMES[0][1].strip() not in out.getvalue()
