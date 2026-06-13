import subprocess, json, re, os, sys, time, warnings, threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
warnings.filterwarnings("ignore")

CUR_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# ==================== 模式配置 ====================
MODES = {
    "概念板块": {
        "api_filter": "code=m:90+t:3",  # <-- 这里 fs= 改为 code=
        "out_prefix": "概念板块",
        "all_file": "all_sectors.txt",
        "whitelist": "概念板块白名单.txt",
        "blacklist": "概念板块黑名单.txt",
        "title": "A股概念板块主力资金流向 TOP{NUMBER}\n{time}  |  数据来源: 东方财富",
    },
    "行业板块": {
        "api_filter": "code=m:90+t:2",  # <-- 这里 fs= 改为 code=
        "out_prefix": "行业板块",
        "all_file": "industry_all.txt",
        "whitelist": "行业板块白名单.txt",
        "blacklist": "行业板块黑名单.txt",
        "title": "A股行业板块主力资金流向 TOP{NUMBER}\n{time}  |  数据来源: 东方财富",
    },
}
# ==================== curl 请求 ====================
def curl_get(url, timeout=10):
    for attempt in (1, 2):
        try:
            result = subprocess.run(
                ["curl.exe", "-s", "--connect-timeout", str(timeout), url],
                capture_output=True, timeout=timeout+5, creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.stdout:
                text = result.stdout.decode("utf-8", errors="replace").strip()
                # ????JSONP?????????jQuery??????????
                if "(" in text and text.endswith(")"):
                    text = text[text.find("(")+1:text.rfind(")")]
                if text:
                    return json.loads(text)
            if result.returncode == 0 and not result.stdout:
                if attempt == 1:
                    time.sleep(3)
                    continue
        except:
            pass
        break
    return None

# ==================== 工具函数 ====================
def setup_font():
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

def get_sector_flow(sec_code, kline_count, mode_name="概念板块"):
    url = (f"http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
           f"cb=&lmt=0&klt=1"
           f"&fields1=f1%2Cf2%2Cf3%2Cf7"
           f"&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58%2Cf59%2Cf60%2Cf61%2Cf62%2Cf63%2Cf64%2Cf65"
           f"&ut=b2884a393a59ad64002292a3e90d46a5"
           f"&secid=90.{sec_code}")
           
    print(f"\n==================== [监控开始: {sec_code}] ====================")
    
    html = None
    try:
        # 抛弃 curl.exe，改用原生 urllib，并注入全套伪装
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                "Referer": f"https://data.eastmoney.com/bkzj/{sec_code}.html",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9"
            }
        )
        # 发送请求并设置 8 秒超时
        with urllib.request.urlopen(req, timeout=8) as response:
            html = response.read().decode("utf-8", errors="replace")
            print(f"🍏 [网络连接成功]: 原生请求拿到了数据！前50个字符为: {html[:50]}")
    except Exception as network_error:
        print(f"❌ [网络请求引发异常]: 无法连接到东财服务器，报错原因: {network_error}")
        print(f"==================== [监控结束: {sec_code}] ====================\n")
        return None

    # 开始解析文本
    if html:
        try:
            # 兼容处理：如果返回的内容被包裹在 jQuery 里面，剥离它
            if "(" in html and html.endswith(")"):
                html = html[html.find("(") + 1 : html.rfind(")")]
                
            data = json.loads(html)
        except Exception as json_error:
            print(f"❌ [JSON解析失败]: 拿到了文本但无法转成字典，格式不对。报错: {json_error}")
            return None
            
        if data and data.get("rc") == 0:
            klines = data.get("data", {}).get("klines", [])
            if klines:
                print(f"📊 [数量]: 成功提取出 {len(klines)} 条分时数据")
                result = []
                for kl in klines:
                    p = kl.split(",")
                    if len(p) >= 3:
                        try:
                            time_str = " ".join(p[0].split())
                            main_flow = float(p[1]) + float(p[2])
                            result.append((time_str, main_flow))
                        except:
                            continue
                
                if result and len(result) > kline_count:
                    result = result[-kline_count:]
                    
                if result:
                    print(f"🚀 [加工成功]: 加工后的前2个点为: {result[:2]}")
                    print(f"==================== [监控结束: {sec_code}] ====================\n")
                    return result
            else:
                print(f"❌ [错误]: data 结构正确，但里面的 'klines' 字段是空的")
                
    print(f"==================== [监控结束: {sec_code}] ====================\n")
    return None
# ==================== 绘图 ====================
def plot_chart(top_in, top_out, forced, all_data, output, mode_name, number):
    setup_font()
    fig = plt.figure(figsize=(24, 10))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.05, 0.10, 0.58, 0.82])
    ax.set_facecolor("white")

    in_colors  = ["#D32F2F","#E53935","#FF5722","#F57C00","#FFA000",
                  "#F9A825","#E91E63","#FF6F00","#D84315","#AD1457"]
    out_colors = ["#1565C0","#1976D2","#0288D1","#0097A7","#00796B",
                  "#2E7D32","#6A1B9A","#4527A0","#00838F","#37474F"]
    wh_color = "#333333"

    all_times = set()
    parsed = {}
    for code, name, _ in top_in + top_out + forced:
        pts = []
        for t, v in all_data.get("90." + code, []):
            try:
                # 将可能由于东财盘后结算导致的多个空格强制压缩清洗为标准的一个空格
                clean_t = " ".join(t.split())
                dt = datetime.strptime(clean_t, "%Y-%m-%d %H:%M")
                pts.append((dt, v))
                all_times.add(dt)
            except:
                pass
        parsed[code + "|" + name] = pts

    if not all_times: return False

    time_list = sorted(all_times)
    label_step = max(len(time_list) // 12, 1)
    tick_positions = list(range(len(time_list)))

    for idx, (code, name, _) in enumerate(top_in):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        c = in_colors[idx % len(in_colors)]
        ax.plot(tick_positions, vals, color=c, lw=0.8, alpha=0.8, marker="o", ms=1.2, mfc=c, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v), xytext=(5, 0),
                        textcoords="offset points", fontsize=6.5, color=c,
                        fontweight="bold", va="center", ha="left")

    for idx, (code, name, _) in enumerate(top_out):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        c = out_colors[idx % len(out_colors)]
        ax.plot(tick_positions, vals, color=c, lw=0.7, alpha=0.75, ls="--", marker="s", ms=1.0, mfc=c, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v), xytext=(5, 0),
                        textcoords="offset points", fontsize=6.5, color=c,
                        fontweight="bold", va="center", ha="left")

    for idx, (code, name, _) in enumerate(forced):
        pts = parsed.get(code + "|" + name, [])
        if not pts: continue
        pt_map = dict(pts)
        vals = [pt_map.get(t) for t in time_list]
        ax.plot(tick_positions, vals, color=wh_color, lw=1.0, alpha=0.85,
                ls="-.", marker="D", ms=1.2, mfc=wh_color, mew=0)
        last_v = vals[-1]
        if last_v is not None:
            ax.annotate(f"\u2605{name} {last_v/1e8:+.2f}亿",
                        xy=(len(time_list)-1, last_v), xytext=(5, 0),
                        textcoords="offset points", fontsize=6.5, color=wh_color,
                        fontweight="bold", va="center", ha="left")

    ax.set_xticks(tick_positions)
    labels = [t.strftime("%H:%M") if i % label_step == 0 or i == len(time_list)-1 else ""
              for i, t in enumerate(time_list)]
    ax.set_xticklabels(labels, rotation=30, fontsize=9, color="#333")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: "%.0f亿" % (x/1e8)))
    ax.tick_params(axis="y", colors="#333", labelsize=9)
    ax.grid(False)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    for s in ["left","bottom"]: ax.spines[s].set_color("#ccc")

    ns = datetime.now().strftime("%Y-%m-%d %H:%M")
    ax.set_xlabel("时间", fontsize=13, color="#333")
    ax.set_ylabel("主力净流入", fontsize=13, color="#333")
    ax.set_title(MODES[mode_name]["title"].format(NUMBER=number, time=ns),
                 fontsize=14, color="#222", pad=10, fontweight="bold")

    leg = [
        Line2D([0],[0],color="#D32F2F",lw=0.8,label="流入前十"),
        Line2D([0],[0],color="#1565C0",lw=0.7,ls="--",label="流出前十"),
        Line2D([0],[0],color="#333333",lw=1.0,ls="-.",label="白名单"),
    ]
    ax.legend(handles=leg, loc="lower center",
              bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=10, frameon=False)

    # 右侧汇总表
    tax = fig.add_axes([0.65, 0.12, 0.08, 0.78])
    tax.axis("off")
    cell_text = []
    cell_colors = []

    def add_row(l, r, bg):
        cell_text.append([l, r])
        cell_colors.append([bg, bg])

    add_row("板块", "净流入(亿)", "#E0E0E0")
    add_row("", "", "#FAFAFA")
    add_row("-- 流入 TOP%d --" % number, "", "#FFEBEE")

    for code, name, _ in top_in:
        pts = parsed.get(code + "|" + name, [])
        if pts:
            v = dict(pts).get(time_list[-1])
            add_row(name, "%.2f" % (v/1e8) if v is not None else "N/A", "#FFF5F5")
        else:
            add_row(name, "N/A", "#FFF5F5")

    add_row("", "", "#FAFAFA")
    add_row("-- 流出 TOP%d --" % number, "", "#E3F2FD")

    for code, name, _ in top_out:
        pts = parsed.get(code + "|" + name, [])
        if pts:
            v = dict(pts).get(time_list[-1])
            add_row(name, "%.2f" % (v/1e8) if v is not None else "N/A", "#F0F5FF")
        else:
            add_row(name, "N/A", "#F0F5FF")

    if forced:
        add_row("", "", "#FAFAFA")
        add_row("-- 白名单 --", "", "#F3E5F5")
        for code, name, _ in forced:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                add_row("\u2605"+name, "%.2f" % (v/1e8) if v is not None else "N/A", "#F5F0FF")
            else:
                add_row("\u2605"+name, "N/A", "#F5F0FF")

    nrows = len(cell_text)
    tbl = tax.table(cellText=cell_text, cellColours=cell_colors,
                    colWidths=[0.3, 0.1], cellLoc="left", bbox=[0, 0, 1, 1])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    for i in range(nrows):
        for j in range(2):
            cell = tbl[i, j]
            cell.set_edgecolor("#DDD")
            cell.set_linewidth(0.3)
            if j == 1:
                cell.set_text_props(ha="right", fontweight="bold")
    for j in range(2):
        tbl[0, j].set_text_props(fontweight="bold", fontsize=8)

    # Color amounts
    ri = 4
    for code, name, _ in top_in:
        if ri < nrows:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                if v is not None:
                    tbl[ri, 1].get_text().set_color("#D32F2F" if v >= 0 else "#C0392B")
            ri += 1
    ri += 2
    for code, name, _ in top_out:
        if ri < nrows:
            pts = parsed.get(code + "|" + name, [])
            if pts:
                v = dict(pts).get(time_list[-1])
                if v is not None:
                    tbl[ri, 1].get_text().set_color("#1565C0")
            ri += 1

    plt.savefig(output, dpi=200, bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close()
    return True
# ==================== 运行核心逻辑 ====================

# ==================== 模板文件自动创建 ====================
def save_templates():
    templates = [
        (os.path.join(CUR_DIR, "概念板块白名单.txt"), "概念板块白名单"),
        (os.path.join(CUR_DIR, "概念板块黑名单.txt"), "概念板块黑名单"),
        (os.path.join(CUR_DIR, "行业板块白名单.txt"), "行业板块白名单"),
        (os.path.join(CUR_DIR, "行业板块黑名单.txt"), "行业板块黑名单"),
    ]
    for p, label in templates:
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                if "白" in label:
                    f.write("# " + label + " - 填入板块名称，每行一个\n")
                    f.write("# 这些板块无论资金排名如何，都会出现在图中\n")
                else:
                    f.write("# " + label + " - 填入板块名称，每行一个\n")
                    f.write("# 这些板块将被排除，不参与排名筛选和绘图\n")


def run_mode(mode_name, number, log_func):
    cfg = MODES[mode_name]
    whitelist_path = os.path.join(CUR_DIR, cfg["whitelist"])
    blacklist_path = os.path.join(CUR_DIR, cfg["blacklist"])

    log_func("模式: " + mode_name + " | 前" + str(number) + "个板块")
    now = datetime.now()

    whitelist = load_list(whitelist_path)
    blacklist = load_list(blacklist_path)
    if whitelist: log_func("  白名单: " + ", ".join(sorted(whitelist)))
    if blacklist: log_func("  黑名单: " + str(len(blacklist)) + "项")

    log_func("\n[1/3] 获取板块排名...")
    inflow = get_sectors_ranked(1, cfg["api_filter"])
    if not inflow:
        log_func("  FAILED: 无法获取数据")
        return
    log_func("  获取到 %d个板块" % len(inflow))

    # Export all sector names
    all_path = os.path.join(CUR_DIR, cfg["all_file"])
    with open(all_path, "w", encoding="utf-8") as f:
        for code, name, flow in inflow:
            f.write(name + "\n")
    log_func("  板块列表已导出: " + cfg["all_file"])

    outflow = get_sectors_ranked(0, cfg["api_filter"])
    if not outflow:
        log_func("  FAILED: 无法获取流出数据")
        return

    if blacklist:
        before = len(inflow)
        inflow = [(c,n,v) for c,n,v in inflow if n not in blacklist]
        outflow = [(c,n,v) for c,n,v in outflow if n not in blacklist]
        log_func("  黑名单已过滤: %d个板块" % (before - len(inflow)))

    top_in = inflow[:number]
    top_out = outflow[:number]

    forced = []
    if whitelist:
        already = {n for _,n,_ in top_in} | {n for _,n,_ in top_out}
        all_map = {n: (c,n,v) for c,n,v in inflow}
        for name in sorted(whitelist - already):
            if name in all_map:
                forced.append(all_map[name])
                log_func("  白名单追加: " + name)
            else:
                log_func("  白名单未找到: " + name)

    log_func("\n  流入 TOP%d:" % number)
    for i,(c,n,v) in enumerate(top_in, 1):
        log_func("    %2d. %s (%s): %+.2f亿" % (i, n, c, v/1e8))
    log_func("\n  流出 TOP%d:" % number)
    for i,(c,n,v) in enumerate(top_out, 1):
        log_func("    %2d. %s (%s): %+.2f亿" % (i, n, c, v/1e8))

    target = top_in + top_out + forced
    seen = set()
    deduped = []
    for item in target:
        if item[0] not in seen:
            seen.add(item[0])
            deduped.append(item)
    target = deduped

    log_func("\n[2/3] 获取1分钟资金流向数据 (%d个板块)..." % len(target))
    all_flow = {}
    ok = 0
    for i, (code, name, _) in enumerate(target, 1):
        log_func("  [%2d/%2d] %s..." % (i, len(target), name), end="")
        # 传入 mode_name 以便函数内部识别使用 125 还是 115 前缀
        data = get_sector_flow(code, 240, mode_name)
        if data:
            all_flow["90."+code] = data  
            log_func(" OK (%d个数据点)" % len(data))
            ok += 1
        else:
            log_func(" N/A")
        time.sleep(0.3)

    log_func("  成功: %d/%d" % (ok, len(target)))
    if ok < len(target):
        log_func("  >> %d个板块数据未获取到" % (len(target)-ok))

    log_func("\n[3/3] 绘制图表...")
    out = os.path.join(CUR_DIR, "%s_%s.png" % (cfg["out_prefix"], now.strftime("%Y-%m-%d")))
    if ok > 0:
        plot_chart(top_in, top_out, forced, all_flow, out, mode_name, number)
        log_func("  图表已保存: " + out)
    else:
        log_func("  没有获取到分时数据")
    log_func("\n完成!")

# ==================== GUI ====================
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("A股板块主力资金流向工具")
        self.root.geometry("720x680")
        self.root.minsize(600, 500)
        self.build_ui()

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="A股板块主力资金流向工具",
                  font=("Microsoft YaHei", 16, "bold")).pack(pady=(0, 15))

        # Mode
        fm1 = ttk.LabelFrame(frame, text="模式选择", padding=10)
        fm1.pack(fill=tk.X, pady=5)
        self.var_mode = tk.StringVar(value="概念板块")
        for m in ["概念板块", "行业板块"]:
            ttk.Radiobutton(fm1, text=m, variable=self.var_mode, value=m).pack(side=tk.LEFT, padx=20)

        # Number
        fm2 = ttk.LabelFrame(frame, text="参数设置", padding=10)
        fm2.pack(fill=tk.X, pady=5)
        ttk.Label(fm2, text="获取前N个板块:").pack(side=tk.LEFT)
        self.entry_num = ttk.Entry(fm2, width=8)
        self.entry_num.pack(side=tk.LEFT, padx=5)
        self.entry_num.insert(0, "10")

        # Files
        fm3 = ttk.LabelFrame(frame, text="黑白名单", padding=10)
        fm3.pack(fill=tk.X, pady=5)

        def make_row(parent, label, get_path):
            row = ttk.Frame(parent)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label+":", width=8).pack(side=tk.LEFT)
            lbl = ttk.Label(row, text=get_path(), foreground="gray")
            lbl.pack(side=tk.LEFT, padx=5)
            def update(*args):
                lbl.config(text=get_path())
            self.var_mode.trace("w", update)
            ttk.Button(row, text="编辑", width=6,
                       command=lambda p=get_path(): os.startfile(os.path.join(CUR_DIR, p))
                       if os.path.exists(os.path.join(CUR_DIR, p)) else None).pack(side=tk.RIGHT)

        def bl_path(): return MODES[self.var_mode.get()]["blacklist"]
        def wl_path(): return MODES[self.var_mode.get()]["whitelist"]
        make_row(fm3, "黑名单", bl_path)
        make_row(fm3, "白名单", wl_path)

        # Start
        self.btn_start = ttk.Button(frame, text="启 动", command=self.run)
        self.btn_start.pack(pady=10, ipadx=30, ipady=5)

        # Log
        ttk.Label(frame, text="运行日志:").pack(anchor=tk.W)
        self.txt_log = tk.Text(frame, height=18, font=("Consolas", 9), wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(self.txt_log)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.config(yscrollcommand=scroll.set)
        scroll.config(command=self.txt_log.yview)

        self.log("欢迎使用 A股板块主力资金流向工具")
        self.log("选择模式并点击「启动」开始")

    def log(self, msg, end="\n"):
        self.txt_log.insert(tk.END, msg + end)
        self.txt_log.see(tk.END)
        self.root.update()

    def run(self):
        mode = self.var_mode.get()
        try:
            num = int(self.entry_num.get().strip())
        except:
            self.log("错误: 请输入有效数字")
            return
        if num < 1:
            self.log("错误: 数字必须大于0")
            return

        self.btn_start.config(state=tk.DISABLED)
        self.txt_log.delete(1.0, tk.END)

        def worker():
            try:
                run_mode(mode, num, self.log)
            except Exception as e:
                self.log("错误: " + str(e))
            finally:
                self.root.after(0, lambda: self.btn_start.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        self.root.mainloop()

if __name__ == "__main__":
    App().start()
