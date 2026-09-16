#!/usr/bin/env python3
"""
工行积存金行情 — 局域网网页：后台线程定时拉取 jcjm_watch 同一数据源，
通过 JSON 接口 + 前端轮询展示表格（见 templates/index.html）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import threading
import time
from datetime import datetime, timezone, timedelta

import httpx
from flask import Flask, jsonify, render_template

import jcjm_watch as jw

# 与命令行工具一致，用于记录「本次拉取时间」
CN_TZ = timezone(timedelta(hours=8))

# 服务启动后只读（由 main 写入一次），供后台线程与 Flask 路由使用
_config: dict = {
    "url": jw.DEFAULT_URL,
    "interval": 60,
    "trust_env": True,
}

# 后台线程写、Flask 请求线程读，访问前必须持锁
_cache_lock = threading.Lock()
_cache: dict = {
    "rows": None,
    "page_update": None,
    "fetched_at": None,
    "error": None,
    # 自进程启动以来、每次成功拉取一条：{ "t": iso8601, "prices": { 列名: 数值 } }
    "price_history": [],
}

# 防止极长时间运行占满内存（约按 60s 间隔可存数月量级）
_MAX_PRICE_HISTORY = 50000


def _parse_float_cell(s: str) -> float | None:
    t = (s or "").strip().replace(",", "")
    if not t or t in ("—", "---", "----"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _extract_jcjm_prices(headers: list[str], rows: list[list[str]]) -> dict[str, float] | None:
    """在表格中定位「积存金」行，将除首列外的数字单元格与表头组成 {列名: 价格}。"""
    if not headers or not rows:
        return None
    target: list[str] | None = None
    for r in rows:
        if r and "积存金" in (r[0] or ""):
            target = r
            break
    if target is None:
        target = rows[0]
    out: dict[str, float] = {}
    for i, h in enumerate(headers):
        if i == 0 or i >= len(target):
            continue
        v = _parse_float_cell(target[i])
        if v is not None:
            name = (h or "").strip() or f"列{i}"
            out[name] = v
    return out or None


def _touch_fetch_error(msg: str) -> None:
    """拉取失败时写入错误信息，并刷新 fetched_at 便于前端显示「最后尝试时间」。"""
    with _cache_lock:
        _cache["error"] = msg
        _cache["fetched_at"] = datetime.now(CN_TZ).isoformat()


def _background_fetcher() -> None:
    """守护线程：按间隔 GET；每次请求新建 Client，避免停盘后复用异常连接导致开盘后仍失败。"""
    url = _config["url"]
    interval = _config["interval"]
    kwargs = jw._client_kwargs(_config["trust_env"])
    while True:
        try:
            with httpx.Client(**kwargs) as client:
                rows, page_ts = jw.fetch_once(client, url)
            with _cache_lock:
                _cache["rows"] = rows
                _cache["page_update"] = page_ts
                fetched_iso = datetime.now(CN_TZ).isoformat()
                _cache["fetched_at"] = fetched_iso
                _cache["error"] = None
                norm = jw._normalize_rows(rows)
                if norm and len(norm) > 1:
                    pts = _extract_jcjm_prices(norm[0], norm[1:])
                    if pts:
                        hist = _cache["price_history"]
                        hist.append({"t": fetched_iso, "prices": pts})
                        if len(hist) > _MAX_PRICE_HISTORY:
                            del hist[: len(hist) - _MAX_PRICE_HISTORY]
        except Exception as e:
            _touch_fetch_error(str(e))
        time.sleep(interval)


def create_app() -> Flask:
    # 模板目录相对本文件固定，避免从其它工作目录启动时找不到 templates
    root = pathlib.Path(__file__).resolve().parent
    app = Flask(__name__, template_folder=str(root / "templates"))

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            poll_interval_ms=_config["interval"] * 1000,
            source_url=_config["url"],
        )

    @app.route("/api/quote")
    def api_quote():
        """返回最近一次后台拉取结果；headers/rows 与页面表格列一致。"""
        with _cache_lock:
            rows = _cache["rows"]
            snapshot = {
                "ok": _cache["error"] is None and rows is not None,
                "page_update": _cache["page_update"],
                "fetched_at": _cache["fetched_at"],
                "error": _cache["error"],
                "interval_sec": _config["interval"],
                "source_url": _config["url"],
                "price_history": list(_cache["price_history"]),
            }
        payload = dict(snapshot)
        if rows:
            norm = jw._normalize_rows(rows)
            # 首行作表头，其余为数据行（与 jcjm_watch 打印逻辑一致）
            if norm and len(norm) > 1:
                payload["headers"] = norm[0]
                payload["rows"] = norm[1:]
            elif norm:
                payload["headers"] = norm[0]
                payload["rows"] = []
            else:
                payload["headers"] = []
                payload["rows"] = []
        else:
            payload["headers"] = []
            payload["rows"] = []
        resp = jsonify(payload)
        # 避免浏览器或中间代理把行情接口缓存成旧数据
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


def main() -> int:
    """解析参数 → 写入 _config → 启动后台拉取线程 → 阻塞运行 Flask。"""
    p = argparse.ArgumentParser(description="工行积存金行情 — 局域网网页服务")
    p.add_argument(
        "--host",
        default=os.environ.get("ICBC_JCJM_WEB_HOST", "0.0.0.0"),
        help="监听地址，默认 0.0.0.0（局域网可访问）",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ICBC_JCJM_WEB_PORT", "8765")),
        help="端口，默认 8765",
    )
    p.add_argument(
        "--url",
        default=os.environ.get("ICBC_JCJM_URL", jw.DEFAULT_URL),
        help="行情页 URL",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("ICBC_JCJM_INTERVAL", "60")),
        help="后台拉取间隔（秒），默认 60",
    )
    p.add_argument(
        "--no-proxy",
        action="store_true",
        help="忽略系统代理，直连银行站点",
    )
    args = p.parse_args()

    _config["url"] = args.url
    # 过短间隔容易触发行方限流，与命令行工具保持下限 10 秒
    _config["interval"] = max(10, args.interval)
    _config["trust_env"] = jw.resolve_trust_env(args.no_proxy)

    t = threading.Thread(target=_background_fetcher, name="icbc-jcjm-fetch", daemon=True)
    t.start()

    app = create_app()
    print(
        f"积存金行情网页: http://{args.host}:{args.port}/\n"
        f"数据源每 {_config['interval']} 秒更新；本机 IP 访问请改用局域网地址。",
        flush=True,
    )
    # threaded=True：浏览器并发请求静态页与 /api/quote 时不互相阻塞
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
