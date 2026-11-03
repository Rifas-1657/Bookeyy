"""
tools/finetune/validate_notebooks.py — check the generated notebooks before spending Kaggle hours.

1. Every code cell compiles (shell `!` lines and `%%` magics are skipped).
2. Dry-runs notebook 1's data loop locally on HF-cached shards: no pip, no secrets, no push.

  tools/voice-eval/.venv-f5/Scripts/python tools/finetune/validate_notebooks.py
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def code_cells(nb: Path) -> list[str]:
    cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
    return ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]


def python_only(src: str) -> str:
    lines = src.splitlines()
    if lines and lines[0].startswith("%%writefile"):
        return "\n".join(lines[1:])
    return "\n".join("pass" if ln.lstrip().startswith(("!", "%")) else ln for ln in lines)


def compile_all() -> None:
    for nb in sorted(HERE.glob("0*.ipynb")):
        for i, src in enumerate(code_cells(nb)):
            compile(python_only(src), f"{nb.name}[cell {i}]", "exec")
        print(f"compiles: {nb.name}")


def dry_run_dataset_loop() -> None:
    cached = {
        ("ai4bharat/IndicVoices", "tamil/"): glob.glob(os.path.expanduser(
            "~/.cache/huggingface/hub/datasets--ai4bharat--IndicVoices/snapshots/*/tamil/*.parquet")),
        ("ai4bharat/indicvoices_r", "Tamil/"): glob.glob(os.path.expanduser(
            "~/.cache/huggingface/hub/datasets--ai4bharat--indicvoices_r/snapshots/*/Tamil/*.parquet")),
    }
    if not all(cached.values()):
        print("skip dry run: need one cached shard of each dataset")
        return
    tmp = Path(tempfile.mkdtemp(prefix="bookey_nb1_"))
    os.chdir(tmp)
    sys.path.insert(0, str(HERE))
    cells = code_cells(HERE / "01_build_tanglish_dataset.ipynb")
    settings, loop, lexicon = cells[3], cells[4], cells[5]   # after pip, secrets, writefile

    import huggingface_hub

    class FakeApi:
        def list_repo_files(self, repo, repo_type=None):
            for (r, prefix), files in cached.items():
                if r == repo:
                    return [prefix + Path(f).name for f in files]
            return []

    def fake_download(repo, shard, repo_type=None, local_dir="."):
        src = next(f for (r, _), fs in cached.items() if r == repo for f in fs if f.endswith(Path(shard).name))
        dst = Path(local_dir) / Path(shard).name
        shutil.copy(src, dst)
        return str(dst)

    huggingface_hub.HfApi = FakeApi
    huggingface_hub.hf_hub_download = fake_download
    env = {"HF_USER": "dry-run", "os": os}
    exec(python_only(settings).replace("TARGET_HOURS = 30", "TARGET_HOURS = 100"), env)
    exec(python_only(loop), env)
    exec(python_only(lexicon), env)
    parts = sorted(glob.glob(f"{env['WORK']}/part-*.parquet"))
    import pyarrow.parquet as pq
    rows = sum(pq.ParquetFile(p).metadata.num_rows for p in parts)
    print(f"dry run OK: {len(parts)} parquet part(s), {rows} rows, "
          f"{env['stats'].kept_seconds / 3600:.2f} h, lexicon {len(env['best'])} words → {tmp}")


if __name__ == "__main__":
    compile_all()
    dry_run_dataset_loop()
