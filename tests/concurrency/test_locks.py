import itertools
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from grippo.errors import LockTimeoutError
from grippo.git.lock import RepoLocks
from grippo.git.repo import discover
from tests.conftest import commit_file, git, init_repo, make_executor, run_op, write

pytestmark = pytest.mark.git


def _intervals(locks, repo, mode, n, hold=0.1):
    spans = []

    def worker():
        with getattr(locks, mode)(repo):
            start = time.monotonic()
            time.sleep(hold)
            spans.append((start, time.monotonic()))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(spans)


def test_exclusive_serializes_threads(repo, tmp_path):
    locks = RepoLocks(tmp_path / "locks", timeout=10)
    spans = _intervals(locks, discover(repo), "exclusive", 4)
    for (_, end), (start, _) in itertools.pairwise(spans):
        assert start >= end - 0.005  # no overlap


def test_shared_reads_overlap(repo, tmp_path):
    locks = RepoLocks(tmp_path / "locks", timeout=10)
    spans = _intervals(locks, discover(repo), "shared", 4, hold=0.2)
    assert spans[-1][0] < spans[0][1]  # all started before the first finished


def test_different_repositories_do_not_block(tmp_path):
    a = discover(init_repo(tmp_path / "a"))
    b = discover(init_repo(tmp_path / "b"))
    locks = RepoLocks(tmp_path / "locks", timeout=0.3)
    with locks.exclusive(a), locks.exclusive(b):
        pass


def test_lock_timeout_and_reentrancy(repo, tmp_path):
    locks = RepoLocks(tmp_path / "locks", timeout=0.2)
    repository = discover(repo)
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with locks.exclusive(repository):
            holder_ready.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    holder_ready.wait(5)
    with pytest.raises(LockTimeoutError), locks.shared(repository):
        pass
    release.set()
    t.join()
    with locks.exclusive(repository), locks.exclusive(repository), locks.shared(repository):
        pass  # re-entrant in one thread
    with pytest.raises(RuntimeError, match="upgrade"), locks.shared(repository), \
            locks.exclusive(repository):
        pass


def test_lock_works_across_processes(repo, tmp_path):
    locks_dir = tmp_path / "locks"
    script = textwrap.dedent(f"""
        import time, sys
        from pathlib import Path
        from grippo.git.lock import RepoLocks
        from grippo.git.repo import discover
        locks = RepoLocks(Path({str(locks_dir)!r}))
        with locks.exclusive(discover(Path({str(repo)!r}))):
            print("locked", flush=True)
            time.sleep(1.5)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    with pytest.raises(LockTimeoutError), RepoLocks(locks_dir, timeout=0.3).exclusive(
            discover(repo)):
        pass
    proc.wait(10)
    proc.stdout.close()
    with RepoLocks(locks_dir, timeout=1).exclusive(discover(repo)):
        pass


def test_concurrent_mutations_same_repo_are_consistent(repo):
    executor = make_executor(repo)
    for i in range(8):
        write(repo, f"f{i}.txt", str(i))
    errors = []

    def commit_one(i):
        try:
            plan = executor.build_plan([("add", {"paths": [f"f{i}.txt"]}),
                                        ("commit", {"message": f"feat: f{i}"})])
            from grippo.engine.permissions import Decision
            from grippo.errors import StateChangedError

            for _ in range(20):
                prepared = executor.prepare(plan)
                auth = executor.authorize(prepared, Decision.CONFIRM)
                try:
                    result = executor.execute(prepared, auth)
                    assert result.ok, result.results
                    return
                except StateChangedError:
                    continue  # another thread changed the repo; re-confirm
            errors.append(f"f{i}: gave up")
        except Exception as error:
            errors.append(repr(error))

    threads = [threading.Thread(target=commit_one, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    log = git(repo, "log", "--format=%s").stdout.splitlines()
    assert sorted(line for line in log if line.startswith("feat: f")) == [
        f"feat: f{i}" for i in range(8)]
    assert git(repo, "fsck", "--no-progress").returncode == 0


def test_parallel_reads_in_different_repos(tmp_path):
    repos = []
    for i in range(4):
        r = init_repo(tmp_path / f"r{i}")
        commit_file(r, "a.txt", "a", "chore: init")
        repos.append(make_executor(r))
    results = []

    def loop(ex):
        for _ in range(20):
            results.append(ex.state().head is not None)

    threads = [threading.Thread(target=loop, args=(ex,)) for ex in repos]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 80 and all(results)
    assert run_op(repos[0], "branch_create", name="x", switch=False).ok
