"""`grippo bench`: measured (not estimated) startup, Git and LLM performance and memory.

Runs against a throwaway repository, so it is safe to execute anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from grippo import __version__
from grippo.errors import GrippoError
from grippo.manage.resources import cpu_seconds, find_processes, gpu_info, process_memory

PROMPTS = (
    "quero fazer um commit das alterações do formulário",
    "desfaça o último commit mas mantenha minhas alterações",
    "quero desfazer as alterações do último commit sem apagar o histórico",
    "atualize minha branch com develop",
    "envie minhas alterações para o remoto",
    "crie uma branch feature/cadastro",
    "compare minha branch com main",
)


def _fixture(root: Path) -> Path:
    git = shutil.which("git") or "git"
    repo = root / "bench-repo"
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def run(*args: str) -> None:
        subprocess.run(
            [git, *args],
            cwd=repo,
            check=True,
            capture_output=True,
            env=env,
            timeout=60,
        )

    repo.mkdir()
    run("init", "-q", "-b", "main")
    run("config", "user.name", "bench")
    run("config", "user.email", "bench@grippo.invalid")
    run("config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# bench\n")
    run("add", "README.md")
    run("commit", "-qm", "chore: init")
    run("branch", "develop")
    (repo / "src").mkdir()
    (repo / "src" / "form.js").write_text("export function form() { return 1 }\n")
    (repo / "README.md").write_text("# bench\n\nform docs\n")
    return repo


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


def run_bench(repo: Path | None, *, runs: int, as_json: bool, use_llm: bool) -> int:
    from grippo.runtime import build_runtime

    report: dict[str, Any] = {"runs": runs, "grippo_version": __version__}
    with tempfile.TemporaryDirectory(prefix="grippo-bench-") as tmp:
        target = _fixture(Path(tmp))
        started = time.monotonic()
        runtime = build_runtime(target, use_llm=use_llm)
        report["startup_sec"] = round(time.monotonic() - started, 3)
        if runtime.llm is not None:
            cfg = runtime.config.llm
            report["llm_config"] = {
                "model": cfg.model,
                "num_ctx": cfg.num_ctx,
                "num_gpu": cfg.num_gpu,
                "num_thread": cfg.num_thread,
                "keep_alive": cfg.keep_alive,
            }
        report["process_start"] = process_memory().__dict__
        try:
            timings = []
            for _ in range(100):
                t0 = time.monotonic()
                runtime.executor.state()
                timings.append(time.monotonic() - t0)
            report["git_snapshot_sec"] = _summary(timings)
            report["process_after_git"] = process_memory().__dict__

            if runtime.llm is not None:
                llm = runtime.llm
                try:
                    llm.unload()
                    time.sleep(1.0)
                except GrippoError as error:
                    report["llm_unload_error"] = str(error)
                interp = runtime.assistant.interpreter
                session = runtime.assistant.session
                walls, loads, tps, ptps, outs, errors = [], [], [], [], [], []
                fast_path = 0
                rss_series = []
                state = runtime.executor.state()
                for index in range(max(runs, 1) * len(PROMPTS)):
                    prompt = PROMPTS[index % len(PROMPTS)]
                    result = interp.interpret(prompt, state, session, ["main", "develop"], [])
                    metric = interp.last_metrics
                    if metric is None and result.fast:
                        fast_path += 1  # answered deterministically, no inference
                        continue
                    if metric is None:
                        errors.append(interp.last_error or f"sem métricas ({result.source})")
                        continue
                    if index == 0:
                        report["llm_cold_first_call_sec"] = round(metric.wall_sec, 3)
                        report["llm_model_load_sec"] = round(metric.load_sec, 3)
                    walls.append(metric.wall_sec)
                    loads.append(metric.load_sec)
                    tps.append(metric.tokens_per_sec)
                    ptps.append(metric.prompt_tokens_per_sec)
                    outs.append(metric.output_tokens)
                    rss_series.append(process_memory().rss_mib)
                report["llm_calls"] = len(walls)
                report["router_fast_path_answers"] = fast_path
                report["llm_errors"] = errors[:5]
                report["llm_wall_sec"] = _summary(walls[1:] or walls)
                report["llm_output_tokens_per_sec"] = _summary(tps)
                report["llm_prompt_tokens_per_sec"] = _summary(ptps)
                report["llm_output_tokens"] = _summary([float(o) for o in outs])
                report["grippo_rss_mib_during_llm"] = _summary(rss_series)
                if len(rss_series) >= 2:
                    report["grippo_rss_growth_mib"] = round(rss_series[-1] - rss_series[0], 2)
                try:
                    report["ollama_loaded"] = [
                        {
                            "name": m.get("name"),
                            "size_mib": round(m.get("size", 0) / 2**20),
                            "vram_mib": round(m.get("size_vram", 0) / 2**20),
                            "ram_mib": round((m.get("size", 0) - m.get("size_vram", 0)) / 2**20),
                            "context_length": m.get("context_length"),
                        }
                        for m in llm.loaded_models()
                    ]
                except GrippoError as error:
                    report["ollama_loaded_error"] = str(error)
                report["ollama_processes"] = [
                    {"pid": pid, "cmd": cmd, "rss_mib": round(rss, 1)}
                    for pid, cmd, rss in find_processes("ollama")
                    if "runner" in cmd or "serve" in cmd
                ]
            gpu = gpu_info()
            report["gpu"] = gpu
            report["process_end"] = process_memory().__dict__
            report["cpu_seconds"] = {k: round(v, 3) for k, v in cpu_seconds().items()}
        finally:
            runtime.close()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            print(f"{key:32} {value}")
    return 0
