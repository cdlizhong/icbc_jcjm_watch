# 工行积存金行情

从工商银行官网公开页面抓取积存金实时行情，提供 **终端 CLI**、**macOS 菜单栏 App**、**iOS 锁屏小组件** 和 **24h Bark 推送监控** 四种使用方式。全程本地运行，无需后端服务器。

## 功能

- 实时积存价、最低价、最高价
- 卖出后金额（实时价 × 0.995，扣除 0.5% 手续费）
- 非交易时段自动识别，不报错
- 支持代理开关（`--no-proxy` 直连银行站点）
- **Bark 推送**：突破今日新高/新低、触及目标价时推送 iPhone，支持多设备

## 文件说明

| 文件 | 用途 |
|---|---|
| `jcjm_watch.py` | 核心库 + 终端 CLI（单次/循环刷新） |
| `menubar_app.py` | macOS 菜单栏 App（rumps + 后台守护线程 + 价格提醒） |
| `monitor.py` | 24h 监控：新高/新低突破 + 目标位，Bark 推送 |
| `notify.py` | 通知渠道（Bark，支持多设备；DryRun 调试） |
| `jcjm_widget.js` | iOS 锁屏小组件（Scriptable 脚本） |
| `jcjm_menubar.spec` | PyInstaller 打包配置 |
| `entitlements.plist` | macOS ad-hoc 签名运行时授权 |
| `config.json` | 监控配置（Bark 设备、目标价、突破参数） |
| `run.sh` | 便捷启动脚本 |

## 使用方式

### 1. 终端 CLI

```bash
# 单次拉取
python jcjm_watch.py --once

# 循环刷新（默认每 60 秒）
python jcjm_watch.py

# 忽略系统代理直连
python jcjm_watch.py --no-proxy

# 自定义间隔
python jcjm_watch.py --interval 30
```

环境变量：`ICBC_JCJM_URL`（行情页 URL）、`ICBC_JCJM_INTERVAL`（刷新秒数）、`ICBC_JCJM_NO_PROXY`（忽略代理）。

### 2. macOS 菜单栏 App

**源码运行：**

```bash
python menubar_app.py
```

**打包为 .app：**

```bash
pip install pyinstaller rumps pyobjc httpx beautifulsoup4 rich
pyinstaller jcjm_menubar.spec --noconfirm
```

产物在 `dist/积存金行情.app`，双击即可运行。菜单栏显示：`金 xxx.xx 低xxx.xx 高xxx.xx 卖xxx.xx`

下拉菜单可查看各产品全部列明细、切换刷新间隔（30/60/120/300 秒）、开关代理直连、在浏览器打开行情源页。

**价格提醒**：菜单「价格提醒」里可设置低价/高价阈值，价格穿越阈值时弹系统通知并在菜单栏闪烁 🔔。配置持久化在 `~/Library/Application Support/积存金行情/alerts.json`。

> 未做苹果开发者签名，仅本机可用。拷给其他 Mac 需对方右键 → 打开。

### 3. iOS 锁屏小组件

1. App Store 安装 **Scriptable**（免费）
2. 将 `jcjm_widget.js` 放到 iCloud Drive → Scriptable 目录（或 AirDrop 传到 iPhone）
3. 锁屏长按 → 自定义 → 添加小组件 → 选 Scriptable → 选 `jcjm_widget`
4. 选矩形样式

小组件显示：积存金实时价（金色）、卖出后金额（绿色）、最低价（橙色）、最高价（红色）、更新时间。

> iOS 小组件刷新由系统调度（约 5-15 分钟），点击组件可立即刷新。

### 4. 24h Bark 推送监控

后台常驻进程，跟踪今日最高/最低价，按规则推送 Bark 通知到 iPhone/iPad：

- **规则 A · 突破提醒**：现价创出今日新高/新低（带最小步长过滤与冷却时间，防抖）
- **规则 B · 目标位提醒**：触及 config.json 中 targets 设定的价格
- 每条通知附带现价在今日区间中的位置

```bash
# 循环监控（每 30 秒）
python monitor.py

# 只执行一轮（调试）
python monitor.py --once

# 不发推送，只打印将发送的通知
python monitor.py --once --dry-run

# 发送一条测试推送，验证 Bark 配置
python monitor.py --test-notify
```

**配置**（`config.json`，已在 .gitignore 中）：

```json
{
  "bark": {
    "devices": [
      { "name": "iPhone", "key": "你的Bark Key", "enabled": true },
      { "name": "iPad",   "key": "第二台设备的 Key", "enabled": false, "sound": "birdsong" }
    ],
    "server": "https://api.day.app",
    "group": "积存金",
    "sound": "bell"
  },
  "interval_sec": 30,
  "breakout": { "enabled": true, "min_step": 0.3, "cooldown_sec": 600 },
  "targets": [
    { "price": 950.0, "direction": "above", "note": "卖出参考" },
    { "price": 900.0, "direction": "below", "note": "买入参考" }
  ]
}
```

- **多设备**：`devices` 数组里每台设备可独立设置 `name` / `key` / `server` / `sound` / `enabled`；顶层字段作为默认值，设备内同名字段可覆盖。单台设备推送失败不影响其他设备
- 兼容旧写法：`bark.key`（单 key）或 `bark.keys`（key 数组）自动识别
- 状态持久化在 `monitor_state.json`，进程重启不丢失当日高低点与已触发目标

## 依赖

```
httpx
beautifulsoup4
rich
rumps
pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz
pyinstaller  # 仅打包时需要
```

## 数据来源

工商银行个人网银公开行情页：

```
https://mybank.icbc.com.cn/icbc/newperbank/perbank3/gold/goldaccrual_query_out.jsp
```

页面编码为 GBK，程序会自动三级容错解码（gb18030 → gbk → utf-8）。

## 免责声明

数据来自工行公开行情页，仅供查阅参考，以银行柜台为准。本程序不存储任何用户数据，不涉及登录或敏感操作。
