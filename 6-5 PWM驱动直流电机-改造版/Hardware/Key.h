#ifndef __KEY_H
#define __KEY_H

#include "stm32f10x.h"

/*
 * 按键说明
 *   PB1  -> 加速键
 *   PB11 -> 减速键
 *
 * Key_Scan() 是"非阻塞"扫描,必须每 10ms 调用一次
 * (在 while(1) 里配合 Delay_ms(KEY_SCAN_PERIOD_MS))。
 * 不能像以前那样用 while 死等松手,否则就没法实现"长按连发"了。
 *
 * 返回值: 0 = 本次没有动作
 *         1 = 加速键产生了一次"加 1 档"
 *         2 = 减速键产生了一次"减 1 档"
 *         短按在"松手"时产生一次;长按在按住 600ms 后开始,每 100ms 产生一次。
 */

#define KEY_SCAN_PERIOD_MS	10		//扫描周期,必须保持 10
#define KEY_PRESS_LEVEL_MS	600		//按住超过 600ms 判定为长按,开始连发
#define KEY_REPEAT_MS		100		//长按连发间隔 100ms(匀速快进)

void Key_Init(void);
uint8_t Key_Scan(void);

#endif
