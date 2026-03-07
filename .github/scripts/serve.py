#!/usr/bin/env python3
"""
本地调试：在仓库根目录下启动 HTTP 服务，托管 docs/ 目录（Pages 静态站点）。
用法：
  python .github/scripts/serve.py
  python .github/scripts/serve.py 8080
"""
from __future__ import annotations

import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# 脚本在 .github/scripts/，仓库根目录为其上两级
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = _REPO_ROOT / "docs"


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not DOCS_DIR.is_dir():
        print(f"错误：未找到目录 {DOCS_DIR}", file=sys.stderr)
        sys.exit(1)
    os.chdir(DOCS_DIR)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            print(format % args)

    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"本地调试：托管 {DOCS_DIR}")
    print(f"访问 http://127.0.0.1:{port}/  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
