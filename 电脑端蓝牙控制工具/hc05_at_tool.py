# -*- coding: utf-8 -*-
"""
HC-05 AT 指令调试助手
======================================================================
用途
    蓝牙模块搜不到 / 连不上时，跳过蓝牙，改用有线串口（USB 转 TTL）
    直接跟模块对话，查清它的真实名字、角色、波特率，定位"搜不到"的原因。

接线（必须用 USB 转串口模块，不能通过蓝牙）
    USB-TTL  TXD  ->  模块 RXD      （建议串 1K 电阻，防 5V 电平打坏模块）
    USB-TTL  RXD  ->  模块 TXD
    USB-TTL  GND  ->  模块 GND
    USB-TTL  5V   ->  模块 VCC      （底板丝印 POWER 3.6-5V，必须 5V）
    USB-TTL  3.3V ->  模块 EN       （拉高才进 AT 模式；无 3.3V 脚则接 5V）

关键提醒
    1. 透传模式下模块不响应 AT 指令，所以 EN 必须拉高。
    2. 另一种进 AT 模式的办法：先把 EN 接高电平，再给模块上电，
       此时模块波特率固定为 38400（正常上电后拉高 EN 则是模块自身波特率）。
    3. 接线时务必断电，VCC/GND 不要接反。
    4. 搜不到设备时按这个顺序怀疑：
       ① AT+ROLE? = 1（主机模式不响应搜索）<- 最常见
       ② AT+CMODE? = 0 且绑定了固定地址
       ③ AT+STATE? = CONNECTED（已被别的设备占用）
       ④ 名字被卖家改过（AT+NAME? 读到的才是真名）
       ⑤ 供电不足（射频发不出去，但 LED 照闪）
======================================================================
"""

import os
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

try:
    import serial
    from serial.tools import list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


# ------------------------------ 常量 ---------------------------------

BAUD_CANDIDATES = [9600, 38400, 115200, 57600, 19200, 4800]

# 一键体检：依次发送的指令与说明
CHECKS = [
    ("AT",          "基础连通性"),
    ("AT+VERSION?", "固件版本"),
    ("AT+NAME?",    "设备名"),
    ("AT+ROLE?",    "角色"),
    ("AT+CMODE?",   "连接模式"),
    ("AT+BIND?",    "绑定地址"),
    ("AT+UART?",    "串口参数"),
    ("AT+PSWD?",    "配对码"),
    ("AT+ADDR?",    "模块 MAC 地址"),
    ("AT+STATE?",   "当前状态"),
]

# 一键修复的兜底方案（体检没跑过、或没发现问题时用这个）
DEFAULT_FIX = [
    ("AT+ROLE=0",  "把模块设为「从机」——从机才会出现在别人的搜索列表里"),
    ("AT+CMODE=1", "允许任意设备连接，解除只认固定地址的限制"),
]

# 米色暖色主题
BG     = "#FAF6EF"
CARD   = "#FFFDF8"
LINE   = "#E3D8C6"
FG     = "#3A3226"
MUTED  = "#8B7B63"
ACCENT = "#B07B4F"
OKC    = "#3F7A4A"
ERRC   = "#A5432F"
WARNC  = "#B8791F"

HINT_TEXT = """接线速查（务必先断电）

  USB-TTL TXD  ->  模块 RXD      建议串 1K 电阻
  USB-TTL RXD  ->  模块 TXD
  USB-TTL GND  ->  模块 GND
  USB-TTL 5V   ->  模块 VCC      底板印着 POWER 3.6-5V
  USB-TTL 3.3V ->  模块 EN       拉高才进 AT 模式

顺序：接线 -> 插 USB-TTL -> 点「自动探测波特率」-> 点「一键体检」-> 有问题就点「一键修复」
如果全都没反应，试试「EN 先接高电平再给模块上电」这种进 AT 模式的方式。"""


# ------------------------------ 主程序 -------------------------------

class ATTool(object):

    def __init__(self, root):
        self.root = root
        self.ser = None
        self.busy = False
        self._fix_plan = []          # 体检结论：需要执行的修复指令

        root.title("HC-05 AT 指令调试助手")
        root.configure(bg=BG)
        root.geometry("900x680")
        root.minsize(760, 560)

        self._build_style()
        self._build_ui()
        self.refresh_ports()

        self.log("HC-05 AT 指令调试助手已就绪。", "info")
        if not HAS_SERIAL:
            self.log("未检测到 pyserial 库，无法使用串口功能。", "err")
        self.log("第一步：把模块从 STM32 上取下来，按下方接线图接到 USB 转串口模块。", "muted")
        self.log("第二步：点「自动探测波特率」，它会挨个试常见波特率发一条 AT。", "muted")
        self.log("第三步：点「一键体检」，把全部结果发给我看。", "muted")

    # ------------------------- 界面构建 -------------------------

    def _build_style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        s.configure("Head.TLabel", background=BG, foreground=FG,
                    font=("Microsoft YaHei UI", 11, "bold"))
        s.configure("Hint.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
        s.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 5))
        s.configure("Go.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 5))
        s.configure("TCombobox", padding=3)

    def _build_ui(self):
        pad = dict(padx=14, pady=(10, 0))

        # ---- 第一行：串口设置 ----
        row1 = ttk.Frame(self.root)
        row1.pack(fill="x", **pad)

        ttk.Label(row1, text="串口").pack(side="left")
        self.cmb_port = ttk.Combobox(row1, width=34, state="readonly")
        self.cmb_port.pack(side="left", padx=(6, 14))

        ttk.Label(row1, text="波特率").pack(side="left")
        self.cmb_baud = ttk.Combobox(
            row1, width=10, state="readonly",
            values=[str(b) for b in BAUD_CANDIDATES])
        self.cmb_baud.set("9600")
        self.cmb_baud.pack(side="left", padx=(6, 14))

        ttk.Button(row1, text="刷新端口", command=self.refresh_ports).pack(side="left")
        self.btn_open = ttk.Button(row1, text="打开串口", command=self.toggle_port)
        self.btn_open.pack(side="left", padx=(8, 0))
        self.lbl_state = ttk.Label(row1, text="未连接", foreground=MUTED)
        self.lbl_state.pack(side="left", padx=(12, 0))

        # ---- 第二行：操作按钮 ----
        row2 = ttk.Frame(self.root)
        row2.pack(fill="x", **pad)

        self.btn_probe = ttk.Button(row2, text="自动探测波特率",
                                    command=lambda: self.run_async(self.do_probe))
        self.btn_probe.pack(side="left")

        self.btn_check = ttk.Button(row2, text="一键体检", style="Go.TButton",
                                    command=lambda: self.run_async(self.do_check))
        self.btn_check.pack(side="left", padx=(8, 0))

        self.btn_repair = ttk.Button(row2, text="一键修复", style="Go.TButton",
                                     command=self.ask_repair)
        self.btn_repair.pack(side="left", padx=(8, 0))

        ttk.Button(row2, text="清空日志", command=self.clear_log).pack(side="right")
        ttk.Button(row2, text="保存日志", command=self.save_log).pack(side="right", padx=(0, 8))

        # ---- 第三行：手动指令 ----
        row3 = ttk.Frame(self.root)
        row3.pack(fill="x", **pad)

        ttk.Label(row3, text="手动指令").pack(side="left")
        self.ent_cmd = ttk.Entry(row3, font=("Consolas", 11))
        self.ent_cmd.pack(side="left", fill="x", expand=True, padx=(6, 8))
        self.ent_cmd.bind("<Return>", lambda e: self.run_async(self.do_manual))
        ttk.Button(row3, text="发送", command=lambda: self.run_async(self.do_manual)).pack(side="left")

        # ---- 日志区 ----
        box = ttk.Frame(self.root)
        box.pack(fill="both", expand=True, padx=14, pady=(10, 6))

        self.txt = scrolledtext.ScrolledText(
            box, wrap="word", font=("Consolas", 10),
            bg=CARD, fg=FG, insertbackground=FG, relief="solid", bd=1,
            highlightthickness=0)
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

        self.txt.tag_config("info",      foreground=ACCENT)
        self.txt.tag_config("ok",        foreground=OKC)
        self.txt.tag_config("err",       foreground=ERRC)
        self.txt.tag_config("warn",      foreground=WARNC)
        self.txt.tag_config("muted",     foreground=MUTED)
        self.txt.tag_config("tx",        foreground="#2F6FA8")
        self.txt.tag_config("rx",        foreground=FG)
        self.txt.tag_config("head",      foreground=FG, font=("Consolas", 10, "bold"))

        # ---- 接线提示 ----
        hint = tk.Label(self.root, text=HINT_TEXT, justify="left", anchor="w",
                        bg="#F3EBDD", fg=MUTED, font=("Consolas", 9),
                        padx=12, pady=8, relief="solid", bd=1)
        hint.pack(fill="x", padx=14, pady=(0, 12))

    # ------------------------- 日志 -------------------------

    def log(self, text, tag=None):
        """线程安全：任何线程都能调用，真正的 UI 更新交给主线程做"""
        self.root.after(0, self._log_now, text, tag)

    def _log_now(self, text, tag=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", tag if tag else ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def save_log(self):
        path = filedialog.asksaveasfilename(
            title="保存日志", defaultextension=".txt",
            initialfile="hc05_at_log.txt",
            filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.txt.get("1.0", "end"))
            self.log("日志已保存到 " + path, "ok")
        except Exception as e:
            self.log("保存失败: %s" % e, "err")

    # ------------------------- 串口管理 -------------------------

    def refresh_ports(self):
        self.cmb_port["values"] = []
        if not HAS_SERIAL:
            return
        items = []
        best = None
        for p in list_ports.comports():
            desc = (p.description or "")
            hwid = (p.hwid or "")
            tag = ""
            if "CH340" in desc.upper() or "CH340" in hwid.upper():
                tag = "  <- USB转串口(CH340)"
                if best is None:
                    best = p.device
            elif "蓝牙" in desc or "Bluetooth" in desc:
                tag = "  <- 蓝牙虚拟串口"
            items.append("%s  |  %s%s" % (p.device, desc, tag))
        self.cmb_port["values"] = items
        if items:
            if best:
                for it in items:
                    if it.startswith(best):
                        self.cmb_port.set(it)
                        break
            else:
                self.cmb_port.set(items[0])

    def _selected_port(self):
        raw = self.cmb_port.get().strip()
        if not raw:
            return None
        return raw.split("|")[0].strip()

    def toggle_port(self):
        if self.ser and self.ser.is_open:
            self.close_port()
        else:
            self.open_port()

    def open_port(self):
        if not HAS_SERIAL:
            self.root.after(0, lambda: messagebox.showerror(
                "缺少依赖", "未安装 pyserial，无法打开串口。"))
            return False
        port = self._selected_port()
        if not port:
            self.root.after(0, lambda: messagebox.showwarning(
                "未选串口", "请先选择一个串口。"))
            return False
        try:
            baud = int(self.cmb_baud.get())
        except ValueError:
            baud = 9600
        try:
            self.ser = serial.Serial(port, baud, timeout=0.2,
                                     write_timeout=1.0)
            self.root.after(0, lambda: self.lbl_state.configure(
                text="已连接 %s @ %d" % (port, baud), foreground=OKC))
            self.root.after(0, lambda: self.btn_open.configure(text="关闭串口"))
            self.log("已打开 %s @ %d 8N1" % (port, baud), "ok")
            return True
        except Exception as e:
            self.ser = None
            self.log("打开失败: %s" % e, "err")
            self.log("提示：串口被别的程序占用（比如刚才的控制工具）时也会失败，先关掉它。", "warn")
            return False

    def close_port(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.root.after(0, lambda: self.lbl_state.configure(
            text="未连接", foreground=MUTED))
        self.root.after(0, lambda: self.btn_open.configure(text="打开串口"))

    def _read_all(self, quiet=0.25, total=1.2):
        """把串口缓冲区里陆续到达的数据全部读回来"""
        end = time.time() + total
        last = time.time()
        buf = b""
        while time.time() < end and (time.time() - last) < quiet:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                last = time.time()
            else:
                time.sleep(0.02)
        return buf

    def transact(self, cmd, wait=0.25):
        """发送一条 AT 指令并返回响应文本"""
        if not self.ser or not self.ser.is_open:
            return None
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r\n").encode("ascii", "ignore"))
        self.ser.flush()
        time.sleep(wait)
        raw = self._read_all()
        return raw.decode("utf-8", "replace").strip()

    # ------------------------- 业务动作 -------------------------

    def do_probe(self):
        """自动探测模块实际使用的波特率"""
        if not hasattr(self, "_busy_guard") or not self._busy_guard():
            return
        port = self._selected_port()
        if not port:
            self.log("请先选择一个串口。", "err")
            return

        self.log("")
        self.log("=== 自动探测波特率 ===", "head")
        self.log("模块自己的波特率出厂常被改成 9600；若用「EN 先接高再上电」进 AT 模式则是 38400。", "muted")

        if self.ser and self.ser.is_open:
            self.close_port()

        found = None
        for baud in BAUD_CANDIDATES:
            try:
                with serial.Serial(port, baud, timeout=0.2, write_timeout=1.0) as s:
                    time.sleep(0.15)
                    s.reset_input_buffer()
                    s.write(b"AT\r\n")
                    s.flush()
                    time.sleep(0.3)
                    raw = s.read(256)
                txt = raw.decode("utf-8", "replace").strip()
                if txt:
                    self.log("  %6d  ->  有响应: %r" % (baud, txt), "ok")
                    if found is None:
                        found = baud
                else:
                    self.log("  %6d  ->  无响应" % baud, "muted")
            except Exception as e:
                self.log("  %6d  ->  打不开: %s" % (baud, e), "err")

        if found is None:
            self.log("")
            self.log("所有波特率都没有响应。往下看「没响应怎么办」。", "err")
            self._no_response_help()
            return

        self.root.after(0, lambda: self.cmb_baud.set(str(found)))
        self.log("")
        self.log("探测结果：模块使用 %d 波特率。已自动帮你选好，现在可以直接点「一键体检」。" % found, "ok")
        self.open_port()

    def _no_response_help(self):
        self.log("可能的原因（按概率排序）：", "warn")
        self.log("  1. EN 脚没拉高 —— 透传模式下模块不响应任何 AT 指令。", "warn")
        self.log("     把模块 EN 接到 USB-TTL 的 3.3V 上（只借电压，不耗电）。", "muted")
        self.log("  2. TXD / RXD 接反了 —— 必须是交叉连接：模块 TXD -> 串口 RXD。", "warn")
        self.log("  3. 串口模块本身没在收发 —— 可以短接 USB-TTL 的 TXD 和 RXD 自测回环。", "warn")
        self.log("  4. 模块已被别的程序或设备占用 —— 关掉手机蓝牙、关掉其他串口工具。", "warn")
        self.log("  5. 再试一次「EN 先接高电平、再给模块上电」，此时波特率固定 38400。", "warn")

    def _busy_guard(self):
        if not HAS_SERIAL:
            self.log("未安装 pyserial，无法操作串口。", "err")
            return False
        if not self._selected_port():
            self.log("请先选择一个串口。", "err")
            return False
        return True

    def do_check(self):
        """一键体检：把模块的关键参数全问一遍，并自动解读"""
        if not self._busy_guard():
            return
        if not (self.ser and self.ser.is_open):
            if not self.open_port():
                return

        self.log("")
        self.log("=== 一键体检 ===", "head")
        results = {}

        for cmd, label in CHECKS:
            resp = self.transact(cmd)
            if resp is None:
                self.log("串口已断开。", "err")
                return
            results[cmd] = resp
            self.log("  %-13s %-12s ->  %s" % (cmd, label, self._oneline(resp)),
                     "ok" if resp else "err")

        self.log("")
        self.log("=== 自动解读 ===", "head")
        self._interpret(results)

    # ------------------------- 响应解析 -------------------------

    @staticmethod
    def _oneline(resp):
        """把多行响应压成一行，方便日志对齐显示"""
        s = (resp or "").replace("\r", " ").replace("\n", " ").strip()
        while "  " in s:
            s = s.replace("  ", " ")
        return s or "(无响应)"

    @staticmethod
    def _clean(resp):
        """去掉空行、ERROR 和结尾单独一行的 OK，取最后一条有效内容行

        模块的响应形如 "+NAME:HC-05\\r\\nOK\\r\\n"，OK 是同一包里的，
        必须剥掉，否则拼出来的值会变成 "HC-05 OK" 而对不上。
        """
        if not resp:
            return ""
        lines = []
        for ln in resp.replace("\r", "\n").split("\n"):
            s = ln.strip()
            if not s:
                continue
            u = s.upper()
            if u == "OK" or u.startswith("ERROR"):
                continue
            lines.append(s)
        return lines[-1] if lines else ""

    @staticmethod
    def _value(resp):
        """从 +XXX:val 或 +XXX=val 里取出 val

        坑：HC-05 的各个固件分隔符不统一 —— NAME/ROLE/STATE 用冒号，
        但 UART/PSWD 有人用等号；而 ADDR 的值本身还含冒号
        （+ADDR=21:F6:47:3A:D8:89）。所以必须先试等号，再退到冒号，
        否则 MAC 会被截成 "F6:47:3A:D8:89"。
        """
        s = ATTool._clean(resp)
        if not s:
            return ""
        for sep in ("=", ":"):
            if sep in s:
                return s.split(sep, 1)[1].strip().strip(",").strip()
        return s

    # ------------------------- 体检解读 -------------------------

    def _interpret(self, results):
        self._fix_plan = []
        got_any = any(v for v in results.values())
        if not got_any:
            self.log("一条指令都没回应 —— 模块没有进入 AT 模式，或接线有问题。", "err")
            self._no_response_help()
            return

        # ---- 角色（搜不到的第一嫌疑）----
        role = self._value(results.get("AT+ROLE?", ""))
        if role:
            if role.startswith("0"):
                self.log("角色 = 0（从机）—— 从机才会出现在别人的搜索列表里，这项正常。", "ok")
            elif role.startswith("1"):
                self.log("角色 = 1（主机）★ 这极可能就是搜不到的根因 ★", "err")
                self.log("  主机模式只会主动去连别人，不响应别人的搜索，所以手机和电脑都看不见它。", "err")
                self._fix_plan.append(("AT+ROLE=0", "把模块从「主机」改成「从机」，从机才会被搜到"))
            elif role.startswith("2"):
                self.log("角色 = 2（回环）—— 需要改成 0 才能被搜索。", "err")
                self._fix_plan.append(("AT+ROLE=0", "把模块从「回环」改成「从机」"))
            else:
                self.log("角色 = %s（没见过的值）" % role, "warn")
        else:
            self.log("没读到角色信息。", "warn")

        # ---- 连接模式 / 绑定地址 ----
        cmode = self._value(results.get("AT+CMODE?", ""))
        if cmode:
            if cmode.startswith("0"):
                self.log("连接模式 = 0（只认绑定地址）—— 别的设备连不上它，建议改成 1。", "warn")
                self._fix_plan.append(("AT+CMODE=1", "允许任意设备连接（解除只认固定地址的限制）"))
            elif cmode.startswith("1"):
                self.log("连接模式 = 1（允许任意设备连接），正常。", "ok")
        else:
            self.log("没读到连接模式（该固件可能不支持 AT+CMODE?，不影响）。", "muted")

        bind = self._value(results.get("AT+BIND?", ""))
        if bind and bind.replace(",", "").replace(":", "").strip("0") != "":
            self.log("模块被绑定了固定地址 %s —— 修复时会一并解除。" % bind, "warn")

        # ---- 设备名 ----
        real = self._value(results.get("AT+NAME?", ""))
        if real:
            if real.upper() == "HC-05":
                self.log("设备名 = %s，和你一直在找的名字一致 —— 名字不是问题。" % real, "ok")
            else:
                self.log("设备真名是「%s」，不是 HC-05 —— 搜索时应该找这个名字。" % real, "warn")

        # ---- 波特率 ----
        uart = self._value(results.get("AT+UART?", ""))
        if uart:
            first = uart.split(",")[0].strip()
            if first == "9600":
                self.log("串口波特率 = 9600，和单片机代码里的一致。", "ok")
            else:
                self.log("串口波特率 = %s，和单片机代码里的 9600 不一致 ——"
                         "改模块（AT+UART=9600,0,0）或改代码。" % first, "warn")

        # ---- 配对码 ----
        pswd = self._value(results.get("AT+PSWD?", ""))
        if pswd:
            if pswd == "1234":
                self.log("配对码 = 1234（常见默认值）。", "ok")
            else:
                self.log("配对码 = %s ★ 注意：不是 1234！配对时请输入 %s" % (pswd, pswd), "warn")

        # ---- 状态 ----
        st = self._value(results.get("AT+STATE?", ""))
        if st:
            u = st.upper()
            if "CONNECTED" in u:
                self.log("当前状态 = CONNECTED ★ 已经连着某个设备了。一个模块同时只服务一个设备，"
                         "先断开那个设备，它才会重新出现在搜索列表里。", "err")
            elif "PAIRABLE" in u:
                self.log("当前状态 = PAIRABLE（处于可配对状态），正常。", "ok")
            else:
                self.log("当前状态 = %s" % st, "muted")

        # ---- 版本 / MAC ----
        ver = self._value(results.get("AT+VERSION?", ""))
        if ver:
            self.log("固件版本 = %s" % ver, "muted")
        addr = self._value(results.get("AT+ADDR?", ""))
        if addr:
            self.log("模块 MAC = %s（电脑里若残留旧配对记录，可按这个地址清理）" % addr, "muted")

        # ---- 汇总结论 ----
        self.log("")
        if self._fix_plan:
            self.log("=== 结论：发现 %d 处需要修改 ===" % len(self._fix_plan), "head")
            for i, (c, d) in enumerate(self._fix_plan, 1):
                self.log("  %d. %-14s %s" % (i, c, d), "warn")
            self.log("")
            self.log("点上方「一键修复」，工具会自动执行上面的指令并重启模块，然后自动复检。", "info")
        else:
            self.log("=== 结论：模块参数没有明显异常 ===", "head")
            self.log("若仍然搜不到：把模块举离面包板/金属，天线朝上、手指别捏天线，手机贴到 10cm 内再搜；"
                     "并确认没有别的设备占着它。", "muted")

    # ------------------------- 一键修复 -------------------------

    def ask_repair(self):
        """修复前在主线程弹窗确认（给模块写设置属于外部动作，必须先问）"""
        if not (self.ser and self.ser.is_open):
            self.log("还没有打开串口 —— 先点「打开串口」，或先跑一次「一键体检」。", "err")
            return
        plan = self._fix_plan or DEFAULT_FIX
        lines = "\n".join("    %d. %-14s %s" % (i, c, d)
                          for i, (c, d) in enumerate(plan, 1))
        ok = messagebox.askyesno(
            "确认修复",
            "即将通过串口向蓝牙模块写入以下设置：\n\n%s\n\n"
            "写完会自动重启模块并复检。这些设置随时能用 AT 指令改回来，"
            "不会损坏模块。\n\n确定继续吗？" % lines)
        if not ok:
            self.log("已取消修复。", "muted")
            return
        self.run_async(self.do_repair)

    def do_repair(self):
        """执行修复指令 -> 重启模块 -> 复检"""
        if not (self.ser and self.ser.is_open):
            if not self.open_port():
                return
        plan = self._fix_plan or DEFAULT_FIX

        self.log("")
        self.log("=== 一键修复 ===", "head")
        all_ok = True
        for cmd, desc in plan:
            resp = self.transact(cmd, wait=0.4)
            good = "OK" in (resp or "").upper()
            self.log("  %-14s %-38s ->  %s" % (cmd, desc, self._oneline(resp)),
                     "ok" if good else "err")
            all_ok = all_ok and good

        self.log("")
        self.log("重启模块让设置生效（会有 1~2 秒没有响应，正常）...", "info")
        self.log("  AT+RESET -> %s" % self._oneline(self.transact("AT+RESET", wait=0.8)), "muted")
        time.sleep(1.5)

        self.log("")
        self.log("=== 修复后复检 ===", "head")
        role = ""
        for cmd, label in (("AT+ROLE?", "角色"), ("AT+CMODE?", "连接模式")):
            resp = self.transact(cmd, wait=0.35)
            val = self._value(resp)
            if cmd == "AT+ROLE?":
                role = val
            self.log("  %-11s %-8s ->  %s" % (cmd, label, self._oneline(resp)),
                     "ok" if resp else "err")

        self.log("")
        if role.startswith("0"):
            self.log("修复成功 ✓ 模块现在是从机，会出现在蓝牙搜索列表里了。", "ok")
            self.log("下一步：拔掉 EN 那根线（让它悬空），把模块接回 STM32 ——", "info")
            self.log("        VCC->5V、GND->GND、TXD->PA10、RXD->PA9、EN 悬空。", "info")
            self.log("        然后用手机蓝牙搜索，能搜到 HC-05 就说明通了（配对码见体检里的 AT+PSWD）。", "info")
        elif all_ok:
            self.log("指令都返回 OK，但角色还没变成 0 —— 断电 5 秒再上电，然后重新体检一次。", "warn")
        else:
            self.log("有指令没返回 OK，把上面这几行发我看看。", "err")

    def do_manual(self):
        """发送手动输入的指令"""
        if not self._busy_guard():
            return
        cmd = self.ent_cmd.get().strip()
        if not cmd:
            return
        if not (self.ser and self.ser.is_open):
            if not self.open_port():
                return
        resp = self.transact(cmd)
        if resp is None:
            self.log("串口已断开。", "err")
            return
        self.log("  > %-14s ->  %s" % (cmd, self._oneline(resp)),
                 "ok" if resp else "err")
        self.root.after(0, lambda: self.ent_cmd.delete(0, "end"))

    # ------------------------- 线程包装 -------------------------

    def run_async(self, fn):
        if self.busy:
            self.log("上一个操作还没结束，稍等一下。", "muted")
            return
        self.busy = True
        self._set_buttons(False)

        def worker():
            try:
                fn()
            except Exception as e:
                self.log("出错: %s" % e, "err")
            finally:
                self.busy = False
                self.root.after(0, lambda: self._set_buttons(True))

        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons(self, enabled):
        st = "normal" if enabled else "disabled"
        for b in (self.btn_probe, self.btn_check, self.btn_repair, self.btn_open):
            try:
                b.configure(state=st)
            except Exception:
                pass

    def on_close(self):
        self.close_port()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ATTool(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
