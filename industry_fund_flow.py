import subprocess, json, re, os, sys, time, warnings, argparse
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
warnings.filterwarnings("ignore")

import sys as _sys
if getattr(_sys, 'frozen', False):
    CUR_DIR = os.path.dirname(os.path.abspath(_sys.executable))
else:
    CUR_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== curl 请求封装 ====================
def curl_get(url, timeout=10):
    """使用 curl.exe 发送 HTTP 请求"""
    try:
        result = subprocess.run(
            ["curl.exe", "-s", "--connect-timeout", str(timeout), url],
            capture_output=True, timeout=timeout+5
        )
        if result.stdout:
            text = result.stdout.decode("utf-8", errors="replace").strip()
            text = re.sub(r"^(?:jQuery\d+|\w+)\(|\);?\s*$", "", text)
            return json.loads(text)
    except:
        pass
    return None

# ==================== 配置 ====================
KLT = 1                     # 1分钟K线
SECTOR_LIMIT = 100
REQUEST_DELAY = 0.3
WHITELIST_FILE = os.path.join(CUR_DIR, "行业板块白名单.txt")
BLACKLIST_FILE = os.path.join(CUR_DIR, "行业板块黑名单.txt")
NUMBER = 5         # ???????N???

# ==================== 工具函数 ====================
def setup_font():
    """设置中文字体"""
    candidates = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei",
                  "Noto Sans CJK SC", "PingFang SC", "STHeiti"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for n in candidates:
        if n in available:
            plt.rcParams["font.sans-serif"] = [n, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return n
    return None

def get_trading_minutes():
    """计算从开盘到现在的交易分钟数"""
    now = datetime.now()
    d = now.date()
    am_start = datetime(d.year, d.month, d.day, 9, 30)
    am_end   = datetime(d.year, d.month, d.day, 11, 30)
    pm_start = datetime(d.year, d.month, d.day, 13, 0)
    pm_end   = datetime(d.year, d.month, d.day, 15, 0)
    if now < am_start: return 0
    if now <= am_end: return int((now - am_start).seconds / 60)
    if now < pm_start: return 120
    if now <= pm_end: return 120 + int((now - pm_start).seconds / 60)
    return 240

def load_list(filepath):
    if not os.path.exists(filepath): return set()
    with open(filepath, "r", encoding="utf-8-sig") as f:
        return {line.strip() for line in f if line.strip() and not line.strip().startswith("#")}

def save_templates():
    for f, label, example in [
        (WHITELIST_FILE, "白名单", "MiniLED"),
        (BLACKLIST_FILE, "黑名单", "小金属"),
    ]:
        if not os.path.exists(f):
            with open(f, "w", encoding="utf-8") as fp:
                fp.write(f"# {label} - 填入板块名称，每行一个\n")
                fp.write(f"# 示例:\n# {example}\n")
            print(f"  Created: {os.path.basename(f)}")

# ==================== 数据获取 ====================
def get_sectors_ranked(po, api_filter):
    all_sectors = []
    page = 1
    page_size = 100
    max_pages = 10
    
    # 优先使用新接口 dataapi，因为它在盘后支持真正的正反双向排序
    timestamp = int(time.time() * 1000)
    sort_param = "f62" if po == 1 else "-f62"
    url = ("https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&"
           "pn=%d&pz=%d&st=%s&np=1&fields=f12,f14,f62&%s&_=%d" % (page, page_size, sort_param, api_filter, timestamp))
    
    data = curl_get(url)
    
    # 如果新接口在盘后返回空包(null)或者失败了，再走旧接口
    if not data or not data.get("data") or not data.get("data", {}).get("diff"):
        old_filter = api_filter.replace("code=", "fs=")
        # 注意：这里如果请求流出(po=0)，旧接口由于盘后Bug也会返回流入数据
        url2 = ("http://push2.eastmoney.com/api/qt/clist/get?cb=&fid=f62"
                "&pz=%d&pn=%d&fltt=2&po=%d&%s&fields=f12,f14,f62" % (page_size, page, po, old_filter))
        data = curl_get(url2)    
        
    if data and data.get("rc") == 0:
        res_data = data.get("data", {})
        diff = res_data.get("diff", {})
        if diff:
            if isinstance(diff, list):
                all_sectors = [(item["f12"], item["f14"].strip(), item.get("f62", 0)) for item in diff if "f12" in item]
            else:
                keys = sorted(diff.keys(), key=int)
                for k in keys:
                    all_sectors.append((diff[k]["f12"], diff[k]["f14"].strip(), diff[k].get("f62", 0)))

    # ==================== 🛠️ 【本地兜底核心防护】 ====================
    # 如果用户请求的是流出排名(po=0)，但东财不听话，返回的第一名金额居然是个巨大的正数(>0)
    # 这说明接口触发了盘后Bug，返回了重复的流入数据。我们直接用 Python 在本地对大名单进行强行重排！
    if len(all_sectors) > 0:
        if po == 0 and all_sectors[0][2] > 0:
            # 按资金金额从小到大（最惨的排最前面）进行本地强制重排
            all_sectors.sort(key=lambda x: x[2], reverse=False)
        elif po == 1 and all_sectors[0][2] < 0:
            # 顺便保护流入：如果是流入但全返回了负数，按从大到小排
            all_sectors.sort(key=lambda x: x[2], reverse=True)

    # 打印监控
    direction_title = "【 🔴 主力净流入 (多到少) 排名监控 】" if po == 1 else "【 🔵 主力净流出 (少到多) 排名监控 】"
    print("\n" + "="*60)
    print(direction_title)
    print("-"*60)
    if all_sectors:
        for idx, (code, name, val) in enumerate(all_sectors[:12], 1):
            val_in_yi = val / 1e8
            padded_name = name.ljust(8, ' ')[:8]
            print(f"  {idx:02d}   | {code:<8} | {padded_name} | {val_in_yi:+.2f} 亿")
    else:
        print(" ❌ 未获取到数据")
    print("="*60 + "\n")
    
    return all_sectors

import urllib.request

def get_sector_flow(sec_code, kline_count):
    """获取单个板块的分时资金流向数据（1分钟K线）"""
    lmt = max(kline_count, 20)
    url = ("http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?cb="
           f"&secid=90.{sec_code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt={KLT}&lmt={lmt}")
    data = curl_get(url, timeout=8)
    if data and data.get("rc") == 0:
        klines = data.get("data", {}).get("klines", [])
        if klines:
            result = []
            for kl in klines:
                p = kl.split(",")
                if len(p) >= 2:
                    try:
                        result.append((p[0], float(p[1])))  # (时间, 主力净流入)
                    except:
                        continue
            if result: return result
    return None

# ==================== 绘图 ====================
def plot_chart(top10_in, top10_out, forced_sectors, all_data, output):
    font_name = setup_font()
    fig = plt.figure(figsize=(24, 10))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.05, 0.10, 0.58, 0.82])
    ax.set_facecolor("white")

    # 暖色系（流入）
    in_colors  = ["#D32F2F","#E53935","#FF5722","#F57C00","#FFA000",
                  "#F9A825","#E91E63","#FF6F00","#D84315","#AD1457"]
    # 冷色系（流出）
    out_colors = ["#1565C0","#1976D2","#0288D1","#0097A7","#00796B",
                  "#2E7D32","#6A1B9A","#4527A0","#00838F","#37474F"]
    wh_color = "#333333"

    all_times = set()
    parsed = {}
    for code, name, _ in top10_in + top10_out + forced_sectors:
        pts = []
        for t, v in all_data.get("90." + code, []):
            try:
                dt = datetime.strptime(t, "%Y-%m-%d %H:%M")
                pts.append((dt, v))
                all_times.add(dt)
            except:
                pass
        parsed[code + "|" + name] = pts

    if not all_times:
        return False

    time_list = sorted(all_times)
    label_step = max(len(time_list) // 12, 1)
    tick_positions = list(range(len(time_list)))

    # 绘制流入曲线（暖色、细实线）
    for idx, (code, name, _) in enumerate(top10_in):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        c = in_colors[idx % len(in_colors)]
        ax.plot(tick_positions, vals, color=c, lw=0.8, alpha=0.8, marker="o", ms=1.2, mfc=c, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v),
                        xytext=(5, 0), textcoords="offset points",
                        fontsize=6.5, color=c, fontweight="bold", va="center", ha="left")

    # 绘制流出曲线（冷色、细虚线）
    for idx, (code, name, _) in enumerate(top10_out):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        c = out_colors[idx % len(out_colors)]
        ax.plot(tick_positions, vals, color=c, lw=0.7, alpha=0.75, ls="--", marker="s", ms=1.0, mfc=c, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v),
                        xytext=(5, 0), textcoords="offset points",
                        fontsize=6.5, color=c, fontweight="bold", va="center", ha="left")

    # 绘制白名单（黑色、点划线）
    for idx, (code, name, _) in enumerate(forced_sectors):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        ax.plot(tick_positions, vals, color=wh_color, lw=1.0, alpha=0.85, ls="-.", marker="D", ms=1.2, mfc=wh_color, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"★{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v),
                        xytext=(5, 0), textcoords="offset points",
                        fontsize=6.5, color=wh_color, fontweight="bold", va="center", ha="left")

    # 轴格式
    ax.set_xticks(tick_positions)
    labels = []
    for i, t in enumerate(time_list):
        labels.append(t.strftime("%H:%M") if i % label_step == 0 or i == len(time_list) - 1 else "")
    ax.set_xticklabels(labels, rotation=30, fontsize=9, color="#333")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: "{:.0f}亿".format(x / 1e8)))
    ax.tick_params(axis="y", colors="#333", labelsize=9)
    ax.grid(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#ccc")
    ax.spines["bottom"].set_color("#ccc")

    ns = datetime.now().strftime("%Y-%m-%d %H:%M")
    ax.set_xlabel("时间", fontsize=13, color="#333")
    ax.set_ylabel("主力净流入", fontsize=13, color="#333")
    ax.set_title(f"A股行业板块主力资金流向 TOP{NUMBER}\n{ns}  |  数据来源: 东方财富",
                 fontsize=14, color="#222", pad=10, fontweight="bold")

    from matplotlib.lines import Line2D
    leg = [
        Line2D([0],[0],color="#D32F2F",lw=0.8,label="流入前十"),
        Line2D([0],[0],color="#1565C0",lw=0.7,ls="--",label="流出前十"),
        Line2D([0],[0],color="#333333",lw=1.0,ls="-.",label="白名单"),
    ]
    ax.legend(handles=leg, loc="lower center",
              bbox_to_anchor=(0.5, -0.08), ncol=3,
              fontsize=10, frameon=False)

    # === 右侧汇总表 ===
    tax = fig.add_axes([0.65, 0.12, 0.08, 0.78])
    tax.axis("off")

    cell_text = []
    cell_colors = []

    def add_row(l, r, bg):
        cell_text.append([l, r])
        cell_colors.append([bg, bg])

    add_row("板块", "净流入(亿)", "#E0E0E0")
    add_row("", "", "#FAFAFA")
    add_row(f"—— 流入 TOP{NUMBER} ——", "", "#FFEBEE")

    for code, name, _ in top10_in:
        pts = parsed.get(code + "|" + name, [])
        if pts:
            v = dict(pts).get(time_list[-1])
            add_row(name, f"{v/1e8:+.2f}" if v is not None else "N/A", "#FFF5F5")
        else:
            add_row(name, "N/A", "#FFF5F5")

    add_row("", "", "#FAFAFA")
    add_row(f"—— 流出 TOP{NUMBER} ——", "", "#E3F2FD")

    for code, name, _ in top10_out:
        pts = parsed.get(code + "|" + name, [])
        if pts:
            v = dict(pts).get(time_list[-1])
            add_row(name, f"{v/1e8:+.2f}" if v is not None else "N/A", "#F0F5FF")
        else:
            add_row(name, "N/A", "#F0F5FF")

    if forced_sectors:
        add_row("", "", "#FAFAFA")
        add_row("—— 白名单 ——", "", "#F3E5F5")
        for code, name, _ in forced_sectors:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                add_row(f"★{name}", f"{v/1e8:+.2f}" if v is not None else "N/A", "#F5F0FF")
            else:
                add_row(f"★{name}", "N/A", "#F5F0FF")

    nrows = len(cell_text)
    tbl = tax.table(cellText=cell_text, cellColours=cell_colors,
                    colWidths=[0.3, 0.1],
                    cellLoc="left",
                    bbox=[0, 0, 1, 1])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)

    for i in range(nrows):
        for j in range(2):
            cell = tbl[i, j]
            cell.set_edgecolor("#DDD")
            cell.set_linewidth(0.3)
            if j == 1:
                cell.set_text_props(ha="right", fontweight="bold")
        # 表头加粗
    for j in range(2):
        tbl[0, j].set_text_props(fontweight="bold", fontsize=8)

    # 金额颜色：流入标红、流出标蓝
    ri = 4
    for code, name, _ in top10_in:
        if ri < nrows:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                if v is not None:
                    tbl[ri, 1].get_text().set_color("#D32F2F" if v >= 0 else "#C0392B")
            ri += 1

    ri += 2
    for code, name, _ in top10_out:
        if ri < nrows:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                if v is not None:
                    tbl[ri, 1].get_text().set_color("#1565C0")
            ri += 1

    plt.savefig(output, dpi=200, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print("  图表已保存:", output)
    return True
# ==================== 主流程 ====================
def main():
    print("=" * 60)
    print("  A股行业板块主力资金流向图")
    print("  数据来源: 东方财富 | 白名单/黑名单支持")
    print("=" * 60)
    save_templates()

    now = datetime.now()
    trading_min = get_trading_minutes()
    if trading_min == 0:
        # 非交易时段：取全天240分钟数据
        kline_count = 240
        status = "非交易时段（获取全天数据）"
    else:
        kline_count = trading_min
        status = f"已交易 {trading_min}分钟"

    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}  | {status}  | K线数: {kline_count}")

    whitelist = load_list(WHITELIST_FILE)
    blacklist = load_list(BLACKLIST_FILE)
    if whitelist: print(f"  白名单: {', '.join(sorted(whitelist))}")
    if blacklist: print(f"  黑名单: {', '.join(sorted(blacklist))}")

    # ========== [1/3] 获取板块排名 ==========
    print("\n[1/3] 获取板块排名...")

    # 流入榜: po=1 (降序)
    inflow_sectors = get_sectors_ranked(po=1)
    if not inflow_sectors:
        print("  FAILED: 无法获取数据")
        print("  >> 东方财富反爬机制：短时间内请求过多会被临时封IP")
        print("  >> 解决方案：① 等3-5分钟再运行 ② 切换网络/开代理换IP")
        sys.exit(1)
    print(f"  获取到 {len(inflow_sectors)} 个行业板块")
    # 导出全部板块名称到txt（供黑白名单参考）
    all_names_path = os.path.join(CUR_DIR, "industry_all.txt")
    with open(all_names_path, "w", encoding="utf-8") as f:
        for code, name, flow in inflow_sectors:
            f.write(f"{name}\n")
    print(f"  板块列表已导出: {os.path.basename(all_names_path)}  ({len(inflow_sectors)}个)")

    # 流出榜: po=0 (升序) — 真正的主力净流出最大的板块排在最前
    outflow_sectors = get_sectors_ranked(po=0)
    if not outflow_sectors:
        print("  FAILED: 无法获取流出数据")
        print("  >> 建议等3-5分钟或切换网络后再试")
        sys.exit(1)

    # 应用黑名单过滤
    if blacklist:
        before = len(inflow_sectors)
        inflow_sectors = [(c, n, v) for c, n, v in inflow_sectors if n not in blacklist]
        outflow_sectors = [(c, n, v) for c, n, v in outflow_sectors if n not in blacklist]
        print(f"  黑名单已过滤: {before - len(inflow_sectors)} 个板块")

    top10_in = inflow_sectors[:NUMBER]
    top10_out = outflow_sectors[:NUMBER]  # 升序榜前10 = 流出最大的10个

    # 白名单额外板块
    forced = []
    if whitelist:
        already_names = {n for _, n, _ in top10_in} | {n for _, n, _ in top10_out}
        all_map = {n: (c, n, v) for c, n, v in inflow_sectors}
        for name in sorted(whitelist - already_names):
            if name in all_map:
                forced.append(all_map[name])
                print(f"  白名单追加: {name}")
            else:
                print(f"  白名单未找到: {name}")

    # 打印排行榜
    print(f"\n  流入 TOP {NUMBER}:")
    for i, (c, n, v) in enumerate(top10_in, 1):
        print(f"    {i:2d}. {n} ({c}): {v/1e8:+.2f}亿")
    print(f"\n  流出 TOP {NUMBER}:")
    for i, (c, n, v) in enumerate(top10_out, 1):
        print(f"    {i:2d}. {n} ({c}): {v/1e8:+.2f}亿")
    if forced:
        print("\n  白名单额外:")
        for i, (c, n, v) in enumerate(forced, 1):
            print(f"    {i:2d}. {n} ({c}): {v/1e8:+.2f}亿")

    # 合并去重
    target = top10_in + top10_out + forced
    seen = set()
    deduped = []
    for item in target:
        if item[0] not in seen:
            seen.add(item[0])
            deduped.append(item)
    target = deduped

    # ========== [2/3] 获取分时数据 ==========
    print(f"\n[2/3] 获取1分钟资金流向数据 ({len(target)}个板块)...")
    all_flow = {}
    ok = 0
    for i, (code, name, _) in enumerate(target, 1):
        print(f"  [{i:2d}/{len(target):2d}] {name} ({code})...", end=" ", flush=True)
        data = get_sector_flow(code, kline_count)
        if data:
            all_flow["90." + code] = data
            print(f"OK ({len(data)}个数据点)")
            ok += 1
        else:
            print("N/A")
        time.sleep(REQUEST_DELAY)
    print(f"  成功: {ok}/{len(target)}")
    if ok < len(target):
        skip = len(target) - ok
        print(f"  >> {skip}个板块未获取到数据，东方财富可能有反爬限制")
        print(f"  >> 下次运行建议等几分钟再试，或切换代理IP")

    # ========== [3/3] 绘图 ==========
    print("\n[3/3] 绘制图表...")
    out = os.path.join(CUR_DIR, f"行业板块_{now.strftime('%Y-%m-%d')}.png")
    if ok > 0:
        plot_chart(top10_in, top10_out, forced, all_flow, out)
    else:
        print("  没有获取到分时数据")
        # 至少保存排名文本
        txt = out.replace(".png", ".txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write(f"日期: {now.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"流入 TOP {NUMBER}:\n")
            for i, (c, n, v) in enumerate(top10_in, 1):
                f.write(f"  {i:2d}. {n} ({c}): {v/1e8:+.2f}亿\n")
            f.write(f"\n流出 TOP {NUMBER}:\n")
            for i, (c, n, v) in enumerate(top10_out, 1):
                f.write(f"  {i:2d}. {n} ({c}): {v/1e8:+.2f}亿\n")
            if forced:
                f.write("\n白名单额外:\n")
                for i, (c, n, v) in enumerate(forced, 1):
                    f.write(f"  {i:2d}. {n} ({c}): {v/1e8:+.2f}亿\n")
        print("  排名已保存:", txt)

    print("\n完成!")

if __name__ == "__main__":
    main()

