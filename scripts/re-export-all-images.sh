#!/usr/bin/env bash
# 在本地用所有已有 PDF 重新导出正文图，并准备好提交与推送。
# 使用前请确保：1) 已安装 pdftoppm (poppler-utils)；2) backup/pdfs 下有真实 PDF（若只有 LFS 指针需先 git lfs pull）。
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "【1/4】检查环境与 PDF..."
if ! command -v pdftoppm &>/dev/null; then
  echo "错误: 未找到 pdftoppm，请安装 poppler-utils（如 apt install poppler-utils）"
  exit 1
fi
PDF_COUNT=$(find backup/pdfs -name "*.pdf" 2>/dev/null | wc -l)
if [ "$PDF_COUNT" -eq 0 ]; then
  echo "错误: backup/pdfs 下没有 PDF。若使用 LFS，请先执行: git lfs pull"
  exit 1
fi
# 若第一个 PDF 很小（<500 字节），多半是 LFS 指针未拉取
FIRST_PDF=$(find backup/pdfs -name "*.pdf" 2>/dev/null | head -1)
if [ -n "$FIRST_PDF" ]; then
  SIZE=$(wc -c < "$FIRST_PDF" 2>/dev/null || echo 0)
  if [ "$SIZE" -lt 500 ]; then
    echo "警告: $FIRST_PDF 体积很小，可能是 LFS 指针。请先执行: git lfs pull"
    exit 1
  fi
fi

echo "【2/4】用本地 PDF 重新生成正文图（update-md-local）..."
pip install -q -r requirements.txt
python .github/scripts/sync.py update-md-local --output backup

echo "【3/4】导出 docs 索引..."
python .github/scripts/sync.py export-docs --output backup

echo "【4/4】准备提交..."
git add backup/ docs/data/index.json
if git diff --staged --quiet; then
  echo "没有变更，无需提交。"
  exit 0
fi
git status --short
echo ""
git commit -m "chore(archive): re-export all body images from local PDFs"
echo "已提交。请执行推送（含 LFS）："
echo "  git push origin main"
