"""Modal entrypoints (same shape as latent-memory/src/modal_app.py).

    modal run ml/src/modal_app.py --job gen_data --limit 50 --per-file 3      # seeds on the volume -> synth records
    modal run ml/src/modal_app.py --job prepare                                 # records -> train/val/test rendered examples
    modal run ml/src/modal_app.py --job sft --config configs/modal_sft_smoke.yaml
    modal run ml/src/modal_app.py --job serve --adapter /data/outputs/sft/final # vLLM OpenAI-compatible endpoint

Datasets, outputs and the HF cache live on the ``prose-code`` volume mounted at ``/data``.
Upload seeds with:  modal volume put prose-code ml/data/seed /datasets/prosesync/seed
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import modal

APP_NAME = "prose-code"
REPO_ROOT = Path(__file__).resolve().parents[2]
GPU = "A100-40GB"

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .pip_install_from_requirements(str(REPO_ROOT / "ml" / "requirements.txt"))
    .env({"HF_HOME": "/data/hf_cache", "PYTHONUNBUFFERED": "1", "PROSESYNC_CONFIG": "/workspace/project/configs/base.yaml"})
    .add_local_dir(
        str(REPO_ROOT), remote_path="/workspace/project",
        ignore=[".git", ".venv", "outputs", "wandb", "__pycache__", ".pytest_cache", "extension/node_modules", "extension/dist", ".ruff_cache"],
    )
)
volume = modal.Volume.from_name("prose-code", create_if_missing=True)
env_secret = modal.Secret.from_dotenv(REPO_ROOT)


def _run(argv: list[str]) -> None:
    print("+", " ".join(shlex.quote(a) for a in argv), flush=True)
    try:
        subprocess.run(argv, check=True, cwd="/workspace/project")
    finally:
        volume.commit()


@app.function(image=image, timeout=6 * 60 * 60, volumes={"/data": volume}, secrets=[env_secret])
def gen_data(limit: int = 0, per_file: int = 2, manifest: str = "/data/datasets/prosesync/seed/manifest.jsonl", out: str = "/data/datasets/prosesync/synth.jsonl"):
    _run(["python", "ml/src/data/perturb.py", "--manifest", manifest, "--out", out, "--per-file", str(per_file), "--limit", str(limit)])


@app.function(image=image, timeout=60 * 60, volumes={"/data": volume}, secrets=[env_secret])
def prepare(records: str = "/data/datasets/prosesync/synth.jsonl", out_dir: str = "/data/datasets/prosesync/v1", val_frac: float = 0.05, test_frac: float = 0.05):
    _run(["python", "ml/src/data/prepare.py", "--records", records, "--out-dir", out_dir, "--val-frac", str(val_frac), "--test-frac", str(test_frac)])


@app.function(image=image, gpu=GPU, timeout=6 * 60 * 60, volumes={"/data": volume}, secrets=[env_secret])
def sft(config_path: str = "configs/modal_sft.yaml", overrides: str = ""):
    argv = ["python", "ml/src/training/sft.py", "--config", config_path]
    if overrides:
        argv += ["--override", *shlex.split(overrides)]
    _run(argv)


@app.function(image=image.pip_install("vllm"), gpu=GPU, timeout=60 * 60, volumes={"/data": volume}, secrets=[env_secret])
def serve(adapter: str = "/data/outputs/sft/final", base: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct", merged: str = "/data/outputs/merged"):
    """Merge the adapter and start an OpenAI-compatible server (for A/B via sync.base_url)."""
    _run(["python", "ml/src/training/merge.py", "--base", base, "--adapter", adapter, "--out", merged])
    subprocess.run(["python", "-m", "vllm.entrypoints.openai.api_server", "--model", merged, "--served-model-name", "prosesync-ft", "--port", "8000"], check=True)


@app.local_entrypoint()
def main(job: str = "sft", config: str = "configs/modal_sft.yaml", overrides: str = "", limit: int = 0, per_file: int = 2, adapter: str = "/data/outputs/sft/final"):
    if job == "gen_data":
        gen_data.remote(limit=limit, per_file=per_file)
    elif job == "prepare":
        prepare.remote()
    elif job == "sft":
        sft.remote(config_path=config, overrides=overrides)
    elif job == "serve":
        serve.remote(adapter=adapter)
    else:
        raise SystemExit(f"unknown job {job}")
