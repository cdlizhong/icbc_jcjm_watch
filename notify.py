"""通知渠道抽象。当前实现 Bark（iOS），配置驱动，后续可扩展 PushPlus 等渠道。"""

from __future__ import annotations

import httpx


class BarkNotifier:
    """Bark 推送。server 可换成自建 Bark 服务器地址。"""

    def __init__(
        self,
        key: str,
        server: str = "https://api.day.app",
        group: str = "积存金",
        sound: str = "bell",
    ) -> None:
        if not key:
            raise ValueError("Bark key 为空，请先在 config.json 里填写 bark.key")
        self.server = server.rstrip("/")
        self.key = key
        self.group = group
        self.sound = sound

    def send(self, title: str, body: str, url: str | None = None) -> None:
        payload: dict = {
            "device_key": self.key,
            "title": title,
            "body": body,
            "group": self.group,
            "sound": self.sound,
        }
        if url:
            payload["url"] = url
        r = httpx.post(f"{self.server}/push", json=payload, timeout=10.0)
        r.raise_for_status()
        resp = r.json()
        if resp.get("code") != 200:
            raise RuntimeError(f"Bark 返回错误: {resp}")


class DryRunNotifier:
    """调试用：不真正推送，只打印到终端。"""

    def send(self, title: str, body: str, url: str | None = None) -> None:
        print(f"[DRY-RUN 通知] {title}\n{body}")
        if url:
            print(f"(跳转: {url})")


def build_notifier(config: dict, dry_run: bool = False):
    """按配置构造通知器；dry_run=True 时强制使用 DryRunNotifier。"""
    if dry_run:
        return DryRunNotifier()
    bark_cfg = config.get("bark", {})
    return BarkNotifier(
        key=bark_cfg.get("key", ""),
        server=bark_cfg.get("server", "https://api.day.app"),
        group=bark_cfg.get("group", "积存金"),
        sound=bark_cfg.get("sound", "bell"),
    )
