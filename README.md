# S.H.I.T Journal 归档

对 [S.H.I.T Journal](https://shitjournal.org/) 的**新闻**与**预印本**进行定期同步与长期归档，以结构化元数据与 Markdown 形式保存于本仓库，PDF 与正文图片托管于 Hugging Face 数据集，便于引用与离线访问。

---

## 目标

| 目标 | 说明 |
|------|------|
| **长期保存** | 为社区期刊内容提供可追溯的存档副本，不依赖单一站点可用性。 |
| **可引用性** | 每条预印本含 URL、标题、作者、机构、学科、提交时间等元数据，便于引用与检索。 |
| **去中心化** | 元数据与索引存于 Git；大体积资源存于 Hugging Face，公开可访问。 |

---

## 收录范围

| 类型 | 说明 |
|------|------|
| **新闻 (News)** | 公告、征稿启事、新功能说明、简讯等。 |
| **预印本 (Preprints)** | 社区投稿文章，按流程分区：旱厕 → 化粪池 → 构石 → 沉淀区。 |

仅收录上述两类正文与元数据；首页、子刊导航等不在收录范围内。预印本通过官网 API 获取列表与详情（含 PDF 直链）。

---

## 归档结构

- **`backup/`**  
  - **`index.json`**：全量索引（新闻 + 预印本），供增量同步与前端展示。  
  - **`news/{首字母}/`**：新闻条目（`.md` + `.meta.json`）。  
  - **`preprints/{前两位}/`**：预印本按 UUID 前两位分子目录，每篇含 `{id}.meta.json`、`{id}.md` 及正文图 `{id}-1.png` 等。  
  - **`pdfs/{前两位}/`**：预印本 PDF（`{id}.pdf`）。  

- **大体积资源**（PDF、预印本正文图）**不进入 Git**，由 [Hugging Face 数据集 `jsonhash/shitjournal-backup`](https://huggingface.co/datasets/jsonhash/shitjournal-backup) 托管；同步脚本会在此数据集中维护与 `backup/` 一致的目录结构。

---

## 浏览归档

仓库内提供用于 **GitHub Pages** 的静态站点（`docs/`），可在线浏览索引与正文。

1. 在仓库 **Settings → Pages** 中，**Source** 选 “Deploy from a branch”，**Branch** 选 `main`，**Folder** 选 **/docs**。  
2. 部署完成后，站点会读取 `docs/data/index.json` 并列出新闻与预印本；正文中的 PDF 与图片链接指向 Hugging Face 数据集。

---

## 同步与自动化

- **定时**：每日 UTC 17:00（北京时间 01:00）自动执行一次同步。  
- **推送触发**：向 `main` 分支 push 时也会触发同步。  
- **手动**：在 **Actions** 中选择 “ShitJournal Archive Sync” → “Run workflow”。  

每次运行会：从官网 API 同步预印本元数据、下载 PDF 并生成正文图，将 PDF 与图片上传至 Hugging Face，将索引与元数据提交并推送到本仓库。

---

## 本地与脚本

- **环境**：Python 3.11+，依赖见 `requirements.txt`（含 `huggingface_hub`、`typer`、`tqdm` 等）。  
- **同步**：`python .github/scripts/sync.py run`（可选 `--preprints-limit`、`--push-every`）。  
- **上传至 HF**：`HF_TOKEN=xxx python .github/scripts/upload_to_hf.py`（需在 [Hugging Face](https://huggingface.co/settings/tokens) 创建 Token；GitHub Action 使用仓库 Secret `HF_TOKEN`）。  
- **其他命令**：`rebuild-index`、`export-docs`、`move-preprints-into-prefix-dirs`、`refresh-md`、`check` 等，见 `python .github/scripts/sync.py --help`。  

完整需求与规格见 [**docs/REQUIREMENTS.md**](docs/REQUIREMENTS.md)。

---

## 许可与免责

本归档仅供学术与个人备份之用。内容版权归 S.H.I.T Journal 及原作者所有；若来源站要求停止同步，将立即停止并配合处理。
