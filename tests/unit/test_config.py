from pathlib import Path

import pytest

from grippo.config import load_config, read_toml, set_value, write_default_config
from grippo.errors import ConfigError


def test_defaults_when_missing(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml", env={})
    assert config.llm.model == "qwen2.5:3b"
    assert config.security.autonomy_level == 2


def test_toml_and_env_precedence(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('[llm]\nmodel = "file-model"\nnum_ctx = 2048\n[security]\nautonomy_level = 1\n')
    config = load_config(path, env={"GRIPPO_LLM_MODEL": "env-model"})
    assert config.llm.model == "env-model"
    assert config.llm.num_ctx == 2048
    assert config.security.autonomy_level == 1


def test_legacy_env_names(tmp_path: Path):
    config = load_config(tmp_path / "x.toml", env={"GRIPPO_MODEL": "legacy",
                                                   "GRIPPO_OLLAMA_BASE_URL": "http://h:1"})
    assert config.llm.model == "legacy" and config.llm.base_url == "http://h:1"


@pytest.mark.parametrize("content", ['[security]\nautonomy_level = 9\n', '[llm]\nnum_ctx = 10\n',
                                     '[nope]\nx = 1\n', '[llm]\nunknown = 1\n',
                                     '[llm]\nnum_ctx = "abc"\n', 'not toml ==='])
def test_invalid_config_rejected(tmp_path: Path, content: str):
    path = tmp_path / "c.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_config(path, env={})


def test_cwd_dotenv_is_ignored(tmp_path: Path, monkeypatch):
    """A managed repository must not be able to reconfigure GRIPPO via .env."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GRIPPO_SECURITY_AUTONOMY_LEVEL=3\nGRIPPO_LLM_MODEL=evil\n")
    config = load_config(tmp_path / "none.toml", env={})
    assert config.security.autonomy_level == 2 and config.llm.model == "qwen2.5:3b"


def test_write_default_never_overwrites(tmp_path: Path):
    path = tmp_path / "cfg" / "config.toml"
    assert write_default_config(path) is True
    path.write_text('[llm]\nmodel = "mine"\n')
    assert write_default_config(path) is False
    assert "mine" in path.read_text()


def test_set_value_validates(tmp_path: Path):
    path = tmp_path / "config.toml"
    set_value(path, "llm.num_ctx", "8192")
    assert read_toml(path)["llm"]["num_ctx"] == 8192
    with pytest.raises(ConfigError):
        set_value(path, "security.autonomy_level", "7")
    assert "autonomy_level" not in read_toml(path).get("security", {})


def test_gpu_info_keeps_gpu_name(monkeypatch):
    """Regression: a process name in nvidia-smi output must not overwrite the GPU name."""
    import subprocess

    from grippo.manage import resources

    outputs = iter([
        "NVIDIA GeForce RTX 3060, 12288, 3000, 10, 595.99\n",
        "123, /usr/share/cursor/cursor --type=gpu, 104\n456, /x/llama-server, 2194\n",
    ])
    monkeypatch.setattr(resources.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(resources.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, next(outputs), ""))
    info = resources.gpu_info()
    assert info["name"] == "NVIDIA GeForce RTX 3060"
    assert [p["name"] for p in info["processes"]] == ["cursor", "llama-server"]
