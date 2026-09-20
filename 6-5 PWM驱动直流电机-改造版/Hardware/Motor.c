#include "stm32f10x.h"                  // Device header
#include "Motor.h"
#include "PWM.h"

/*
 * 硬件接线(TB6612FNG 的 A 通道)
 *   PA4 -> AIN1
 *   PA5 -> AIN2
 *   PA2 -> PWMA (TIM2_CH3)
 *
 * 本程序只保留"正转":
 *   AIN1 一直拉高、AIN2 一直拉低,方向固定死,
 *   转速完全由 PWMA 的占空比决定。
 */

/* ---- 运行期状态 ---- */
static uint16_t DutyMin    = MOTOR_DUTY_MIN;	//1 档占空比(标定时可临时改)
static uint16_t DutyMax    = MOTOR_DUTY_MAX;	//最高档占空比
static uint16_t CurDuty    = 0;					//当前真正写进 PWM 的占空比
static uint16_t TargetDuty = 0;					//助推结束后要保持的占空比
static uint16_t KickLeft   = 0;					//助推还剩几个 tick(0 = 没有助推在进行)

/* 把占空比真正写进 PWM, 同时记住当前值 */
static void Motor_Apply(uint16_t Duty)
{
	CurDuty = Duty;
	PWM_SetCompare3(Duty);
}

void Motor_Init(void)
{
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
	
	GPIO_InitTypeDef GPIO_InitStructure;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_4 | GPIO_Pin_5;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOA, &GPIO_InitStructure);
	
	/* 方向固定为正转: AIN1 = 1, AIN2 = 0 */
	GPIO_SetBits(GPIOA, GPIO_Pin_4);
	GPIO_ResetBits(GPIOA, GPIO_Pin_5);
	
	PWM_Init();
	
	DutyMin    = MOTOR_DUTY_MIN;
	DutyMax    = MOTOR_DUTY_MAX;
	KickLeft   = 0;
	TargetDuty = 0;
	Motor_Apply(0);			/* 上电先不转 */
}

/*
 * 把档位换算成占空比(单位 0.1%)
 *   档位 0  -> 0      (停转)
 *   档位 1  -> 280    (28.0%, 靠启动助推才起得来)
 *   档位 50 -> 380    (38.0%, 最高速度)
 *   中间线性插值
 *
 * 注意:这里用的是"四舍五入"的整数运算(先加半个除数再除)。
 *       如果用普通整除,低档位会被压成同一个占空比,按了没反应。
 */
uint16_t Motor_GetDuty(uint8_t Gear)
{
	uint32_t Duty;
	
	if (Gear == 0)
	{
		return 0;
	}
	if (Gear > MOTOR_GEAR_MAX)
	{
		Gear = MOTOR_GEAR_MAX;
	}
	if (DutyMax <= DutyMin)			//防止参数配错导致算飞
	{
		return DutyMin;
	}
	
	Duty = DutyMin
	     + ((uint32_t)(DutyMax - DutyMin) * (Gear - 1)
	        + (MOTOR_GEAR_MAX - 1) / 2) / (MOTOR_GEAR_MAX - 1);
	
	return (uint16_t)Duty;
}

/*
 * 切档位。核心是这里的"启动助推":
 *   只有当电机此刻是"完全静止"(CurDuty == 0)时,才先给一个大占空比把它推起来;
 *   已经在转的时候直接切到目标占空比, 不会冲一下。
 */
void Motor_SetGear(uint8_t Gear)
{
	uint16_t Duty = Motor_GetDuty(Gear);
	
	TargetDuty = Duty;
	
	if (Duty == 0)
	{
		KickLeft = 0;
		Motor_Apply(0);
		return;
	}
	
	if (CurDuty == 0)
	{
		Motor_Apply(MOTOR_KICK_DUTY);
		KickLeft = MOTOR_KICK_MS / MOTOR_TICK_MS;
		if (KickLeft == 0)
		{
			KickLeft = 1;
		}
	}
	else
	{
		Motor_Apply(Duty);
	}
}

/* 每 10ms 调一次: 助推时间到了就降到目标占空比 */
void Motor_Tick(void)
{
	if (KickLeft > 0)
	{
		KickLeft--;
		if (KickLeft == 0)
		{
			Motor_Apply(TargetDuty);
		}
	}
}

/* 直接设占空比, 绕过档位换算(给蓝牙标定命令 TUNE 用) */
void Motor_SetRaw(uint16_t Duty)
{
	if (Duty > 1000)
	{
		Duty = 1000;
	}
	KickLeft   = 0;
	TargetDuty = Duty;
	Motor_Apply(Duty);
}

uint16_t Motor_GetRaw(void)
{
	return CurDuty;
}

/* 临时改"1 档下限"(标定用, 掉电就复位, 不影响源文件里的宏) */
void Motor_SetFloor(uint16_t Duty)
{
	if (Duty < 100)
	{
		Duty = 100;
	}
	if (Duty > DutyMax)
	{
		Duty = DutyMax;
	}
	DutyMin = Duty;
}

uint16_t Motor_GetFloor(void)
{
	return DutyMin;
}
