# 预印本归档说明

本目录存放 S.H.I.T Journal 预印本的元数据与简略说明，正文以 PDF 形式单独归档。

## 目录结构

- 按文章 UUID 的**前 2 位**分子目录：`{前缀}/{uuid}.meta.json`、`{uuid}.md`
- 示例：`eb/ebf14364-ae66-480d-a590-4a684132ee15.meta.json`、`eb/ebf14364-ae66-480d-a590-4a684132ee15.md`

## 文件说明

| 文件 | 说明 |
|------|------|
| **`{id}.meta.json`** | 完整文章元信息：`article`（API 返回的完整对象，含 `pdf_url`、作者、分区、提交时间等）、`comments`（评论列表）、`fetched_at`（抓取时间） |
| **`{id}.md`** | 简略说明：标题、作者、机构、提交时间、在线阅读链接、PDF 下载链接及本仓库 PDF 路径 |

## PDF 存放位置

PDF 不放在本目录，统一存放在 **`backup/pdfs/{前缀}/{id}.pdf`**，由 Git LFS 托管。  
需要拉取完整 PDF 时可执行：`git lfs pull`。

## 数据来源

预印本列表与详情来自官网 API（`api.shitjournal.org`），同步脚本见仓库 `.github/scripts/sync.py`。
