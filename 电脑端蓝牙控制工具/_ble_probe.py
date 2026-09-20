# -*- coding: utf-8 -*-
"""BLE 诊断：扫描 -> 连接 -> 枚举 GATT 服务/特征值"""
import asyncio, sys
from bleak import BleakScanner, BleakClient

TARGET = "21:F6:47:3A:D8:89"

async def main():
    print("扫描 BLE 设备（8 秒）...")
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    print("共发现 %d 个 BLE 设备" % len(found))
    hit = None
    for dev, adv in found.values():
        name = dev.name or adv.local_name or "(无名)"
        mark = ""
        if dev.address.upper() == TARGET or "HC-05" in name.upper():
            mark = "   <== 目标"
            hit = dev
        print("   %-20s  %-24s RSSI=%-5s%s" % (dev.address, name, adv.rssi, mark))
    if not hit:
        print("\n没扫到目标设备。可能原因：")
        print("  1) 模块已被手机/电脑占着连接（BLE 同时只服务一个中心设备）")
        print("  2) 模块没在广播（EN 拉高进 AT 模式时可能停止广播）")
        print("  3) 模块断电了")
        return

    print("\n连接 %s ..." % hit.address)
    async with BleakClient(hit.address, timeout=20) as c:
        print("已连接:", c.is_connected)
        for s in c.services:
            print("  服务 %s" % s.uuid)
            for ch in s.characteristics:
                print("      特征 %-46s %s" % (ch.uuid, ",".join(ch.properties)))

asyncio.run(main())
