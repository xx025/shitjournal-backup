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
REFRESH_DELAY_SECONDS = 5.0  # refresh-md / migrate-legacy 批量更新时的请求间隔

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
    zones = article.get("zones") or ""
    discipline = article.get("discipline") or ""
    tag = article.get("tag") or ""
    rating_count = article.get("rating_count")
    avg_score = article.get("avg_score")
    co_authors = article.get("co_authors") or []
    author = article.get("author") or {}
    display_name = author.get("display_name") or ""
    institution = author.get("institution") or ""
    social_media = author.get("social_media") or ""
    web_url = f"https://shitjournal.org/preprints/{aid}"

    md_lines = [
        f"# {title}",
        "",
        "## 元信息",
        "",
        f"- **作者**: {display_name}",
        f"- **机构**: {institution}",
    ]
    if social_media:
        md_lines.append(f"- **社交媒体**: {social_media}")
    md_lines.extend([
        f"- **分区**: {zones}",
        f"- **学科**: {discipline}",
        f"- **标签**: {tag}",
        f"- **提交时间**: {created}",
    ])
    if rating_count is not None and avg_score is not None:
        md_lines.append(f"- **评分**: {avg_score:.2f} / 5（{rating_count} 人）")
    if co_authors:
        names = [c.get("display_name") or c.get("name") or str(c) for c in co_authors]
        md_lines.append(f"- **共同作者**: {', '.join(names)}")
    md_lines.extend([
        "",
        "## 链接",
        "",
        f"- [网站原始文章]({web_url})",
    ])
    if pdf_url:
        md_lines.append(f"- [PDF]({pdf_url})")
    md_lines.append(f"- [文章元信息]({aid}.meta.json)")
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


def pdf_to_images(pdf_path: Path, article_id: str, preprints_subpath: Path, max_pages: int = 50) -> list[str]:
    """用 pdftoppm 将 PDF 转为 PNG，写入 preprints 目录，返回图片文件名列表。"""
    if not pdf_path.exists() or not article_id:
        return []
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-r", "150", str(pdf_path.resolve()), article_id],
            cwd=preprints_subpath,
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        typer.echo(f"PDF 转图片失败 {article_id}: {e}", err=True)
        return []
    out = sorted(preprints_subpath.glob(f"{article_id}-*.png"), key=lambda p: p.name)
    if len(out) > max_pages:
        for f in out[max_pages:]:
            try:
                f.unlink()
            except OSError:
                pass
        out = out[:max_pages]
    return [f.name for f in out]


def append_body_images_to_md(md_path: Path, image_filenames: list[str]) -> None:
    """在 .md 末尾追加 ## 正文 与图片引用；若已有 ## 正文 则不再追加（幂等）。"""
    if not image_filenames or not md_path.exists():
        return
    content = md_path.read_text(encoding="utf-8")
    if "## 正文" in content:
        return
    lines = ["", "## 正文", ""]
    for i, name in enumerate(image_filenames, 1):
        lines.append(f"![第{i}页]({name})")
        lines.append("")
    md_path.write_text(content + "\n".join(lines), encoding="utf-8")


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
            if download_pdf(pdf_url, aid, OUTPUT_DIR):
                prefix = aid[:2].lower()
                pdf_path = OUTPUT_DIR / "pdfs" / prefix / f"{aid}.pdf"
                subpath = OUTPUT_DIR / "preprints" / prefix
                images = pdf_to_images(pdf_path, aid, subpath)
                if images:
                    append_body_images_to_md(subpath / f"{aid}.md", images)
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


def is_legacy_md(content: str) -> bool:
    """判断 .md 是否仍为旧版（含正文截图引用）。"""
    if not content:
        return False
    return "![正文" in content or ".png)" in content


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


def collect_ids_with_legacy_md(out_dir: Path) -> list[str]:
    """扫描 backup/preprints，返回 .md 仍含图片引用的预印本 id 列表。"""
    preprints_dir = out_dir / "preprints"
    if not preprints_dir.is_dir():
        return []
    ids: list[str] = []
    for prefix_dir in preprints_dir.iterdir():
        if not prefix_dir.is_dir():
            continue
        for md_file in prefix_dir.glob("*.md"):
            aid = md_file.stem
            if len(aid) < 2:
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                if is_legacy_md(text):
                    ids.append(aid)
            except OSError:
                pass
    return sorted(set(ids))


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
    delay: float = REFRESH_DELAY_SECONDS,
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
            if download_pdf(pdf_url, aid, OUTPUT_DIR):
                prefix = aid[:2].lower()
                pdf_path = OUTPUT_DIR / "pdfs" / prefix / f"{aid}.pdf"
                subpath = OUTPUT_DIR / "preprints" / prefix
                images = pdf_to_images(pdf_path, aid, subpath)
                if images:
                    append_body_images_to_md(subpath / f"{aid}.md", images)
        if delete_images:
            prefix = aid[:2].lower()
            subpath = OUTPUT_DIR / "preprints" / prefix
            if subpath.is_dir():
                n = remove_legacy_images(subpath, aid)
                if n:
                    typer.echo(f"  已删除 {aid} 下 {n} 张旧截图。")

    typer.echo("迁移完成。索引未改动，仅更新了对应条目的 .meta.json、.md 与 PDF，并移除旧截图。")


def run_refresh_md(
    output_dir: Path | None = None,
    limit: int = 0,
    delay: float = REFRESH_DELAY_SECONDS,
    delete_images: bool = True,
) -> None:
    """将 .md 仍含图片引用的预印本用 API 重写为丰富元信息版 .md，并更新 .meta.json、下载 PDF、删除旧截图。"""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(output_dir or OUTPUT_DIR)
    if not OUTPUT_DIR.is_dir():
        typer.echo(f"目录不存在: {OUTPUT_DIR}", err=True)
        raise SystemExit(1)

    ids = collect_ids_with_legacy_md(OUTPUT_DIR)
    to_process = ids if limit <= 0 else ids[:limit]
    typer.echo(f"仍含图片引用的 .md 共 {len(ids)} 篇，本次处理 {len(to_process)} 篇。")

    for aid in tqdm(to_process, desc="刷新 .md"):
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
            if download_pdf(pdf_url, aid, OUTPUT_DIR):
                prefix = aid[:2].lower()
                pdf_path = OUTPUT_DIR / "pdfs" / prefix / f"{aid}.pdf"
                subpath = OUTPUT_DIR / "preprints" / prefix
                images = pdf_to_images(pdf_path, aid, subpath)
                if images:
                    append_body_images_to_md(subpath / f"{aid}.md", images)
        if delete_images:
            prefix = aid[:2].lower()
            subpath = OUTPUT_DIR / "preprints" / prefix
            if subpath.is_dir():
                n = remove_legacy_images(subpath, aid)
                if n:
                    typer.echo(f"  已删除 {aid} 下 {n} 张旧截图。")

    typer.echo("刷新完成。已更新 .meta.json 与 .md（含丰富元信息），并处理 PDF、旧截图。")


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
    delay: float = typer.Option(REFRESH_DELAY_SECONDS, "--delay", help="请求间隔秒数，默认 5"),
    no_delete_images: bool = typer.Option(False, "--no-delete-images", help="不删除旧版正文截图"),
) -> None:
    """将旧版（靠图片/body_text 存储）的预印本更新为 API 元数据 + 丰富 .md + PDF，并删除旧截图。"""
    run_migrate_legacy(
        output_dir=output_dir,
        limit=limit,
        delay=delay,
        delete_images=not no_delete_images,
    )


@app.command("refresh-md")
def refresh_md_cmd(
    output_dir: Path = typer.Option(OUTPUT_DIR, "--output", "-o", help="归档输出目录"),
    limit: int = typer.Option(0, "--limit", help="最多处理篇数，0 表示全部"),
    delay: float = typer.Option(REFRESH_DELAY_SECONDS, "--delay", help="请求间隔秒数，默认 5"),
    no_delete_images: bool = typer.Option(False, "--no-delete-images", help="不删除旧版正文截图"),
) -> None:
    """将 .md 仍含图片引用的预印本用 API 重写为丰富元信息版 .md，并更新 .meta.json、下载 PDF。"""
    run_refresh_md(
        output_dir=output_dir,
        limit=limit,
        delay=delay,
        delete_images=not no_delete_images,
    )


if __name__ == "__main__":
    app()
