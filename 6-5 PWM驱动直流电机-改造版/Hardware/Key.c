#include "stm32f10x.h"                  // Device header
#include "Key.h"

#define KEY_COUNT				2
#define KEY_DEBOUNCE_SAMPLES	2		//连续 2 次采样(20ms)电平一致才认可变化

/* 每个按键自己的状态机 */
typedef struct
{
	GPIO_TypeDef *GPIOx;
	uint16_t Pin;
	uint8_t  PrevRaw;		//上一次的原始采样,0=松开 1=按下
	uint8_t  RawCnt;		//连续相同采样的次数(消抖用)
	uint8_t  Stable;		//已确认的稳定状态: 0=松开 1=按下
	uint16_t HoldMs;		//已经按住了多少毫秒
	uint16_t RepMs;			//长按连发计时
} Key_t;

static Key_t Key[KEY_COUNT] =
{
	{GPIOB, GPIO_Pin_1,  0, 0, 0, 0, 0},	//加速键
	{GPIOB, GPIO_Pin_11, 0, 0, 0, 0, 0}		//减速键
};

void Key_Init(void)
{
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);
	
	GPIO_InitTypeDef GPIO_InitStructure;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_1 | GPIO_Pin_11;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOB, &GPIO_InitStructure);
}

/*
 * 每 10ms 调用一次。返回 0 / 1 / 2,含义见 Key.h
 *
 * 工作流程:
 *   1) 采样引脚电平,按下为低电平,统一成 1=按下
 *   2) 消抖:连续 2 次采样一致,才承认电平真的变了
 *   3) 确认按下  -> 清零计时,开始累计按住时长
 *      确认松开  -> 如果按住时长没到长按门槛,这就是一次"短按",产生一次动作
 *   4) 按住期间:到 600ms 立刻产生一次动作,之后每 100ms 产生一次(连发)
 */
uint8_t Key_Scan(void)
{
	uint8_t i;
	uint8_t raw;
	uint8_t Event = 0;
	
	for (i = 0; i < KEY_COUNT; i++)
	{
		raw = (GPIO_ReadInputDataBit(Key[i].GPIOx, Key[i].Pin) == 0) ? 1 : 0;
		
		/* ---------- 消抖 ---------- */
		if (raw != Key[i].PrevRaw)
		{
			Key[i].PrevRaw = raw;
			Key[i].RawCnt = 0;
		}
		else if (Key[i].RawCnt < KEY_DEBOUNCE_SAMPLES)
		{
			Key[i].RawCnt++;
			if (Key[i].RawCnt == KEY_DEBOUNCE_SAMPLES && raw != Key[i].Stable)
			{
				Key[i].Stable = raw;
				if (raw == 1)
				{
					/* 刚刚确认按下 */
					Key[i].HoldMs = 0;
					Key[i].RepMs = 0;
				}
				else
				{
					/* 刚刚确认松开:按住时长没到长按门槛,算一次短按 */
					if (Key[i].HoldMs > 0 && Key[i].HoldMs < KEY_PRESS_LEVEL_MS)
					{
						if (Event == 0)
						{
							Event = (uint8_t)(i + 1);
						}
					}
					Key[i].HoldMs = 0;
				}
			}
		}
		
		/* ---------- 长按计时与连发 ---------- */
		if (Key[i].Stable == 1)
		{
			if (Key[i].HoldMs < 60000)		//钳位,防止按太久溢出
			{
				Key[i].HoldMs += KEY_SCAN_PERIOD_MS;
			}
			
			if (Key[i].HoldMs == KEY_PRESS_LEVEL_MS)
			{
				/* 刚好达到长按门槛,立刻产生第一次动作 */
				Key[i].RepMs = 0;
				if (Event == 0)
				{
					Event = (uint8_t)(i + 1);
				}
			}
			else if (Key[i].HoldMs > KEY_PRESS_LEVEL_MS)
			{
				Key[i].RepMs += KEY_SCAN_PERIOD_MS;
				if (Key[i].RepMs >= KEY_REPEAT_MS)
				{
					Key[i].RepMs = 0;
					if (Event == 0)
					{
						Event = (uint8_t)(i + 1);
					}
				}
			}
		}
	}
	
	return Event;
}
