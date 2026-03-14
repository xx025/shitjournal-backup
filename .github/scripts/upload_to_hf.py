#!/usr/bin/env python3
"""
将 backup/pdfs 与 backup/preprints 上传至 Hugging Face 数据集，供前端与归档使用。
PDF 与正文图仅存于 HF，Git 仓库不再跟踪。
"""
from __future__ import annotations

from pathlib import Path

import typer

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKUP_DIR = _REPO_ROOT / "backup"
HF_REPO_ID = "jsonhash/shitjournal-backup"


def upload_to_hf(
    backup_dir: Path = BACKUP_DIR,
    repo_id: str = HF_REPO_ID,
    token: str | None = None,
) -> None:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        typer.echo("请安装: pip install huggingface_hub", err=True)
        raise SystemExit(1)

    if not token or not token.strip():
        typer.echo("API 上传需要 Access Token（公钥仅用于 git）。请：", err=True)
        typer.echo("  1. 打开 https://huggingface.co/settings/tokens 创建 Token（需 write 权限）", err=True)
        typer.echo("  2. 执行: HF_TOKEN=你的token python .github/scripts/upload_to_hf.py", err=True)
        raise SystemExit(1)

    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        typer.echo(f"目录不存在: {backup_dir}", err=True)
        raise SystemExit(1)

    api = HfApi(token=token)
    typer.echo("使用 upload_large_folder 上传 backup/（含 pdfs/、preprints/），支持断点续传与大体积。")
    api.upload_large_folder(
        folder_path=str(backup_dir),
        repo_id=repo_id,
        repo_type="dataset",
    )
    typer.echo("Hugging Face 上传完成。")


def main(
    backup_dir: Path = typer.Option(BACKUP_DIR, "--output", "-o", help="backup 目录"),
    repo_id: str = typer.Option(HF_REPO_ID, "--repo", "-r", help="HF 数据集 repo_id"),
    token: str = typer.Option("", "--token", envvar="HF_TOKEN", help="HF token，或环境变量 HF_TOKEN"),
) -> None:
    upload_to_hf(backup_dir=backup_dir, repo_id=repo_id, token=token or None)


if __name__ == "__main__":
    typer.run(main)
