from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRIPPO_", env_file=".env", extra="ignore")

    repo_root: Path | None = None
    skills_path: Path | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b"
    shell_timeout_sec: float = 30.0
    max_diff_bytes: int = 32 * 1024
    allow_arbitrary_shell: bool = False

    def resolve_repo_root(self, cwd: Path | None = None) -> Path:
        if self.repo_root is not None:
            return self.repo_root.resolve()
        start = (cwd or Path.cwd()).resolve()
        for path in [start, *start.parents]:
            if (path / ".git").exists():
                return path
        raise FileNotFoundError(f"No git repository found from {start}")

    def resolve_skills_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self.skills_path is not None:
            dirs.append(self.skills_path.resolve())
        default = _default_skills_dir()
        if default not in dirs:
            dirs.append(default)
        return dirs


settings = Settings()
