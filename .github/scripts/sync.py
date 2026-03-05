#!/usr/bin/env python3
"""
S.H.I.T Journal 同步脚本 — 从 shitjournal.org 同步新闻与预印本并归档为 Markdown。
站点为 SPA，使用 Playwright 渲染后解析。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

import typer
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from tqdm import tqdm

# 脚本位于 .github/scripts/，backup 在仓库根目录下
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_URL = "https://shitjournal.org"
OUTPUT_DIR = _REPO_ROOT / "backup"
DELAY_SECONDS = 1.0  # 礼貌请求间隔


# 预印本四个分区（用于收集所有文章链接）
PREPRINT_ZONES = ("latrine", "septic", "stone", "sediment")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def slugify(s: str) -> str:
    """生成安全的文件名 slug。若已是 UUID 则原样返回。"""
    s = (s or "").strip()
    if UUID_PATTERN.match(s):
        return s
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s[:80] or "untitled"


def _collect_all_preprint_urls(
    page, delay: float, max_pages_per_zone: int = 100, stop_after_total: int = 0
) -> list[str]:
    """从四个 zone 的列表页（含分页）收集预印本详情页 URL。stop_after_total>0 时收集满即停，减少 CI 时间。"""
    seen: set[str] = set()
    js_collect = r"""
    (els) => {
        const urls = [];
        const re = /^\/preprints\/[0-9a-f-]{36}\/?$/i;
        for (const e of els) {
            const h = (e.getAttribute('href') || '').split('?')[0];
            if (re.test(h)) urls.push(new URL(e.href).href);
        }
        return urls;
    }
    """
    for zone in PREPRINT_ZONES:
        if stop_after_total > 0 and len(seen) >= stop_after_total:
            break
        page_num = 1
        while page_num <= max_pages_per_zone:
            page.goto(
                f"{BASE_URL}/preprints?zone={zone}&page={page_num}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            time.sleep(delay)
            links = page.eval_on_selector_all('a[href*="/preprints/"]', js_collect)
            links = list(links) if links else []
            new_in_page = sum(1 for u in links if u not in seen)
            for u in links:
                seen.add(u)
            if stop_after_total > 0 and len(seen) >= stop_after_total:
                break
            if new_in_page == 0 or not links:
                break
            page_num += 1
    return list(seen)


def extract_news_links_from_page(page) -> list[str]:
    """从当前新闻列表页获取所有新闻详情页 URL。"""
    links = page.eval_on_selector_all(
        'a[href*="/news/"]',
        """els => {
        const set = new Set();
        for (const e of els) {
            const h = e.getAttribute('href') || '';
            if (h.startsWith('/news/') && h !== '/news' && h !== '/news/') {
                set.add(new URL(e.href).href);
            }
        }
        return Array.from(set);
    }""",
    )
    return list(links) if links else []


def extract_article_content(soup: BeautifulSoup, is_preprint: bool = False) -> dict:
    """
    从文章页 HTML 提取：标题、副标题、正文。
    新闻页用 h1 作标题；预印本页 h1 为站名，用第一个 h2 作标题，并提取元数据。
    返回 {"title", "subtitle", "body_html", "body_text", ...}，预印本多 "meta" 等。
    """
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"content|article|post", re.I))
        or soup.find("body")
    )
    if not main:
        main = soup

    title_el = main.find("h1") or soup.find("h1")
    h1_text = (title_el.get_text(strip=True) if title_el else "") or ""
    h2_el = main.find("h2") or soup.find("h2")

    if is_preprint and h1_text.strip().upper() == "S.H.I.T" and h2_el:
        title = h2_el.get_text(strip=True) or "Untitled"
        subtitle = ""
        meta = _extract_preprint_meta(main)
    else:
        title = h1_text or (h2_el.get_text(strip=True) if h2_el else "") or "Untitled"
        subtitle = ""
        next_el = title_el.next_sibling if title_el else None
        for _ in range(10):
            if next_el is None:
                break
            if isinstance(next_el, str):
                t = next_el.strip()
                if t and len(t) < 200:
                    subtitle = t
                    break
            elif getattr(next_el, "name", None) and next_el.name in ("p", "div", "span"):
                subtitle = next_el.get_text(strip=True)[:200]
                break
            next_el = getattr(next_el, "next_sibling", None)
        meta = {}

    body_html = ""
    if main:
        for tag in main.find_all(["script", "style", "nav", "header"]):
            tag.decompose()
        body_html = main.decode_contents() if main else ""

    body_text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)

    out = {
        "title": title,
        "subtitle": subtitle,
        "body_html": body_html,
        "body_text": body_text,
    }
    if meta:
        out["meta"] = meta
    return out


def _extract_preprint_meta(main) -> dict:
    """从预印本 main 区域提取作者、单位、学科、提交时间等键值对。"""
    text = main.get_text(separator="\n", strip=True)
    meta = {}
    labels = [
        ("Author", "作者", "author"),
        ("Institution", "单位", "institution"),
        ("Discipline", "学科", "discipline"),
        ("Submitted", "提交时间", "submitted"),
        ("Viscosity", "粘度", "viscosity"),
    ]
    for en, zh, key in labels:
        m = re.search(rf"{re.escape(en)}\s*/\s*{re.escape(zh)}\s*\n\s*(\S.+?)(?=\n(?:[A-Za-z]|[\u4e00-\u9fff])|\n\n|$)", text, re.S)
        if m:
            meta[key] = m.group(1).strip().split("\n")[0][:500]
    return meta


def html_to_simple_markdown(html: str) -> str:
    """将正文 HTML 转为简易 Markdown。"""
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "a"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        if el.name == "h1":
            parts.append(f"\n# {text}\n")
        elif el.name == "h2":
            parts.append(f"\n## {text}\n")
        elif el.name == "h3":
            parts.append(f"\n### {text}\n")
        elif el.name == "h4":
            parts.append(f"\n#### {text}\n")
        elif el.name in ("h5", "h6"):
            parts.append(f"\n##### {text}\n")
        elif el.name == "a" and el.get("href"):
            parts.append(f"[{text}]({el['href']})")
        elif el.name == "li":
            parts.append(f"- {text}")
        else:
            parts.append(f"\n{text}\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts).strip())


def _id_prefix(slug: str, is_uuid: bool) -> str:
    """按 id 生成子文件夹名：UUID 取前 2 位，否则取 slug 首字符。"""
    if not slug:
        return "x"
    if is_uuid:
        return slug[:2].lower()
    return slug[0].lower() if slug else "x"


def save_article(data: dict, subdir: str, organize_by_id: bool = True) -> Path | None:
    """将文章保存为 Markdown 和 JSON 元数据。organize_by_id 为 True 时按 id 放入子文件夹。"""
    slug = data.get("slug") or slugify(data.get("title", "untitled"))
    safe_slug = slugify(slug) or "untitled"
    is_uuid = bool(slug and UUID_PATTERN.match(slug))
    if organize_by_id:
        prefix = _id_prefix(slug, is_uuid)
        subpath = OUTPUT_DIR / subdir / prefix
    else:
        subpath = OUTPUT_DIR / subdir
    subpath.mkdir(parents=True, exist_ok=True)

    meta_lines = []
    meta_lines.append(f"- **URL**: {data.get('url', '')}")
    if data.get("subtitle"):
        meta_lines.append(f"- **Subtitle**: {data.get('subtitle', '')}")
    for k, v in (data.get("meta") or {}).items():
        meta_lines.append(f"- **{k}**: {v}")
    meta_block = "\n".join(meta_lines)

    md_content = f"""# {data.get('title', 'Untitled')}

{meta_block}

---

{html_to_simple_markdown(data.get('body_html', ''))}
"""
    md_path = subpath / f"{safe_slug}.md"
    md_path.write_text(md_content, encoding="utf-8")

    meta_path = subpath / f"{safe_slug}.meta.json"
    meta = {k: v for k, v in data.items() if k != "body_html"}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return md_path, subpath, safe_slug


def _screenshot_article(page, subpath: Path, safe_slug: str) -> None:
    """对正文区域截图并保存为 PNG（站点部分内容为图片形式，便于归档留档）。成功后将截图链接写入同目录 .md。"""
    png_path = subpath / f"{safe_slug}.png"
    try:
        main = page.locator("main").first
        if main.count() > 0:
            main.screenshot(path=str(png_path), timeout=15000)
        else:
            page.screenshot(path=str(png_path), full_page=True, timeout=30000)
    except Exception:
        try:
            page.screenshot(path=str(png_path), full_page=True, timeout=30000)
        except Exception:
            return
    if png_path.exists():
        md_path = subpath / f"{safe_slug}.md"
        if md_path.exists():
            md_path.write_text(
                md_path.read_text(encoding="utf-8") + f"\n\n![正文截图]({safe_slug}.png)\n",
                encoding="utf-8",
            )


def _load_existing_index(out_dir: Path) -> tuple[set[str], set[str], dict]:
    """加载已有 index.json，返回 (已收录新闻 url 集合, 已收录预印本 url 集合, 原 index 字典)。"""
    index_path = out_dir / "index.json"
    existing_news_urls: set[str] = set()
    existing_preprint_urls: set[str] = set()
    previous_index: dict = {"news": [], "preprints": []}
    if index_path.exists():
        try:
            previous_index = json.loads(index_path.read_text(encoding="utf-8"))
            for item in previous_index.get("news", []):
                if item.get("url"):
                    existing_news_urls.add(item["url"])
            for item in previous_index.get("preprints", []):
                if item.get("url"):
                    existing_preprint_urls.add(item["url"])
        except (json.JSONDecodeError, OSError):
            pass
    return existing_news_urls, existing_preprint_urls, previous_index


def _push_backup(repo_root: Path, index: dict) -> bool:
    """将当前 backup/ 与 index 提交并 push，便于被取消后下次 run 复用。返回是否 push 成功。"""
    index_path = OUTPUT_DIR / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "add", "backup/"], cwd=repo_root, check=True, capture_output=True)
        r = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=repo_root,
            capture_output=True,
        )
        if r.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore(archive): sync shitjournal [automated]"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "pull", "--rebase", "origin", "main"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "push", "origin", "main"], cwd=repo_root, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_sync(
    output_dir: Path | None = None,
    news_only: bool = False,
    delay: float = DELAY_SECONDS,
    headless: bool = True,
    preprints_limit: int = 0,
    push_every: int = 0,
) -> None:
    """执行同步：仅请求尚未收录的 URL，与已有索引合并后写回。push_every>0 时每隔 N 篇预印本提交并 push，便于被取消后下次复用。"""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(output_dir or OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    repo_root = OUTPUT_DIR.parent

    existing_news, existing_preprints, previous_index = _load_existing_index(OUTPUT_DIR)
    typer.echo(f"已收录：新闻 {len(existing_news)} 篇，预印本 {len(existing_preprints)} 篇。")

    new_news: list[dict] = []
    new_preprints: list[dict] = []

    def _maybe_push() -> None:
        if push_every <= 0:
            return
        merged = {
            "news": previous_index.get("news", []) + new_news,
            "preprints": previous_index.get("preprints", []) + new_preprints,
            "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if _push_backup(repo_root, merged):
            typer.echo("已增量推送至远程。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="ShitJournalBackup/1.0 (+https://github.com; backup bot)",
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.set_default_timeout(20000)

        # 1) 新闻：仅同步未收录的
        page.goto(f"{BASE_URL}/news", wait_until="networkidle")
        time.sleep(0.5)
        news_urls = extract_news_links_from_page(page)
        news_to_fetch = [u for u in news_urls if u not in existing_news]
        typer.echo(f"新闻：共 {len(news_urls)} 篇，待同步 {len(news_to_fetch)} 篇。")

        for url in tqdm(news_to_fetch, desc="新闻"):
            time.sleep(delay)
            page.goto(url, wait_until="networkidle")
            time.sleep(0.3)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            data = extract_article_content(soup)
            data["url"] = url
            data["slug"] = url.rstrip("/").split("/")[-1] or "index"
            result = save_article(data, "news")
            if result:
                _md, subpath, safe_slug = result
                _screenshot_article(page, subpath, safe_slug)
            new_news.append({"url": url, "slug": data.get("slug"), "title": data.get("title")})

        if new_news and push_every > 0:
            _maybe_push()

        # 2) 预印本：仅同步未收录的，再应用 limit
        if not news_only:
            stop_after = (preprints_limit * 2) if preprints_limit > 0 else 0
            preprint_urls = _collect_all_preprint_urls(page, delay, stop_after_total=stop_after)
            to_fetch = [u for u in preprint_urls if u not in existing_preprints]
            if preprints_limit > 0:
                to_fetch = to_fetch[:preprints_limit]
            typer.echo(f"预印本：共 {len(preprint_urls)} 篇，已收录 {len(existing_preprints)} 篇，本次待同步 {len(to_fetch)} 篇。")

            for i, url in enumerate(tqdm(to_fetch, desc="预印本")):
                time.sleep(delay)
                page.goto(url, wait_until="networkidle")
                time.sleep(0.3)
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                data = extract_article_content(soup, is_preprint=True)
                data["url"] = url
                data["slug"] = url.rstrip("/").split("/")[-1] or "index"
                result = save_article(data, "preprints")
                if result:
                    _md, subpath, safe_slug = result
                    _screenshot_article(page, subpath, safe_slug)
                new_preprints.append({"url": url, "slug": data.get("slug"), "title": data.get("title")})
                if push_every > 0 and (i + 1) % push_every == 0:
                    _maybe_push()

        if new_preprints and push_every > 0:
            _maybe_push()

        context.close()
        browser.close()

    # 合并索引：已有 + 本次新增
    index = {
        "news": previous_index.get("news", []) + new_news,
        "preprints": previous_index.get("preprints", []) + new_preprints,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    index_path = OUTPUT_DIR / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"索引已写入: {index_path}（共新闻 {len(index['news'])} 篇，预印本 {len(index['preprints'])} 篇）")


app = typer.Typer(help="S.H.I.T Journal 归档同步")

@app.command("run")
def run_cmd(
    output_dir: Path = typer.Option(OUTPUT_DIR, "--output", "-o", help="归档输出目录"),
    news_only: bool = typer.Option(False, "--news-only", help="仅同步新闻"),
    preprints_limit: int = typer.Option(0, "--preprints-limit", help="预印本最多同步篇数，0 表示不限制"),
    push_every: int = typer.Option(0, "--push-every", help="每隔 N 篇预印本提交并 push，0 不增量推送（CI 建议 25）"),
    delay: float = typer.Option(DELAY_SECONDS, "--delay", help="请求间隔秒数"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="是否无头模式"),
) -> None:
    """从 shitjournal.org 同步新闻与预印本并归档为 Markdown。"""
    run_sync(
        output_dir=output_dir,
        news_only=news_only,
        delay=delay,
        headless=headless,
        preprints_limit=preprints_limit,
        push_every=push_every,
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        run_sync(news_only=True)  # 默认仅新闻，避免误跑全量预印本


if __name__ == "__main__":
    app()
