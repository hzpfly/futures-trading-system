#!/usr/bin/env python3
"""
使用 AkShare 获取期货历史数据
支持盘后历史数据查询，免费无需认证
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def get_cotton_futures_history():
    """获取棉花期货历史数据"""
    print("=" * 60)
    print("获取棉花期货(CF)历史数据")
    print("=" * 60)
    
    try:
        # 方法1: 获取棉花期货主力合约日线数据
        print("\n1. 尝试获取棉花主力合约日线数据...")
        df_main = ak.futures_main_sina(symbol="CF0", start_date="20250101", end_date="20260619")
        print(f"✅ 成功！共 {len(df_main)} 条数据")
        print("\n最近5天数据:")
        print(df_main.tail())
        return df_main
    except Exception as e:
        print(f"❌ 方法1失败: {e}")
    
    try:
        # 方法2: 获取具体合约CF2609数据
        print("\n2. 尝试获取CF2609合约数据...")
        df_contract = ak.futures_zh_daily_sina(symbol="CF2609")
        print(f"✅ 成功！共 {len(df_contract)} 条数据")
        print("\n最近5天数据:")
        print(df_contract.tail())
        return df_contract
    except Exception as e:
        print(f"❌ 方法2失败: {e}")
    
    try:
        # 方法3: 使用东方财富数据源
        print("\n3. 尝试东方财富数据源...")
        df_em = ak.futures_daily_em(symbol="CF", start_date="20250101", end_date="20260619")
        print(f"✅ 成功！共 {len(df_em)} 条数据")
        print("\n最近5天数据:")
        print(df_em.tail())
        return df_em
    except Exception as e:
        print(f"❌ 方法3失败: {e}")
    
    print("\n❌ 所有方法都失败了")
    return None

def get_iron_ore_history():
    """获取铁矿石历史数据（对比参考）"""
    print("\n" + "=" * 60)
    print("获取铁矿石(i)历史数据")
    print("=" * 60)
    
    try:
        df = ak.futures_main_sina(symbol="I0", start_date="20250101", end_date="20260619")
        print(f"✅ 成功！共 {len(df)} 条数据")
        print("\n最近5天数据:")
        print(df.tail())
        return df
    except Exception as e:
        print(f"❌ 失败: {e}")
        return None

def save_to_csv(df, filename):
    """保存数据到CSV"""
    if df is not None and len(df) > 0:
        csv_path = f"/Users/michaelhe/WorkBuddy/Claw/{filename}"
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"\n✅ 数据已保存到: {csv_path}")
        return csv_path
    return None

if __name__ == '__main__':
    print("开始获取期货历史数据...\n")
    
    # 获取棉花数据
    cotton_data = get_cotton_futures_history()
    if cotton_data is not None:
        save_to_csv(cotton_data, "cotton_cf_history.csv")
    
    # 获取铁矿石数据
    iron_data = get_iron_ore_history()
    if iron_data is not None:
        save_to_csv(iron_data, "iron_ore_history.csv")
    
    print("\n" + "=" * 60)
    print("数据获取完成！")
    print("=" * 60)
