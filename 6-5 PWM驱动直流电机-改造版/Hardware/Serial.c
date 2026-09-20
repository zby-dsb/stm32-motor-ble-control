#include "stm32f10x.h"                  // Device header
#include "Serial.h"

/*
 * 两块缓冲:
 *   s_Build  —— 中断正在拼装的这一行
 *   s_Line   —— 已经拼好、等着主循环取走的那一行
 * 分开是为了防止"主循环还没取走, 中断又开始拼下一行"把数据冲掉。
 */
static volatile char    s_Build[SERIAL_LINE_MAX];
static volatile uint8_t s_BuildLen = 0;
static volatile char    s_Line[SERIAL_LINE_MAX];
static volatile uint8_t s_LineReady = 0;

void Serial_Init(void)
{
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1, ENABLE);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
	
	GPIO_InitTypeDef GPIO_InitStructure;
	/* PA9 = USART1_TX, 复用推挽输出 */
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_9;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOA, &GPIO_InitStructure);
	/* PA10 = USART1_RX, 上拉输入 */
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_10;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOA, &GPIO_InitStructure);
	
	USART_InitTypeDef USART_InitStructure;
	USART_InitStructure.USART_BaudRate = 9600;			//HC-05 出厂默认就是 9600
	USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
	USART_InitStructure.USART_Mode = USART_Mode_Tx | USART_Mode_Rx;
	USART_InitStructure.USART_Parity = USART_Parity_No;
	USART_InitStructure.USART_StopBits = USART_StopBits_1;
	USART_InitStructure.USART_WordLength = USART_WordLength_8b;
	USART_Init(USART1, &USART_InitStructure);
	
	USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
	
	NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);
	
	NVIC_InitTypeDef NVIC_InitStructure;
	NVIC_InitStructure.NVIC_IRQChannel = USART1_IRQn;
	NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
	NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 1;
	NVIC_InitStructure.NVIC_IRQChannelSubPriority = 1;
	NVIC_Init(&NVIC_InitStructure);
	
	USART_Cmd(USART1, ENABLE);
}

void Serial_SendByte(uint8_t Byte)
{
	USART_SendData(USART1, Byte);
	while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

void Serial_SendString(char *String)
{
	uint8_t i;
	for (i = 0; String[i] != '\0'; i++)
	{
		Serial_SendByte(String[i]);
	}
}

void Serial_SendUInt(uint32_t Number)
{
	char Tmp[11];
	int8_t i = 0;
	
	if (Number == 0)
	{
		Serial_SendByte('0');
		return;
	}
	
	while (Number > 0 && i < 10)
	{
		Tmp[i++] = (char)('0' + (Number % 10));
		Number /= 10;
	}
	while (i > 0)
	{
		Serial_SendByte(Tmp[--i]);
	}
}

void Serial_SendLine(void)
{
	Serial_SendByte('\r');
	Serial_SendByte('\n');
}

uint8_t Serial_GetLine(char *Buf)
{
	uint8_t i;
	
	if (s_LineReady == 0)
	{
		return 0;
	}
	
	for (i = 0; i < SERIAL_LINE_MAX; i++)
	{
		Buf[i] = s_Line[i];
		if (Buf[i] == '\0')
		{
			break;
		}
	}
	s_LineReady = 0;
	return 1;
}

void USART1_IRQHandler(void)
{
	char ch;
	uint8_t i;
	
	if (USART_GetITStatus(USART1, USART_IT_RXNE) == SET)
	{
		ch = (char)USART_ReceiveData(USART1);
		
		if (ch == '\r' || ch == '\n')
		{
			/* 收到换行符 = 一整行结束(空行直接丢掉) */
			if (s_BuildLen > 0)
			{
				s_Build[s_BuildLen] = '\0';
				for (i = 0; i < SERIAL_LINE_MAX; i++)
				{
					s_Line[i] = s_Build[i];
					if (s_Build[i] == '\0')
					{
						break;
					}
				}
				s_BuildLen = 0;
				s_LineReady = 1;
			}
		}
		else if (s_BuildLen < SERIAL_LINE_MAX - 1)
		{
			s_Build[s_BuildLen++] = ch;
		}
		/* 超长直接丢弃后面的字符, 防止越界 */
		
		USART_ClearITPendingBit(USART1, USART_IT_RXNE);
	}
}
