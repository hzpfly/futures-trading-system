#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玉米期货行情监控 - TQSDK 邮件预警系统
功能：实时监控玉米主力合约（C），价格异动时发送邮件提醒
作者：WorkBuddy
日期：2026-03-17
"""

import tqsdk as tq
from tqsdk import TqApi, TqAuth
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import time
from datetime import datetime, timedelta
import json
import os

# ==================== 配置区域 ====================

import os

def load_config():
    """从家目录读取配置文件（避免误提交到仓库）"""
    import tomllib
    config_path = os.path.expanduser("~/.futures_config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

_config = load_config()

# TQSDK 账号配置
TQ_USERNAME = _config["tqsdk"]["username"]
TQ_PASSWORD = _config["tqsdk"]["password"]

# 邮件配置
SMTP_SERVER    = _config["smtp"]["server"]
SMTP_PORT      = _config["smtp"]["port"]
SMTP_USE_SSL   = _config["smtp"]["use_ssl"]
SMTP_USERNAME  = _config["smtp"]["username"]
SMTP_AUTH_CODE = _config["smtp"]["auth_code"]

# 收件人列表（可以是多个）
RECEIVERS = _config["smtp"]["receivers"]["c"]

# 是否启用邮件发送（如果邮件配置有问题，先设为False）
ENABLE_EMAIL = True

# 监控合约（玉米主力）
QUOTE_SYMBOL = "KQ.m@DCE.c"  # 主力合约
SYMBOL = QUOTE_SYMBOL  # TQSDK可以直接用主力合约代码获取K线

# 预警配置
ALERT_CONFIG = {
    # 涨跌幅预警（基于昨结算价）
    "change_pct_threshold": 0.015,  # 1.5%（玉米波动相对较小）
    
    # 成交量异动（单笔成交量超过近N笔均量的X倍）
    "volume_tick_count": 10,  # 统计最近10笔
    "volume_multiple": 3.0,  # 3倍
    
    # 快速拉升/下跌（N秒内涨跌幅超过X）
    "rapid_change_seconds": 60,  # 60秒
    "rapid_change_threshold": 0.005,  # 0.5%
    
    # 提醒频率限制（秒）
    "alert_cooldown": 1800,  # 30分钟内不重复发送同类预警
}

# 技术指标配置
TECH_CONFIG = {
    # 布林带参数
    "bb_period": 20,  # 布林带周期
    "bb_std": 2,  # 标准差倍数
    
    # VWAP参数
    "vwap_period": "5min",  # 用于VWAP计算的K线周期
}

# 数据文件路径
DATA_DIR = os.path.join(os.path.dirname(__file__), ".tqsdk_data")
ALERT_HISTORY_FILE = os.path.join(DATA_DIR, "c_alert_history.json")

# ==================== 邮件发送类 ====================

class EmailNotifier:
    """邮件发送器"""
    
    def __init__(self):
        self.server = None
        if ENABLE_EMAIL:
            self.connect()
        else:
            print("⚠️  邮件功能已禁用（ENABLE_EMAIL=False）")
    
    def connect(self):
        """连接SMTP服务器"""
        try:
            if SMTP_USE_SSL:
                self.server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            else:
                self.server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                self.server.starttls()
            self.server.login(SMTP_USERNAME, SMTP_AUTH_CODE)
            print(f"✓ 邮件服务器连接成功")
        except Exception as e:
            print(f"✗ 邮件服务器连接失败: {e}")
            print(f"  请检查：1) 授权码是否正确  2) QQ邮箱SMTP服务是否已开启")
            raise
    
    def send_alert(self, title, content):
        """发送预警邮件"""
        if not ENABLE_EMAIL:
            print(f"📧 邮件功能已禁用，模拟发送: {title}")
            print(f"  内容预览: {content[:100]}...")
            return False
        
        try:
            # 如果连接断开，重新连接
            if not self.server:
                self.connect()
            
            # 构建邮件
            msg = MIMEMultipart()
            msg['From'] = SMTP_USERNAME
            msg['To'] = ', '.join(RECEIVERS)
            msg['Subject'] = f"【玉米期货】{title}"
            msg.attach(MIMEText(content, 'plain', 'utf-8'))
            
            # 发送
            self.server.sendmail(SMTP_USERNAME, RECEIVERS, msg.as_string())
            print(f"✓ 邮件已发送: {title}")
            print(f"  收件人: {', '.join(RECEIVERS)}")
            return True
        except Exception as e:
            print(f"✗ 邮件发送失败: {e}")
            # 尝试重连
            try:
                self.connect()
            except:
                pass
            return False
    
    def close(self):
        """关闭连接"""
        if self.server:
            self.server.quit()
            print("✓ 邮件服务器连接已关闭")


# ==================== 预警管理类 ====================

class AlertManager:
    """预警管理器 - 控制提醒频率"""
    
    def __init__(self):
        self.ensure_data_dir()
        self.alert_history = self.load_history()
    
    def ensure_data_dir(self):
        """确保数据目录存在"""
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            print(f"✓ 创建数据目录: {DATA_DIR}")
    
    def load_history(self):
        """加载预警历史"""
        if os.path.exists(ALERT_HISTORY_FILE):
            try:
                with open(ALERT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_history(self):
        """保存预警历史"""
        with open(ALERT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.alert_history, f, ensure_ascii=False, indent=2)
    
    def should_alert(self, alert_type, key=""):
        """检查是否应该发送预警（控制频率）"""
        now = datetime.now()
        cooldown = ALERT_CONFIG["alert_cooldown"]
        
        alert_key = f"{alert_type}_{key}"
        
        if alert_key not in self.alert_history:
            return True
        
        last_alert_time = datetime.fromisoformat(self.alert_history[alert_key])
        if (now - last_alert_time).total_seconds() >= cooldown:
            return True
        
        return False
    
    def record_alert(self, alert_type, key=""):
        """记录已发送的预警"""
        alert_key = f"{alert_type}_{key}"
        self.alert_history[alert_key] = datetime.now().isoformat()
        self.save_history()


# ==================== 行情监控类 ====================

class CQuoteMonitor:
    """玉米期货行情监控"""
    
    def __init__(self):
        self.notifier = EmailNotifier()
        self.alert_manager = AlertManager()
        self.api = None
        self.quote = None
        self.kline_day = None  # 日K线（用于布林带）
        self.kline_min = None  # 分钟K线（用于VWAP）
        
        # 数据缓存
        self.pre_settlement = None  # 昨结算价（固定基准）
        self.last_price = None
        self.last_time = None
        self.last_volume = None
        self.volume_history = []
        
        # 快速变动检测
        self.price_history = []
        
        # 技术指标缓存
        self.vwap = None  # 成交量加权平均价
        self.bb_upper = None  # 布林带上轨
        self.bb_middle = None  # 布林带中轨
        self.bb_lower = None  # 布林带下轨
    
    def calculate_vwap(self):
        """计算VWAP（成交量加权平均价）"""
        if self.kline_min is None or len(self.kline_min) < 2:
            return None
        
        klines = self.kline_min
        total_pv = 0
        total_vol = 0
        
        for i in range(len(klines)):
            high = klines.iloc[i].get('high', 0)
            low = klines.iloc[i].get('low', 0)
            close = klines.iloc[i].get('close', 0)
            volume = klines.iloc[i].get('volume', 0)
            
            if high > 0 and low > 0:
                typical_price = (high + low + close) / 3
                total_pv += typical_price * volume
                total_vol += volume
        
        if total_vol > 0:
            self.vwap = total_pv / total_vol
            return self.vwap
        return None
    
    def calculate_bollinger_bands(self):
        """计算布林带（日线）"""
        if self.kline_day is None or len(self.kline_day) < TECH_CONFIG["bb_period"]:
            return None
        
        period = TECH_CONFIG["bb_period"]
        std_mult = TECH_CONFIG["bb_std"]
        closes = self.kline_day['close'].values[-period:]
        self.bb_middle = sum(closes) / len(closes)
        variance = sum((x - self.bb_middle) ** 2 for x in closes) / len(closes)
        std = variance ** 0.5
        self.bb_upper = self.bb_middle + std_mult * std
        self.bb_lower = self.bb_middle - std_mult * std
        
        return {
            'upper': self.bb_upper,
            'middle': self.bb_middle,
            'lower': self.bb_lower
        }
    
    def get_tech_summary(self, current_price):
        """获取技术指标摘要"""
        summary = []
        
        if self.vwap is not None:
            diff = current_price - self.vwap
            diff_pct = (diff / self.vwap) * 100 if self.vwap > 0 else 0
            direction = "▲" if diff >= 0 else "▼"
            summary.append(f"VWA {direction}{abs(diff_pct):.1f}%")
        
        if self.bb_upper is not None:
            band_width = self.bb_upper - self.bb_lower
            if band_width > 0:
                position = (current_price - self.bb_lower) / band_width * 100
                if current_price >= self.bb_upper:
                    pos_desc = "触上轨"
                elif current_price <= self.bb_lower:
                    pos_desc = "触下轨"
                elif position > 75:
                    pos_desc = "上轨附近"
                elif position < 25:
                    pos_desc = "下轨附近"
                else:
                    pos_desc = "中轨区间"
                summary.append(f"BB {pos_desc}")
        
        return " | ".join(summary) if summary else ""
    
    def format_price(self, price):
        """格式化价格"""
        return f"{price:.0f}"
    
    def format_change(self, current, base):
        """格式化涨跌幅"""
        if base is None or base == 0:
            return "N/A"
        change = (current - base) / base * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.2f}%"
    
    def format_volume(self, volume):
        """格式化成交量"""
        if volume >= 10000:
            return f"{volume/10000:.1f}万"
        return f"{volume:.0f}"
    
    def check_change_alert(self, current_price):
        """检查涨跌幅预警（基于昨结算价）"""
        if self.pre_settlement is None:
            return None
        
        change_pct = (current_price - self.pre_settlement) / self.pre_settlement
        threshold = ALERT_CONFIG["change_pct_threshold"]
        
        direction = "上涨" if change_pct > 0 else "下跌"
        abs_pct = abs(change_pct)
        
        if abs_pct >= threshold:
            alert_key = f"{'up' if change_pct > 0 else 'down'}"
            if self.alert_manager.should_alert("change", alert_key):
                return {
                    "type": "涨跌幅预警",
                    "direction": direction,
                    "change_pct": f"{abs_pct:.2f}%",
                    "current": self.format_price(current_price),
                    "pre_settlement": self.format_price(self.pre_settlement)
                }
        
        return None
    
    def check_volume_alert(self, tick_volume):
        """检查成交量异动"""
        self.volume_history.append(tick_volume)
        if len(self.volume_history) > ALERT_CONFIG["volume_tick_count"]:
            self.volume_history.pop(0)
        
        if len(self.volume_history) < ALERT_CONFIG["volume_tick_count"]:
            return None
        
        avg_volume = sum(self.volume_history[:-1]) / (len(self.volume_history) - 1)
        multiple = ALERT_CONFIG["volume_multiple"]
        
        if tick_volume >= avg_volume * multiple:
            alert_key = "volume_spike"
            if self.alert_manager.should_alert("volume", alert_key):
                return {
                    "type": "成交量异动",
                    "tick_volume": self.format_volume(tick_volume),
                    "avg_volume": f"{self.format_volume(avg_volume)}/笔",
                    "multiple": f"{tick_volume/avg_volume:.1f}倍"
                }
        
        return None
    
    def check_rapid_change_alert(self, current_price, current_time):
        """检查快速拉升/下跌"""
        self.price_history.append((current_time, current_price))
        
        cutoff_time = current_time - timedelta(seconds=ALERT_CONFIG["rapid_change_seconds"])
        self.price_history = [p for p in self.price_history if p[0] >= cutoff_time]
        
        if len(self.price_history) < 2:
            return None
        
        prices = [p[1] for p in self.price_history]
        min_price = min(prices)
        max_price = max(prices)
        
        if min_price == 0:
            return None
        
        change_pct = (max_price - min_price) / min_price
        threshold = ALERT_CONFIG["rapid_change_threshold"]
        
        if change_pct >= threshold:
            direction = "拉升" if current_price > min_price else "下跌"
            time_span = (current_time - self.price_history[0][0]).total_seconds()
            
            alert_key = f"rapid_{direction}"
            if self.alert_manager.should_alert("rapid", alert_key):
                return {
                    "type": f"快速{direction}",
                    "change_pct": f"{change_pct*100:.2f}%",
                    "time_span": f"{time_span:.0f}秒",
                    "price_range": f"{self.format_price(min_price)} -> {self.format_price(max_price)}"
                }
        
        return None
    
    def send_alert_email(self, alert_info, current_quote):
        """发送预警邮件"""
        title = f"{alert_info['type']}触发"
        
        content = f"""【玉米期货行情预警】

⚠️  预警类型：{alert_info['type']}
⏰  触发时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

━━━━━━━━━━━━━━━━━━━━
📊 当前行情信息
━━━━━━━━━━━━━━━━━━━━

合约：玉米主力 (C)
最新价：{self.format_price(current_quote['last_price'])}
涨跌：{self.format_change(current_quote['last_price'], current_quote.get('pre_settlement'))}
最高：{self.format_price(current_quote['highest'])}
最低：{self.format_price(current_quote['lowest'])}
成交量：{self.format_volume(current_quote['volume'])}
持仓量：{self.format_volume(current_quote.get('open_oi', 0))}

━━━━━━━━━━━━━━━━━━━━
📋 预警详情
━━━━━━━━━━━━━━━━━━━━
"""
        
        for key, value in alert_info.items():
            if key != 'type':
                label = {
                    'direction': '方向',
                    'change_pct': '涨跌幅',
                    'current': '当前价',
                    'pre_settlement': '昨结算价',
                    'tick_volume': '单笔成交',
                    'avg_volume': '平均成交',
                    'multiple': '倍数',
                    'time_span': '时间跨度',
                    'price_range': '价格区间'
                }.get(key, key)
                content += f"{label}：{value}\n"
        
        # 添加技术指标信息
        content += f"""
━━━━━━━━━━━━━━━━━━━━
📈 技术指标参考
━━━━━━━━━━━━━━━━━━━━
"""
        if self.vwap is not None:
            diff = current_quote['last_price'] - self.vwap
            diff_pct = (diff / self.vwap) * 100 if self.vwap > 0 else 0
            direction = "高于" if diff >= 0 else "低于"
            content += f"VAP（成交量加权均价）：{self.format_price(self.vwap)}\n"
            content += f"  → 当前价{direction}VWAP {abs(diff_pct):.2f}%\n"
        
        if self.bb_upper is not None:
            content += f"布林带（{TECH_CONFIG['bb_period']}日）：\n"
            content += f"  上轨：{self.format_price(self.bb_upper)}\n"
            content += f"  中轨：{self.format_price(self.bb_middle)}\n"
            content += f"  下轨：{self.format_price(self.bb_lower)}\n"
            if current_quote['last_price'] >= self.bb_upper:
                position = "▲ 触及上轨（超买区域）"
            elif current_quote['last_price'] <= self.bb_lower:
                position = "▼ 触及下轨（超卖区域）"
            else:
                band_width = self.bb_upper - self.bb_lower
                pos_pct = (current_quote['last_price'] - self.bb_lower) / band_width * 100
                position = f"处于中位置 ({pos_pct:.0f}%)"
            content += f"  → {position}\n"
        
        content += f"""
━━━━━━━━━━━━━━━━━━━━

此邮件由自动化行情监控系统发送，仅供参考，不构成投资建议。
期货有风险，投资需谨慎。
"""
        
        self.notifier.send_alert(title, content)
        
        alert_type = alert_info['type'].replace('快速', '').replace('拉升', 'up').replace('下跌', 'down')
        self.alert_manager.record_alert(alert_type)
    
    def run(self):
        """运行监控"""
        print("=" * 60)
        print("玉米期货行情监控系统启动")
        print("=" * 60)
        
        print(f"正在连接 TQSDK (账号: {TQ_USERNAME})...")
        self.api = TqApi(auth=TqAuth(TQ_USERNAME, TQ_PASSWORD))
        print("✓ TQSDK 连接成功")
        
        self.quote = self.api.get_quote(QUOTE_SYMBOL)
        
        # 订阅日K线（用于布林带计算）
        self.kline_day = self.api.get_kline_serial(SYMBOL, 86400, data_length=30)
        
        # 订阅5分钟K线（用于VWAP计算）
        self.kline_min = self.api.get_kline_serial(SYMBOL, 300, data_length=100)
        
        print(f"✓ 已订阅合约: {QUOTE_SYMBOL}")
        print(f"✓ 已订阅日K线（布林带）+ 5分钟K线（VWAP）")
        
        print("=" * 60)
        print("监控中...（支持日盘+夜盘）")
        print("=" * 60)
        
        try:
            while True:
                self.api.wait_update(time.time() + 1)
                
                if not self.api.is_changing(self.quote):
                    continue
                
                current_price = self.quote.last_price
                dt = self.quote.datetime
                if isinstance(dt, (int, float)):
                    current_time = datetime.fromtimestamp(dt / 1e9)
                else:
                    current_time = datetime.now()
                
                if self.pre_settlement is None and self.quote.pre_settlement > 0:
                    self.pre_settlement = self.quote.pre_settlement
                    print(f"✓ 昨结算价（基准）: {self.format_price(self.pre_settlement)}")
                
                # 计算技术指标
                self.calculate_vwap()
                self.calculate_bollinger_bands()
                
                quote_info = {
                    'last_price': current_price,
                    'highest': self.quote.highest,
                    'lowest': self.quote.lowest,
                    'volume': self.quote.volume,
                    'pre_settlement': self.pre_settlement,
                    'open_oi': self.quote.open_interest
                }
                
                # 打印行情
                tech_info = self.get_tech_summary(current_price)
                tech_line = f" [{tech_info}]" if tech_info else ""
                
                print(f"[{current_time.strftime('%H:%M:%S')}] "
                      f"价格: {self.format_price(current_price)} "
                      f"({self.format_change(current_price, self.pre_settlement)}) "
                      f"成交: {self.format_volume(self.quote.volume)}{tech_line}")
                
                alerts = []
                
                alert = self.check_change_alert(current_price)
                if alert:
                    alerts.append(alert)
                
                self.last_volume = self.quote.volume
                
                alert = self.check_rapid_change_alert(current_price, current_time)
                if alert:
                    alerts.append(alert)
                
                for alert in alerts:
                    print(f"⚠️  触发预警: {alert['type']}")
                    self.send_alert_email(alert, quote_info)
                
                self.last_price = current_price
                self.last_time = current_time
        
        except KeyboardInterrupt:
            print("\n\n收到中断信号，正在退出...")
        except Exception as e:
            print(f"\n✗ 监控出错: {e}")
        finally:
            self.notifier.close()
            self.api.close()
            print("监控系统已停止")


# ==================== 主程序 ====================

def main():
    """主函数"""
    monitor = CQuoteMonitor()
    monitor.run()


if __name__ == "__main__":
    main()
