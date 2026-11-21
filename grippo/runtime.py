"""Composition root: builds every component from configuration for one repository."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from grippo.agent.assistant import Assistant
from grippo.agent.planner import Planner
from grippo.agent.session import Session
from grippo.config import Config, load_config
from grippo.engine.audit import AuditLog
from grippo.engine.executor import GitExecutor
from grippo.engine.operations import set_protected_branches
from grippo.engine.permissions import GitPermissionManager
from grippo.git.lock import RepoLocks
from grippo.git.repo import Repository, discover
from grippo.llm.client import OllamaClient
from grippo.llm.commit import CommitDrafter
from grippo.llm.interpreter import Interpreter
from grippo.logs import setup_logging
from grippo.paths import AppPaths, get_paths

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    config: Config
    paths: AppPaths
    repo: Repository
    executor: GitExecutor
    assistant: Assistant
    llm: OllamaClient | None
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.llm is not None:
            if self.config.llm.unload_on_exit:
                try:
                    self.llm.unload()
                except Exception as error:  # noqa: BLE001 - best effort on shutdown
                    log.info("unload ignorado: %s", error)
            self.llm.close()
        self.executor.audit.close()


def init_environment(config: Config | None = None) -> tuple[Config, AppPaths]:
    paths = get_paths()
    paths.ensure()
    config = config or load_config(paths.config_file)
    setup_logging(
        paths.logs_dir, config.logging.level, config.logging.max_bytes, config.logging.backups
    )
    return config, paths


def build_runtime(
    repo_path: Path | None = None, *, use_llm: bool = True, config: Config | None = None
) -> Runtime:
    config, paths = init_environment(config)
    repo = discover(repo_path or Path.cwd(), paths=paths)
    set_protected_branches(config.security.protected_branches)
    permissions = GitPermissionManager(
        autonomy_level=config.security.autonomy_level,
        ttl_sec=config.security.auth_ttl_sec,
        auto_approve=config.security.auto_approve,
    )
    executor = GitExecutor(
        repo,
        permissions=permissions,
        audit=AuditLog(paths.audit_dir),
        locks=RepoLocks(paths.locks_dir, config.git.lock_timeout_sec),
        git_timeout=config.git.timeout_sec,
        network_timeout=config.git.network_timeout_sec,
    )
    llm = OllamaClient(config.llm) if use_llm and config.llm.enabled else None
    interpreter = Interpreter(llm, max_prompt_chars=config.llm.max_prompt_chars)
    planner = Planner(executor, CommitDrafter(llm, config.llm.max_diff_chars))
    assistant = Assistant(executor, interpreter, planner, Session(config.llm.max_history_turns))
    log.info(
        "runtime pronto: repo=%s llm=%s autonomy=%s",
        repo.root,
        config.llm.model if llm else "desativada",
        config.security.autonomy_level,
    )
    return Runtime(config, paths, repo, executor, assistant, llm)
