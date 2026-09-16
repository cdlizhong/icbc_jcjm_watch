#!/usr/bin/env bash
# 统一用项目虚拟环境运行：默认命令行行情；子命令 web 启动 Flask 网页
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "请先创建虚拟环境并安装依赖：" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
case "${1:-}" in
  web)
    shift
    exec .venv/bin/python web.py "$@"
    ;;
  app)
    shift
    exec .venv/bin/python menubar_app.py "$@"
    ;;
esac
exec .venv/bin/python jcjm_watch.py "$@"
