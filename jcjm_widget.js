// 工行积存金行情 — iOS 锁屏小组件（Scriptable）
//
// 安装：
//   1. iPhone 装 Scriptable（App Store 免费搜索 "Scriptable"）
//   2. 将本文件放到 iCloud Drive → Scriptable 目录（或 AirDrop 传到 iPhone）
//   3. 长按锁屏 → 自定义 → 添加小组件 → 选 Scriptable → 选本脚本
//
// 刷新：
//   系统自动 ~15 分钟一轮；可配 iOS 快捷指令自动化「屏幕点亮时」运行本脚本加速刷新
//
// 数据来自工行公开行情页，仅供查阅，以银行柜台为准

const URL = "https://mybank.icbc.com.cn/icbc/newperbank/perbank3/gold/goldaccrual_query_out.jsp";
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
const SELL_FEE = 0.005; // 卖出手续费 0.5%

// ─── HTML 解析 ───

function parseRows(tableHtml) {
  // 提取所有 <tr>，每行返回单元格文本数组
  const rows = [];
  const trRe = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
  let m;
  while ((m = trRe.exec(tableHtml)) !== null) {
    const cells = parseCells(m[1]);
    if (cells.length > 0) rows.push(cells);
  }
  return rows;
}

function parseCells(trHtml) {
  // 提取 <td>/<th> 内纯文本
  const cells = [];
  const tdRe = /<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi;
  let m;
  while ((m = tdRe.exec(trHtml)) !== null) {
    let t = m[1]
      .replace(/<[^>]*>/g, " ")
      .replace(/&nbsp;|&#160;|\xa0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    cells.push(t);
  }
  return cells;
}

function parseHtml(html) {
  // 选「单元格总数最多」的 <table> 作为行情表
  const tableRe = /<table[^>]*>([\s\S]*?)<\/table>/gi;
  let best = null;
  let bestCount = 0;
  let m;
  while ((m = tableRe.exec(html)) !== null) {
    const rows = parseRows(m[1]);
    if (rows.length >= 2 && rows[0].length >= 3) {
      const count = rows.length * rows[0].length;
      if (count > bestCount) {
        bestCount = count;
        best = rows;
      }
    }
  }

  if (!best) {
    if (/非交易|休市|停盘|暂停交易|交易时间外|不在交易|暂未开盘/.test(html)) {
      return { error: "非交易时段" };
    }
    return { error: "未解析到行情表" };
  }

  // 提取更新时间
  let updateTime = null;
  const tm = html.match(/更新\s*时间\s*[:：]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/);
  if (tm) updateTime = tm[1];

  // 找「积存金」行；找不到用第一条数据行
  let target = null;
  for (let i = 1; i < best.length; i++) {
    if (best[i][0] && best[i][0].includes("积存金")) {
      target = best[i];
      break;
    }
  }
  if (!target) target = best[1];

  // 按已知列结构提取（表头顺序固定）：
  // [0]金种 [1]实时积存价 [2]涨跌 [3]最低价 [4]最高价 [5]定期积存价 ...
  const live = cleanVal(target[1]);
  const low = cleanVal(target[3]);
  const high = cleanVal(target[4]);

  // 卖出后金额 = 实时积存价 × (1 - 0.5%)
  let sell = null;
  if (live) {
    sell = (parseFloat(live.replace(/,/g, "")) * (1 - SELL_FEE)).toFixed(2);
  }

  return { live, low, high, sell, updateTime };
}

function cleanVal(s) {
  if (!s) return null;
  s = s.trim();
  if (s === "" || s === "—" || s === "---" || s === "----") return null;
  return s;
}

// ─── 网络请求 ───

async function fetchQuote() {
  const req = new Request(URL);
  req.headers = { "User-Agent": UA, "Connection": "close" };
  req.timeout = 30;
  req.method = "GET";

  // 响应头 Content-Type: text/html;charset=GBK
  // iOS NSURLSession 会按 charset 自动解码
  const html = await req.loadString();
  return parseHtml(html);
}

// ─── 小组件渲染 ───

function createWidget(data) {
  const w = new ListWidget();

  // 错误或非交易时段
  if (data.error) {
    w.backgroundColor = new Color("#1a1a1a");
    const errText = w.addText(data.error);
    errText.font = Font.systemFont(12);
    errText.textColor = Color.gray();
    const subText = w.addText("开盘后自动恢复");
    subText.font = Font.systemFont(9);
    subText.textColor = Color.gray();
    return w;
  }

  // 暗色背景（锁屏美观）
  w.backgroundColor = new Color("#1a1a1a");
  w.setPadding(8, 10, 8, 10);

  // 标题
  const title = w.addText("积存金");
  title.font = Font.boldSystemFont(10);
  title.textColor = new Color("#FFD60A");

  w.addSpacer(2);

  // 实时价（大字、金色）
  if (data.live) {
    const liveRow = w.addText(data.live);
    liveRow.font = Font.semiboldSystemFont(18);
    liveRow.textColor = new Color("#FFD60A");
  }

  // 卖出后金额（突出显示）
  if (data.sell) {
    const sellRow = w.addText("卖 " + data.sell);
    sellRow.font = Font.semiboldSystemFont(13);
    sellRow.textColor = new Color("#34C759"); // 绿色
  }

  w.addSpacer(2);

  // 最低/最高（小字并排）
  const rangeRow = w.addStack();
  if (data.low) {
    const lowText = rangeRow.addText("低 " + data.low);
    lowText.font = Font.systemFont(10);
    lowText.textColor = new Color("#FF9500");
  }
  rangeRow.addSpacer();
  if (data.high) {
    const highText = rangeRow.addText("高 " + data.high);
    highText.font = Font.systemFont(10);
    highText.textColor = new Color("#FF3B30");
  }

  // 更新时间（最小字）
  if (data.updateTime) {
    w.addSpacer(2);
    const timeText = w.addText(data.updateTime.slice(11)); // 只显示时间部分
    timeText.font = Font.systemFont(8);
    timeText.textColor = Color.gray();
  }

  return w;
}

// ─── 入口 ───

async function main() {
  let data;
  try {
    data = await fetchQuote();
  } catch (e) {
    data = { error: "网络错误: " + String(e).slice(0, 40) };
  }

  if (config.runsInWidget) {
    // 小组件模式：渲染并设置
    const widget = createWidget(data);
    Script.setWidget(widget);
  } else {
    // 预览模式（在 Scriptable App 内运行时弹窗预览）
    const widget = createWidget(data);
    await widget.presentMedium();
  }

  Script.complete();
}

main();
