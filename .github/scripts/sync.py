#!/usr/bin/env python3
"""
S.H.I.T Journal 同步脚本 — 通过官网 API 同步预印本元数据并归档。
下载 PDF 至 backup/pdfs/{prefix}/{id}.pdf（由 Git LFS 托管，控制仓库体积）；新闻暂无 API，保留既有索引。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import typer
from tqdm import tqdm

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_BASE = "https://api.shitjournal.org"
OUTPUT_DIR = _REPO_ROOT / "backup"
DELAY_SECONDS = 0.5

PREPRINT_ZONES = ("latrine", "septic", "stone", "sediment")


def _http_get(url: str) -> dict:
    """GET JSON，失败抛异常。"""
    req = Request(url, headers={"User-Agent": "ShitJournalBackup/2.0 (+https://github.com; archive bot)"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_articles_list(zone: str, page: int) -> dict:
    """拉取某 zone 某页文章列表。返回 { status, data, count, page, total_pages }。"""
    url = f"{API_BASE}/api/articles/?zone={zone}&sort=newest&discipline=all&page={page}"
    return _http_get(url)


def fetch_article_detail(article_id: str) -> dict:
    """拉取单篇文章详情。返回 { status, article, comments }。"""
    url = f"{API_BASE}/api/articles/{article_id}"
    return _http_get(url)


def collect_all_article_ids() -> list[str]:
    """遍历四区与分页，收集全部文章 id。"""
    seen: set[str] = set()
    for zone in PREPRINT_ZONES:
        page = 1
        while True:
            try:
                resp = fetch_articles_list(zone, page)
            except (HTTPError, URLError, json.JSONDecodeError) as e:
                typer.echo(f"列表请求失败 zone={zone} page={page}: {e}", err=True)
                break
            if resp.get("status") != "success":
                break
            data = resp.get("data") or []
            total_pages = resp.get("total_pages") or 1
            for item in data:
                aid = item.get("id")
                if aid:
                    seen.add(aid)
            if page >= total_pages or not data:
                break
            page += 1
            time.sleep(DELAY_SECONDS)
    return list(seen)


def save_preprint_article(
    article: dict,
    out_dir: Path,
    *,
    comments: list | None = None,
    fetched_at: str | None = None,
) -> None:
    """将单篇预印本写入 backup/preprints/{prefix}/{id}.meta.json（完整 API 元信息）与 .md（简略）。"""
    aid = article.get("id")
    if not aid or len(aid) < 2:
        return
    prefix = aid[:2].lower()
    subpath = out_dir / "preprints" / prefix
    subpath.mkdir(parents=True, exist_ok=True)

    full_meta = {
        "article": article,
        "comments": comments if comments is not None else [],
        "fetched_at": fetched_at or "",
    }
    meta_path = subpath / f"{aid}.meta.json"
    meta_path.write_text(json.dumps(full_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    title = article.get("title") or "Untitled"
    pdf_url = article.get("pdf_url") or ""
    created = article.get("created_at") or ""
    author = article.get("author") or {}
    display_name = author.get("display_name") or ""
    institution = author.get("institution") or ""
    web_url = f"https://shitjournal.org/preprints/{aid}"

    md_lines = [
        f"# {title}",
        "",
        f"- **作者**: {display_name}",
        f"- **机构**: {institution}",
        f"- **提交时间**: {created}",
        f"- **分区**: {article.get('zones', '')}",
        "",
        f"- [在线阅读]({web_url})",
    ]
    if pdf_url:
        md_lines.append(f"- [PDF 下载（无水印）]({pdf_url})")
        md_lines.append(f"- 本仓库归档: `backup/pdfs/{prefix}/{aid}.pdf`")
    md_lines.append("")

    md_path = subpath / f"{aid}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")


def download_pdf(pdf_url: str, article_id: str, out_dir: Path) -> bool:
    """将 PDF 下载到 backup/pdfs/{prefix}/{id}.pdf。已存在则跳过。返回是否成功。"""
    if not pdf_url or not article_id or len(article_id) < 2:
        return False
    prefix = article_id[:2].lower()
    pdf_dir = out_dir / "pdfs" / prefix
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{article_id}.pdf"
    if pdf_path.exists():
        return True
    try:
        req = Request(pdf_url, headers={"User-Agent": "ShitJournalBackup/2.0 (+https://github.com; archive bot)"})
        with urlopen(req, timeout=120) as r:
            pdf_path.write_bytes(r.read())
        return True
    except (HTTPError, URLError, OSError) as e:
        typer.echo(f"PDF 下载失败 {article_id}: {e}", err=True)
        return False


def load_existing_index(out_dir: Path) -> tuple[set[str], dict]:
    """加载已有 index.json，返回 (已收录预印本 id 集合, 原 index 字典)。"""
    index_path = out_dir / "index.json"
    existing_ids: set[str] = set()
    previous: dict = {"news": [], "preprints": []}
    if index_path.exists():
        try:
            previous = json.loads(index_path.read_text(encoding="utf-8"))
            for item in previous.get("preprints", []):
                slug = item.get("slug")
                if slug:
                    existing_ids.add(slug)
                else:
                    url = item.get("url") or ""
                    if "/preprints/" in url:
                        existing_ids.add(url.rstrip("/").split("/")[-1])
        except (json.JSONDecodeError, OSError):
            pass
    return existing_ids, previous


def push_backup(repo_root: Path, index: dict) -> bool:
    """提交并 push backup/。"""
    index_path = OUTPUT_DIR / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for args in [
            ["git", "config", "user.name", "github-actions[bot]"],
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            ["git", "add", "backup/"],
        ]:
            subprocess.run(args, cwd=repo_root, check=True, capture_output=True)
        r = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=repo_root, capture_output=True)
        if r.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore(archive): sync shitjournal [automated]"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=repo_root, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_sync(
    output_dir: Path | None = None,
    preprints_limit: int = 0,
    push_every: int = 0,
) -> None:
    """通过 API 同步预印本：仅处理未收录 id，写入元数据与简略 .md，并下载 PDF 至 backup/pdfs/（Git LFS）。"""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(output_dir or OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    repo_root = OUTPUT_DIR.parent

    existing_ids, previous_index = load_existing_index(OUTPUT_DIR)
    typer.echo(f"已收录预印本: {len(existing_ids)} 篇。")

    all_ids = collect_all_article_ids()
    to_fetch = [i for i in all_ids if i not in existing_ids]
    if preprints_limit > 0:
        to_fetch = to_fetch[:preprints_limit]
    typer.echo(f"预印本: 共 {len(all_ids)} 篇，本次待同步 {len(to_fetch)} 篇。")

    new_preprints: list[dict] = []
    for i, aid in enumerate(tqdm(to_fetch, desc="预印本")):
        time.sleep(DELAY_SECONDS)
        try:
            resp = fetch_article_detail(aid)
        except (HTTPError, URLError, json.JSONDecodeError) as e:
            typer.echo(f"详情请求失败 {aid}: {e}", err=True)
            continue
        if resp.get("status") != "success":
            continue
        article = resp.get("article")
        if not article:
            continue
        save_preprint_article(
            article,
            OUTPUT_DIR,
            comments=resp.get("comments", []),
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        pdf_url = article.get("pdf_url")
        if pdf_url:
            time.sleep(DELAY_SECONDS)
            download_pdf(pdf_url, aid, OUTPUT_DIR)
        new_preprints.append({
            "url": f"https://shitjournal.org/preprints/{aid}",
            "slug": aid,
            "title": article.get("title") or aid,
            "pdf_url": article.get("pdf_url"),
            "created_at": article.get("created_at"),
            "author": article.get("author"),
        })
        if push_every > 0 and (i + 1) % push_every == 0:
            merged = {
                "news": previous_index.get("news", []),
                "preprints": previous_index.get("preprints", []) + new_preprints,
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if push_backup(repo_root, merged):
                typer.echo("已增量推送至远程。")

    index = {
        "news": previous_index.get("news", []),
        "preprints": previous_index.get("preprints", []) + new_preprints,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    index_path = OUTPUT_DIR / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"索引已写入: {index_path}（新闻 {len(index['news'])} 篇，预印本 {len(index['preprints'])} 篇）")


def is_legacy_meta(meta: dict) -> bool:
    """判断 .meta.json 是否为旧版（靠图片/body_text 存储）。"""
    if not meta or not isinstance(meta, dict):
        return False
    if "body_text" in meta:
        return True
    if "article" in meta and isinstance(meta.get("article"), dict):
        return False
    return True


def collect_legacy_preprint_ids(out_dir: Path) -> list[str]:
    """扫描 backup/preprints，返回仍为旧版（图片/body_text）的预印本 id 列表。"""
    preprints_dir = out_dir / "preprints"
    if not preprints_dir.is_dir():
        return []
    legacy: list[str] = []
    for prefix_dir in preprints_dir.iterdir():
        if not prefix_dir.is_dir():
            continue
        for meta_file in prefix_dir.glob("*.meta.json"):
            aid = meta_file.name.removesuffix(".meta.json")
            if len(aid) < 2:
                continue
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                if is_legacy_meta(data):
                    legacy.append(aid)
            except (json.JSONDecodeError, OSError):
                legacy.append(aid)
    return sorted(legacy)


def remove_legacy_images(subpath: Path, aid: str) -> int:
    """删除该预印本目录下旧版正文截图（{id}-*.png 等），返回删除数量。"""
    removed = 0
    for f in list(subpath.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            if f.stem == aid or (f.stem.startswith(aid + "-") and f.stem[len(aid) :].lstrip("-").isdigit()):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def run_migrate_legacy(
    output_dir: Path | None = None,
    limit: int = 0,
    delay: float = DELAY_SECONDS,
    delete_images: bool = True,
) -> None:
    """将旧版（靠图片/body_text 存储）的预印本更新为 API 元数据 + PDF，并删除旧截图。"""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(output_dir or OUTPUT_DIR)
    if not OUTPUT_DIR.is_dir():
        typer.echo(f"目录不存在: {OUTPUT_DIR}", err=True)
        raise SystemExit(1)

    legacy_ids = collect_legacy_preprint_ids(OUTPUT_DIR)
    to_process = legacy_ids if limit <= 0 else legacy_ids[:limit]
    typer.echo(f"旧版预印本共 {len(legacy_ids)} 篇，本次处理 {len(to_process)} 篇。")

    for aid in tqdm(to_process, desc="迁移"):
        time.sleep(delay)
        try:
            resp = fetch_article_detail(aid)
        except (HTTPError, URLError, json.JSONDecodeError) as e:
            typer.echo(f"详情请求失败 {aid}: {e}", err=True)
            continue
        if resp.get("status") != "success":
            continue
        article = resp.get("article")
        if not article:
            continue
        save_preprint_article(
            article,
            OUTPUT_DIR,
            comments=resp.get("comments", []),
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        pdf_url = article.get("pdf_url")
        if pdf_url:
            time.sleep(delay)
            download_pdf(pdf_url, aid, OUTPUT_DIR)
        if delete_images:
            prefix = aid[:2].lower()
            subpath = OUTPUT_DIR / "preprints" / prefix
            if subpath.is_dir():
                n = remove_legacy_images(subpath, aid)
                if n:
                    typer.echo(f"  已删除 {aid} 下 {n} 张旧截图。")

    typer.echo("迁移完成。索引未改动，仅更新了对应条目的 .meta.json、.md 与 PDF，并移除旧截图。")


app = typer.Typer(help="S.H.I.T Journal 归档同步（API）")


@app.command("run")
def run_cmd(
    output_dir: Path = typer.Option(OUTPUT_DIR, "--output", "-o", help="归档输出目录"),
    preprints_limit: int = typer.Option(0, "--preprints-limit", help="预印本最多同步篇数，0 表示不限制"),
    push_every: int = typer.Option(0, "--push-every", help="每 N 篇提交并 push 一次，0 表示仅最后 push"),
) -> None:
    """通过官网 API 同步预印本元数据并下载 PDF（存于 backup/pdfs/，Git LFS）；不同步新闻。"""
    run_sync(output_dir=output_dir, preprints_limit=preprints_limit, push_every=push_every)


@app.command("migrate-legacy")
def migrate_legacy_cmd(
    output_dir: Path = typer.Option(OUTPUT_DIR, "--output", "-o", help="归档输出目录"),
    limit: int = typer.Option(0, "--limit", help="最多处理篇数，0 表示全部"),
    delay: float = typer.Option(DELAY_SECONDS, "--delay", help="请求间隔秒数"),
    no_delete_images: bool = typer.Option(False, "--no-delete-images", help="不删除旧版正文截图"),
) -> None:
    """将旧版（靠图片/body_text 存储）的预印本更新为 API 元数据 + 简略 .md + PDF，并删除旧截图。"""
    run_migrate_legacy(
        output_dir=output_dir,
        limit=limit,
        delay=delay,
        delete_images=not no_delete_images,
    )


if __name__ == "__main__":
    app()
