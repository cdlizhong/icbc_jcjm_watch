#!/usr/bin/env python3
"""积存金 24h 监控：跟踪今日最高/最低价，按规则推送 Bark 通知到 iPhone。

规则：
  A. 突破提醒 —— 现价创出今日新高/新低（带最小步长过滤与冷却时间，防抖）
  B. 目标位提醒 —— 触及 config.json 中 targets 设定的价格
  D. 区间位置 —— 每条通知附带现价在今日区间中的位置，辅助判断买卖时机

状态持久化在 monitor_state.json，进程重启不丢失当日高低点与已触发目标。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import httpx

import jcjm_watch as quote
from notify import build_notifier

CN_TZ = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.json")
DEFAULT_STATE = os.path.join(BASE_DIR, "monitor_state.json")

# 页面是「积存金监控」占位表（非交易时段）时的特征首列
NON_TRADING_FIRST_CELL = "提示"


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_state(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _to_float(s: str) -> float | None:
    t = s.replace(",", "").strip()
    if t in ("", "—", "---", "----"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def extract_quote(rows: list[list[str]]) -> dict | None:
    """从行情表中定位「积存金」行，返回 现价/页面最低/页面最高/赎回价。

    列位置按表头名称定位（表头里含空格，先归一化），避免列序变动时取错。
    """
    if not rows or len(rows) < 2:
        return None
    header = [c.replace(" ", "") for c in rows[0]]

    def col(keyword: str) -> int | None:
        for i, h in enumerate(header):
            if keyword in h:
                return i
        return None

    i_price = col("实时")
    i_low = col("最低价")
    i_high = col("最高价")
    i_redeem = col("赎回价")
    if i_price is None:
        return None

    for r in rows[1:]:
        if not r or r[0] == NON_TRADING_FIRST_CELL:
            return None
        if "积存金" in r[0]:
            price = _to_float(r[i_price]) if i_price < len(r) else None
            if price is None:
                return None
            return {
                "price": price,
                "page_low": _to_float(r[i_low]) if i_low is not None and i_low < len(r) else None,
                "page_high": _to_float(r[i_high]) if i_high is not None and i_high < len(r) else None,
                "redeem": _to_float(r[i_redeem]) if i_redeem is not None and i_redeem < len(r) else None,
            }
    return None


def fresh_state(today: str, price: float, page_low: float | None, page_high: float | None) -> dict:
    return {
        "date": today,
        "open": price,
        # 会话内跟踪的极值用于「突破」判定；优先取页面官方值作为初始基准
        "session_high": page_high if page_high is not None else price,
        "session_low": page_low if page_low is not None else price,
        "last_high_alert_at": 0.0,
        "last_low_alert_at": 0.0,
        "triggered_targets": [],
    }


def range_position(price: float, low: float, high: float) -> str:
    if high <= low:
        return "区间待定"
    pct = (price - low) / (high - low) * 100
    return f"区间位置 {pct:.0f}%"


def check_rules(q: dict, state: dict, config: dict, notifier, now: float) -> dict:
    """按 A/B 规则检查并推送，返回更新后的 state。通知附带 D 区间信息。"""
    price = q["price"]
    # 展示用区间：页面官方值优先，缺失退回会话跟踪值
    day_low = q["page_low"] if q["page_low"] is not None else state["session_low"]
    day_high = q["page_high"] if q["page_high"] is not None else state["session_high"]
    pos = range_position(price, day_low, day_high)

    def body_lines(extra: str) -> str:
        lines = [
            f"实时积存价 {price:.2f} | 赎回价 {q['redeem']:.2f}" if q["redeem"] else f"实时积存价 {price:.2f}",
            f"今日区间 {day_low:.2f} ~ {day_high:.2f} | {pos}",
        ]
        if extra:
            lines.append(extra)
        return "\n".join(lines)

    # ---- 规则 B：目标位（优先级高，先检查）----
    for idx, t in enumerate(config.get("targets", [])):
        if idx in state["triggered_targets"]:
            continue
        tp = float(t["price"])
        direction = t.get("direction", "above")
        hit = (direction == "above" and price >= tp) or (direction == "below" and price <= tp)
        if hit:
            note = t.get("note", "")
            title = f"🎯 触及目标价 {tp:.2f}" + (f"（{note}）" if note else "")
            notifier.send(title, body_lines(f"方向: {'≥' if direction == 'above' else '≤'} {tp:.2f}"))
            state["triggered_targets"].append(idx)

    # ---- 规则 A：突破今日极值（带最小步长 + 冷却）----
    bo = config.get("breakout", {})
    min_step = float(bo.get("min_step", 0.3))
    cooldown = float(bo.get("cooldown_sec", 600))
    enabled = bo.get("enabled", True)

    if enabled:
        prev_high = state["session_high"]
        prev_low = state["session_low"]
        if price > prev_high + min_step and now - state["last_high_alert_at"] >= cooldown:
            notifier.send(
                f"📈 今日新高 {price:.2f}",
                body_lines(f"较前高 {prev_high:.2f} 突破 +{price - prev_high:.2f}"),
            )
            state["last_high_alert_at"] = now
        if price < prev_low - min_step and now - state["last_low_alert_at"] >= cooldown:
            notifier.send(
                f"📉 今日新低 {price:.2f}",
                body_lines(f"较前低 {prev_low:.2f} 跌破 -{prev_low - price:.2f}"),
            )
            state["last_low_alert_at"] = now

    # 会话极值跟随现价更新（下一轮的突破基准）
    state["session_high"] = max(state["session_high"], price)
    state["session_low"] = min(state["session_low"], price)
    return state


def run_once(client_kwargs: dict, url: str, config: dict, notifier, state_path: str) -> bool:
    """拉取一次并执行规则；非交易时段/解析失败返回 False。供主循环与 --once 复用。"""
    with httpx.Client(**client_kwargs) as client:
        rows, _ = quote.fetch_once(client, url)
    q = extract_quote(rows)
    if q is None:
        return False

    today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    state = load_state(state_path)
    if state is None or state.get("date") != today:
        state = fresh_state(today, q["price"], q["page_low"], q["page_high"])

    state = check_rules(q, state, config, notifier, time.time())
    save_state(state_path, state)
    return True


def run_loop(url: str, config: dict, notifier, state_path: str, interval: int, trust_env: bool) -> None:
    client_kwargs = quote._client_kwargs(trust_env)
    while True:
        now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            ok = run_once(client_kwargs, url, config, notifier, state_path)
            if not ok:
                print(f"[{now}] 非交易时段或未解析到行情，跳过。", file=sys.stderr)
        except httpx.HTTPError as e:
            print(f"[{now}] HTTP 错误: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[{now}] 异常: {e}", file=sys.stderr)
        time.sleep(interval)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="积存金 24h 监控：新高/新低突破 + 目标位，Bark 推送。")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径（默认项目下 config.json）")
    p.add_argument("--state", default=DEFAULT_STATE, help="状态文件路径（默认项目下 monitor_state.json）")
    p.add_argument("--url", default=os.environ.get("ICBC_JCJM_URL", quote.DEFAULT_URL), help="行情页 URL")
    p.add_argument("--interval", type=int, default=None, help="轮询间隔秒数，覆盖配置文件的 interval_sec")
    p.add_argument("--once", action="store_true", help="只执行一轮（调试）")
    p.add_argument("--dry-run", action="store_true", help="不发推送，只打印将发送的通知")
    p.add_argument("--test-notify", action="store_true", help="发送一条测试推送后退出")
    p.add_argument("--no-proxy", action="store_true", help="忽略系统代理，直连银行站点")
    args = p.parse_args(argv)

    config = load_config(args.config)
    notifier = build_notifier(config, dry_run=args.dry_run)

    if args.test_notify:
        notifier.send(
            "积存金监控测试",
            "如果你看到这条推送，Bark 配置已生效。",
        )
        print("测试推送已发送。")
        return 0

    trust_env = quote.resolve_trust_env(args.no_proxy)
    interval = args.interval or int(config.get("interval_sec", 30))
    if interval < 10:
        print("间隔过短可能被服务端限制，建议 >= 10 秒。", file=sys.stderr)

    if args.once:
        ok = run_once(quote._client_kwargs(trust_env), args.url, config, notifier, args.state)
        print("已执行一轮。" if ok else "非交易时段或未解析到行情。")
        return 0 if ok else 1

    print(f"监控已启动：每 {interval} 秒拉取，按 Ctrl+C 结束。", file=sys.stderr)
    try:
        run_loop(args.url, config, notifier, args.state, interval, trust_env)
    except KeyboardInterrupt:
        print("\n已退出。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
