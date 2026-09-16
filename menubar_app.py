#!/usr/bin/env python3
"""
工行积存金行情 — macOS 菜单栏应用。

复用 jcjm_watch 的抓取/解码/表格解析；后台守护线程定时拉取（不阻塞 UI），
rumps.Timer 在主线程把最新结果刷新到顶部菜单栏：
  - 状态栏标题：积存金行选定列的价格（默认第一个数字列，可在菜单里切换）
  - 下拉菜单：各产品子菜单（列名: 值）、更新时间、立即刷新、间隔、代理、退出

打包见 jcjm_menubar.spec（LSUIElement=1，无 Dock 图标）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import httpx
import rumps

import jcjm_watch as jw

# 价格提醒配置文件路径
ALERT_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "积存金行情" / "alerts.json"


def _notify(title: str, subtitle: str, message: str, sound: str = "Glass") -> None:
    """用 osascript 发送系统通知（比 rumps.notification 在 macOS 12+ 更可靠）。"""
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'subtitle "{subtitle}" '
        f'sound name "{sound}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass

# 可选刷新间隔（秒）；与命令行工具一致，不建议低于 30 秒以免触发行方限流
INTERVAL_CHOICES = [30, 60, 120, 300]
DEFAULT_INTERVAL = 60

# 状态栏/菜单里错误文案最大长度，避免超长 URL 异常撑宽菜单
MAX_ERROR_IN_MENU = 60


class AlertManager:
    """价格提醒：管理低价/高价阈值，穿越时发系统通知，持久化到 JSON。"""

    def __init__(self) -> None:
        self._low: float | None = None
        self._high: float | None = None
        self._enabled: bool = True
        # 上次价格所处区间："below" / "between" / "above"；None 表示尚未初始化
        self._prev_zone: str | None = None
        self._load()

    # ---------- 属性 ----------

    @property
    def low(self) -> float | None:
        return self._low

    @property
    def high(self) -> float | None:
        return self._high

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_alerting(self) -> bool:
        """当前是否处于触发区间（低于低价或高于高价）。"""
        return self._prev_zone in ("below", "above")

    # ---------- 设置/清除 ----------

    def set_low(self, val: float | None) -> None:
        self._low = val
        self._prev_zone = None  # 重置状态，下次检查时若已在区间内则触发
        self._save()

    def set_high(self, val: float | None) -> None:
        self._high = val
        self._prev_zone = None
        self._save()

    def toggle(self) -> None:
        self._enabled = not self._enabled
        self._prev_zone = None
        self._save()

    def clear(self) -> None:
        self._low = None
        self._high = None
        self._prev_zone = None
        self._save()

    # ---------- 检查 ----------

    def check(self, live_price: float | None) -> None:
        if not self._enabled or live_price is None:
            return
        if self._low is None and self._high is None:
            return

        new_zone = "between"
        if self._low is not None and live_price <= self._low:
            new_zone = "below"
        elif self._high is not None and live_price >= self._high:
            new_zone = "above"

        if self._prev_zone is None:
            # 首次检查（启动或刚设置阈值）：已在触发区间则立即通知
            if new_zone == "below":
                _notify(
                    "积存金低价提醒",
                    f"当前价 {live_price:.2f}",
                    f"已低于 {self._low:.2f}，可考虑买入",
                )
            elif new_zone == "above":
                _notify(
                    "积存金高价提醒",
                    f"当前价 {live_price:.2f}",
                    f"已高于 {self._high:.2f}，可考虑卖出",
                )
        elif new_zone != self._prev_zone:
            # 区间变化才通知
            if new_zone == "below":
                _notify(
                    "积存金低价提醒",
                    f"当前价 {live_price:.2f}",
                    f"已跌破 {self._low:.2f}，可考虑买入",
                )
            elif new_zone == "above":
                _notify(
                    "积存金高价提醒",
                    f"当前价 {live_price:.2f}",
                    f"已突破 {self._high:.2f}，可考虑卖出",
                )
        self._prev_zone = new_zone

    # ---------- 持久化 ----------

    def _load(self) -> None:
        try:
            data = json.loads(ALERT_CONFIG_PATH.read_text("utf-8"))
            self._low = data.get("low")
            self._high = data.get("high")
            self._enabled = data.get("enabled", True)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass  # 首次运行或文件损坏，用默认值

    def _save(self) -> None:
        ALERT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERT_CONFIG_PATH.write_text(
            json.dumps(
                {"low": self._low, "high": self._high, "enabled": self._enabled},
                ensure_ascii=False,
            ),
            "utf-8",
        )


class QuoteStore:
    """线程间共享的最近一次拉取结果与配置；fetcher 写、UI 定时器读。"""

    def __init__(self, url: str, interval: int, trust_env: bool):
        self._lock = threading.Lock()
        self.url = url
        self.interval = interval
        self.trust_env = trust_env
        # 状态栏展示的列名（表头）；None 表示取第一个数字列
        self.bar_column: str | None = None
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.page_update: str | None = None
        self.fetched_at: str | None = None
        self.error: str | None = None
        # 每次成功/失败或配置变化自增，UI 据此决定是否重建菜单
        self.version = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "url": self.url,
                "interval": self.interval,
                "trust_env": self.trust_env,
                "bar_column": self.bar_column,
                "headers": list(self.headers),
                "rows": [list(r) for r in self.rows],
                "page_update": self.page_update,
                "fetched_at": self.fetched_at,
                "error": self.error,
                "version": self.version,
            }

    def set_result(
        self,
        rows: list[list[str]],
        page_update: str | None,
        fetched_at: str,
    ) -> None:
        with self._lock:
            norm = jw._normalize_rows(rows)
            self.headers = norm[0] if norm else []
            self.rows = norm[1:] if norm else []
            self.page_update = page_update
            self.fetched_at = fetched_at
            self.error = None
            self.version += 1

    def set_error(self, msg: str, fetched_at: str) -> None:
        with self._lock:
            self.error = msg
            self.fetched_at = fetched_at
            self.version += 1

    def touch(self) -> None:
        """配置变化后自增版本，触发菜单重建。"""
        with self._lock:
            self.version += 1


def _now_cn() -> str:
    return datetime.now(jw.CN_TZ).strftime("%m-%d %H:%M:%S")


def _find_jcjm_row(rows: list[list[str]]) -> list[str] | None:
    """优先定位首列含「积存金」的行；否则用第一条数据行。"""
    for r in rows:
        if r and "积存金" in (r[0] or ""):
            return r
    return rows[0] if rows else None


def _header_labels(headers: list[str]) -> list[str]:
    """重名表头加 (2)/(3) 后缀（行情表中「涨跌」会重复出现），便于菜单区分。"""
    raw = [(h or "").strip() or f"列{i}" for i, h in enumerate(headers)]
    total: dict[str, int] = {}
    for name in raw:
        total[name] = total.get(name, 0) + 1
    counts: dict[str, int] = {}
    final: list[str] = []
    for name in raw:
        counts[name] = counts.get(name, 0) + 1
        final.append(name if total[name] == 1 else f"{name}({counts[name]})")
    return final


def _bar_prices(store_snapshot: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """状态栏标题用四项：实时积存价、最低价、最高价、卖出后金额（扣 0.5% 手续费）。"""
    headers, rows = store_snapshot["headers"], store_snapshot["rows"]
    labels = _header_labels(headers)
    target = _find_jcjm_row(rows)
    if target is None:
        return None, None, None, None

    # 按关键词在表头中定位列索引（排除"定期"列和占位"涨跌"列）
    def _find_col(keyword: str, exclude: str = "定期") -> int | None:
        for i in range(1, len(labels)):
            lbl = labels[i]
            if keyword in lbl and exclude not in lbl and i < len(target):
                v = target[i]
                if v and v not in ("", "—"):
                    return i
        return None

    live_idx = _find_col("实时") or _find_col("积存")
    low_idx = _find_col("最低")
    high_idx = _find_col("最高")

    def _val(idx: int | None) -> str | None:
        if idx is None or idx >= len(target):
            return None
        v = target[idx]
        return None if v in ("", "—") else v

    live_val = _val(live_idx)

    # 卖出后金额 = 实时积存价 × (1 - 0.5%)
    sell_after_fee: str | None = None
    if live_val is not None:
        try:
            sell_after_fee = f"{float(live_val.replace(',', '')) * 0.995:.2f}"
        except ValueError:
            pass

    return live_val, _val(low_idx), _val(high_idx), sell_after_fee


def fetch_worker(store: QuoteStore, wake: threading.Event) -> None:
    """守护线程：立即拉一次，之后按间隔循环；wake 可提前唤醒（手动刷新/设置变更）。"""
    while True:
        trust_env = store.trust_env
        try:
            with httpx.Client(**jw._client_kwargs(trust_env)) as client:
                rows, page_ts = jw.fetch_once(client, store.url)
            store.set_result(rows, page_ts, _now_cn())
        except Exception as e:  # 网络/解析失败不退出线程，保留上次成功数据
            store.set_error(str(e), _now_cn())
        # 等待期间被唤醒则立即进入下一轮（间隔或代理变更也走这里生效）
        wake.wait(timeout=store.interval)
        wake.clear()


class JCJMMenuBarApp(rumps.App):
    def __init__(self, store: QuoteStore, wake: threading.Event):
        super().__init__("积存金行情", title="金 …", quit_button=None)
        self.store = store
        self.wake = wake
        self.alerts = AlertManager()
        self._last_version = -1
        self._base_title = "金 …"
        self._flash_on = False
        # 每 2 秒在主线程检查一次：仅在数据版本变化时重建菜单/标题
        self._timer = rumps.Timer(self._on_ui_tick, 2)
        self._timer.start()
        # 闪烁计时器：提醒中每 0.8 秒切换 🔔 显示/隐藏
        self._flash_timer = rumps.Timer(self._on_flash_tick, 0.8)
        self._flash_timer.start()

    # ---------- UI 构建 ----------

    def _on_ui_tick(self, _sender) -> None:
        snap = self.store.snapshot()
        if snap["version"] == self._last_version:
            return
        self._last_version = snap["version"]
        live, low, high, sell = _bar_prices(snap)
        if live is None and low is None and high is None and sell is None:
            self._base_title = "金 --"
        else:
            parts = []
            if live is not None:
                parts.append(live)
            if low is not None:
                parts.append(f"低{low}")
            if high is not None:
                parts.append(f"高{high}")
            if sell is not None:
                parts.append(f"卖{sell}")
            self._base_title = "金 " + " ".join(parts)
        # 价格提醒检查（仅在拿到实时价时）
        if live is not None:
            try:
                self.alerts.check(float(live.replace(",", "")))
            except ValueError:
                pass
        # 标题：提醒中由闪烁计时器接管，否则直接显示
        if self.alerts.is_alerting:
            self._flash_on = True
            self.title = "🔔 " + self._base_title
        else:
            self._flash_on = False
            self.title = self._base_title
        # rumps 的 menu setter 只做增量 update，重建前必须清空，否则菜单项会累积
        self.menu.clear()
        self.menu = self._build_menu(snap)

    def _on_flash_tick(self, _sender) -> None:
        """提醒中时每 0.8 秒切换 🔔 显示/隐藏，形成闪烁效果。"""
        if self.alerts.is_alerting:
            self._flash_on = not self._flash_on
            if self._flash_on:
                self.title = "🔔 " + self._base_title
            else:
                self.title = self._base_title
        elif self._flash_on:
            self._flash_on = False
            self.title = self._base_title

    def _build_menu(self, snap: dict) -> list:
        headers, rows = snap["headers"], snap["rows"]
        labels = _header_labels(headers)
        items: list = []

        if snap["error"]:
            err = snap["error"]
            if len(err) > MAX_ERROR_IN_MENU:
                err = err[: MAX_ERROR_IN_MENU - 1] + "…"
            items.append(rumps.MenuItem(f"⚠ 上次拉取失败：{err}"))
        items.append(rumps.MenuItem(f"页面更新：{snap['page_update'] or '—'}"))
        items.append(rumps.MenuItem(f"本地拉取：{snap['fetched_at'] or '—'}"))
        items.append(None)

        if rows:
            for r in rows:
                name = r[0] or "(未命名)"
                sub = [
                    rumps.MenuItem(f"{labels[i] if i < len(labels) else f'列{i}'}：{r[i] if i < len(r) else '—'}")
                    for i in range(1, len(headers))
                ]
                # rumps 0.4：(父菜单项, 子项可迭代对象) 二元组即构成子菜单
                items.append((rumps.MenuItem(name), sub or ["—"]))
        else:
            items.append(rumps.MenuItem("暂无行情数据"))

        items.append(None)

        # 刷新间隔
        interval_sub: list = []
        for sec in INTERVAL_CHOICES:
            mi = rumps.MenuItem(f"{sec} 秒", callback=self._make_interval_callback(sec))
            if sec == snap["interval"]:
                mi.state = 1
            interval_sub.append(mi)
        items.append((rumps.MenuItem("刷新间隔"), interval_sub))

        # 代理开关
        proxy_mi = rumps.MenuItem("忽略系统代理（直连）", callback=self._toggle_proxy)
        proxy_mi.state = 0 if snap["trust_env"] else 1
        items.append(proxy_mi)

        # 价格提醒子菜单
        alert_sub: list = []
        low_text = f"≤{self.alerts.low:.2f}" if self.alerts.low is not None else "未设置"
        high_text = f"≥{self.alerts.high:.2f}" if self.alerts.high is not None else "未设置"
        alert_sub.append(rumps.MenuItem(f"当前: 低{low_text}  高{high_text}"))
        alert_sub.append(None)
        alert_sub.append(rumps.MenuItem("设置低价提醒…", callback=self._set_low_alert))
        alert_sub.append(rumps.MenuItem("设置高价提醒…", callback=self._set_high_alert))
        alert_sub.append(None)
        toggle_alert = rumps.MenuItem("开启提醒", callback=self._toggle_alerts)
        toggle_alert.state = 1 if self.alerts.enabled else 0
        alert_sub.append(toggle_alert)
        alert_sub.append(rumps.MenuItem("清除所有提醒", callback=self._clear_alerts))
        items.append((rumps.MenuItem("价格提醒"), alert_sub))

        items.append(rumps.MenuItem("立即刷新", callback=self._refresh_now))
        items.append(rumps.MenuItem("在浏览器打开行情页", callback=self._open_source))
        items.append(None)
        items.append(rumps.MenuItem("退出积存金行情", callback=rumps.quit_application))
        return items

    # ---------- 菜单回调（主线程） ----------

    def _refresh_now(self, _sender) -> None:
        self.wake.set()

    def _make_interval_callback(self, sec: int):
        def cb(_sender):
            self.store.interval = sec
            self.store.touch()
            self.wake.set()  # 唤醒后按新间隔计时
        return cb

    def _toggle_proxy(self, _sender) -> None:
        self.store.trust_env = not self.store.trust_env
        self.store.touch()
        self.wake.set()  # 立即用新代理策略重试

    def _open_source(self, _sender) -> None:
        webbrowser.open(self.store.url)

    # ---------- 价格提醒回调 ----------

    def _set_low_alert(self, _sender) -> None:
        cur = str(self.alerts.low) if self.alerts.low is not None else ""
        resp = rumps.Window(
            message="输入低于此价格则提醒买入（留空清除）",
            title="设置低价提醒",
            default_text=cur,
            dimensions=(200, 30),
        ).run()
        if not resp.clicked:
            return
        text = resp.text.strip()
        if not text:
            self.alerts.set_low(None)
            self.store.touch()
            _notify("价格提醒", "", "低价提醒已清除")
            return
        try:
            self.alerts.set_low(float(text))
            self.store.touch()
            _notify("价格提醒", "", f"低价提醒已设置: ≤{text}")
        except ValueError:
            _notify("输入无效", "", "请输入数字")

    def _set_high_alert(self, _sender) -> None:
        cur = str(self.alerts.high) if self.alerts.high is not None else ""
        resp = rumps.Window(
            message="输入高于此价格则提醒卖出（留空清除）",
            title="设置高价提醒",
            default_text=cur,
            dimensions=(200, 30),
        ).run()
        if not resp.clicked:
            return
        text = resp.text.strip()
        if not text:
            self.alerts.set_high(None)
            self.store.touch()
            _notify("价格提醒", "", "高价提醒已清除")
            return
        try:
            self.alerts.set_high(float(text))
            self.store.touch()
            _notify("价格提醒", "", f"高价提醒已设置: ≥{text}")
        except ValueError:
            _notify("输入无效", "", "请输入数字")

    def _toggle_alerts(self, _sender) -> None:
        self.alerts.toggle()
        self.store.touch()

    def _clear_alerts(self, _sender) -> None:
        self.alerts.clear()
        self.store.touch()
        _notify("价格提醒", "", "所有提醒已清除")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="工行积存金行情 — macOS 菜单栏应用")
    p.add_argument("--url", default=os.environ.get("ICBC_JCJM_URL", jw.DEFAULT_URL), help="行情页 URL")
    p.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("ICBC_JCJM_INTERVAL", str(DEFAULT_INTERVAL))),
        help="刷新间隔（秒），默认 60，菜单内可切换",
    )
    p.add_argument("--no-proxy", action="store_true", help="忽略系统代理，直连银行站点")
    args = p.parse_args(argv)

    interval = args.interval
    if interval not in INTERVAL_CHOICES:
        # 允许任意 >=10 秒的自定义值；菜单勾选将落空但不影响运行
        interval = max(10, interval)

    store = QuoteStore(args.url, interval, jw.resolve_trust_env(args.no_proxy))
    wake = threading.Event()
    t = threading.Thread(target=fetch_worker, args=(store, wake), name="icbc-jcjm-fetch", daemon=True)
    t.start()

    JCJMMenuBarApp(store, wake).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
