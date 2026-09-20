# -*- coding: utf-8 -*-
"""
================================================================
 电机 BLE 蓝牙控制工具   (电脑端 -> BLE 模块 -> STM32)
================================================================

和 motor_bt_gui.py 的区别
    motor_bt_gui.py   走【经典蓝牙】: 配对后出现 COM 口, 用 pyserial 收发
    本工具            走【低功耗蓝牙 BLE】: 不出现 COM 口, 用 bleak 直接读写 GATT

    市面上有一批"披着 HC-05 皮的 BLE 模块"(固件号 hc05V2.3_le),
    它永远不会产生 COM 口, 只能用本工具这种方式通讯。
    判断方法: AT+VERSION? 回 +VERSION:hc05V2.3_le 就是这一批。

通讯方式
    服务 0000FFE0-0000-1000-8000-00805F9B34FB
    特征 0000FFE1-...   notify + write   <- 写这个发数据, 订阅它收数据
    默认 MTU 下单次写不超过 20 字节, 长命令自动分片;
    收到的数据也可能被分片, 内部做了行缓冲, 按 \r\n 切行。

文本协议(与单片机端一致, 每行以回车换行结尾, 不区分大小写)
    RUN<档位>,<毫秒>    例: RUN30,5000   30 档转 5 秒, 到点自动停转
    SPD<档位>           例: SPD30        只改转速, 一直转(不定时)
    STOP                                立即停转
    STATUS  或  ?                       查询状态
    PING                                测试链路, 回 PONG
开发板主动发回: READY / OK ... / DONE 档位 时长 / ERR ...

使用前提(重要)
    1. 电脑蓝牙已打开
    2. 模块的 EN 脚【必须悬空】。EN 接了 3.3V/5V 会进 AT 模式, 此时数据不透传到单片机
    3. 模块接在 STM32 上: VCC->5V, GND->GND, TXD->PA10, RXD->PA9
    4. BLE 外设同时只服务一个中心设备 —— 手机连着的时候电脑就连不上, 反之亦然

运行:  python motor_ble_gui.py
依赖:  pip install bleak

---------------- 踩过的坑(改代码前务必先看) ----------------
1. 【扫描列表要显示无名设备】
   BLE 设备的名字通常在 scan response 包里, 收到得比主广播包晚。
   扫描时间太短时, 模块能被扫到, 但 name 字段还是空的。
   早先版本里写了 `if d["name"]` 过滤, 结果 HC-05 被自己滤掉了。
   >>> 现在一律显示全部设备, 没名字的标 "(无名)", 靠地址认人。

2. 【建立连接要十几秒, 超时不能设太短】
   本机实测: 从发起连接到拿到 MTU 需要 12~15 秒(Windows 在等模块的广播包)。
   早先默认 timeout=15.0 正好卡在边缘, 表现就是"时好时坏"。
   >>> 现在连接超时 30 秒, 界面上有实时秒数提示, 连接中不要重复点。

3. 【连错设备会把蓝牙栈搞脏】
   点到不可连接的陌生设备(比如随机地址的笔记本), 每个都要空等超时;
   WinRT 在失败的连接之后可能残留挂起操作, 连累后续的扫描也不返回。
   >>> 现在扫描/连接互斥, 加了硬超时和【重置蓝牙栈】按钮。

4. 【FFE1 只支持"带响应写"】
   write_gatt_char(..., response=False) 会静默失败: 不报错, 也没回包。
   必须 response=True。
================================================================
"""

import asyncio
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from bleak import BleakClient, BleakScanner
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False

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
MOTOR_DUTY_MIN = 280          # 1 档  = 28.0%   (单位 0.1%)  必须与固件 Motor.h 一致
MOTOR_DUTY_MAX = 380          # 50 档 = 38.0%                 必须与固件 Motor.h 一致
RUN_MS_MIN = 100
RUN_MS_MAX = 600000

# ---------------- BLE 串口透传的固定 UUID ----------------
BLE_UART_SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
BLE_UART_CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"

MAX_WRITE = 20                # BLE 默认 MTU 下单次写的安全上限(字节)
TITLE = "电机 BLE 控制工具  ·  STM32 + BLE 蓝牙"

# 已经确认过的模块地址, 作为"按地址直连"的默认值。
# 之所以写死在这里: 扫描列表里可能混着一堆陌生设备, 而 BLE 的地址是固定的,
# 直接按地址连最省事, 也不用等扫描。
KNOWN_ADDRESS = "21:F6:47:3A:D8:89"

CONNECT_TIMEOUT = 30.0        # 实测要 12~15 秒, 留足余量
CONNECT_HARD_LIMIT = 40.0     # 协程层面的硬上限, 超了就把循环整个丢掉
SCAN_CHOICES = ["8", "12", "20"]   # 扫描秒数下拉
SCAN_DEFAULT = "12"

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ble_config.json")

ADDR_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


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
class BleLink(object):
    """把 bleak 的 asyncio 接口包成"后台线程 + 回调", 供 tkinter 使用

    线程模型(重要):
        GUI 主线程  --submit()-->  BLE 线程里的 event loop
        BLE 线程    --on_line / on_status--> 只往 queue 里塞(线程安全)
        GUI 用 root.after() 轮询 queue 再刷界面
    BLE 线程绝对不允许直接碰 tkinter 控件, 否则 Windows 上会随机卡死。
    """

    def __init__(self, on_line, on_status):
        self.on_line = on_line
        self.on_status = on_status

        self.loop = asyncio.new_event_loop()
        self.client = None
        self.address = None
        self.connected = False
        self.auto_reconnect = False

        self._buf = b""
        self._closing = False
        self._lock = threading.Lock()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---------------- 线程管理 ----------------
    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro, on_done=None):
        """从 GUI 线程提交一个协程到 BLE 线程执行"""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        if on_done is not None:
            fut.add_done_callback(on_done)
        return fut

    def shutdown(self):
        self._closing = True
        try:
            self.submit(self.disconnect()).result(timeout=2.0)
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass

    def reset_loop(self):
        """强制丢掉当前事件循环线程, 换一个干净的。

        什么时候需要: WinRT 的连接操作挂起时, 旧循环里的协程永远等不到结果,
        后续所有请求都会排队卡住(表现就是"点了没反应")。这种时候只能整个丢掉重来。
        返回 True 表示旧线程干净地退出了, False 表示它被丢弃(线程可能还在前台挂着)。
        注意: 这个方法会阻塞最多 3 秒, 请在后台线程里调用, 不要占着 GUI 线程。
        """
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        self._thread.join(timeout=3.0)
        orphaned = self._thread.is_alive()

        self.client = None
        self.connected = False
        self.address = None
        self._buf = b""
        self._closing = False

        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return not orphaned

    # ---------------- 扫描 ----------------
    async def _scan_impl(self, timeout):
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        out = []
        for dev, adv in found.values():
            name = (dev.name or adv.local_name or "").strip()
            out.append({"address": dev.address.upper(), "name": name,
                        "rssi": adv.rssi if adv.rssi is not None else -127})

        def key(d):
            n = d["name"].upper()
            if n.startswith("HC"):
                pri = 0
            elif n:
                pri = 1
            else:
                pri = 2
            return (pri, -d["rssi"])
        out.sort(key=key)
        return out

    async def scan(self, timeout=12.0):
        """带硬超时: 就算 WinRT 卡住, 也不会让 BLE 线程永远回不来"""
        return await asyncio.wait_for(self._scan_impl(timeout),
                                      timeout=timeout + 10.0)

    # ---------------- 连接 ----------------
    async def connect(self, address):
        await self.disconnect()
        self._closing = False
        self.address = address
        return await asyncio.wait_for(self._connect_once(address),
                                      timeout=CONNECT_HARD_LIMIT)

    async def _connect_once(self, address):
        self._buf = b""
        cli = BleakClient(address, timeout=CONNECT_TIMEOUT,
                          disconnected_callback=self._on_disconnected)
        try:
            await cli.connect()
            await cli.start_notify(BLE_UART_CHAR, self._on_notify)
        except Exception:
            # 失败也要收拾干净, 否则残留的半连接会污染后面的操作
            try:
                await cli.disconnect()
            except Exception:
                pass
            self.client = None
            self.connected = False
            raise

        self.client = cli
        try:
            mtu = cli.mtu_size
        except Exception:
            mtu = 23
        self.connected = True
        self.on_status("已连接  %s  (MTU %s)" % (address, mtu), True)
        return mtu

    def _on_disconnected(self, _client):
        self.connected = False
        self.client = None
        if self._closing:
            return
        self.on_status("连接已断开", False)
        if self.auto_reconnect and self.address:
            self.loop.create_task(self._reconnect())

    async def _reconnect(self):
        for i in range(6):
            await asyncio.sleep(2.0)
            if self._closing:
                return
            self.on_status("正在重连 (%d/6) ..." % (i + 1), False)
            try:
                await self._connect_once(self.address)
                return
            except Exception:
                continue
        self.on_status("自动重连失败, 请手动连接", False)

    async def disconnect(self):
        self._closing = True
        cli = self.client
        self.client = None
        self.connected = False
        if cli is None:
            return
        try:
            await cli.stop_notify(BLE_UART_CHAR)
        except Exception:
            pass
        try:
            await cli.disconnect()
        except Exception:
            pass

    # ---------------- 收发 ----------------
    def _on_notify(self, _sender, data):
        """BLE 线程回调: 收到的数据可能被分片, 必须做行缓冲"""
        with self._lock:
            self._buf += bytes(data)
            self._drain()

    def _drain(self):
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw, self._buf = self._buf[:i], self._buf[i + 1:]
            self._emit(raw)
        if len(self._buf) >= 200:
            raw, self._buf = self._buf, b""
            self._emit(raw)

    def _emit(self, raw):
        txt = raw.decode("utf-8", "ignore").replace("\r", "").strip()
        if txt:
            self.on_line(txt)

    async def write(self, text):
        if self.client is None or not self.connected:
            raise RuntimeError("尚未连接蓝牙模块")
        data = text.encode("ascii", "ignore")
        for i in range(0, len(data), MAX_WRITE):
            await self.client.write_gatt_char(
                BLE_UART_CHAR, data[i:i + MAX_WRITE], response=True)


# ================================================================
class MotorBLEApp(object):
    def __init__(self, root):
        self.root = root
        self.rx_queue = queue.Queue()
        self.labels = []          # 下拉框每一行对应的原始信息
        self.scanning = False
        self.connecting = False
        self.connect_t0 = 0.0
        self.ble = None

        root.title(TITLE)
        root.geometry("900x700")
        root.minsize(840, 640)
        root.configure(bg=BG)

        self.var_gear = tk.IntVar(value=30)
        self.var_ms = tk.StringVar(value="5000")
        self.var_duty = tk.StringVar()
        self.var_popup = tk.BooleanVar(value=True)
        self.var_reconn = tk.BooleanVar(value=True)
        self.var_addr = tk.StringVar(value=KNOWN_ADDRESS)
        self.var_scan = tk.StringVar(value=SCAN_DEFAULT)

        self._init_style()
        self._build_ui()

        if HAS_BLEAK:
            self.ble = BleLink(
                on_line=lambda s: self.rx_queue.put(("rx", s)),
                on_status=lambda s, ok: self.rx_queue.put(("status", (s, ok))))
            self.ble.auto_reconnect = self.var_reconn.get()
            self.log("就绪。可以点【扫描】找设备, 也可以直接用下面的【按地址直连】。", "dim")
        else:
            self.log("缺少 bleak 库, 无法使用。请先运行: pip install bleak", "err")

        self._restore_last()
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
        # ===== 1. 设备连接条 =====
        top = ttk.Frame(self.root, padding=(12, 10, 12, 2))
        top.pack(fill="x")

        ttk.Label(top, text="蓝牙设备:").pack(side="left")
        self.cmb_dev = ttk.Combobox(top, width=40, font=UI_FONT, state="readonly")
        self.cmb_dev.pack(side="left", padx=(4, 6))

        self.cmb_scan = ttk.Combobox(top, width=3, font=UI_FONT, state="readonly",
                                     values=SCAN_CHOICES, textvariable=self.var_scan)
        self.cmb_scan.pack(side="left")

        self.btn_scan = ttk.Button(top, text="扫描", width=7, command=self.do_scan)
        self.btn_scan.pack(side="left", padx=(4, 0))

        self.btn_conn = ttk.Button(top, text="连接", style="Accent.TButton",
                                   command=self.toggle_conn)
        self.btn_conn.pack(side="left", padx=(8, 0))

        self.lbl_conn = ttk.Label(top, text="\u25cf 未连接", foreground=ERR_FG,
                                  font=UI_FONT_B)
        self.lbl_conn.pack(side="left", padx=(12, 0))

        ttk.Checkbutton(top, text="断线自动重连", variable=self.var_reconn,
                        command=self._on_reconn).pack(side="right")

        # ===== 1b. 按地址直连(推荐路径) =====
        top2 = ttk.Frame(self.root, padding=(12, 4, 12, 2))
        top2.pack(fill="x")
        ttk.Label(top2, text="按地址直连:").pack(side="left")
        ttk.Entry(top2, textvariable=self.var_addr, width=22, font=MONO).pack(
            side="left", padx=(4, 6))
        self.btn_direct = ttk.Button(top2, text="直连", width=7,
                                     command=self.do_direct)
        self.btn_direct.pack(side="left")
        self.btn_reset = ttk.Button(top2, text="重置蓝牙栈", width=11,
                                    command=self.do_reset)
        self.btn_reset.pack(side="left", padx=(8, 0))
        ttk.Label(top2, text="(连不上时先点这个; 地址在扫描列表的中间一列)",
                  foreground=FG_DIM, font=UI_FONT_S).pack(side="left", padx=(8, 0))

        hint = (
            "接线(务必先断电): 模块 VCC->5V   GND->GND   TXD->PA10   RXD->PA9   "
            "EN 悬空(接高电平会进 AT 模式, 数据就不透传了)\n"
            "BLE 模块不会产生 COM 口, 所以这里直接扫蓝牙设备。BLE 同时只服务一个设备 —— "
            "手机连着时电脑连不上, 反之亦然。\n"
            "注意: 建立连接要 10~15 秒(Windows 在等模块的广播包), 连接中请耐心等, 别重复点。"
        )
        ttk.Label(self.root, text=hint, foreground=FG_DIM, font=UI_FONT_S,
                  justify="left").pack(fill="x", padx=14, pady=(4, 0))

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
        self.btn_spd = ttk.Button(act, text="即时设速", command=self.cmd_spd)
        self.btn_spd.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(act, text="\u25a0  停转", command=self.cmd_stop)
        self.btn_stop.pack(side="left", padx=8)
        self.btn_status = ttk.Button(act, text="查询状态", command=self.cmd_status)
        self.btn_status.pack(side="left", padx=8)
        self.btn_ping = ttk.Button(act, text="测链路", command=self.cmd_ping)
        self.btn_ping.pack(side="left", padx=8)

        # ===== 3b. 标定最低速 =====
        tun = ttk.Frame(self.root, padding=(12, 0, 12, 4))
        tun.pack(fill="x")
        self.btn_tune = ttk.Button(tun, text="\u2460  标定最低速(TUNE)", command=self.cmd_tune)
        self.btn_tune.pack(side="left")
        ttk.Label(tun, text="点它 -> 电机从 65% 自动慢慢降速 -> 看到电机停住就按板上任意按钮",
                  foreground=FG_DIM, font=UI_FONT).pack(side="left", padx=8)

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
        ttk.Button(head, text="清空", width=6, command=self.clear_log).pack(side="right")
        ttk.Checkbutton(head, text="收到完成消息时响铃 + 标题闪烁",
                        variable=self.var_popup).pack(side="right", padx=(0, 8))

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
        self.txt.tag_configure("done", foreground=ACCENT_D, font=("Consolas", 10, "bold"))
        self.txt.tag_configure("err", foreground=ERR_FG)
        self.txt.tag_configure("dim", foreground=FG_DIM)

        self._enable_actions(False)

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

        tk.Label(inner, text="1 档 = %.1f%%  ·  %d 档 = %.1f%%" % (
                     MOTOR_DUTY_MIN / 10.0, MOTOR_GEAR_MAX, MOTOR_DUTY_MAX / 10.0),
                 bg=CARD, fg=FG_DIM, font=UI_FONT_S).pack(anchor="w")
        tk.Label(inner, text="低档靠「启动助推」起转, 所以能比启动门槛更低",
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

    def _on_reconn(self):
        if self.ble:
            self.ble.auto_reconnect = self.var_reconn.get()

    # ------------------------------------------------------------
    def log(self, text, tag="dim"):
        prefix = {"tx": "PC >", "rx": "板 <", "done": "完成",
                  "err": "错误", "dim": "    "}.get(tag, "    ")
        self.txt.insert("end", "[%s] %s %s\n" % (time.strftime("%H:%M:%S"), prefix, text), tag)
        self.txt.see("end")

    def clear_log(self):
        self.txt.delete("1.0", "end")

    def _enable_actions(self, on):
        st = "normal" if on else "disabled"
        for b in (self.btn_run, self.btn_spd, self.btn_stop,
                  self.btn_status, self.btn_ping, self.btn_tune):
            try:
                b.configure(state=st)
            except Exception:
                pass

    def _busy(self, on):
        """扫描/连接期间把入口按钮锁住, 避免并发操作把蓝牙栈搞脏"""
        st = "disabled" if on else "normal"
        for b in (self.btn_scan, self.btn_conn, self.btn_direct):
            try:
                b.configure(state=st)
            except Exception:
                pass

    # ------------------------------------------------------------
    #  配置记忆
    # ------------------------------------------------------------
    def _load_cfg(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self, d):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _restore_last(self):
        last = self._load_cfg().get("last_address")
        if last:
            self.var_addr.set(last)
            self.log("上次连接成功的模块地址: %s (已填在【按地址直连】里)" % last, "dim")
        else:
            self.log("已知模块地址: %s (已填在【按地址直连】里, 直接点【直连】最快)" % KNOWN_ADDRESS,
                     "dim")

    # ------------------------------------------------------------
    #  扫描
    # ------------------------------------------------------------
    def _scan_seconds(self):
        try:
            return float(self.var_scan.get())
        except Exception:
            return 12.0

    def do_scan(self):
        if not self.ble:
            messagebox.showerror("缺少依赖", "未安装 bleak, 请先运行:\n\npip install bleak")
            return
        if self.scanning or self.connecting:
            return
        sec = self._scan_seconds()
        self.scanning = True
        self._busy(True)
        self.btn_scan.configure(text="扫描中")
        self.log("正在扫描 BLE 设备(%.0f 秒) ... 名字要等 scan response 包, 时间短了会漏" % sec, "dim")
        self.ble.submit(self.ble.scan(sec), self._scan_done)

    def _scan_done(self, fut):
        """注意: 这个回调跑在 BLE 线程, 只能往队列里塞"""
        try:
            self.rx_queue.put(("scan", fut.result()))
        except Exception as e:
            self.rx_queue.put(("scan_fail", "%s: %s" % (type(e).__name__, e)))

    def _apply_scan(self, res):
        self.scanning = False
        self._busy(False)
        self.btn_scan.configure(text="扫描")

        if not res:
            self.log("没扫到任何 BLE 设备。检查: ① 电脑蓝牙开了吗 ② 模块通电了吗 "
                     "③ 是不是被手机连着(一个模块同时只能连一个设备) ④ 点【重置蓝牙栈】再试", "err")
            return

        named = [d for d in res if d["name"]]
        self.labels = []
        items = []
        for d in res:
            label = "%s   |   %s   |   %d dBm" % (d["name"] or "(无名)", d["address"], d["rssi"])
            self.labels.append(d)
            items.append(label)

        self.cmb_dev["values"] = items

        last = (self._load_cfg().get("last_address") or "").upper()
        want = (self.var_addr.get().strip() or KNOWN_ADDRESS).upper()
        pick, hit_target = None, False
        for d, label in zip(self.labels, items):
            if d["address"] == want:
                pick, hit_target = label, True
                break
        if pick is None:
            for d, label in zip(self.labels, items):
                if last and d["address"] == last:
                    pick = label
                    break
        if pick is None:
            pick = items[0]
        self.cmb_dev.set(pick)

        self.log("扫到 %d 个设备(其中 %d 个有名字)。已选中: %s"
                 % (len(items), len(named), pick), "dim")
        if hit_target:
            self.log("目标模块 %s 在列表里, 直接点【连接】即可。" % want, "dim")
        else:
            self.log("列表里没有 %s。可以点【重置蓝牙栈】后重扫, 或把扫描秒数调到 20 秒。" % want,
                     "dim")
        if len(named) < len(items):
            self.log("没名字的设备也列出来了 —— BLE 的名字在 scan response 包里, 有时收不到。"
                     "认人请看中间那列地址。", "dim")

    # ------------------------------------------------------------
    #  连接
    # ------------------------------------------------------------
    def _selected_address(self):
        label = self.cmb_dev.get().strip()
        if not label:
            return None
        m = ADDR_RE.search(label)
        return m.group(1).upper() if m else None

    def toggle_conn(self):
        if not self.ble:
            messagebox.showerror("缺少依赖", "未安装 bleak, 请先运行:\n\npip install bleak")
            return
        if self.ble.connected:
            self.log("正在断开 ...", "dim")
            self.ble.submit(self.ble.disconnect())
            self._on_conn_lost()
        else:
            addr = self._selected_address()
            if not addr:
                messagebox.showwarning("提示", "请先点【扫描】选一个设备, "
                                              "或者用上面的【按地址直连】")
                return
            self.do_connect(addr)

    def do_direct(self):
        addr = self.var_addr.get().strip().upper()
        if not ADDR_RE.fullmatch(addr):
            messagebox.showwarning("提示", "地址格式不对, 应该是 AA:BB:CC:DD:EE:FF 这样")
            return
        if not self.ble:
            messagebox.showerror("缺少依赖", "未安装 bleak, 请先运行:\n\npip install bleak")
            return
        if self.connecting:
            return
        if self.ble.connected:
            self.ble.submit(self.ble.disconnect())
            self._on_conn_lost()
        self.do_connect(addr)

    def do_connect(self, addr):
        self.connecting = True
        self.connect_t0 = time.time()
        self._busy(True)
        self.btn_conn.configure(text="连接中")
        self.log("正在连接 %s ... (可能要 10~15 秒, 请稍等)" % addr, "dim")
        self.ble.submit(self.ble.connect(addr), self._connect_done)
        self.root.after(3000, self._tick_connect)

    def _tick_connect(self):
        """连接过程中每 3 秒报一次已等待时长, 免得用户以为卡死了"""
        if not self.connecting:
            return
        el = time.time() - self.connect_t0
        self.lbl_conn.configure(text="\u25cf 连接中 %.0fs" % el, foreground=ACCENT_D)
        if el > 6:
            self.log("  还在连接中, 已等待 %.0f 秒 ..." % el, "dim")
        if el > CONNECT_HARD_LIMIT + 5:
            self.connecting = False
            self._busy(False)
            self.log("连接超时太久, 建议点【重置蓝牙栈】后重试。", "err")
            return
        self.root.after(3000, self._tick_connect)

    def _connect_done(self, fut):
        try:
            fut.result()
            self.rx_queue.put(("connected", True))
        except Exception as e:
            self.rx_queue.put(("conn_fail", "%s: %s" % (type(e).__name__, e)))

    def _on_conn_lost(self):
        self.connecting = False
        self._busy(False)
        self.lbl_conn.configure(text="\u25cf 未连接", foreground=ERR_FG)
        self.btn_conn.configure(text="连接")
        self._enable_actions(False)

    # ------------------------------------------------------------
    #  重置蓝牙栈
    # ------------------------------------------------------------
    def do_reset(self):
        if not self.ble:
            return
        self.log("正在重置蓝牙栈(丢掉旧的连接上下文, 重建一个干净的) ...", "dim")
        self._busy(True)
        self.connecting = False
        threading.Thread(target=self._reset_worker, daemon=True).start()

    def _reset_worker(self):
        try:
            clean = self.ble.reset_loop()
            msg = "蓝牙栈已重置。" + ("" if clean else "(旧的连接上下文被丢弃, 它可能还在后台超时)")
            self.rx_queue.put(("log", msg, "dim"))
            self.rx_queue.put(("reset_done", None))
        except Exception as e:
            self.rx_queue.put(("err", "重置失败: %s" % e))
            self.rx_queue.put(("reset_done", None))

    # ------------------------------------------------------------
    def _pump_rx(self):
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()

                if kind == "rx":
                    self._on_rx_line(payload)
                elif kind == "err":
                    self.log(payload, "err")
                elif kind == "log":
                    self.log(payload[0], payload[1])
                elif kind == "status":
                    text, ok = payload
                    self.lbl_conn.configure(
                        text="\u25cf " + text, foreground=OK_FG if ok else ERR_FG)
                    if not ok:
                        self._enable_actions(False)
                elif kind == "connected":
                    self.connecting = False
                    self._busy(False)
                    self.lbl_conn.configure(text="\u25cf 已连接", foreground=OK_FG)
                    self.btn_conn.configure(text="断开")
                    self._enable_actions(True)
                    addr = self.ble.address
                    if addr:
                        self._save_cfg({"last_address": addr})
                        self.var_addr.set(addr)
                    self.log("连接成功。现在可以发命令了。", "dim")
                    self.log("如果发命令没反应, 检查模块的 EN 脚是不是悬空(接高电平会进 AT 模式)。",
                             "dim")
                elif kind == "conn_fail":
                    self.connecting = False
                    self._busy(False)
                    self.lbl_conn.configure(text="\u25cf 连接失败", foreground=ERR_FG)
                    self.btn_conn.configure(text="连接")
                    self._enable_actions(False)
                    self.log("连接失败: %s" % payload, "err")
                    self.log("排查顺序: ① 点【重置蓝牙栈】再连 ② 确认没被手机连着"
                             " ③ 模块通电了吗、离电脑近不近 ④ EN 必须悬空", "err")
                    self.log("如果反复超时: 去 设置→蓝牙和其他设备 把蓝牙开关关掉再打开"
                             "(或重启电脑), 然后重新点【直连】。", "err")
                elif kind == "conn_btn":
                    self.btn_conn.configure(text=payload, state="normal")
                elif kind == "scan_fail":
                    self.scanning = False
                    self._busy(False)
                    self.btn_scan.configure(text="扫描")
                    self.log("扫描失败: %s" % payload, "err")
                    self.log("这是蓝牙栈卡住的表现, 点【重置蓝牙栈】后再扫一次。", "err")
                elif kind == "scan":
                    self._apply_scan(payload)
                elif kind == "reset_done":
                    self._busy(False)
                    self._on_conn_lost()
                    self.log("重置完成, 现在可以重新扫描或直连。", "dim")
        except queue.Empty:
            pass
        self.root.after(60, self._pump_rx)

    def _on_rx_line(self, text):
        if text.startswith("TUNE MARK"):
            self.log(text + "     <<< 已记录「电机停转点」", "done")
            return
        if text.startswith("TUNE DONE"):
            self.log(text + "     <<< 标定完成, 已切到新的最低档", "done")
            self._parse_tune_done(text)
            return
        if text.startswith("TUNE WARN"):
            self.log(text + "     <<< 能用的速度区间太窄, 请把这个结果发给我", "err")
            return
        if text.startswith("TUNE"):
            self.log(text, "rx")
            return
        if text.startswith("DONE"):
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

    def _flash_title(self):
        self.root.title("\u2714  旋转完成!  -  电机 BLE 控制工具")
        self.root.after(2500, lambda: self.root.title(TITLE))

    # ------------------------------------------------------------
    def send(self, cmd):
        if not self.ble or not self.ble.connected:
            messagebox.showwarning("提示", "还没连接蓝牙模块")
            return False
        try:
            self.ble.submit(self.ble.write(cmd + "\r\n"))
        except Exception as e:
            self.log("发送失败: %s" % e, "err")
            return False
        self.log(cmd, "tx")
        return True

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

    def cmd_tune(self):
        if not messagebox.askokcancel(
                "标定最低转速",
                "点确定后:\n\n"
                "1. 电机先停一下, 然后被「助推」到 65% 转起来\n"
                "2. 之后自动往下降速:\n"
                "     45% 以上 -> 每步降 5%, 停 0.8 秒\n"
                "     45% 以下 -> 每步降 1%, 停 0.9 秒\n"
                "3. 你盯着电机, 看到它明显停住/不转了,\n"
                "   立刻按开发板上任意一个按钮\n"
                "4. 板子把当时的数值发回来, 并自动用新的最低档\n"
                "   转起来给你试 (掉电复位, 不改固件)\n\n"
                "全程约 35 秒。中途想放弃就点【停转】。\n\n现在开始吗?"):
            return
        if self.send("TUNE"):
            self.lbl_last.configure(text="标定中: 电机停住后按板上任意按钮", foreground=FG)

    def _parse_tune_done(self, text):
        import re as _re
        m = _re.search(r"mark=(\d+)\s+floor=(\d+)\s+span=(\d+)", text)
        if not m:
            return
        mark, floor, span = (int(x) for x in m.groups())
        self.lbl_last.configure(
            text="标定结果: 停转点 %.1f%% | 新最低档 %.1f%% | 可用区间 %d" % (
                mark / 10.0, floor / 10.0, span),
            foreground=OK_FG)
        if span == 0:
            self.log("区间为 0 说明按键时电机还转得很快, 请重新点一次标定, 等电机真的停了再按。", "err")
        elif span < 50:
            self.log("可用区间只有 %d, 50 个档位会有重复 —— 建议改小档位总数, 把这个数字发我。" % span, "err")

    def send_manual(self):
        cmd = self.ent_manual.get().strip()
        if cmd and self.send(cmd):
            self.ent_manual.delete(0, "end")

    # ------------------------------------------------------------
    def on_close(self):
        if self.ble:
            self.ble.shutdown()
        self.root.destroy()


# ================================================================
def main():
    root = tk.Tk()
    MotorBLEApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
