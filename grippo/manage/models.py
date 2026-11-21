"""`grippo model …`: list / pull / import GGUF / unload (Ollama)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from grippo.config import load_config
from grippo.errors import GrippoError
from grippo.llm.client import OllamaClient
from grippo.paths import get_paths


def import_gguf(path: Path, name: str) -> None:
    """Register a local GGUF file as an Ollama model (needs the `ollama` CLI)."""
    path = path.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise GrippoError(f"arquivo GGUF inválido: {path}")
    binary = shutil.which("ollama")
    if not binary:
        raise GrippoError("comando `ollama` não encontrado no PATH")
    with tempfile.TemporaryDirectory(prefix="grippo-model-") as tmp:
        modelfile = Path(tmp) / "Modelfile"
        modelfile.write_text(f"FROM {path}\n", encoding="utf-8")
        result = subprocess.run(
            [binary, "create", name, "-f", str(modelfile)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    if result.returncode != 0:
        raise GrippoError(f"ollama create falhou: {result.stderr.strip()}")


def run_model(args: argparse.Namespace) -> int:
    config = load_config(get_paths().config_file)
    client = OllamaClient(config.llm)
    action = args.action or "list"
    try:
        if action == "list":
            loaded = {m.get("name"): m for m in client.loaded_models()}
            for model in client.list_models():
                name = model.get("name")
                details = model.get("details") or {}
                marker = "*" if name == config.llm.model else " "
                line = (
                    f"{marker} {name:30} {details.get('parameter_size', '?'):>6} "
                    f"{details.get('quantization_level', '?'):8} "
                    f"{model.get('size', 0) / 2**30:5.2f} GiB"
                )
                if name in loaded:
                    vram = loaded[name].get("size_vram", 0) / 2**30
                    total = loaded[name].get("size", 0) / 2**30
                    line += f"  carregado: VRAM {vram:.2f} GiB / RAM {total - vram:.2f} GiB"
                print(line)
            print("(* = modelo configurado em llm.model)")
        elif action == "pull":
            name = args.name or config.llm.model
            last = ""

            def progress(status: str) -> None:
                nonlocal last
                if status != last:
                    print(f"  {status}")
                    last = status

            client.pull(name, progress)
            print(f"modelo {name} disponível.")
        elif action == "import":
            import_gguf(args.path, args.name)
            print(f"modelo {args.name} registrado. Use: grippo config set llm.model {args.name}")
        elif action == "unload":
            client.unload()
            print(f"{config.llm.model} descarregado da memória.")
        return 0
    finally:
        client.close()
