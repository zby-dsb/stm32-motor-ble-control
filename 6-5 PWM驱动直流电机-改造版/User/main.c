#include "stm32f10x.h"                  // Device header
#include "Delay.h"
#include "OLED.h"
#include "Motor.h"
#include "Key.h"
#include "Serial.h"

/*
 * ============================ 功能总览 ============================
 * 按键(任何时刻都能用):
 *   PB1  短按 +1 档 / 长按快速加速
 *   PB11 短按 -1 档 / 长按快速减速
 *
 * 电脑通过 HC-05 蓝牙发文本命令(每行以回车或换行结尾, 不区分大小写):
 *   RUN<档位>,<毫秒>   例 RUN30,5000  -> 30 档转 5 秒, 到点自动停转并回 DONE
 *   SPD<档位>          例 SPD30       -> 只改转速, 一直转(不定时)
 *   STOP                              -> 立即停转
 *   STATUS 或 ?                       -> 查询当前状态
 *   PING                              -> 回 PONG, 用来测链路
 *   TUNE                              -> 进入"自动标定"(见下)
 *
 * 开发板主动发给电脑的:
 *   READY                     上电就绪(电脑端可以据此判断板子重启了)
 *   OK RUN 30 5000            命令已接受
 *   OK SPD 30 / OK STOP       命令已接受
 *   DONE 30 5000              定时旋转完成(档位和时间就是命令里的那两个数)
 *   ERR ......                命令有问题
 *   STATUS gear=30 duty=311 run=1 left_ms=4200
 *
 * 重要: 定时旋转期间按键依然能插队改速度, 改动立刻生效, 但倒计时照走。
 *
 * ---------------------------- TUNE 自动标定 ----------------------------
 * 目的: 找出"电机能维持转动的最低占空比"这个数字(决定 1 档能有多慢)。
 *
 * 流程:
 *   1. 电脑发 TUNE, 电机立刻停一下, 然后被"助推"到 65% 转起来
 *   2. 之后自动往下降, 每降一次就把当前值发给电脑:  TUNE 630
 *        45% 以上: 每次降 5.0%, 停 800ms  (这段肯定能转, 快速掠过)
 *        45% 以下: 每次降 1.0%, 停 900ms  (临界区间, 慢下来给足反应时间)
 *   3. 你盯着电机, 看到它"明显停住/不转了"的那一瞬间, 按一下任意按钮
 *   4. 板子记录当时的值并回:
 *        TUNE MARK 290         <- 你标记的停转点
 *        TUNE DONE mark=290 floor=320 span=60
 *      floor = 290 + 3% 余量(人眼看"停住"会慢约 1 秒, 所以要留余量)
 *      span  = 最高档 38% 减去 floor, 也就是"还能分出多少档位"
 *      同时把 1 档下限临时改成 floor, 并立刻用新的 1 档转起来给你试手感
 *      (只在内存里, 掉电复位, 不会改源文件里的数字)
 *   5. 如果一路降到 13% 电机都还在转, 板子自己结束并回 TUNE no_mark
 *
 * 注意: 标定期间按键被"征用"了 —— 按一下就是"就是现在, 停住了"。
 *       想中途放弃就发 STOP。
 * =================================================================
 */

/* ==================== 可调参数 ==================== */

#define TICK_MS				10			//主循环周期(ms), 与 Key.h 的 KEY_SCAN_PERIOD_MS 一致

#define RUN_MS_MIN			100			//电脑定时旋转的最短时长
#define RUN_MS_MAX			600000		//电脑定时旋转的最长时长(600000ms = 10 分钟)

#define TUNE_START_DUTY		650			//标定起始占空比(0.1%): 65.0%
#define TUNE_STOP_DUTY		130			//标定最低降到(0.1%): 13.0%
#define TUNE_COARSE_DUTY	450			//高于这个值 = "肯定还能转"的区间, 用大步长快速掠过
#define TUNE_COARSE_STEP	50			//大步长: 每次降 5.0%
#define TUNE_COARSE_MS		800			//大步长停留 800ms
#define TUNE_FINE_STEP		10			//小步长: 每次降 1.0%(留足反应时间, 也保证分辨率)
#define TUNE_FINE_MS		900			//小步长停留 900ms
#define TUNE_MARGIN			30			//标定结果再加 3% 余量(人眼看"停住"会有 1 秒左右延迟)

/* ==================== 运行时状态 ==================== */

uint8_t  Gear = 0;					//当前档位: 0 = 停转, 1~50
uint8_t  RunActive = 0;				//1 = 正在执行电脑下发的"定时旋转"
uint32_t RunRemainMs = 0;			//本次定时还剩多少毫秒
uint8_t  RunCmdGear = 0;			//命令里的档位(完成消息要原样回给电脑)
uint32_t RunCmdMs = 0;				//命令里的时长(完成消息要原样回给电脑)

static uint8_t  TuneActive = 0;		//1 = 正在标定
static uint16_t TuneDuty = 0;		//标定当前占空比
static uint16_t TuneStepLeft = 0;	//距离下一次降速还剩多少毫秒

static char LineBuf[SERIAL_LINE_MAX];		//从串口取到的整行命令
static char RxShow[13];						//OLED 第 4 行显示用(最多 12 个字符)
static char TimeStr[6];						//OLED 第 3 行的时间字符串(5 字符)
static char LastTimeStr[6] = "-----";		//上次显示过的时间, 用来避免重复写屏

static uint8_t  ShownGear = 0xFF;			//屏幕上正在显示的档位(0xFF = 未知)
static uint8_t  ShownTune = 0;				//屏幕上是否处于"标定"画面
static uint16_t ShownDuty = 0xFFFF;			//屏幕上正在显示的占空比

/* ==================== 小工具函数 ==================== */

static char ToUpper(char c)
{
	if (c >= 'a' && c <= 'z')
	{
		return (char)(c - 'a' + 'A');
	}
	return c;
}

static char * SkipSpace(char *p)
{
	while (*p == ' ' || *p == '\t')
	{
		p++;
	}
	return p;
}

/* p 是否以 Word 开头(不区分大小写) */
static uint8_t MatchWord(char *p, char *Word)
{
	while (*Word != '\0')
	{
		if (ToUpper(*p) != ToUpper(*Word))
		{
			return 0;
		}
		p++;
		Word++;
	}
	return 1;
}

/* 从 *pp 处读一个十进制整数; *ok 返回是否真的读到了数字 */
static uint32_t TakeNumber(char **pp, uint8_t *ok)
{
	uint32_t Value = 0;
	uint8_t  Count = 0;
	char    *p = *pp;
	
	while (*p >= '0' && *p <= '9')
	{
		Value = Value * 10 + (uint32_t)(*p - '0');
		if (Value > 9999999)			//防止输入一长串数字导致溢出
		{
			Value = 9999999;
		}
		Count++;
		p++;
	}
	*ok = (Count > 0) ? 1 : 0;
	*pp = p;
	return Value;
}

/* 在第 2 行显示占空比, 形如 28.0% */
static void ShowDuty(uint16_t Duty)
{
	if (Duty > 999)
	{
		Duty = 999;					//第 2 行只留了 2 位整数, 超过 99.9% 就压住
	}
	OLED_ShowNum(2, 7, Duty / 10, 2);
	OLED_ShowChar(2, 9, '.');
	OLED_ShowNum(2, 10, Duty % 10, 1);
}

/* 把 OLED 第 1 / 第 4 行恢复成"平时"的固定文字 */
static void ShowIdleLabels(void)
{
	OLED_ShowString(1, 1, "Gear:");
	OLED_ShowString(1, 9, "/");
	OLED_ShowNum(1, 10, MOTOR_GEAR_MAX, 2);
	OLED_ShowString(4, 1, "RX:          ");
}

/* ==================== 动作 ==================== */

static void ApplyGear(uint8_t NewGear)
{
	if (NewGear > MOTOR_GEAR_MAX)
	{
		NewGear = MOTOR_GEAR_MAX;
	}
	Gear = NewGear;
	Motor_SetGear(Gear);
}

static void StopMotor(void)
{
	RunActive = 0;
	RunRemainMs = 0;
	ApplyGear(0);
}

static void SendStatus(void)
{
	Serial_SendString("STATUS gear=");
	Serial_SendUInt(Gear);
	Serial_SendString(" duty=");
	Serial_SendUInt(Motor_GetDuty(Gear));
	Serial_SendString(" run=");
	Serial_SendUInt(RunActive);
	Serial_SendString(" left_ms=");
	Serial_SendUInt(RunActive ? RunRemainMs : 0);
	Serial_SendLine();
}

/* ==================== TUNE 自动标定 ==================== */

static void Tune_Start(void)
{
	StopMotor();						//先停下来, 清掉定时旋转
	
	TuneActive   = 1;
	TuneDuty     = TUNE_START_DUTY;
	TuneStepLeft = TUNE_COARSE_MS;		//起始点在高区间, 先按大步长的节奏走
	Motor_SetRaw(TuneDuty);				//直接给 65% 把电机推起来
	
	Serial_SendString("OK TUNE start=");
	Serial_SendUInt(TUNE_START_DUTY);
	Serial_SendString(" stop=");
	Serial_SendUInt(TUNE_STOP_DUTY);
	Serial_SendString(" fine_step=");
	Serial_SendUInt(TUNE_FINE_STEP);
	Serial_SendString(" every_ms=");
	Serial_SendUInt(TUNE_FINE_MS);
	Serial_SendLine();
	Serial_SendString("TUNE watch motor, press any key when it stops");
	Serial_SendLine();
}

/*
 * 结束标定
 *   Reason = 1 : 用户按了按钮, 标记"电机停转点"
 *   Reason = 2 : 被 STOP / 新命令打断
 *   Reason = 0 : 一路降到底, 电机始终没停
 */
static void Tune_Finish(uint8_t Reason)
{
	uint16_t MarkDuty = TuneDuty;
	uint16_t NewFloor;
	uint16_t Span;
	
	TuneActive   = 0;
	TuneStepLeft = 0;
	Motor_SetRaw(0);
	ShowIdleLabels();
	ShownTune = 0;
	ShownGear = 0xFF;
	ShownDuty = 0xFFFF;
	
	if (Reason == 2)
	{
		Serial_SendString("TUNE ABORT");
		Serial_SendLine();
		Gear = 0;
		return;
	}
	
	if (Reason == 1)
	{
		/* 用户按了按钮: 这个值就是"电机停转点" */
		Serial_SendString("TUNE MARK ");
		Serial_SendUInt(MarkDuty);
		Serial_SendLine();
		NewFloor = MarkDuty + TUNE_MARGIN;
	}
	else
	{
		/* 一路降到最小值电机都还在转 —— 说明它还能更慢 */
		Serial_SendString("TUNE no_mark min=");
		Serial_SendUInt(TuneDuty);
		Serial_SendLine();
		NewFloor = TuneDuty + TUNE_MARGIN;
	}
	
	/* 临时把 1 档下限设成"停转点 + 余量"(掉电复位, 不改源文件) */
	Motor_SetFloor(NewFloor);
	
	Span = Motor_GetFloor();
	if (Span < MOTOR_DUTY_MAX)
	{
		Span = (uint16_t)(MOTOR_DUTY_MAX - Span);
	}
	else
	{
		Span = 0;				//下限被钳到了最高档, 区间已经塌了
	}
	
	Serial_SendString("TUNE DONE mark=");
	Serial_SendUInt(MarkDuty);
	Serial_SendString(" floor=");
	Serial_SendUInt(Motor_GetFloor());
	Serial_SendString(" span=");
	Serial_SendUInt(Span);
	Serial_SendLine();
	
	if (Span < MOTOR_GEAR_MAX)
	{
		Serial_SendString("TUNE WARN span too small -> gears will repeat");
		Serial_SendLine();
	}
	
	/* 立刻用新的 1 档转起来给你试手感 */
	Gear = 1;
	Motor_SetGear(Gear);
}

/* ==================== 命令解析 ==================== */

static void HandleCommand(char *Line)
{
	char    *p;
	uint8_t  ok;
	uint32_t GearVal;
	uint32_t MsVal;
	
	p = SkipSpace(Line);
	if (*p == '\0')
	{
		return;
	}
	
	/* ---------------- RUN<档位>,<时长ms> ---------------- */
	if (MatchWord(p, "RUN"))
	{
		if (TuneActive == 1)
		{
			Tune_Finish(2);
		}
		
		p = SkipSpace(p + 3);
		GearVal = TakeNumber(&p, &ok);
		if (ok == 0)
		{
			Serial_SendString("ERR RUN needs gear, e.g. RUN30,5000");
			Serial_SendLine();
			return;
		}
		
		p = SkipSpace(p);
		if (*p == ',')
		{
			p++;
		}
		p = SkipSpace(p);
		MsVal = TakeNumber(&p, &ok);
		if (ok == 0)
		{
			Serial_SendString("ERR RUN needs time, e.g. RUN30,5000");
			Serial_SendLine();
			return;
		}
		
		if (GearVal > MOTOR_GEAR_MAX)
		{
			Serial_SendString("ERR gear must be 0~");
			Serial_SendUInt(MOTOR_GEAR_MAX);
			Serial_SendLine();
			return;
		}
		if (MsVal < RUN_MS_MIN || MsVal > RUN_MS_MAX)
		{
			Serial_SendString("ERR time must be ");
			Serial_SendUInt(RUN_MS_MIN);
			Serial_SendString("~");
			Serial_SendUInt(RUN_MS_MAX);
			Serial_SendString(" ms");
			Serial_SendLine();
			return;
		}
		
		ApplyGear((uint8_t)GearVal);		//先按命令里的转速转起来
		RunCmdGear  = (uint8_t)GearVal;
		RunCmdMs    = MsVal;
		RunActive   = 1;
		RunRemainMs = MsVal;
		
		Serial_SendString("OK RUN ");
		Serial_SendUInt(GearVal);
		Serial_SendByte(' ');
		Serial_SendUInt(MsVal);
		Serial_SendLine();
		return;
	}
	
	/* ---------------- SPD<档位>: 只改转速, 不参与定时 ---------------- */
	if (MatchWord(p, "SPD"))
	{
		if (TuneActive == 1)
		{
			Tune_Finish(2);
		}
		
		p = SkipSpace(p + 3);
		GearVal = TakeNumber(&p, &ok);
		if (ok == 0 || GearVal > MOTOR_GEAR_MAX)
		{
			Serial_SendString("ERR SPD needs gear 0~");
			Serial_SendUInt(MOTOR_GEAR_MAX);
			Serial_SendLine();
			return;
		}
		ApplyGear((uint8_t)GearVal);
		Serial_SendString("OK SPD ");
		Serial_SendUInt(GearVal);
		Serial_SendLine();
		return;
	}
	
	/* ---------------- STOP: 立即停转 ---------------- */
	if (MatchWord(p, "STOP"))
	{
		if (TuneActive == 1)
		{
			Tune_Finish(2);
		}
		StopMotor();
		Serial_SendString("OK STOP");
		Serial_SendLine();
		return;
	}
	
	/* ---------------- TUNE: 自动标定"最低可维持占空比" ---------------- */
	if (MatchWord(p, "TUNE"))
	{
		Tune_Start();
		return;
	}
	
	/* ---------------- STATUS 或 ? : 查询状态 ---------------- */
	if (MatchWord(p, "STATUS") || *p == '?')
	{
		SendStatus();
		return;
	}
	
	/* ---------------- PING: 测链路 ---------------- */
	if (MatchWord(p, "PING"))
	{
		Serial_SendString("PONG");
		Serial_SendLine();
		return;
	}
	
	Serial_SendString("ERR unknown command");
	Serial_SendLine();
}

/* ==================== 主程序 ==================== */

int main(void)
{
	uint8_t  Event;
	uint8_t  i;
	uint8_t  TimeMode;			//0=空闲 1=<100秒(带0.1秒) 2=>=100秒
	uint32_t Tenth;
	uint32_t LeftSec;
	uint16_t Step;				//标定时本次要降多少(0.1%)
	uint16_t Period;			//标定时本次停留多久(ms)
	
	OLED_Init();
	Motor_Init();
	Key_Init();
	Serial_Init();
	
	/* 固定的文字只写一次, 画面不会闪 */
	ShowIdleLabels();
	OLED_ShowString(2, 1, "Duty:");
	OLED_ShowString(2, 11, "%");
	OLED_ShowString(3, 1, "Time:");
	
	Motor_SetGear(0);
	ShownGear = 0xFF;
	ShownDuty = 0xFFFF;
	Serial_SendString("READY");
	Serial_SendLine();
	
	while (1)
	{
		/* ---------------- 1. 按键 ---------------- */
		Event = Key_Scan();
		
		if (TuneActive == 1)
		{
			/* 标定期间: 按任意按钮 = "就是现在, 电机停住了" */
			if (Event != 0)
			{
				Tune_Finish(1);
			}
		}
		else
		{
			if (Event == 1)
			{
				if (Gear < MOTOR_GEAR_MAX)
				{
					Gear++;
					Motor_SetGear(Gear);
				}
			}
			else if (Event == 2)
			{
				if (Gear > 0)
				{
					Gear--;
					Motor_SetGear(Gear);
				}
			}
		}
		
		/* ---------------- 2. 蓝牙命令 ---------------- */
		if (Serial_GetLine(LineBuf) == 1)
		{
			for (i = 0; i < 12 && LineBuf[i] != '\0'; i++)
			{
				RxShow[i] = LineBuf[i];
			}
			RxShow[i] = '\0';
			OLED_ShowString(4, 4, "            ");		//先清掉上一次的内容
			OLED_ShowString(4, 4, RxShow);
			
			HandleCommand(LineBuf);
		}
		
		/* ---------------- 3. 标定: 自动逐步降速(两段式) ---------------- */
		if (TuneActive == 1)
		{
			if (TuneStepLeft > TICK_MS)
			{
				TuneStepLeft -= TICK_MS;
			}
			else
			{
				/* 还停在"肯定能转"的高占空比区间就用大步长, 到了临界区间换小步长 */
				if (TuneDuty > TUNE_COARSE_DUTY)
				{
					Step   = TUNE_COARSE_STEP;
					Period = TUNE_COARSE_MS;
				}
				else
				{
					Step   = TUNE_FINE_STEP;
					Period = TUNE_FINE_MS;
				}
				
				if (TuneDuty >= TUNE_STOP_DUTY + Step)
				{
					TuneDuty -= Step;
					TuneStepLeft = Period;
					Motor_SetRaw(TuneDuty);
					
					Serial_SendString("TUNE ");
					Serial_SendUInt(TuneDuty);
					Serial_SendLine();
				}
				else
				{
					Tune_Finish(0);		//一路降到底都没停
				}
			}
		}
		
		/* ---------------- 4. 定时倒计时 ---------------- */
		if (RunActive == 1)
		{
			if (RunRemainMs > TICK_MS)
			{
				RunRemainMs -= TICK_MS;
			}
			else
			{
				RunRemainMs = 0;
				RunActive = 0;
				ApplyGear(0);						//到点停转
				
				/* 主动通知电脑: 转完了 */
				Serial_SendString("DONE ");
				Serial_SendUInt(RunCmdGear);
				Serial_SendByte(' ');
				Serial_SendUInt(RunCmdMs);
				Serial_SendLine();
			}
		}
		
		/* ---------------- 5. 电机助推收尾(必须每 10ms 一次) ---------------- */
		Motor_Tick();
		
		/* ---------------- 6. 刷新 OLED ---------------- */
		if (TuneActive == 1)
		{
			if (ShownTune == 0 || ShownDuty != TuneDuty)
			{
				OLED_ShowString(1, 1, "TUNE        ");
				OLED_ShowString(4, 1, "Stop?Press key");
				ShowDuty(TuneDuty);
				ShownTune = 1;
				ShownDuty = TuneDuty;
				ShownGear = 0xFF;
			}
		}
		else
		{
			if (ShownTune == 1)
			{
				ShownTune = 0;
				ShownGear = 0xFF;
				ShownDuty = 0xFFFF;
			}
			if (Gear != ShownGear)
			{
				OLED_ShowNum(1, 7, Gear, 2);
				ShowDuty(Motor_GetDuty(Gear));
				ShownGear = Gear;
				ShownDuty = Motor_GetDuty(Gear);
			}
		}
		
		/* 第 3 行显示剩余时间, 只在显示内容真的变了时才写屏, 免得拖慢主循环 */
		if (RunActive == 0)
		{
			TimeMode = 0;
		}
		else if (RunRemainMs <= 99900)
		{
			TimeMode = 1;
		}
		else
		{
			TimeMode = 2;
		}
		
		if (TimeMode == 0)
		{
			TimeStr[0] = ' ';
			TimeStr[1] = ' ';
			TimeStr[2] = '-';
			TimeStr[3] = '-';
			TimeStr[4] = '-';
		}
		else if (TimeMode == 1)
		{
			Tenth = (RunRemainMs + 99) / 100;			//向上取整到 0.1 秒
			TimeStr[0] = (char)('0' + (Tenth / 100) % 10);
			TimeStr[1] = (char)('0' + (Tenth / 10) % 10);
			TimeStr[2] = '.';
			TimeStr[3] = (char)('0' + Tenth % 10);
			TimeStr[4] = 's';
		}
		else
		{
			LeftSec = (RunRemainMs + 999) / 1000;		//向上取整到秒
			TimeStr[0] = ' ';
			TimeStr[1] = (char)('0' + (LeftSec / 100) % 10);
			TimeStr[2] = (char)('0' + (LeftSec / 10) % 10);
			TimeStr[3] = (char)('0' + LeftSec % 10);
			TimeStr[4] = 's';
		}
		TimeStr[5] = '\0';
		
		if (TimeStr[0] != LastTimeStr[0] || TimeStr[1] != LastTimeStr[1] ||
		    TimeStr[2] != LastTimeStr[2] || TimeStr[3] != LastTimeStr[3] ||
		    TimeStr[4] != LastTimeStr[4])
		{
			OLED_ShowString(3, 6, TimeStr);
			for (i = 0; i < 5; i++)
			{
				LastTimeStr[i] = TimeStr[i];
			}
		}
		
		Delay_ms(TICK_MS);
	}
}
