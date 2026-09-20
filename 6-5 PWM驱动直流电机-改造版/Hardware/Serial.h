#ifndef __SERIAL_H
#define __SERIAL_H

#include "stm32f10x.h"

/*
 * USART1 串口驱动(接 HC-05 蓝牙模块)
 *
 * 接线:
 *   PA9  (USART1_TX) -> HC-05 的 RXD
 *   PA10 (USART1_RX) <- HC-05 的 TXD
 *   参数: 9600 波特率, 8 位数据, 无校验, 1 位停止位(HC-05 出厂默认)
 *
 * 接收方式: 中断里一个字节一个字节收, 攒满一整行(遇到 \r 或 \n)后置标志,
 *           主循环用 Serial_GetLine() 取走 —— 全程不阻塞, 不影响按键和电机。
 *
 * 注意: 本模块不能用 printf! 本工程没有勾选 MicroLIB,
 *       一旦引入 stdio 的 vsprintf, 代码体积会暴涨好几 KB。
 */

#define SERIAL_LINE_MAX		32		//一行命令的最大长度(含结束符)

void Serial_Init(void);
void Serial_SendByte(uint8_t Byte);
void Serial_SendString(char *String);
void Serial_SendUInt(uint32_t Number);		//按十进制发送无符号整数
void Serial_SendLine(void);					//发送回车换行 \r\n

uint8_t Serial_GetLine(char *Buf);			//取走一整行: 1=取到了 0=还没收到

#endif
