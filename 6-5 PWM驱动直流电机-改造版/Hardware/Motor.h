#ifndef __MOTOR_H
#define __MOTOR_H

#include "stm32f10x.h"

/* ============ 电机调速参数(想改速度手感就改这几个数字) ============ */

#define MOTOR_GEAR_MAX		50		//最高档位: 档位 1~50, 0 档 = 停转

#define MOTOR_DUTY_MIN		280		//1 档占空比, 单位 0.1%  ->  280 = 28.0%
#define MOTOR_DUTY_MAX		380		//50 档占空比, 单位 0.1% ->  380 = 38.0%

/* ---- 启动助推(解决"低占空比电机起不来"的物理门槛) ----
 *
 * 为什么需要它 ?
 *   电机从静止起步要克服"静摩擦", 需要较大的力 —— 实测这台电机
 *   低于 40% 占空比根本转不起来。
 *   但一旦转起来了, 维持转动只需要克服"动摩擦", 力气小得多 ——
 *   可能 28% 就够了。
 *
 * 所以做法是: 每次从"完全停转"启动时, 先给一个大占空比
 * (MOTOR_KICK_DUTY)把电机"推"起来, 保持 MOTOR_KICK_MS 毫秒后,
 * 再立刻降到目标档位的占空比。
 *
 * 已经在转的时候改档位不会触发助推 —— 只有从 0 档启动才会。
 */
#define MOTOR_KICK_DUTY		650		//助推占空比, 单位 0.1% -> 65.0%
#define MOTOR_KICK_MS		300		//助推持续时长(ms)
#define MOTOR_TICK_MS		10		//Motor_Tick() 的调用周期(ms), 与主循环一致

/* 说明: 如果 1 档(28%)实测维持不住(电机不转或一顿一顿), 把
 * MOTOR_DUTY_MIN 调大(例如 320); 想更慢就调小。
 * 更准的数字可以用蓝牙发一条 TUNE 命令自动标定出来。 */

void Motor_Init(void);

void Motor_SetGear(uint8_t Gear);			//切档位(内部会自动处理启动助推)
uint16_t Motor_GetDuty(uint8_t Gear);		//查某个档位对应的占空比(单位 0.1%)
void Motor_Tick(void);						//必须每 10ms 调一次, 负责结束助推

void Motor_SetRaw(uint16_t Duty);			//直接设占空比, 绕过档位(标定用)
uint16_t Motor_GetRaw(void);				//当前实际输出的占空比

void Motor_SetFloor(uint16_t Duty);			//运行期临时改"1 档下限"(标定用)
uint16_t Motor_GetFloor(void);

#endif
