#ifndef __PWM_H
#define __PWM_H

#include "stm32f10x.h"

void PWM_Init(void);
void PWM_SetCompare3(uint16_t Compare);		//Compare: 0~1000 对应占空比 0.0%~100.0%

#endif
