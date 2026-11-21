"""Long-running stability checks (opt-in: pytest -m perf tests/perf)."""

import gc
import threading
import time

import pytest

from grippo.agent.assistant import Assistant
from grippo.agent.planner import Planner
from grippo.agent.session import Session
from grippo.engine.permissions import Decision
from grippo.git import runner
from grippo.llm.commit import CommitDrafter
from grippo.llm.interpreter import Interpreter
from grippo.manage.resources import process_memory
from tests.conftest import FakeOllama, init_repo, make_executor, run_op, write

pytestmark = pytest.mark.perf


def _children() -> int:
    import os

    me = str(os.getpid())
    count = 0
    for stat in __import__("pathlib").Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().split()
        except OSError:
            continue
        if fields[3] == me:
            count += 1
    return count


def test_many_queries_no_memory_or_process_growth(repo):
    executor = make_executor(repo)
    for _ in range(50):
        executor.state()
    gc.collect()
    baseline = process_memory()
    started = time.monotonic()
    for i in range(1000):
        executor.state()
        if i % 100 == 0:
            executor.run_read("log", {"n": 5})
    elapsed = time.monotonic() - started
    gc.collect()
    after = process_memory()
    print(f"\n1000 snapshots in {elapsed:.1f}s; RSS {baseline.rss_mib:.1f}→{after.rss_mib:.1f} "
          f"MiB; threads {after.threads}; fds {baseline.open_fds}→{after.open_fds}")
    assert after.rss_mib - baseline.rss_mib < 15
    assert after.open_fds <= baseline.open_fds + 2
    assert after.threads == baseline.threads
    assert not runner._live and _children() == 0


def test_many_mutations_and_audit_rotation(tmp_path):
    repo = init_repo(tmp_path / "r")
    write(repo, "seed.txt", "s")
    audit_dir = tmp_path / "audit"
    executor = make_executor(repo, audit_dir=audit_dir)
    executor.audit._handler.maxBytes = 50_000
    run_op(executor, "add", paths=["seed.txt"])
    run_op(executor, "commit", message="chore: seed")
    baseline = process_memory()
    for i in range(150):
        write(repo, f"f{i % 10}.txt", str(i))
        result = executor.execute(*_confirmed(executor, [("add", {"paths": [f"f{i % 10}.txt"]}),
                                                         ("commit", {"message": f"feat: {i}"})]))
        assert result.ok
    after = process_memory()
    print(f"\n150 commits; RSS {baseline.rss_mib:.1f}→{after.rss_mib:.1f} MiB")
    assert after.rss_mib - baseline.rss_mib < 20
    files = sorted(p.name for p in audit_dir.iterdir())
    assert "audit.jsonl" in files and "audit.jsonl.1" in files  # rotated, bounded
    assert all(p.stat().st_size <= 60_000 for p in audit_dir.iterdir())


def _confirmed(executor, steps):
    prepared = executor.prepare(executor.build_plan(steps))
    return prepared, executor.authorize(prepared, Decision.CONFIRM)


def test_session_memory_is_bounded(repo):
    fake = FakeOllama()
    executor = make_executor(repo)
    assistant = Assistant(executor, Interpreter(fake.client()),
                          Planner(executor, CommitDrafter(None)), Session(max_turns=6))
    for i in range(300):
        fake.replies.append({"action": "answer", "steps": [], "reply": "x" * 2000})
        assistant.handle(f"pergunta genérica número {i} " + "y" * 500)
    assert len(assistant.session.turns) == 6
    sizes = [len(c["messages"][1]["content"]) for c in fake.calls[-5:]]
    assert max(sizes) <= 12000
    assert len(assistant.interpreter.llm.history) <= 200


def test_parallel_repositories_under_load(tmp_path):
    executors = []
    for i in range(6):
        repo = init_repo(tmp_path / f"r{i}")
        write(repo, "a", "a")
        executors.append((repo, make_executor(repo)))
    errors = []

    def work(repo, executor):
        try:
            for j in range(20):
                write(repo, f"x{j}", str(j))
                prepared, auth = _confirmed(executor, [("add", {"paths": [f"x{j}"]}),
                                                       ("commit", {"message": f"feat: {j}"})])
                assert executor.execute(prepared, auth).ok
        except Exception as error:
            errors.append(repr(error))

    threads = [threading.Thread(target=work, args=pair) for pair in executors]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"\n6 repos x 20 commits in {time.monotonic() - started:.1f}s")
    assert not errors and threading.active_count() == 1
