# 项目上下文

> 本文档维护项目级上下文信息，供 agent 在开始非简单任务前读取。
> 只保留当前有效信息，不写成无限追加日志。
> 文件过长时先压缩为摘要，再归档旧内容到 agent_memory/archive/ 。

## 项目概述
STM32F103C8T6 直流电机调速 + 蓝牙遥控工作区，基于江协科技《STM32入门教程》第 6-5 节「PWM 驱动直流电机」改造而来。
下位机：TB6612FNG 驱动直流电机，按**档位号**（0~50）调速，带"启动助推"解决低占空比起步门槛，并支持 `TUNE` 自动标定最低可维持转速；
4 针 I2C OLED 显示状态，两个按键可做标记/交互；蓝牙接收上位机文本指令。
上位机：电脑端 Python GUI（BLE 版）通过 BLE 透传下发运行/停机/调速/标定命令，并显示回包与运行倒计时。

## 技术栈
- 下位机：C / Keil MDK5（uVision5）/ **ARMCC V5.06 update 5 (build 528)**（AC5，不是 AC6）/ STM32 **标准外设库 SPL**（不是 HAL）。
- 上位机：Python 3 + **bleak 3.0.2**（WinRT BLE 后端，含 winrt-* 依赖）+ tkinter 标准库。零额外 UI 依赖。
- 链路：**BLE（GATT 透传）**，非经典蓝牙 SPP；模块对下仍是 9600 8N1 串口透传，固件侧不感知差异。
- 版本管理：本工作区**尚未初始化 git**；推送 GitHub 的流程见 `~/.workbuddy/skills/github-push-local-project/SKILL.md`。

## 目录结构
- `6-5 PWM驱动直流电机-改造版/` —— Keil 工程（Target 1，产物 `Objects/Project.axf`）
  - `User/`：`main.c`（命令解析 `MatchWord` + 主循环 + TUNE 标定状态机，约 15.5KB）、`stm32f10x_conf.h`、`stm32f10x_it.c/h`
  - `Hardware/`：`Motor.c/h`（档位/占空比换算、启动助推、标定接口）、`PWM.c/h`、`Serial.c/h`（USART1）、`Key.c/h`、`LED.c/h`、`OLED.c/h` + `OLED_Font.h`
  - `System/`：`Delay.c/h`
  - `Start/`、`Library/`：启动文件与 SPL 库，**只读，不要改**
  - `Objects/`、`Listings/`：编译产物与列表文件（可再生，约 110 个文件：35 `.o` / 34 `.crf` / 35 `.d` / `.map` / `.lst` / `.lnp` / `.dep` / `.htm`）
  - `Project.uvprojx`、`Project.uvoptx`：工程与调试配置
  - `Project.uvguix.Admin`、`Project.uvguix.zby`：编辑器窗口布局缓存（个人痕迹，非必需）
  - `build_log.txt`：命令行编译日志；`keilkill.bat`：清理编译产物（会删 `.o/.d/.crf/.map/.axf/.lst/.htm` 等）
- `电脑端蓝牙控制工具/` —— 电脑端 Python 工具（5 个文件）
  - `motor_ble_gui.py`（约 43KB）：**当前在用**的 BLE GUI，已实机验证全链路通
  - `motor_bt_gui.py`（约 19KB）：串口 pyserial 版 GUI，**备用**，换真经典蓝牙模块时用
  - `hc05_at_tool.py`（约 29KB）：AT 指令体检工具（自动探测波特率 / 一键体检 / 一键修复 `ROLE`）
  - `_ble_probe.py`：BLE 扫描 + GATT 服务枚举探针
  - `ble_config.json`：记录上次连接地址字段 `last_address`（当前 `21:F6:47:3A:D8:89`）
- `用户手册.pdf`（1.8MB）：参考资料，放在根目录**尚未归类**
- `agent_memory/`：本目录（agent 上下文/进度/问题）；`archive/` 存被压缩归档的旧内容
- `.workbuddy/`：WorkBuddy 项目记忆与技能，**不属于交付物，不要删**

## 关键约定
1. **原始教程源码一律不改**：`桌面\STM32入门教程资料\...\STM32Project-无注释版\` 下的工程只读；任何改造先 `cp -r` 副本到本工作区再动手。
2. **改动后必须编译验证**：`D:\Keil5\UV4\UV4.exe -b "<工程>.uvprojx" -j0 -o build_log.txt`，要求退出码 0 且日志 `0 Error(s), 0 Warning(s)`。当前基线：`Code=7636 RO-data=1788 RW-data=104 ZI-data=1744`。
3. **Keil 源码存 GBK 编码**（本机 Keil 编辑器为 ANSI）：UTF-8 中文注释在 Keil 里显示乱码。写完必须扫一遍——GBK 转换后任何一行行尾字节不能是 `0x5C`（会把 `//` 注释的换行吞掉）。
4. **串口字符串必须纯 ASCII**：源码是 GBK，字符串里带中文会在上位机显示乱码。
5. **档位宏必须三处同步**：固件 `Hardware/Motor.h` 的 `MOTOR_GEAR_MAX` / `MOTOR_DUTY_MIN` / `MOTOR_DUTY_MAX` ↔ PC 工具 `motor_ble_gui.py`、`motor_bt_gui.py` 里的同名常量 ↔ 本文件的参数记录。（2026-09-20 漏同步过一次，界面占空比显示一直是旧值。）
6. **不得把未上机验证的结论写成事实**：速度手感、占空比阈值这类结论必须标注实测日期与条件。

## 硬件与接线约定
- 芯片 STM32F103C8T6，标准外设库；电机驱动 **TB6612FNG**：`PA4→AIN1`、`PA5→AIN2`、`PA2→PWMA(TIM2_CH3)`。
- 按键：`PB1`、`PB11`（上拉输入，按下为低电平）。
- 显示屏：4 针 I2C OLED，16 字 × 4 行（`SCL→PB8`、`SDA→PB9`）。
- 蓝牙：模块接 USART1，**TXD→PA10、RXD→PA9**；EN 悬空（=透传模式），STATE 暂未使用。
- PWM：`PSC=4-1 / ARR=1000-1` → 约 18kHz、0.1% 分辨率。**不要**把 ARR 退回 100（分辨率只剩 1%，档位会大量重复）。

## 通信协议约定（PC ↔ 蓝牙 ↔ 板）
- 文本行协议，`\r\n` 结尾，**大小写不敏感**（`MatchWord` 逐词匹配），9600 8N1。
- PC→板：`RUN<档位>,<毫秒>`、`SPD<档位>`、`STOP`、`STATUS`（或 `?`）、`PING`、`TUNE`
- 板→PC：`READY`、`OK ...`、`DONE <档位> <毫秒>`、`ERR <原因>`、`STATUS gear=.. duty=.. run=.. left_ms=..`
- 时长范围 100~600000 ms；`DONE` 原样回命令里的档位与时长。
- **改转速的唯一入口是档位号（0~50）**，占空比由 `Motor_GetDuty()` 换算，PC 端不直接发占空比。
  唯一例外：`TUNE` 标定期间板子内部用 `Motor_SetRaw()` 直接扫占空比，PC 端仍不发占空比。
- TUNE 报文：`OK TUNE start=.. stop=.. fine_step=.. every_ms=..` / `TUNE <duty>` / `TUNE MARK <duty>` /
  `TUNE no_mark min=..` / `TUNE DONE mark=.. floor=.. span=..` / `TUNE WARN span too small -> gears will repeat` / `TUNE ABORT`。

## 蓝牙模块现状（重要结论，勿重复排查）
- 手上这块**标称 HC-05、实为 BLE-only 模块**（克隆固件 `hc05V2.3_le`，无 BR/EDR 射频）。
  判据：系统枚举为 `BTHLE\DEV_21F6473AD889`，服务 `{00001800}/{00001801}/{0000FFE0-...}`，
  BTHENUM 下**没有任何记录**，全系统 `{00001101}`（SPP）实例 **0 个**；实测 FFE0 下 `FFE1`(notify+write) + `FFE2`(write)。
  参数：`NAME=HC-05`、`UART=9600,0,0`、`PSWD=123456`（不是 1234）、`ADDR=21:F6:47:3A:D8:89`。
- **BLE 没有 RFCOMM 层 → Windows 永远不会给它建 COM 口**，"配对成功但没串口"不是配置问题。**不能靠刷固件变成经典蓝牙**（芯片本身 BLE-only）。
- 供电：手上是**带排针底板**的模块，底板丝印 `POWER 3.6-5V` → **必须接 5V**（曾误接 3.3V）。裸模块才只能接 3.3V。
- 真经典蓝牙要用 COM 口方案时，只能另买模块，买前必须确认规格写 **Bluetooth 2.0/2.1 BR/EDR + SPP**。

## 硬件限制与物理门槛（不要重复踩坑）
- 该直流电机**约 40% 占空比才能从静止转起来**（静摩擦门槛）；但**维持转动门槛低得多**（动摩擦 < 静摩擦）。
  所以低速档靠 **"启动助推"(kick-start)**：从停转启动时先给 `MOTOR_KICK_DUTY=650` 约 `MOTOR_KICK_MS=300` 推起来，再降到目标档。
  实现位置：`Motor_SetGear()` 只在 `CurDuty == 0` 时助推，`Motor_Tick()` 每 10ms 收尾。
- 当前档位参数（2026-09-20）：`MOTOR_GEAR_MAX=50`、`MOTOR_DUTY_MIN=280 (28.0%)`、`MOTOR_DUTY_MAX=380 (38.0%)`、
  `MOTOR_KICK_DUTY=650 (65.0%)`、`MOTOR_KICK_MS=300`、`MOTOR_TICK_MS=10`。
- **"1 档和 50 档速度区别很小"是正常现象不是 bug**：占空比区间只有 28→38，比值仅 1.36 倍。报这句话时先想这一点，别怀疑 PWM 没生效。
  真正确认 PWM 生效的判据：**按减速退到 0 档，电机必须停**。
- 电机**无编码器 → 无法自动检测堵转**，标定只能靠人眼观察 + 按键标记。

## 已知约束
- `MOTOR_DUTY_MIN/MAX` 改小虽让最低档更慢，但区间会变窄、50 个档位会出现重复；`TUNE` 报 `span too small` 说明该把 `MOTOR_GEAR_MAX` 调小。
- `TUNE` 把 1 档下限改成 `mark + 3% 余量`，**只在运行期生效，掉电复位**，不写进 Flash。
- 人眼看到"电机停住"再按键会晚 1~2%，故余量必须 ≥3%；低区间必须用 1% 小步长（当前 45% 以上每步 5%/停 800ms，45% 以下每步 1%/停 900ms，最低 13%）。
- 上位机 BLE 链路：连接建立需 12~15 秒（Windows 等外设广播，实测 14.6~14.9s），故 `BleakClient` 超时设 30 秒；往返约 130ms。
- 工作区根目录目前**文件未归类**（手册、日志、编辑器缓存与源码混放），待用户确认整理范围后再动。
