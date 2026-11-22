"""The only place in GRIPPO that spawns ``git``.

Guarantees:
* argv list, never ``shell=True``; the program is always the resolved ``git`` binary;
* the subcommand must be in an allowlist, so global options (``-c``, ``-C``,
  ``--git-dir``…) and aliases can never be injected — Git never lets an alias
  shadow a builtin;
* dangerous options (``--upload-pack``, ``--exec``, ``--output``, interactive modes…)
  are rejected even if a caller builds them;
* any command not classified as read-only needs a single-use :class:`MutationTicket`,
  which only the executor issues after the permission check;
* stdin is closed, prompts are disabled, the child runs in its own process group and
  is killed (TERM, then KILL) on timeout, interrupt or interpreter exit.
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from grippo.errors import GrippoError
from grippo.logs import redact

log = logging.getLogger(__name__)


class GitNotFoundError(GrippoError):
    pass


class CommandRejectedError(GrippoError):
    """A git invocation was refused by the runner's own validation."""


READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "ls-files",
        "merge-base",
        "rev-list",
        "for-each-ref",
        "cat-file",
        "ls-remote",
        "check-ref-format",
        "show-ref",
        "version",
    }
)
CONDITIONAL_SUBCOMMANDS = frozenset(
    {"branch", "tag", "remote", "stash", "reflog", "config", "clean", "symbolic-ref", "worktree"}
)
MUTATING_SUBCOMMANDS = frozenset(
    {
        "add",
        "restore",
        "commit",
        "switch",
        "checkout",
        "merge",
        "cherry-pick",
        "revert",
        "reset",
        "rebase",
        "fetch",
        "pull",
        "push",
        "rm",
        "mv",
        "init",
        "update-ref",
    }
)
ALLOWED_SUBCOMMANDS = READ_ONLY_SUBCOMMANDS | CONDITIONAL_SUBCOMMANDS | MUTATING_SUBCOMMANDS

# Rejected for every subcommand (checked only before the "--" separator).
_DENIED_EVERYWHERE = (
    "--upload-pack",
    "--receive-pack",
    "--exec",
    "--output",
    "--open-files-in-pager",
    "--ext-diff",
    "--interactive",
    "--edit",
    "--template",
    "--config",
    "--git-dir",
    "--work-tree",
    "--exec-path",
    "--namespace",
)
_DENIED_BY_SUBCOMMAND: dict[str, tuple[str, ...]] = {
    "rebase": ("-x", "-i", "--rebase-merges"),
    "add": ("-p", "-i", "-e", "--patch"),
    "restore": ("-p", "--patch"),
    "reset": ("-p", "--patch"),
    "checkout": ("-p", "--patch"),
    "stash": ("-p", "--patch"),
    "commit": ("-e", "-p", "--patch"),
    "clean": ("-i",),
    "fetch": ("-u",),
    "pull": ("-u",),
}
_BRANCH_WRITE = (
    "-d",
    "-D",
    "-m",
    "-M",
    "-c",
    "-C",
    "-f",
    "-u",
    "--delete",
    "--move",
    "--copy",
    "--force",
    "--set-upstream-to",
    "--unset-upstream",
    "--edit-description",
    "--track",
    "--no-track",
)
_TAG_WRITE = (
    "-d",
    "-a",
    "-s",
    "-u",
    "-f",
    "-m",
    "-F",
    "--delete",
    "--annotate",
    "--sign",
    "--force",
    "--message",
    "--file",
)
_CONFIG_READ = ("--get", "--get-all", "--get-regexp", "--list", "-l")
_CONFIG_WRITE = (
    "--add",
    "--unset",
    "--unset-all",
    "--replace-all",
    "--rename-section",
    "--remove-section",
    "--edit",
    "-e",
)

_SCRUBBED_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_SCRUBBED_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_EXEC_PATH",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_EXTERNAL_DIFF",
    "GIT_PAGER",
    "PAGER",
    "LC_ALL",
    "LANG",
    "LANGUAGE",
)


def _options(args: Sequence[str]) -> list[str]:
    """Arguments before the first ``--`` separator (everything after is a path)."""
    out: list[str] = []
    for arg in args:
        if arg == "--":
            break
        out.append(arg)
    return out


def _has(options: Sequence[str], names: Sequence[str]) -> bool:
    for option in options:
        name = option.split("=", 1)[0] if option.startswith("--") else option
        if name in names:
            return True
        # Short option clusters such as "-fdx" or "-D".
        cluster = option.startswith("-") and not option.startswith("--") and len(option) > 2
        if cluster and any(len(n) == 2 and n[1] in option[1:] for n in names if n[0] == "-"):
            return True
    return False


def is_read_only(subcommand: str, args: Sequence[str]) -> bool:
    """Conservative classification: anything not provably read-only is mutating."""
    if subcommand in READ_ONLY_SUBCOMMANDS:
        return True
    options = _options(args)
    if subcommand == "branch":
        return ("--list" in options or "--show-current" in options) and not _has(
            options, _BRANCH_WRITE
        )
    if subcommand == "tag":
        return ("--list" in options or "-l" in options) and not _has(options, _TAG_WRITE)
    if subcommand == "remote":
        return not args or list(args) == ["-v"] or args[0] in {"get-url", "show"}
    if subcommand == "stash":
        return bool(args) and args[0] in {"list", "show"}
    if subcommand == "reflog":
        return not args or args[0] == "show"
    if subcommand == "worktree":
        return bool(args) and args[0] == "list"
    if subcommand == "config":
        return _has(options, _CONFIG_READ) and not _has(options, _CONFIG_WRITE)
    if subcommand == "clean":
        return _has(options, ("-n", "--dry-run")) and not _has(options, ("-f", "--force"))
    if subcommand == "symbolic-ref":
        positional = [a for a in options if not a.startswith("-")]
        return len(positional) == 1
    return False


BACKUP_REF_PREFIX = "refs/grippo/backup/"
_BACKUP_REF = re.compile(r"^refs/grippo/backup/[A-Za-z0-9_\-]{1,120}$")


def _is_backup_ref_update(args: Sequence[str]) -> bool:
    """Exactly ``update-ref refs/grippo/backup/<safe-name> <40-hex sha>`` — nothing else."""
    return (
        len(args) == 3
        and bool(_BACKUP_REF.fullmatch(args[1]))
        and ".." not in args[1]
        and bool(re.fullmatch(r"[0-9a-f]{40}", args[2]))
    )


def validate_invocation(args: Sequence[str]) -> str:
    """Return the subcommand or raise :class:`CommandRejectedError`."""
    if not args:
        raise CommandRejectedError("comando git vazio")
    for arg in args:
        if not isinstance(arg, str):
            raise CommandRejectedError(f"argumento não textual: {arg!r}")
        if "\x00" in arg:
            raise CommandRejectedError("argumento contém byte NUL")
    subcommand = args[0]
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise CommandRejectedError(f"subcomando git não permitido: {subcommand!r}")
    if subcommand == "update-ref" and not _is_backup_ref_update(args):
        raise CommandRejectedError(
            "update-ref só é permitido para criar refs de backup em refs/grippo/backup/"
        )
    options = _options(args[1:])
    denied = _DENIED_EVERYWHERE + _DENIED_BY_SUBCOMMAND.get(subcommand, ())
    for option in options:
        name = option.split("=", 1)[0]
        if name in denied:
            raise CommandRejectedError(f"opção bloqueada para git {subcommand}: {name}")
    if subcommand in _DENIED_BY_SUBCOMMAND and _has(
        options, [d for d in _DENIED_BY_SUBCOMMAND[subcommand] if len(d) == 2]
    ):
        raise CommandRejectedError(f"opção curta bloqueada para git {subcommand}")
    return subcommand


_TICKET_KEY = object()


class MutationTicket:
    """Single-use capability for one exact mutating argv. Issued only by the executor."""

    __slots__ = ("_args", "_lock", "_used")

    def __init__(self, args: Sequence[str], key: object) -> None:
        if key is not _TICKET_KEY:
            raise CommandRejectedError("tickets de mutação só podem ser emitidos pelo executor")
        self._args = tuple(args)
        self._used = False
        self._lock = threading.Lock()

    def consume(self, args: Sequence[str]) -> None:
        with self._lock:
            if self._used:
                raise CommandRejectedError("ticket de mutação já utilizado")
            if tuple(args) != self._args:
                raise CommandRejectedError("ticket de mutação não corresponde ao comando")
            self._used = True


def _issue_ticket(args: Sequence[str]) -> MutationTicket:
    """Internal: called by :mod:`grippo.engine.executor` after authorization."""
    return MutationTicket(args, _TICKET_KEY)


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False
    interrupted: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.interrupted

    @property
    def command(self) -> str:
        return redact("git " + shlex.join(self.args))

    def error_text(self) -> str:
        if self.timed_out:
            return f"tempo limite excedido ({self.duration_sec:.0f}s)"
        if self.interrupted:
            return "interrompido pelo usuário"
        return redact((self.stderr or self.stdout).strip())


_live: set[subprocess.Popen[bytes]] = set()
_live_lock = threading.Lock()


def _kill_group(proc: subprocess.Popen[bytes], grace: float = 2.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


@atexit.register
def _reap_children() -> None:
    with _live_lock:
        procs = list(_live)
    for proc in procs:
        if proc.poll() is None:
            _kill_group(proc, grace=0.5)


def git_binary() -> str:
    found = shutil.which("git")
    if not found:
        raise GitNotFoundError("git não encontrado no PATH")
    return found


def _child_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _SCRUBBED_ENV and not k.startswith(_SCRUBBED_ENV_PREFIXES)
    }
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_EDITOR="true",
        GIT_SEQUENCE_EDITOR="true",
        GIT_MERGE_AUTOEDIT="no",
        GIT_PAGER="cat",
        LC_MESSAGES="C",
        LANGUAGE="C",
        GCM_INTERACTIVE="never",
    )
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    timeout: float,
    ticket: MutationTicket | None = None,
) -> GitResult:
    subcommand = validate_invocation(args)
    read_only = is_read_only(subcommand, args[1:])
    if not read_only:
        if ticket is None:
            raise CommandRejectedError(
                f"git {subcommand} altera o repositório e exige autorização do executor"
            )
        ticket.consume(args)
    if not cwd.is_dir():
        raise CommandRejectedError(f"diretório inexistente: {cwd}")

    argv = [git_binary(), "--no-pager", "-c", "core.quotepath=false", "-c", "color.ui=never"]
    if read_only:
        argv.append("--no-optional-locks")
    argv.extend(args)

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=_child_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    with _live_lock:
        _live.add(proc)
    timed_out = interrupted = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        out, err = proc.communicate()
    except KeyboardInterrupt:
        interrupted = True
        _kill_group(proc)
        out, err = proc.communicate()
    finally:
        with _live_lock:
            _live.discard(proc)
    result = GitResult(
        args=tuple(args),
        cwd=str(cwd),
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=_decode(out or b""),
        stderr=_decode(err or b""),
        duration_sec=time.monotonic() - started,
        timed_out=timed_out,
        interrupted=interrupted,
    )
    log.debug("%s rc=%s %.3fs", result.command, result.returncode, result.duration_sec)
    if timed_out:
        log.warning("timeout: %s", result.command)
    return result
