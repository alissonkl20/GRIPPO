import io

from rich.console import Console

from grippo.cli import renderer, theme
from grippo.engine.operations import Risk


def test_ansi_respects_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    colored = theme.ansi("grippo", "brand")
    assert colored.startswith("\x1b[") and theme.strip_ansi(colored) == "grippo"
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.ansi("grippo", "brand") == "grippo"


def test_confirm_prompt_is_plain_text_once_stripped(monkeypatch):
    from grippo.cli.confirm import _prompt

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    high = theme.strip_ansi(_prompt(Risk.HIGH, True))
    assert "RISCO ALTO" in high and "confirmar" in high and "[e] editar mensagem" in high
    assert "[c] confirmar" in theme.strip_ansi(_prompt(Risk.MODERATE, False))


def test_risk_badges_and_output_highlight():
    out = Console(file=io.StringIO(), theme=theme.THEME, force_terminal=True, width=100)
    out.print(renderer.risk_badge(Risk.HIGH), renderer.risk_badge(Risk.LOW))
    text = out.file.getvalue()
    assert "ALTO" in text and "BAIXO" in text and "\x1b[" in text
    styled = renderer.colorize_output("+added\n-removed\n@@ -1 +1 @@\nabc1234 feat: x\nplain")
    styles = {str(span.style) for span in styled.spans}
    assert {"green", "red", "cyan"} <= styles and "color(220)" in styles


def test_every_theme_style_is_valid():
    console = Console(file=io.StringIO(), theme=theme.THEME)
    for name in theme.THEME.styles:
        console.print(f"[{name}]x[/]")
