# S.H.I.T Journal 归档项目 — 需求与规格说明

本文档描述本项目的目标、收录范围、归档规则、同步与触发机制、技术约束及对外表述规范，供开发与维护参考。对外简要说明见仓库根目录 `README.md`。

**文档结构**：

```mermaid
flowchart LR
  subgraph doc [需求文档]
    A[1. 项目概述与目标]
    B[2. 数据来源与收录范围]
    C[3. 归档结构与存储规则]
    D[4. 同步与触发机制]
    E[5. 技术约束与实现要点]
    F[6. 对外表述规范]
    G[7. 许可与免责]
  end
```

---

## 1. 项目概述与目标

**项目名称**：S.H.I.T Journal 归档

**一句话描述**：对 [S.H.I.T Journal](https://shitjournal.org/)（公开社区期刊）进行定期同步与长期归档，将新闻与预印本以结构化形式保存于公开 Git 仓库，便于引用与离线访问。

**目标**：

- **长期保存**：为社区期刊内容提供可追溯的存档副本。
- **学术可引用**：每篇条目含 URL、标题、作者、单位、学科、提交时间等元数据，便于引用与检索。
- **去中心化备份**：公开仓库中的归档不依赖单一站点可用性。

**交付物**：公开 Git 仓库中的 `backup/` 目录，内含预印本元数据、简略 Markdown 及索引文件；PDF 不存入仓库，通过官网 API 返回的 `pdf_url` 获取（无水印）。

---

## 2. 数据来源与收录范围

**来源站点**：shitjournal.org（公开社区期刊）。

**收录类型**：

| 类型 | 路径/说明 |
|------|-----------|
| **新闻 / News** | `/news` 下所有条目的详情页（公告、征稿启事、新功能说明、简讯等）。 |
| **预印本 / Preprints** | `/preprints` 下四个分区（旱厕 / Latrine、化粪池 / Septic Tank、构石 / Stone、沉淀区 / Sediment）的全部文章详情页；支持分页遍历与 URL 去重。 |

**不收录**：首页社论、子刊（`/journals`）、投稿（`/submit`）、社区（`/community-guard`）等导航与功能页。

**技术说明**：预印本通过官网 API（`api.shitjournal.org`）获取列表与详情，含 `pdf_url` 直链；新闻暂无 API，仅保留既有索引中的新闻条目。

---

## 3. 归档结构与存储规则

**根目录**：`backup/`。

**存储策略**：预印本 PDF 下载至 **`backup/pdfs/{UUID 前 2 位}/{uuid}.pdf`**，由 **Git LFS** 托管，避免大文件撑大仓库历史；克隆时默认得到 LFS 指针，按需可 `git lfs pull` 拉取完整 PDF。

**按 id 分子目录**：

- **新闻**：以 slug 为 id，按 slug 的**首字母**分目录（沿用既有结构；当前同步不新增新闻，因官网暂无新闻 API）。
  - 路径：`backup/news/{slug 首字母}/{slug}.md`、`backup/news/{slug 首字母}/{slug}.meta.json`
- **预印本**：以 UUID 为 id，按 UUID 的**前 2 位**分目录。
  - 路径：`backup/preprints/{UUID 前 2 位}/{uuid}.meta.json`、`backup/preprints/{UUID 前 2 位}/{uuid}.md`
  - `.meta.json`：完整文章元信息，含 `article`（API 返回的完整文章对象）、`comments`（评论列表）、`fetched_at`（抓取时间）。
  - `.md`：简略说明（标题、作者、机构、提交时间、在线阅读链接、PDF 链接及本仓库归档路径）。
  - **PDF**：`backup/pdfs/{UUID 前 2 位}/{uuid}.pdf`（Git LFS），与 preprints 同前缀分目录，便于对应。

**索引**：`backup/index.json`，记录已收录的新闻与预印本（url、slug、title、pdf_url 等）；随每次同步合并更新，用于增量同步时跳过已收录 id。

**元数据要求**：

- **预印本**：来自 API，含作者（display_name）、单位（institution）、学科（discipline）、提交时间（created_at）、分区（zones）、`pdf_url` 等。

---

## 4. 同步与触发机制

**三种触发方式**（需全部支持）：

1. **定时**：每日 UTC 17:00（北京时间凌晨 01:00）自动执行一次。
2. **推送触发**：向 `main` 分支 push 时执行一次。
3. **手动**：在 GitHub Actions 中选择 “ShitJournal Archive Sync” → “Run workflow” 可立即执行。

**禁止并行**：同一时间只允许一次同步任务执行；使用 `concurrency` 组（如 `shitjournal-archive`）且不取消进行中的运行（`cancel-in-progress: false`），新触发的运行排队等待。

**增量同步**：已收录的预印本 id 由 `backup/index.json` 记录；每次同步前加载该索引，仅对尚未收录的 id 调用 API 并写入，避免重复请求。同步结束后将本次新增条目与原有索引合并写回 `index.json`。

**增量推送（避免取消后重复）**：在 CI 中可开启 `--push-every N`（如 25），每同步 N 篇预印本即提交并 push 一次；若 Action 被取消，已推送内容保留，下次基于最新 index 继续。

**执行结果**：

- 仅当 `backup/` 有变更时执行提交并 push。
- 提交信息使用统一格式：`chore(archive): sync shitjournal [automated]`。

**可配置项**：预印本单次同步可设上限（如 100 篇）以控制单次运行时间；该上限作用于「本次待同步」的预印本数量。当前 workflow 中为 `--preprints-limit 100`，可按需调整或取消限制。

---

## 5. 技术约束与实现要点

- **运行环境**：GitHub Actions（`ubuntu-latest`）、Python 3.11；仅依赖标准库与 typer、tqdm，无需浏览器。
- **数据来源**：官网 API `api.shitjournal.org` — 文章列表（`/api/articles/?zone=…&page=…`）与文章详情（`/api/articles/{id}`），详情含 `pdf_url`。
- **输出格式**：预印本元数据以 JSON 存储；简略 Markdown 仅含标题、作者、链接；文件名与路径按「UUID 前 2 位」分目录。
- **礼貌策略**：请求间隔、固定 User-Agent；见 `.github/scripts/sync.py`。

---

## 6. 对外表述规范（学术化、去争议化）

**原则**：对外文档（如 README、GitHub Actions 名称与步骤描述、commit 信息）不提及具体抓取或爬虫实现，以「归档」「同步」「收录」「备份」等中性、学术化用语为主。

**推荐用语**：同步、归档、收录、备份、元数据、索引、长期保存、可引用。

**避免用语**：爬虫、抓取、爬取等易引发争议的表述。

**适用范围**：README、workflow 名称与步骤描述、对外说明；内部代码与本文档（`docs/REQUIREMENTS.md`）可保留必要技术术语以便维护。

---

## 7. 许可与免责

- 本归档仅供学术与个人备份之用。
- 内容版权归 S.H.I.T Journal 及原作者所有。
- 若来源站要求停止同步，须立即停止并配合处理。
