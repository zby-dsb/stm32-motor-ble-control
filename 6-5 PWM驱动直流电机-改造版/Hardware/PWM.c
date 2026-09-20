#include "stm32f10x.h"                  // Device header

/*
 * PWM 参数说明
 *   TIM2 时钟 = 72MHz
 *   预分频 PSC = 4      -> 72MHz / 4 = 18MHz
 *   自动重装 ARR = 1000 -> 18MHz / 1000 = 18kHz    (PWM 频率, TB6612 完全支持)
 *   比较值 CCR = 0~1000, 分辨率 0.1%
 *
 * 为什么把 ARR 从原来的 100 改成 1000 ?
 *   本程序最高档只有 60%、最低档 42%,可调区间只有 18%。
 *   如果分辨率还是 1%(ARR=100),这 18% 里只有 18 种不同的占空比,
 *   50 个档位会大量重复 —— 按了按钮转速却不变,手感很差。
 *   提高到 0.1% 之后每一档都有真实变化(每档约 0.37%)。
 */
void PWM_Init(void)
{
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, ENABLE);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
	
	GPIO_InitTypeDef GPIO_InitStructure;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_2;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOA, &GPIO_InitStructure);
	
	TIM_InternalClockConfig(TIM2);
	
	TIM_TimeBaseInitTypeDef TIM_TimeBaseInitStructure;
	TIM_TimeBaseInitStructure.TIM_ClockDivision = TIM_CKD_DIV1;
	TIM_TimeBaseInitStructure.TIM_CounterMode = TIM_CounterMode_Up;
	TIM_TimeBaseInitStructure.TIM_Period = 1000 - 1;		//ARR
	TIM_TimeBaseInitStructure.TIM_Prescaler = 4 - 1;		//PSC
	TIM_TimeBaseInitStructure.TIM_RepetitionCounter = 0;
	TIM_TimeBaseInit(TIM2, &TIM_TimeBaseInitStructure);
	
	TIM_OCInitTypeDef TIM_OCInitStructure;
	TIM_OCStructInit(&TIM_OCInitStructure);
	TIM_OCInitStructure.TIM_OCMode = TIM_OCMode_PWM1;
	TIM_OCInitStructure.TIM_OCPolarity = TIM_OCPolarity_High;
	TIM_OCInitStructure.TIM_OutputState = TIM_OutputState_Enable;
	TIM_OCInitStructure.TIM_Pulse = 0;		//CCR
	TIM_OC3Init(TIM2, &TIM_OCInitStructure);
	
	TIM_Cmd(TIM2, ENABLE);
}

void PWM_SetCompare3(uint16_t Compare)
{
	if (Compare > 1000)		//防止越界,1000 = 100.0%
	{
		Compare = 1000;
	}
	TIM_SetCompare3(TIM2, Compare);
}
