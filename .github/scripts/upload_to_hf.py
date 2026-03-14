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
ALWAYS_UPLOAD = {"index.json", "removed-from-website.json"}


def _list_local_files(backup_dir: Path) -> list[str]:
    files: list[str] = []
    for path in backup_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(backup_dir).as_posix()
        if rel.startswith(".cache/"):
            continue
        files.append(rel)
    return sorted(files)


def upload_to_hf(
    backup_dir: Path = BACKUP_DIR,
    repo_id: str = HF_REPO_ID,
    token: str | None = None,
    num_workers: int = 2,
    full_sync: bool = False,
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
    local_files = _list_local_files(backup_dir)
    remote_files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    if full_sync:
        allow_patterns = local_files
    else:
        allow_patterns = [path for path in local_files if path in ALWAYS_UPLOAD or path not in remote_files]

    if not allow_patterns:
        typer.echo("HF 已包含当前 backup/ 中的文件，本次无需上传。")
        return

    typer.echo(
        f"使用 upload_large_folder 上传 {len(allow_patterns)}/{len(local_files)} 个文件"
        "（跳过 HF 上已存在的路径，忽略本地缓存并降低并发以减少限流）。"
    )
    api.upload_large_folder(
        folder_path=str(backup_dir),
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=allow_patterns,
        ignore_patterns=[".cache", ".cache/**"],
        num_workers=max(1, num_workers),
    )
    typer.echo("Hugging Face 上传完成。")


def main(
    backup_dir: Path = typer.Option(BACKUP_DIR, "--output", "-o", help="backup 目录"),
    repo_id: str = typer.Option(HF_REPO_ID, "--repo", "-r", help="HF 数据集 repo_id"),
    token: str = typer.Option("", "--token", envvar="HF_TOKEN", help="HF token，或环境变量 HF_TOKEN"),
    num_workers: int = typer.Option(2, "--num-workers", help="上传 worker 数，默认 2；遇到 HF 429 可进一步调小到 1"),
    full_sync: bool = typer.Option(False, "--full-sync", help="即使 HF 上已存在同路径文件，也重新上传全部本地文件"),
) -> None:
    upload_to_hf(
        backup_dir=backup_dir,
        repo_id=repo_id,
        token=token or None,
        num_workers=num_workers,
        full_sync=full_sync,
    )


if __name__ == "__main__":
    typer.run(main)
