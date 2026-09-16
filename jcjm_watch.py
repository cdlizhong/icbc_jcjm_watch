#!/usr/bin/env python3
"""
工行积存金行情：从官网公开 HTML 页面拉取表格，在终端用 Rich 对齐输出。

默认使用 goldaccrual_query_out.jsp；带 serviceId=PBL200603 的 frame 入口在脚本请求下
常返回「浏览器不支持网银」类页面，故不作为默认地址。URL 可用 ICBC_JCJM_URL / --url 覆盖。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Iterable

import httpx
from bs4 import BeautifulSoup
from rich import box
from rich.console import Console
from rich.table import Table

# 终端时间与页面「更新时间」展示用东八区
CN_TZ = timezone(timedelta(hours=8))

# 工行个人网银 — 积存金行情表（公开 GET 即可拿到表格）
DEFAULT_URL = (
    "https://mybank.icbc.com.cn/icbc/newperbank/perbank3/gold/goldaccrual_query_out.jsp"
)

# 模拟常见桌面浏览器，降低被简单 UA 规则拦截的概率
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 从整页文本中提取「更新时间: yyyy-mm-dd hh:mm:ss」
UPDATE_TIME_RE = re.compile(
    r"更新\s*时间\s*[:：]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
)

# 停盘或非交易时段页面常见文案（无标准行情表时不应视为「结构损坏」）
NON_TRADING_PAGE_RE = re.compile(
    r"非交易|休市|停盘|暂停交易|交易时间外|不在交易|暂未开盘|非我行交易|开市时间"
)


def resolve_trust_env(no_proxy: bool) -> bool:
    """返回 True 表示 httpx 读取系统代理；False 表示忽略 HTTP(S)_PROXY 直连。"""
    if no_proxy:
        return False
    return os.environ.get("ICBC_JCJM_NO_PROXY") not in ("1", "true", "yes")


def _decode_html(content: bytes) -> str:
    """工行页面多为 GB 系编码，依次尝试再退回 UTF-8（容错）。"""
    for enc in ("gb18030", "gbk", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _cell_text(td) -> str:
    """取 td/th 内纯文本，合并内部空白并去掉 &nbsp; 类字符。"""
    t = td.get_text(separator=" ", strip=True)
    t = t.replace("\xa0", " ").strip()
    return t


def parse_quote_table(html: str) -> tuple[list[list[str]], str | None]:
    """解析 HTML 中「最大」的一张数据表作为行情表，并提取页面更新时间字符串。"""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    best: list[list[str]] | None = None
    for table in tables:
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [_cell_text(td) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        # 过滤太小的表；在候选里选单元格总数最多的作为主表
        if len(rows) >= 2 and len(rows[0]) >= 3:
            if best is None or len(rows) * len(rows[0]) > len(best) * len(best[0]):
                best = rows

    flat_text = soup.get_text(" ", strip=True)
    page_update: str | None = None
    m = UPDATE_TIME_RE.search(flat_text)
    if m:
        page_update = m.group(1)

    if best is None:
        if NON_TRADING_PAGE_RE.search(flat_text):
            return (
                [
                    ["提示", "说明"],
                    [
                        "非交易或暂无行情表",
                        "当前时段页面未返回标准行情表格，开盘后将自动恢复；非报错。",
                    ],
                ],
                page_update,
            )
        raise ValueError("未在页面中解析到行情表格（页面结构可能已变更）。")
    return best, page_update


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    """统一列数（右侧补空），空单元格显示为 em dash，便于打印与 JSON 对齐。"""
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    out: list[list[str]] = []
    for r in rows:
        cells = [c if c else "—" for c in r]
        cells.extend([""] * (ncols - len(cells)))
        out.append(cells)
    return out


def _numericish(s: str) -> bool:
    """判断是否可视为数字列中的值（含占位符 —），用于列对齐。"""
    t = s.strip()
    if t in ("", "—", "---", "----"):
        return True
    try:
        float(t.replace(",", ""))
        return True
    except ValueError:
        return False


def _column_justify(col_idx: int, data_rows: list[list[str]]) -> str:
    """首列左对齐；其余列若全是数字/占位则右对齐（终端表格更易读）。"""
    if col_idx == 0:
        return "left"
    if not data_rows:
        return "left"
    col_vals = [r[col_idx] for r in data_rows if col_idx < len(r)]
    if not col_vals:
        return "left"
    if all(_numericish(v) for v in col_vals):
        return "right"
    return "left"


def print_quote_table(rows: list[list[str]], *, console: Console | None = None) -> None:
    """用 Rich 输出表格（中英文显示宽度正确）；仅一行时退化为单行文本。"""
    norm = _normalize_rows(rows)
    if not norm:
        return
    c = console or Console()
    header = norm[0]
    body = norm[1:] if len(norm) > 1 else []
    ncols = len(header)

    if not body:
        c.print(" ".join(header))
        return

    table = Table(
        show_header=True,
        box=box.SIMPLE,
        header_style="bold",
        pad_edge=False,
        collapse_padding=False,
    )
    for i, h in enumerate(header):
        table.add_column(
            h,
            justify=_column_justify(i, body),
            overflow="ellipsis",
            no_wrap=False,
        )
    for r in body:
        table.add_row(*r[:ncols])

    c.print(table)


def fetch_once(client: httpx.Client, url: str) -> tuple[list[list[str]], str | None]:
    """GET 行情页，解码后解析表格；返回 (行二维列表, 页面更新时间或 None)。"""
    r = client.get(
        url,
        headers={"User-Agent": USER_AGENT, "Connection": "close"},
    )
    r.raise_for_status()
    html = _decode_html(r.content)
    return parse_quote_table(html)


def _client_kwargs(trust_env: bool) -> dict:
    """构造共用的 httpx.Client 参数（超时、重定向、代理、连接池上限）。"""
    return {
        "timeout": 30.0,
        "follow_redirects": True,
        "trust_env": trust_env,
        # 与每次新建 Client + Connection: close 配合，避免池内残留半关闭连接
        "limits": httpx.Limits(max_keepalive_connections=0, max_connections=2),
    }


def run_loop(url: str, interval_sec: int, trust_env: bool) -> None:
    """命令行常驻模式：每次拉取使用独立 Client，避免停盘等异常后复用损坏的 keep-alive 连接。"""
    kwargs = _client_kwargs(trust_env)
    while True:
        now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        try:
            with httpx.Client(**kwargs) as client:
                rows, page_ts = fetch_once(client, url)
            print(f"\n===== {now} =====")
            if page_ts:
                print(f"页面更新时间: {page_ts}")
            print_quote_table(rows)
        except httpx.HTTPError as e:
            print(f"\n[{now}] HTTP 错误: {e}", file=sys.stderr)
        except Exception as e:
            print(f"\n[{now}] 解析失败: {e}", file=sys.stderr)
        time.sleep(interval_sec)


def main(argv: Iterable[str] | None = None) -> int:
    """入口：--once 单次拉取；否则进入 run_loop。"""
    p = argparse.ArgumentParser(description="工行积存金行情（官网表格），定时刷新。")
    p.add_argument(
        "--url",
        default=os.environ.get("ICBC_JCJM_URL", DEFAULT_URL),
        help="行情页 URL（默认官方 goldaccrual_query_out.jsp）",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("ICBC_JCJM_INTERVAL", "60")),
        help="刷新间隔（秒），默认 60",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="只拉取一次后退出（调试用）",
    )
    p.add_argument(
        "--no-proxy",
        action="store_true",
        help="忽略系统代理（HTTP(S)_PROXY），直连银行站点",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    trust_env = resolve_trust_env(args.no_proxy)

    if args.interval < 10:
        print("间隔过短可能被服务端限制，建议 >= 60 秒。", file=sys.stderr)

    if args.once:
        try:
            with httpx.Client(**_client_kwargs(trust_env)) as client:
                rows, page_ts = fetch_once(client, args.url)
                if page_ts:
                    print(f"页面更新时间: {page_ts}")
                print_quote_table(rows)
        except httpx.ProxyError as e:
            print(f"HTTP 代理错误: {e}", file=sys.stderr)
            if trust_env:
                print("可尝试加 --no-proxy 或设置环境变量 ICBC_JCJM_NO_PROXY=1。", file=sys.stderr)
            return 1
        except httpx.HTTPError as e:
            print(f"HTTP 错误: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
            return 1
        return 0

    print(
        f"每 {args.interval} 秒刷新: {args.url}\n"
        "按 Ctrl+C 结束。",
        file=sys.stderr,
    )
    try:
        run_loop(args.url, args.interval, trust_env)
    except KeyboardInterrupt:
        print("\n已退出。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
