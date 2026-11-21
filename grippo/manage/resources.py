"""Resource measurement (Linux /proc + optional nvidia-smi). No extra dependencies."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024


def _status_fields(pid: int | str) -> dict[str, str]:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _kib(value: str | None) -> float:
    if not value:
        return 0.0
    return float(value.split()[0]) / 1024.0  # → MiB


@dataclass(frozen=True)
class ProcessMemory:
    rss_mib: float
    peak_rss_mib: float
    threads: int
    open_fds: int


def process_memory(pid: int | str = "self") -> ProcessMemory:
    fields = _status_fields(pid)
    try:
        fds = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        fds = -1
    return ProcessMemory(
        _kib(fields.get("VmRSS")),
        _kib(fields.get("VmHWM")),
        int(fields.get("Threads", "0") or 0),
        fds,
    )


def system_memory() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                out[key] = _kib(value.strip())
    except OSError:
        pass
    return out


def find_processes(name_fragment: str) -> list[tuple[int, str, float]]:
    """(pid, cmd, rss MiB) of processes whose command line contains ``name_fragment``."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if name_fragment in cmd and "grep" not in cmd:
            found.append((int(entry.name), cmd.strip()[:120], process_memory(entry.name).rss_mib))
    return found


def cpu_seconds() -> dict[str, float]:
    times = os.times()
    return {
        "user": times.user,
        "system": times.system,
        "children_user": times.children_user,
        "children_system": times.children_system,
    }


def gpu_info() -> dict[str, object]:
    """NVIDIA only (nvidia-smi). Other GPUs are reported as unknown, not as absent."""
    binary = shutil.which("nvidia-smi")
    if not binary:
        return {"available": False, "reason": "nvidia-smi não encontrado"}
    try:
        gpu = subprocess.run(
            [
                binary,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        apps = subprocess.run(
            [
                binary,
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "reason": str(error)}
    if gpu.returncode != 0 or not gpu.stdout.strip():
        return {"available": False, "reason": gpu.stderr.strip() or "sem GPU NVIDIA"}
    name, total, used, util, driver = [p.strip() for p in gpu.stdout.splitlines()[0].split(",")]
    processes = []
    for line in apps.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[2].isdigit():
            process = Path(parts[1].split()[0]).name if parts[1] else "?"
            processes.append({"pid": int(parts[0]), "name": process, "used_mib": int(parts[2])})
    return {
        "available": True,
        "name": name,
        "total_mib": int(total),
        "used_mib": int(used),
        "utilization_pct": int(util) if util.isdigit() else None,
        "driver": driver,
        "processes": processes,
    }
