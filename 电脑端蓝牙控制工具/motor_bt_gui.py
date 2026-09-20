# -*- coding: utf-8 -*-
"""
================================================================
 电机蓝牙控制工具   (电脑端 -> HC-05 -> STM32)
================================================================

功能:
    通过电脑蓝牙连接 HC-05, 用图形界面控制 STM32 上的直流电机,
    并接收开发板主动发回的"旋转完成"消息。

对应单片机端的文本协议(每行以回车换行结尾, 不区分大小写):
    RUN<档位>,<毫秒>    例: RUN30,5000   30 档转 5 秒, 到点自动停转
    SPD<档位>           例: SPD30        只改转速, 一直转(不定时)
    STOP                                立即停转
    STATUS  或  ?                       查询状态
    PING                                测试链路, 回 PONG

开发板主动发回的:
    READY                  上电就绪
    OK RUN 30 5000         命令已接受
    DONE 30 5000           定时旋转完成   <-- "完成消息"
    ERR ......             命令有问题

使用前提(重要):
    1. 先在 Windows 设置里把 HC-05 配对好, PIN 一般是 1234
    2. 配对后系统会出现【两个】COM 口, 名字类似
       "HC-05 'Dev B' 传出" 和 "HC-05 'Dev B' 传入"
       请选【传出】那个, 选成『传入』会一条数据都收不到
    3. 波特率选 9600 (HC-05 出厂默认)

运行:  python motor_bt_gui.py
依赖:  pyserial
================================================================
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

# ---------------- 字体 ----------------
UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_B = ("Microsoft YaHei UI", 9, "bold")
UI_FONT_T = ("Microsoft YaHei UI", 11, "bold")
UI_FONT_S = ("Microsoft YaHei UI", 8)
MONO = ("Consolas", 10)

# ---------------- 暖色(米色)主题 ----------------
BG = "#F3EDE4"
CARD = "#FBF7F0"
BORDER = "#DFD3C3"
FG = "#4A3F35"
FG_DIM = "#8B7A68"
ACCENT = "#C4773B"
ACCENT_D = "#A85F2B"
OK_FG = "#3F7A4A"
ERR_FG = "#B4453A"
TX_FG = "#2F6FA8"

# ---------------- 与单片机保持一致的换算参数 ----------------
# 必须和 Hardware/Motor.h 里的三个宏完全一致, 否则界面显示的占空比是错的
MOTOR_GEAR_MAX = 50
MOTOR_DUTY_MIN = 420          # 1 档  = 42.0%   (单位 0.1%)
MOTOR_DUTY_MAX = 600          # 50 档 = 60.0%
RUN_MS_MIN = 100
RUN_MS_MAX = 600000


def gear_to_duty(g):
    """档位 -> 占空比(单位 0.1%)。算法与 Motor.c 的 Motor_GetDuty() 完全一致"""
    if g <= 0:
        return 0
    if g > MOTOR_GEAR_MAX:
        g = MOTOR_GEAR_MAX
    return MOTOR_DUTY_MIN + (
        (MOTOR_DUTY_MAX - MOTOR_DUTY_MIN) * (g - 1) + (MOTOR_GEAR_MAX - 1) // 2
    ) // (MOTOR_GEAR_MAX - 1)


# ================================================================
class MotorBTApp:
    def __init__(self, root):
        self.root = root
        self.ser = None
        self.reader = None
        self.running = False
        self.rx_queue = queue.Queue()

        root.title("电机蓝牙控制工具  ·  STM32 + HC-05")
        root.geometry("860x640")
        root.minsize(800, 580)
        root.configure(bg=BG)

        # 界面用的状态变量(必须在建界面之前创建)
        self.var_gear = tk.IntVar(value=30)
        self.var_ms = tk.StringVar(value="5000")
        self.var_duty = tk.StringVar()
        self.var_popup = tk.BooleanVar(value=True)

        self._init_style()
        self._build_ui()
        self.refresh_ports()

        root.after(60, self._pump_rx)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------
    def _init_style(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=FG, font=UI_FONT)
        st.configure("TButton", font=UI_FONT, padding=(10, 5))
        st.configure("Accent.TButton", font=UI_FONT_B, padding=(10, 6),
                     background=ACCENT, foreground="#FFFFFF", borderwidth=0)
        st.map("Accent.TButton",
               background=[("active", ACCENT_D), ("disabled", "#D9C8B6")])
        st.configure("TCheckbutton", background=BG, foreground=FG, font=UI_FONT)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TCombobox", font=UI_FONT)

    # ------------------------------------------------------------
    def _build_ui(self):
        # ===== 1. 串口连接条 =====
        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill="x")

        ttk.Label(top, text="串口:").pack(side="left")
        self.cmb_port = ttk.Combobox(top, width=36, font=UI_FONT, state="readonly")
        self.cmb_port.pack(side="left", padx=(4, 8))
        ttk.Button(top, text="刷新", width=6, command=self.refresh_ports).pack(side="left")

        ttk.Label(top, text="波特率:").pack(side="left", padx=(14, 0))
        self.cmb_baud = ttk.Combobox(top, width=9, font=UI_FONT, state="readonly",
                                     values=["9600", "19200", "38400", "57600", "115200"])
        self.cmb_baud.set("9600")
        self.cmb_baud.pack(side="left", padx=(4, 10))

        self.btn_conn = ttk.Button(top, text="连接", style="Accent.TButton",
                                   command=self.toggle_conn)
        self.btn_conn.pack(side="left")
        self.lbl_conn = ttk.Label(top, text="● 未连接", foreground=ERR_FG, font=UI_FONT_B)
        self.lbl_conn.pack(side="left", padx=(12, 0))

        ttk.Label(self.root, foreground=FG_DIM, font=UI_FONT_S,
                  text="提示: HC-05 配对后会多出两个 COM 口, 请选『传出』(名字里带 Dev B) 的那个, 波特率 9600"
                  ).pack(fill="x", padx=14)

        # ===== 2. 两张控制卡片 =====
        mid = ttk.Frame(self.root, padding=(12, 8, 12, 4))
        mid.pack(fill="x")
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)
        self._build_speed_card(mid)
        self._build_time_card(mid)

        # ===== 3. 动作按钮 =====
        act = ttk.Frame(self.root, padding=(12, 6, 12, 4))
        act.pack(fill="x")

        self.btn_run = ttk.Button(act, text="\u25b6  定时旋转", style="Accent.TButton",
                                  command=self.cmd_run)
        self.btn_run.pack(side="left")
        ttk.Button(act, text="即时设速", command=self.cmd_spd).pack(side="left", padx=8)
        ttk.Button(act, text="\u25a0  停转", command=self.cmd_stop).pack(side="left", padx=8)
        ttk.Button(act, text="查询状态", command=self.cmd_status).pack(side="left", padx=8)
        ttk.Button(act, text="测链路", command=self.cmd_ping).pack(side="left", padx=8)

        self.lbl_last = ttk.Label(act, text="上次任务: —", foreground=FG_DIM, font=UI_FONT)
        self.lbl_last.pack(side="right")

        # ===== 4. 手动命令 =====
        man = ttk.Frame(self.root, padding=(12, 4, 12, 4))
        man.pack(fill="x")
        ttk.Label(man, text="手动命令:").pack(side="left")
        self.ent_manual = ttk.Entry(man, font=MONO)
        self.ent_manual.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.ent_manual.bind("<Return>", lambda e: self.send_manual())
        ttk.Button(man, text="发送", width=8, command=self.send_manual).pack(side="left")

        # ===== 5. 日志 =====
        logf = ttk.Frame(self.root, padding=(12, 4, 12, 12))
        logf.pack(fill="both", expand=True)

        head = ttk.Frame(logf)
        head.pack(fill="x")
        ttk.Label(head, text="通信日志").pack(side="left")
        ttk.Checkbutton(head, text="收到完成消息时响铃 + 标题闪烁",
                        variable=self.var_popup).pack(side="right")

        wrap = tk.Frame(logf, bg=BORDER)
        wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.txt = tk.Text(wrap, bg=CARD, fg=FG, font=MONO, relief="flat",
                           wrap="word", padx=8, pady=6, insertbackground=FG)
        self.txt.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb = ttk.Scrollbar(wrap, command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.configure(yscrollcommand=sb.set)

        self.txt.tag_configure("tx", foreground=TX_FG)
        self.txt.tag_configure("rx", foreground=OK_FG)
        self.txt.tag_configure("done", foreground=ACCENT_D,
                               font=("Consolas", 10, "bold"))
        self.txt.tag_configure("err", foreground=ERR_FG)
        self.txt.tag_configure("dim", foreground=FG_DIM)

        self.log("就绪。先在 Windows 里配对 HC-05(配对码用 AT+PSWD? 查, 可能不是 1234), 再选『传出』COM 口点连接。", "dim")

    # ------------------------------------------------------------
    def _build_speed_card(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        inner = tk.Frame(card, bg=CARD, padx=12, pady=10)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text="转速(档位)", bg=CARD, fg=FG, font=UI_FONT_T).pack(anchor="w")

        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        self.lbl_gear = tk.Label(row, text="30", bg=CARD, fg=ACCENT_D,
                                 font=("Consolas", 20, "bold"), width=3, anchor="e")
        self.lbl_gear.pack(side="left")
        tk.Label(row, text="/ %d 档" % MOTOR_GEAR_MAX, bg=CARD, fg=FG_DIM,
                 font=UI_FONT).pack(side="left", padx=(4, 0))
        tk.Label(row, textvariable=self.var_duty, bg=CARD, fg=FG_DIM,
                 font=UI_FONT).pack(side="right")

        tk.Scale(inner, from_=0, to=MOTOR_GEAR_MAX, orient="horizontal",
                 variable=self.var_gear, command=self._on_gear,
                 bg=CARD, fg=FG, troughcolor="#E7DBCB", highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 showvalue=False, bd=0).pack(fill="x", pady=(4, 0))
        self._on_gear()

        tk.Label(inner, text="1 档 = 42.0%%  ·  %d 档 = 60.0%%" % MOTOR_GEAR_MAX,
                 bg=CARD, fg=FG_DIM, font=UI_FONT_S).pack(anchor="w")
        tk.Label(inner, text="最低档已高于这台电机的启动门槛(约 40%)",
                 bg=CARD, fg=FG_DIM, font=UI_FONT_S).pack(anchor="w")

    # ------------------------------------------------------------
    def _build_time_card(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        inner = tk.Frame(card, bg=CARD, padx=12, pady=10)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text="旋转时长(毫秒)", bg=CARD, fg=FG, font=UI_FONT_T).pack(anchor="w")

        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Entry(row, textvariable=self.var_ms, width=10, font=("Consolas", 14),
                 bg="#FFFFFF", fg=FG, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT,
                 insertbackground=FG, justify="right").pack(side="left", ipady=4)
        tk.Label(row, text="ms", bg=CARD, fg=FG_DIM, font=UI_FONT).pack(side="left", padx=(4, 0))
        self.lbl_sec = tk.Label(row, text="= 5.0 秒", bg=CARD, fg=FG_DIM, font=UI_FONT)
        self.lbl_sec.pack(side="left", padx=(10, 0))
        self.var_ms.trace_add("write", lambda *a: self._on_ms())

        quick = tk.Frame(inner, bg=CARD)
        quick.pack(fill="x", pady=(8, 0))
        for text, ms in [("1 秒", 1000), ("3 秒", 3000), ("5 秒", 5000),
                         ("10 秒", 10000), ("30 秒", 30000), ("1 分", 60000)]:
            ttk.Button(quick, text=text, width=6,
                       command=lambda m=ms: self.var_ms.set(str(m))).pack(side="left", padx=(0, 4))

        tk.Label(inner, text="范围 %d ~ %d ms (最长 10 分钟)" % (RUN_MS_MIN, RUN_MS_MAX),
                 bg=CARD, fg=FG_DIM, font=UI_FONT_S).pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------
    def _on_gear(self, _=None):
        g = int(self.var_gear.get())
        self.lbl_gear.configure(text=str(g))
        self.var_duty.set("占空比 %.1f%%" % (gear_to_duty(g) / 10.0))

    def _on_ms(self):
        try:
            self.lbl_sec.configure(text="= %.1f 秒" % (int(self.var_ms.get()) / 1000.0))
        except ValueError:
            self.lbl_sec.configure(text="= ?")

    # ------------------------------------------------------------
    def log(self, text, tag="dim"):
        prefix = {"tx": "PC >", "rx": "板 <", "done": "完成", "err": "错误", "dim": "    "}[tag]
        self.txt.insert("end", "[%s] %s %s\n" % (time.strftime("%H:%M:%S"), prefix, text), tag)
        self.txt.see("end")

    # ------------------------------------------------------------
    #  串口
    # ------------------------------------------------------------
    def refresh_ports(self):
        from serial.tools import list_ports
        items, preferred = [], None
        for p in list_ports.comports():
            desc = p.description or ""
            items.append("%s  %s" % (p.device, desc))
            hay = ("%s %s" % (p.device, desc)).lower()
            if any(k in hay for k in ("hc-05", "hc05", "dev b", "bluetooth", "蓝牙")):
                if preferred is None or "dev b" in hay:
                    preferred = items[-1]
        self.cmb_port["values"] = items
        if items:
            self.cmb_port.set(preferred or items[0])
        else:
            self.cmb_port.set("")
            self.log("没找到任何 COM 口。HC-05 配对了吗? Windows 蓝牙设置里先配对(配对码用 AT+PSWD? 查, 可能不是 1234)。", "err")

    def toggle_conn(self):
        if self.ser is None:
            self.do_connect()
        else:
            self.do_disconnect()

    def do_connect(self):
        import serial
        sel = self.cmb_port.get().strip()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个 COM 口")
            return
        port = sel.split()[0]
        baud = int(self.cmb_baud.get())
        try:
            self.ser = serial.Serial(port=port, baudrate=baud, bytesize=8,
                                     parity="N", stopbits=1, timeout=0.2,
                                     write_timeout=1.0)
        except Exception as e:
            self.ser = None
            self.log("打开 %s 失败: %s" % (port, e), "err")
            messagebox.showerror("连接失败", "打不开 %s\n\n%s\n\n常见原因:\n"
                                 "1) 选成了『传入』的口, 要选『传出』(Dev B)\n"
                                 "2) 别的软件(串口助手 / Keil)正占着这个口\n"
                                 "3) HC-05 没供电 或 没配对成功" % (port, e))
            return

        self.running = True
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.btn_conn.configure(text="断开")
        self.lbl_conn.configure(text="\u25cf 已连接 %s @ %d" % (port, baud), foreground=OK_FG)
        self.log("已打开 %s, %d 8N1" % (port, baud))

    def do_disconnect(self):
        self.running = False
        time.sleep(0.03)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.btn_conn.configure(text="连接")
        self.lbl_conn.configure(text="\u25cf 未连接", foreground=ERR_FG)
        self.log("已断开")

    def _read_loop(self):
        while self.running and self.ser:
            try:
                data = self.ser.readline()
            except Exception as e:
                self.rx_queue.put(("err", "读取失败: %s" % e))
                break
            if data:
                text = data.decode("ascii", "ignore").strip()
                if text:
                    self.rx_queue.put(("rx", text))

    def _pump_rx(self):
        try:
            while True:
                kind, text = self.rx_queue.get_nowait()
                if kind == "err":
                    self.log(text, "err")
                elif text.startswith("DONE"):
                    self.log(text + "     <<< 电机已按命令完成旋转", "done")
                    self.lbl_last.configure(text="上次任务: 已完成 " + text[5:].strip(),
                                            foreground=OK_FG)
                    if self.var_popup.get():
                        self.root.bell()
                        self._flash_title()
                elif text.startswith("ERR"):
                    self.log(text, "err")
                elif text.startswith("READY"):
                    self.log(text + "     <<< 开发板已上电就绪", "done")
                else:
                    self.log(text, "rx")
        except queue.Empty:
            pass
        self.root.after(60, self._pump_rx)

    def _flash_title(self):
        self.root.title("\u2714  旋转完成!  -  电机蓝牙控制工具")
        self.root.after(2500, lambda: self.root.title("电机蓝牙控制工具  ·  STM32 + HC-05"))

    # ------------------------------------------------------------
    def send(self, cmd):
        if self.ser is None:
            messagebox.showwarning("提示", "还没连接串口")
            return False
        try:
            self.ser.write((cmd + "\r\n").encode("ascii"))
        except Exception as e:
            self.log("发送失败: %s" % e, "err")
            return False
        self.log(cmd, "tx")
        return True

    # ------------------------------------------------------------
    def cmd_run(self):
        g = int(self.var_gear.get())
        try:
            ms = int(self.var_ms.get())
        except ValueError:
            messagebox.showwarning("提示", "时长必须是整数毫秒")
            return
        if not (RUN_MS_MIN <= ms <= RUN_MS_MAX):
            messagebox.showwarning("提示", "时长要在 %d ~ %d ms 之间" % (RUN_MS_MIN, RUN_MS_MAX))
            return
        if self.send("RUN%d,%d" % (g, ms)):
            self.lbl_last.configure(text="执行中: %d 档 / %.1f 秒" % (g, ms / 1000.0),
                                    foreground=FG)

    def cmd_spd(self):
        self.send("SPD%d" % int(self.var_gear.get()))

    def cmd_stop(self):
        self.send("STOP")

    def cmd_status(self):
        self.send("STATUS")

    def cmd_ping(self):
        self.send("PING")

    def send_manual(self):
        cmd = self.ent_manual.get().strip()
        if cmd and self.send(cmd):
            self.ent_manual.delete(0, "end")

    def on_close(self):
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()


# ================================================================
def main():
    root = tk.Tk()
    MotorBTApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
