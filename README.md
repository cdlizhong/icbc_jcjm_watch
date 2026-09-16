# 工行积存金行情

从工商银行官网公开页面抓取积存金实时行情，提供 **终端 CLI**、**macOS 菜单栏 App** 和 **iOS 锁屏小组件** 三种使用方式。全程本地运行，无需后端服务器。

## 功能

- 实时积存价、最低价、最高价
- 卖出后金额（实时价 × 0.995，扣除 0.5% 手续费）
- 非交易时段自动识别，不报错
- 支持代理开关（`--no-proxy` 直连银行站点）

## 文件说明

| 文件 | 用途 |
|---|---|
| `jcjm_watch.py` | 核心库 + 终端 CLI（单次/循环刷新） |
| `menubar_app.py` | macOS 菜单栏 App（rumps + 后台守护线程） |
| `jcjm_widget.js` | iOS 锁屏小组件（Scriptable 脚本） |
| `jcjm_menubar.spec` | PyInstaller 打包配置 |
| `entitlements.plist` | macOS ad-hoc 签名运行时授权 |
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

> 未做苹果开发者签名，仅本机可用。拷给其他 Mac 需对方右键 → 打开。

### 3. iOS 锁屏小组件

1. App Store 安装 **Scriptable**（免费）
2. 将 `jcjm_widget.js` 放到 iCloud Drive → Scriptable 目录（或 AirDrop 传到 iPhone）
3. 锁屏长按 → 自定义 → 添加小组件 → 选 Scriptable → 选 `jcjm_widget`
4. 选矩形样式

小组件显示：积存金实时价（金色）、卖出后金额（绿色）、最低价（橙色）、最高价（红色）、更新时间。

> iOS 小组件刷新由系统调度（约 5-15 分钟），点击组件可立即刷新。

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
