"""`grippo doctor`: environment diagnosis and essential self-tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from grippo import __version__
from grippo.config import load_config
from grippo.errors import AuthorizationError, GrippoError
from grippo.manage.install_layout import read_manifest
from grippo.manage.resources import gpu_info, system_memory
from grippo.paths import get_paths

MIN_GIT = (2, 23)  # git switch / git restore


@dataclass
class Check:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str


def _git_version() -> tuple[int, ...] | None:
    binary = shutil.which("git")
    if not binary:
        return None
    out = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    numbers = [p for p in out.split()[-1].split(".") if p.isdigit()]
    return tuple(int(n) for n in numbers[:3]) or None


def run_self_test() -> list[Check]:
    """Exercise runner + executor + permission gate on a throwaway repository."""
    from grippo.engine.audit import AuditLog
    from grippo.engine.executor import GitExecutor
    from grippo.engine.permissions import Decision, GitPermissionManager
    from grippo.git.lock import RepoLocks
    from grippo.git.repo import discover
    from grippo.git.runner import CommandRejectedError, run_git

    checks: list[Check] = []
    git = shutil.which("git") or "git"
    with tempfile.TemporaryDirectory(prefix="grippo-selftest-") as tmp:
        root = Path(tmp) / "repo"
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        for args in (
            ["init", "-q", "-b", "main", str(root)],
            ["-C", str(root), "config", "user.name", "GRIPPO self-test"],
            ["-C", str(root), "config", "user.email", "selftest@grippo.invalid"],
            ["-C", str(root), "config", "commit.gpgsign", "false"],
        ):
            subprocess.run(
                [git, *args],
                check=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
        (root / "a.txt").write_text("a\n")
        repo = discover(root)
        executor = GitExecutor(
            repo,
            permissions=GitPermissionManager(),
            audit=AuditLog(None),
            locks=RepoLocks(Path(tmp) / "locks"),
        )
        plan = executor.build_plan(
            [("add", {"paths": ["a.txt"]}), ("commit", {"message": "test: self-test"})]
        )
        prepared = executor.prepare(plan)
        try:
            executor.execute(prepared)
            checks.append(Check("permissão sem autorização", "FAIL", "executou sem autorização"))
        except AuthorizationError:
            checks.append(Check("permissão sem autorização", "OK", "bloqueado"))
        auth = executor.authorize(prepared, Decision.CONFIRM)
        result = executor.execute(prepared, auth)
        checks.append(Check("commit autorizado", "OK" if result.ok else "FAIL", result.message))
        for name, argv in (
            ("clean sem ticket", ["clean", "-fdx"]),
            ("injeção -c", ["-c", "core.pager=sh", "log"]),
            ("upload-pack", ["fetch", "--upload-pack=touch /tmp/x", "origin"]),
        ):
            try:
                run_git(root, argv, timeout=10)
                checks.append(Check(name, "FAIL", "não bloqueado"))
            except CommandRejectedError:
                checks.append(Check(name, "OK", "bloqueado"))
    return checks


def run_doctor(repo: Path | None, *, as_json: bool, self_test: bool, offline: bool) -> int:
    paths = get_paths()
    checks: list[Check] = []
    py = sys.version_info
    checks.append(
        Check(
            "python",
            "OK" if py >= (3, 11) else "FAIL",
            f"{py.major}.{py.minor}.{py.micro} ({sys.executable})",
        )
    )
    version = _git_version()
    if version is None:
        checks.append(Check("git", "FAIL", "git não encontrado no PATH"))
    else:
        checks.append(
            Check("git", "OK" if version >= MIN_GIT else "FAIL", ".".join(map(str, version)))
        )
    checks.append(Check("grippo", "OK", __version__))
    try:
        config = load_config(paths.config_file)
        checks.append(
            Check(
                "configuração",
                "OK",
                f"{paths.config_file}" + ("" if paths.config_file.exists() else " (padrões)"),
            )
        )
    except GrippoError as error:
        checks.append(Check("configuração", "FAIL", str(error)))
        config = None
    try:
        paths.ensure()
        probe = paths.logs_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        checks.append(Check("diretórios XDG", "OK", f"estado em {paths.state_dir}"))
    except OSError as error:
        checks.append(Check("diretórios XDG", "FAIL", str(error)))

    manifest = read_manifest(paths)
    if manifest:
        checks.append(
            Check(
                "instalação",
                "OK",
                f"{manifest.get('version')} via install.sh ({paths.current_link})",
            )
        )
    else:
        checks.append(Check("instalação", "WARN", "não instalado via install.sh (modo dev/pipx?)"))
    on_path = str(paths.bin_dir) in os.environ.get("PATH", "").split(os.pathsep)
    which = shutil.which("grippo")
    checks.append(
        Check(
            "PATH",
            "OK" if which and on_path else "WARN",
            f"grippo → {which}" if which else f"{paths.bin_dir} não está no PATH (veja INSTALL.md)",
        )
    )
    try:
        from grippo.git.repo import discover

        found = discover(repo or Path.cwd(), paths=paths)
        checks.append(
            Check(
                "repositório",
                "OK",
                str(found.root) + (" (worktree vinculada)" if found.is_linked_worktree else ""),
            )
        )
    except GrippoError as error:
        checks.append(Check("repositório", "WARN", str(error)))

    memory = system_memory()
    if memory:
        checks.append(
            Check(
                "RAM",
                "OK",
                f"total {memory.get('MemTotal', 0) / 1024:.1f} GiB · "
                f"disponível {memory.get('MemAvailable', 0) / 1024:.1f} GiB",
            )
        )
    gpu = gpu_info()
    checks.append(
        Check(
            "GPU",
            "OK" if gpu.get("available") else "WARN",
            f"{gpu.get('name')} · {gpu.get('used_mib')}/{gpu.get('total_mib')} MiB"
            if gpu.get("available")
            else f"{gpu.get('reason')} (CPU será usada)",
        )
    )

    if config is not None and config.llm.enabled and not offline:
        from grippo.llm.client import OllamaClient

        client = OllamaClient(config.llm)
        try:
            checks.append(Check("ollama", "OK", f"{client.version()} em {config.llm.base_url}"))
            try:
                client.ensure_model()
                models = {m.get("name"): m for m in client.list_models()}
                info = models.get(config.llm.model, {})
                details = info.get("details", {}) if isinstance(info, dict) else {}
                size = (info.get("size", 0) if isinstance(info, dict) else 0) / 2**30
                checks.append(
                    Check(
                        "modelo",
                        "OK",
                        f"{config.llm.model} · "
                        f"{details.get('parameter_size', '?')} · "
                        f"{details.get('quantization_level', '?')} · {size:.2f} GiB",
                    )
                )
                available = memory.get("MemAvailable", 0) / 1024
                if not gpu.get("available") and size and available < size + 0.5:
                    checks.append(
                        Check(
                            "memória p/ modelo",
                            "WARN",
                            f"RAM disponível ({available:.1f} GiB) próxima do tamanho "
                            f"do modelo ({size:.1f} GiB)",
                        )
                    )
            except GrippoError as error:
                checks.append(Check("modelo", "FAIL", str(error)))
        except GrippoError as error:
            checks.append(
                Check(
                    "ollama",
                    "WARN",
                    f"{error} — GRIPPO funciona sem LLM, com o roteador determinístico",
                )
            )
        finally:
            client.close()

    if self_test:
        try:
            checks.extend(run_self_test())
        except Exception as error:  # noqa: BLE001 - report any self-test crash
            checks.append(Check("self-test", "FAIL", f"{type(error).__name__}: {error}"))

    failed = any(c.status == "FAIL" for c in checks)
    if as_json:
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        icons = {"OK": "\033[32m✔\033[0m", "WARN": "\033[33m!\033[0m", "FAIL": "\033[31m✘\033[0m"}
        for check in checks:
            print(f" {icons[check.status]} {check.name:22} {check.detail}")
        print("\n" + ("Problemas encontrados." if failed else "Tudo certo."))
    return 1 if failed else 0
