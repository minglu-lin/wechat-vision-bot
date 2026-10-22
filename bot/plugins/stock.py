#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Quant Plugin: Multi-Market Real-Time Quotes & Quantitative Diagnostic Engine
Supports China A-Shares (SH/SZ/BJ), Hong Kong Stocks, US Stocks, Indices, and ETFs.
Provides zero-key real-time quotes, K-lines, MA, BOLL, MACD, KDJ, RSI, CYQ chip distribution,
and Pivot Points support/resistance boundaries.
"""

import math
import re
import json
import urllib.request
from typing import Dict, List, Optional, Tuple, Any

from .base import BasePlugin


# Comprehensive Chinese Aliases mapping to standard symbols
NAME_TO_CODE = {
    # Domestic Indices
    "上证指数": "sh000001", "上证": "sh000001", "大盘": "sh000001", "沪指": "sh000001",
    "深证成指": "sz399001", "深成指": "sz399001",
    "创业板指": "sz399006", "创业板": "sz399006",
    "科创50": "sh000688", "沪深300": "sh000300", "中证500": "sh000905", "中证1000": "sh000852",

    # Hong Kong & Global Indices
    "恒生指数": "hkHSI", "恒指": "hkHSI",
    "恒生科技": "hkHSTECH", "恒生科技指数": "hkHSTECH",
    "纳斯达克": "usIXIC", "纳指": "usIXIC", "纳斯达克指数": "usIXIC",
    "纳指100": "QQQ", "QQQ": "QQQ",
    "标普500": "SPY", "标普": "SPY", "SPY": "SPY",
    "道琼斯": "usDJI", "道指": "usDJI",

    # Popular China A-Shares
    "茅台": "600519", "贵州茅台": "600519",
    "五粮液": "000858", "泸州老窖": "000568", "汾酒": "600809", "山西汾酒": "600809",
    "比亚迪": "002594", "宁德时代": "300750", "宁德": "300750",
    "中芯国际": "688981", "中芯": "688981",
    "中国平安": "601318", "平安": "601318",
    "招商银行": "600036", "招行": "600036",
    "工商银行": "601398", "建设银行": "601939",
    "东方财富": "300059", "中信证券": "600030",
    "海康威视": "002415", "立讯精密": "002475", "北方华创": "002371",
    "中际旭创": "300308", "新易盛": "300502", "天孚通信": "300394",
    "工业富联": "601138", "寒武纪": "688256", "中科曙光": "603019",
    "长江电力": "600900", "中国神华": "601088", "中国石油": "601857", "中国海油": "600938",
    "赛力斯": "601127", "长安汽车": "000625",

    # Popular Hong Kong Shares
    "腾讯": "00700", "腾讯控股": "00700",
    "阿里": "09988", "阿里巴巴": "09988",
    "美团": "03690",
    "小米": "01810", "小米集团": "01810",
    "快手": "01024", "京东": "09618", "网易": "09999", "百度": "09888",
    "理想汽车": "02015", "小鹏汽车": "09868", "蔚来": "09866",
    "中芯港股": "00981", "药明生物": "02269",

    # Popular US Shares
    "英伟达": "NVDA", "苹果": "AAPL", "特斯拉": "TSLA",
    "微软": "MSFT", "谷歌": "GOOG", "亚马逊": "AMZN",
    "脸书": "META", "Meta": "META", "台积电": "TSM",
    "奈飞": "NFLX", "网飞": "NFLX", "AMD": "AMD", "超微": "AMD",
    "博通": "AVGO", "高通": "QCOM", "美光": "MU", "阿斯麦": "ASML",
    "伯克希尔": "BRK.B", "巴菲特": "BRK.B",
    "拼多多": "PDD", "贝壳": "BEKE", "满帮": "YMM", "瑞幸": "LKNCY",
    "Coinbase": "COIN", "Palantir": "PLTR", "超微电脑": "SMCI",
}


class StockDataEngine:
    """Zero-key multi-market financial data engine."""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }

    @classmethod
    def normalize_symbol(cls, raw_symbol: str) -> Tuple[str, str, str, str, str]:
        """
        Normalize input into standard codes.
        Returns: (clean_code, market_type, tencent_realtime_code, tencent_kline_code, eastmoney_secid)
        market_type: 'a_share' | 'hk_share' | 'us_share' | 'cn_index' | 'hk_index' | 'us_index'
        """
        s = (raw_symbol or "").strip()
        # 1. Alias lookup
        for k in sorted(NAME_TO_CODE.keys(), key=len, reverse=True):
            if k in s:
                s = NAME_TO_CODE[k]
                break

        s_clean = s.strip().upper()

        # 2. Chinese Index
        if s_clean in ["SH000001", "000001", "SH000300", "SH000905", "SH000688"] or (s_clean.startswith("SH000") and len(s_clean) == 8):
            code = s_clean.replace("SH", "")
            return code, "cn_index", f"sh{code}", f"sh{code}", f"1.{code}"
        if s_clean in ["SZ399001", "399001"]:
            return "399001", "cn_index", "sz399001", "sz399001", "0.399001"
        if s_clean in ["SZ399006", "399006"]:
            return "399006", "cn_index", "sz399006", "sz399006", "0.399006"

        # 3. HK Index
        if s_clean in ["HKHSI", "HSI"]:
            return "HSI", "hk_index", "hkHSI", "hkHSI", "100.HSI"
        if s_clean in ["HKHSTECH", "HSTECH"]:
            return "HSTECH", "hk_index", "hkHSTECH", "hkHSTECH", "100.HSTECH"

        # 4. US Index
        if s_clean in ["USIXIC", "IXIC"]:
            return "IXIC", "us_index", "usIXIC", "usQQQ.OQ", "100.NDX"
        if s_clean in ["USDJI", "DJI"]:
            return "DJI", "us_index", "usDJI", "usDIA.N", "100.DJI"

        # 5. A-Share (6 digits)
        m_a = re.search(r'(?<!\d)(sh|sz|bj)?(\d{6})(?!\d)', s, re.IGNORECASE)
        if m_a:
            code = m_a.group(2)
            pref = (m_a.group(1) or "").lower()
            if not pref:
                if code.startswith(("60", "688", "689", "51")):
                    pref = "sh"
                elif code.startswith(("00", "300", "301", "15")):
                    pref = "sz"
                else:
                    pref = "bj"
            secid = f"1.{code}" if pref == "sh" else f"0.{code}"
            return code, "a_share", f"{pref}{code}", f"{pref}{code}", secid

        # 6. HK Share (5 digits)
        m_hk = re.search(r'(?<!\d)(hk)?(\d{5})(?!\d)', s, re.IGNORECASE)
        if m_hk:
            code = m_hk.group(2)
            return code, "hk_share", f"hk{code}", f"hk{code}", f"116.{code}"

        # 7. US Share / ETF (letters)
        ticker = re.sub(r'[^A-Z\.]', '', s_clean)
        if not ticker:
            ticker = "NVDA"
        return ticker, "us_share", f"s_us{ticker}", f"us{ticker}", f"105.{ticker}"

    @staticmethod
    def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if val is None or val == "" or val == "None" or val == "-":
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    @classmethod
    def fetch_realtime_quote(cls, tencent_code: str, market_type: str, fallback_symbol: str) -> Dict[str, Any]:
        """Fetch ultra-low latency pan-kou quote from Tencent API."""
        url = f"https://qt.gtimg.cn/q={tencent_code}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                raw_bytes = resp.read()
                raw_text = raw_bytes.decode("gbk", errors="ignore")
        except Exception as e:
            return {"error": f"行情接口请求超时: {e}"}

        if not raw_text or "=" not in raw_text or "none_match" in raw_text:
            return {"error": f"未找到标的‘{fallback_symbol}’的实时行情"}

        line = raw_text.split(";")[0].strip()
        _, val = line.split("=", 1)
        parts = val.strip('"').split("~")

        quote = {
            "symbol": fallback_symbol,
            "raw_code": tencent_code,
            "market_type": market_type,
            "currency": "USD" if market_type == "us_share" else ("点" if "index" in market_type else ("HKD" if market_type == "hk_share" else "CNY")),
        }

        try:
            if market_type == "us_share":
                # Tencent US simple quote: [1]=name, [2]=ticker.suffix, [3]=price, [4]=chg, [5]=pct, [6]=volume, [7]=turnover, [8]=market_cap
                quote["name"] = parts[1] if len(parts) > 1 else fallback_symbol
                quote["ticker_with_suffix"] = parts[2] if len(parts) > 2 else fallback_symbol
                quote["price"] = cls._safe_float(parts[3] if len(parts) > 3 else None, 0.0)
                quote["change"] = cls._safe_float(parts[4] if len(parts) > 4 else None, 0.0)
                quote["pct_change"] = cls._safe_float(parts[5] if len(parts) > 5 else None, 0.0)
                quote["volume"] = cls._safe_float(parts[6] if len(parts) > 6 else None, 0.0)
                quote["turnover"] = cls._safe_float(parts[7] if len(parts) > 7 else None, 0.0)
                quote["market_cap_usd"] = cls._safe_float(parts[8] if len(parts) > 8 else None, 0.0)
            elif "index" in market_type:
                quote["name"] = parts[1] if len(parts) > 1 else fallback_symbol
                quote["price"] = cls._safe_float(parts[3] if len(parts) > 3 else None, 0.0)
                quote["prev_close"] = cls._safe_float(parts[4] if len(parts) > 4 else None, 0.0)
                quote["open"] = cls._safe_float(parts[5] if len(parts) > 5 else None, 0.0)
                quote["change"] = cls._safe_float(parts[31] if len(parts) > 31 else None, (quote["price"] - quote.get("prev_close", quote["price"])))
                quote["pct_change"] = cls._safe_float(parts[32] if len(parts) > 32 else None, 0.0)
                quote["high"] = cls._safe_float(parts[33] if len(parts) > 33 else None, quote["price"])
                quote["low"] = cls._safe_float(parts[34] if len(parts) > 34 else None, quote["price"])
            else:
                # A-share and HK share
                quote["name"] = parts[1] if len(parts) > 1 else fallback_symbol
                quote["code"] = parts[2] if len(parts) > 2 else fallback_symbol
                quote["price"] = cls._safe_float(parts[3] if len(parts) > 3 else None, 0.0)
                quote["prev_close"] = cls._safe_float(parts[4] if len(parts) > 4 else None, 0.0)
                quote["open"] = cls._safe_float(parts[5] if len(parts) > 5 else None, 0.0)
                quote["volume"] = cls._safe_float(parts[6] if len(parts) > 6 else None, 0.0)
                quote["change"] = cls._safe_float(parts[31] if len(parts) > 31 else None, 0.0)
                quote["pct_change"] = cls._safe_float(parts[32] if len(parts) > 32 else None, 0.0)
                quote["high"] = cls._safe_float(parts[33] if len(parts) > 33 else None, quote["price"])
                quote["low"] = cls._safe_float(parts[34] if len(parts) > 34 else None, quote["price"])

                raw_to = cls._safe_float(parts[37] if len(parts) > 37 else None, 0.0)
                if market_type == "hk_share":
                    # HK turnover is in raw HKD
                    quote["turnover_yi"] = round(raw_to / 1e8, 2) if raw_to else 0.0
                else:
                    # A-share turnover is in 10k CNY (万元)
                    quote["turnover_yi"] = round(raw_to / 10000.0, 2) if raw_to else 0.0

                quote["turnover_rate"] = cls._safe_float(parts[38] if len(parts) > 38 else None, 0.0)
                quote["pe_ttm"] = cls._safe_float(parts[39] if len(parts) > 39 else None, None)
                quote["market_cap_yi"] = cls._safe_float(parts[44] if len(parts) > 44 else None, None)
                quote["pb"] = cls._safe_float(parts[46] if len(parts) > 46 else None, None)

                # Pan-kou Bid 1 / Ask 1
                if len(parts) > 20 and parts[9] and parts[19]:
                    quote["bid1"] = {"price": cls._safe_float(parts[9], 0.0), "volume": int(cls._safe_float(parts[10], 0))}
                    quote["ask1"] = {"price": cls._safe_float(parts[19], 0.0), "volume": int(cls._safe_float(parts[20], 0))}
        except Exception as ex:
            quote["parse_warning"] = str(ex)

        return quote

    @classmethod
    def fetch_klines(cls, kline_code: str, secid: str, market_type: str, limit: int = 60, us_suffix: str = "") -> List[Dict[str, Any]]:
        """
        Fetch historical daily K-lines with dual-source fallback (Tencent -> Eastmoney).
        """
        klines = []

        # 1. Primary: Tencent K-line API
        candidate_codes = []
        if market_type == "us_share":
            if us_suffix:
                candidate_codes.append(f"us{us_suffix}")
            candidate_codes.extend([f"{kline_code}.OQ", f"{kline_code}.N", f"{kline_code}.AM", kline_code])
        else:
            candidate_codes.append(kline_code)

        for q_code in candidate_codes:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={q_code},day,,,{limit},qfq"
            try:
                req = urllib.request.Request(url, headers=cls.HEADERS)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    stock_info = data.get("data", {}).get(q_code, {})
                    raw_bars = stock_info.get("qfqday") or stock_info.get("day", [])
                    if len(raw_bars) >= 5:
                        for b in raw_bars:
                            if len(b) >= 6:
                                klines.append({
                                    "date": b[0],
                                    "open": float(b[1]),
                                    "close": float(b[2]),
                                    "high": float(b[3]),
                                    "low": float(b[4]),
                                    "volume": float(b[5]),
                                    "turnover": 0.0,
                                })
                        if klines:
                            return klines
            except Exception:
                continue

        # 2. Fallback: Eastmoney K-line API
        url_em = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = (
            f"?secid={secid}&klt=101&fqt=1&lmt={limit}&end=20500101&iscca=1"
            "&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        )
        try:
            req = urllib.request.Request(url_em + params, headers=cls.HEADERS)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_em = data.get("data", {}).get("klines", [])
                for line in raw_em:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        klines.append({
                            "date": parts[0],
                            "open": float(parts[1]),
                            "close": float(parts[2]),
                            "high": float(parts[3]),
                            "low": float(parts[4]),
                            "volume": float(parts[5]),
                            "turnover": float(parts[10]) if len(parts) > 10 else 0.0,
                        })
                if klines:
                    return klines
        except Exception:
            pass

        return klines


class TechnicalQuantEngine:
    """Pure-Python Technical Indicator & Quant Diagnostic Engine."""

    @staticmethod
    def compute_all(klines: List[Dict[str, Any]], current_price: Optional[float] = None) -> Dict[str, Any]:
        if not klines or len(klines) < 5:
            return {}

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        vols = [k["volume"] for k in klines]
        n = len(closes)
        curr_p = current_price if current_price and current_price > 0 else closes[-1]

        # 1. Moving Averages
        def sma(period: int) -> Optional[float]:
            if n < period:
                return None
            return round(sum(closes[-period:]) / float(period), 3)

        ma5 = sma(5)
        ma10 = sma(10)
        ma20 = sma(20)
        ma60 = sma(60)

        ma_trend = "震荡整理"
        bullish_score = 0
        if ma5 and ma10 and ma20:
            if curr_p > ma5 > ma10 > ma20:
                ma_trend = "强势多头排列 (各周期均线呈发散多头上攻形态)"
                bullish_score += 25
            elif curr_p < ma5 < ma10 < ma20:
                ma_trend = "标准空头排列 (均线全面向下发散，空方主导)"
                bullish_score -= 25
            elif curr_p > ma20:
                ma_trend = "站在生命线(MA20)上方，短多震荡"
                bullish_score += 10
            else:
                ma_trend = "受制于生命线(MA20)下方，短空承压"
                bullish_score -= 10

        # 2. Bollinger Bands (20, 2)
        boll = {}
        if n >= 20:
            m20 = sum(closes[-20:]) / 20.0
            var = sum((c - m20) ** 2 for c in closes[-20:]) / 20.0
            std = math.sqrt(var)
            upper = m20 + 2.0 * std
            lower = m20 - 2.0 * std
            pct_b = (curr_p - lower) / (upper - lower) if upper != lower else 0.5
            bandwidth = (upper - lower) / m20 * 100.0 if m20 else 0
            boll_sig = "轨道内部平稳运行"
            if curr_p >= upper:
                boll_sig = "突破或贴近上轨 (短期超买/强势冲顶)"
                bullish_score += 5
            elif curr_p <= lower:
                boll_sig = "跌破或贴近下轨 (短期超卖/存在超跌反弹动能)"
                bullish_score -= 5

            boll = {
                "mid": round(m20, 3),
                "upper": round(upper, 3),
                "lower": round(lower, 3),
                "pct_b": round(pct_b, 3),
                "bandwidth_pct": round(bandwidth, 2),
                "signal": boll_sig,
            }

        # 3. MACD (12, 26, 9)
        def calc_ema_series(data: List[float], period: int) -> List[float]:
            alpha = 2.0 / (period + 1.0)
            ema_list = [data[0]]
            for val in data[1:]:
                ema_list.append(alpha * val + (1.0 - alpha) * ema_list[-1])
            return ema_list

        macd_data = {}
        if n >= 26:
            ema12 = calc_ema_series(closes, 12)
            ema26 = calc_ema_series(closes, 26)
            dif_series = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
            dea_series = calc_ema_series(dif_series, 9)
            dif = dif_series[-1]
            dea = dea_series[-1]
            hist = (dif - dea) * 2.0
            prev_dif = dif_series[-2] if len(dif_series) >= 2 else dif
            prev_dea = dea_series[-2] if len(dea_series) >= 2 else dea

            cross_status = "多头死叉调整中" if dif < dea else "多头金叉运行中"
            if prev_dif <= prev_dea and dif > dea:
                cross_status = "⚡【金叉形成】DIF由下向上穿破DEA，发出做多信号！"
                bullish_score += 20
            elif prev_dif >= prev_dea and dif < dea:
                cross_status = "⚠️【死叉形成】DIF由上向下穿破DEA，短期注意回调避险！"
                bullish_score -= 20
            elif dif > 0:
                bullish_score += 10
            else:
                bullish_score -= 10

            macd_data = {
                "dif": round(dif, 3),
                "dea": round(dea, 3),
                "macd_bar": round(hist, 3),
                "cross_status": cross_status,
                "axis": "零轴上方 (多头占优)" if dif > 0 else "零轴下方 (空头占优)",
            }

        # 4. KDJ (9, 3, 3)
        kdj_data = {}
        if n >= 9:
            k_val = 50.0
            d_val = 50.0
            for i in range(max(0, n - 25), n):
                sub_low = min(lows[max(0, i - 8):i + 1])
                sub_high = max(highs[max(0, i - 8):i + 1])
                rsv = 50.0
                if sub_high != sub_low:
                    rsv = (closes[i] - sub_low) / (sub_high - sub_low) * 100.0
                k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
                d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
            j_val = 3.0 * k_val - 2.0 * d_val

            zone = "中性合理区间 (20~80)"
            if k_val >= 80 or j_val >= 100:
                zone = "严重超买警戒区 (>80)，谨防短线钝化见顶"
                bullish_score -= 5
            elif k_val <= 20 or j_val <= 0:
                zone = "深度超卖低估区 (<20)，短线孕育超跌反弹"
                bullish_score += 5

            kdj_data = {
                "k": round(k_val, 1),
                "d": round(d_val, 1),
                "j": round(j_val, 1),
                "zone": zone,
            }

        # 5. RSI (14)
        rsi_14 = None
        if n >= 15:
            gains = []
            losses = []
            for i in range(1, n):
                delta = closes[i] - closes[i - 1]
                gains.append(max(0.0, delta))
                losses.append(max(0.0, -delta))
            avg_gain = sum(gains[:14]) / 14.0
            avg_loss = sum(losses[:14]) / 14.0
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13.0 + gains[i]) / 14.0
                avg_loss = (avg_loss * 13.0 + losses[i]) / 14.0
            if avg_loss == 0:
                rsi_14 = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 1)

            if rsi_14 > 70:
                bullish_score += 5
            elif rsi_14 < 30:
                bullish_score -= 5

        # 6. Pivot Points & Key Support/Resistance
        last_k = klines[-1]
        prev_k = klines[-2] if n >= 2 else last_k
        p_high = prev_k["high"]
        p_low = prev_k["low"]
        p_close = prev_k["close"]
        pivot = (p_high + p_low + p_close) / 3.0
        r1 = 2.0 * pivot - p_low
        s1 = 2.0 * pivot - p_high
        r2 = pivot + (p_high - p_low)
        s2 = pivot - (p_high - p_low)

        h20 = max(highs[-20:])
        l20 = min(lows[-20:])

        # 7. CYQ Chip Distribution Simulation
        min_p = min(lows) * 0.98
        max_p = max(highs) * 1.02
        bin_num = 100
        bin_size = (max_p - min_p) / bin_num if max_p > min_p else 1.0
        bins = [0.0] * bin_num

        for k in klines:
            k_vol = k["volume"]
            k_low = k["low"]
            k_high = k["high"]
            k_tr = k.get("turnover", 0.0)
            tr = min(0.40, max(0.01, k_tr / 100.0)) if k_tr > 0 else 0.04
            decay = 1.0 - tr
            for b in range(bin_num):
                bins[b] *= decay

            low_idx = min(bin_num - 1, max(0, int((k_low - min_p) / bin_size)))
            high_idx = min(bin_num - 1, max(0, int((k_high - min_p) / bin_size)))
            num_spread = max(1, high_idx - low_idx + 1)
            added_per_bin = (k_vol * tr) / num_spread
            for b in range(low_idx, high_idx + 1):
                bins[b] += added_per_bin

        total_chips = sum(bins) or 1.0
        curr_bin = min(bin_num - 1, max(0, int((curr_p - min_p) / bin_size)))
        profit_chips = sum(bins[:curr_bin + 1])
        profit_ratio = round(profit_chips / total_chips * 100.0, 1)

        weighted_cost_sum = sum((min_p + (b + 0.5) * bin_size) * bins[b] for b in range(bin_num))
        avg_cost = round(weighted_cost_sum / total_chips, 2)

        # 70% concentration range
        accum = 0.0
        cost_70_low, cost_70_high = avg_cost, avg_cost
        for b in range(bin_num):
            accum += bins[b] / total_chips
            if accum >= 0.15 and cost_70_low == avg_cost:
                cost_70_low = round(min_p + b * bin_size, 2)
            if accum >= 0.85 and cost_70_high == avg_cost:
                cost_70_high = round(min_p + (b + 1) * bin_size, 2)
                break

        # 8. Volume Dynamics (Volume Ratio vs 5d avg)
        avg_vol_5d = sum(vols[-5:]) / 5.0 if n >= 5 else vols[-1]
        vol_ratio_5d = round(vols[-1] / avg_vol_5d, 2) if avg_vol_5d > 0 else 1.0
        last_pct_chg = round((curr_p - klines[-2]["close"]) / klines[-2]["close"] * 100.0, 2) if n >= 2 else 0.0

        vol_status = "平量运行"
        if vol_ratio_5d >= 1.8:
            if last_pct_chg > 2.0:
                vol_status = "放量强势上攻 (主力资金积极入场抢筹)"
                bullish_score += 15
            elif last_pct_chg < -2.0:
                vol_status = "放量破位下挫 (恐慌盘与止损盘涌出)"
                bullish_score -= 15
            else:
                vol_status = "放量滞涨 (高位多空剧烈分歧换手)"
        elif vol_ratio_5d <= 0.65:
            if last_pct_chg < 0:
                vol_status = "缩量洗盘调整 (抛压衰竭，等待企稳信号)"
            else:
                vol_status = "缩量弱势反弹 (跟风资金不足，存量博弈)"

        # 9. Composite Quant Diagnosis
        bullish_score = max(-100, min(100, bullish_score))
        if bullish_score >= 50:
            rating_stars = "⭐⭐⭐⭐⭐"
            diagnosis_stance = "强力看多 (多指标共振强势突破)"
        elif bullish_score >= 20:
            rating_stars = "⭐⭐⭐⭐"
            diagnosis_stance = "偏多运行 (均线与动能指标稳步上行)"
        elif bullish_score >= -20:
            rating_stars = "⭐⭐⭐"
            diagnosis_stance = "震荡中立 (箱体拉锯，方向暂不明朗)"
        elif bullish_score >= -50:
            rating_stars = "⭐⭐"
            diagnosis_stance = "偏空调整 (均线受阻，空方占优)"
        else:
            rating_stars = "⭐"
            diagnosis_stance = "破位探底 (空头排列，谨慎抄底避险)"

        return {
            "price": curr_p,
            "quant_score": bullish_score,
            "rating_stars": rating_stars,
            "diagnosis_stance": diagnosis_stance,
            "ma": {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "trend": ma_trend},
            "boll": boll,
            "macd": macd_data,
            "kdj": kdj_data,
            "rsi14": rsi_14,
            "support_resistance": {
                "pivot": round(pivot, 2),
                "r1": round(r1, 2),
                "r2": round(r2, 2),
                "s1": round(s1, 2),
                "s2": round(s2, 2),
                "high_20d": round(h20, 2),
                "low_20d": round(l20, 2),
            },
            "chips": {
                "profit_ratio_pct": profit_ratio,
                "avg_holding_cost": avg_cost,
                "concentration_70_range": f"{cost_70_low} ~ {cost_70_high}",
            },
            "volume": {
                "vol_ratio_5d": vol_ratio_5d,
                "status": vol_status,
            },
        }


class StockQuantPlugin(BasePlugin):
    name = "analyze_stock"
    description = (
        "查询并量化诊断全球股票（A股、港股、美股、指数ETF等）的实时盘口行情、"
        "日K线多空均线结构、MACD/KDJ/RSI指标、布林带形态、CYQ筹码集中度与主力获利盘分布、"
        "以及Pivot核心攻防支撑阻力位。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "股票代码或中文名称，如：600519、NVDA、AAPL、00700、茅台、腾讯、英伟达、特斯拉、纳指、上证指数、标普500",
            },
            "period": {
                "type": "string",
                "description": "K线周期，默认为 daily（日K）",
                "default": "daily",
            },
        },
        "required": ["symbol"],
    }

    # Fast-Path patterns for instant quote inquiry (0-token, <100ms)
    fast_patterns = [
        r'^(?:查一下?|看下?|查询?)?\s*(?:实时|最新)?\s*(?P<symbol>[A-Za-z0-9\.\^]+|[\u4e00-\u9fa5]{2,6})\s*(?:实时|最新)?\s*(?:股价|价格|行情|盘口|现价)$',
        r'^(?:股价|行情|盘口)\s*(?P<symbol>[A-Za-z0-9\.\^]+|[\u4e00-\u9fa5]{2,6})$',
        r'^(?:查一下?|看下?|查询?)?\s*(?P<symbol>[A-Za-z0-9\.\^]+|[\u4e00-\u9fa5]{2,6})\s*(?:现在|目前)?\s*(?:多少钱|多少点|现价是多少)$',
    ]

    @classmethod
    def extract_stock_intent(cls, text: str) -> Optional[str]:
        """
        Detect if user query has stock analysis intent and extract the target stock/index symbol.
        Returns symbol string if matched, None otherwise.
        """
        if not text:
            return None
        t = text.strip()
        t = re.sub(r'^(?:@\S+\s*|bot\s*|albot\s*|aibot\s*|小号\s*|：|:|,|，)+', '', t, flags=re.IGNORECASE).strip()

        stock_kws = [
            "分析", "技术面", "基本面", "k线", "走势", "操盘", "压力位", "支撑位",
            "量化", "多空", "诊断", "怎么看", "能买吗", "抄底", "逢高", "行情分析",
            "盘面", "估值", "筹码", "买入", "卖出", "止损", "目标价", "怎么样", "看下", "看看"
        ]

        has_intent = any(kw in t for kw in stock_kws)

        # 1. Known names/aliases
        for name in sorted(NAME_TO_CODE.keys(), key=len, reverse=True):
            if name in t:
                # If explicit stock kw present or name is specific index/asset
                if has_intent or name in ["上证指数", "大盘", "沪指", "恒指", "纳指", "标普500", "道指", "恒生科技"]:
                    return name

        # 2. 6-digit A-share code
        m_a = re.search(r'(?<!\d)(sh|sz|bj)?(\d{6})(?!\d)', t, re.IGNORECASE)
        if m_a:
            return m_a.group(0)

        # 3. 5-digit HK share code
        m_hk = re.search(r'(?<!\d)(hk)?(\d{5})(?!\d)', t, re.IGNORECASE)
        if m_hk:
            return m_hk.group(0)

        # 4. US ticker (1-5 letters) with explicit stock intent
        if has_intent:
            m_us = re.search(r'(?<![A-Za-z])([A-Za-z]{1,5})(?![A-Za-z])', t)
            if m_us:
                sym = m_us.group(1).upper()
                if sym not in ["BOT", "AI", "HI", "THE", "AND", "IS", "K", "D", "J", "RSI", "MACD", "MA", "BOLL", "ETF", "OK"]:
                    return sym

        return None

    def execute(self, symbol: str = "", **kwargs) -> dict:
        sym = (symbol or kwargs.get("stock", "") or kwargs.get("code", "")).strip()
        # Clean potential noise words
        sym = re.sub(r'^(?:实时|最新|股票|查|看)', '', sym).strip()
        sym = re.sub(r'(?:实时|最新|股票|股价|行情|多少钱|现价)$', '', sym).strip()
        if not sym:
            return {"error": "未提供有效的股票代码或名称"}

        # 1. Normalize code and market routing
        clean_code, market_type, tc_code, kline_code, secid = StockDataEngine.normalize_symbol(sym)

        # 2. Fetch real-time pan-kou quote
        quote = StockDataEngine.fetch_realtime_quote(tc_code, market_type, clean_code)
        if "error" in quote:
            return quote

        # 3. Extract US suffix if present
        us_suffix = quote.get("ticker_with_suffix", "") if market_type == "us_share" else ""

        # 4. Fetch 60 daily K-line bars
        curr_price = quote.get("price", 0.0)
        klines = StockDataEngine.fetch_klines(kline_code, secid, market_type, limit=60, us_suffix=us_suffix)

        # 5. Compute Quantitative Indicators
        quant = TechnicalQuantEngine.compute_all(klines, current_price=curr_price)

        # 6. Build structured result
        res = {
            "query_symbol": sym,
            "resolved_code": clean_code,
            "market_type": market_type,
            "quote": quote,
            "quant": quant,
            "kline_count": len(klines),
        }

        # Build readable report markdown
        res["report_markdown"] = self._build_quant_report(res)
        return res

    def _build_quant_report(self, res: dict) -> str:
        q = res.get("quote", {})
        quant = res.get("quant", {})
        name = q.get("name", res.get("resolved_code"))
        price = q.get("price", 0.0)
        chg = q.get("change", 0.0)
        pct = q.get("pct_change", 0.0)
        cur = q.get("currency", "")
        sign = "+" if chg > 0 else ""

        lines = [
            f"📊 【{name} ({res.get('resolved_code')}) 量化操盘诊断】",
            f"• 最新现价：{price:,.2f} {cur} ({sign}{pct:.2f}%)",
        ]

        if "high" in q and "low" in q:
            lines.append(f"• 今日振幅：{q['low']:,.2f} ~ {q['high']:,.2f}")

        # Valuation & Pan-kou
        val_parts = []
        if q.get("pe_ttm"):
            val_parts.append(f"PE(TTM): {q['pe_ttm']:.1f}")
        if q.get("pb"):
            val_parts.append(f"PB: {q['pb']:.2f}")
        if q.get("market_cap_yi"):
            val_parts.append(f"总市值: {q['market_cap_yi']:.1f}亿")
        elif q.get("market_cap_usd"):
            val_parts.append(f"总市值: ${q['market_cap_usd']:.1f}亿")
        if val_parts:
            lines.append("• 估值数据：" + " | ".join(val_parts))

        # Technical structure
        if quant:
            lines.append(f"• 量化评级：{quant.get('rating_stars', '⭐⭐⭐')} 【{quant.get('diagnosis_stance', '震荡')}】")
            ma = quant.get("ma", {})
            lines.append(f"• 均线形态：{ma.get('trend', '-')}")
            lines.append(f"  MA5: {ma.get('ma5', '-')} | MA20(生命线): {ma.get('ma20', '-')} | MA60(决策线): {ma.get('ma60', '-')}")

            # MACD & KDJ
            macd = quant.get("macd", {})
            kdj = quant.get("kdj", {})
            lines.append(f"• MACD状态：{macd.get('cross_status', '-')} ({macd.get('axis', '-')})")
            lines.append(f"• KDJ动能：K={kdj.get('k', '-')} D={kdj.get('d', '-')} J={kdj.get('j', '-')} -> {kdj.get('zone', '-')}")

            # RSI & BOLL
            boll = quant.get("boll", {})
            lines.append(f"• 布林通道：{boll.get('signal', '-')} (上轨 {boll.get('upper', '-')} / 下轨 {boll.get('lower', '-')})")
            if quant.get("rsi14") is not None:
                lines.append(f"• RSI(14)：{quant['rsi14']} ({'超买' if quant['rsi14'] > 70 else ('超卖' if quant['rsi14'] < 30 else '中性')})")

            # CYQ Chips
            chips = quant.get("chips", {})
            lines.append(f"• 筹码结构：主力获利盘比例 {chips.get('profit_ratio_pct', '-')}% | 平均持仓成本 {chips.get('avg_holding_cost', '-')} {cur}")

            # Support & Resistance
            sr = quant.get("support_resistance", {})
            lines.append(f"• 攻防关口：第一阻力位(R1) {sr.get('r1', '-')} | 第一支撑位(S1) {sr.get('s1', '-')}")
            lines.append(f"  极端压力(R2): {sr.get('r2', '-')} | 破位止损(S2): {sr.get('s2', '-')}")

            # Volume
            vol = quant.get("volume", {})
            lines.append(f"• 量能表现：量比 {vol.get('vol_ratio_5d', 1.0)}x -> {vol.get('status', '-')}")

        return "\n".join(lines)

    def format_fast_reply(self, result: dict) -> str:
        """Format instant WeChat quote card for fast-path."""
        if "error" in result:
            return f"查询行情失败：{result['error']}"

        q = result.get("quote", {})
        quant = result.get("quant", {})
        name = q.get("name", result.get("resolved_code"))
        code = result.get("resolved_code")
        price = q.get("price", 0.0)
        chg = q.get("change", 0.0)
        pct = q.get("pct_change", 0.0)
        cur = q.get("currency", "")
        sign = "+" if chg > 0 else ""

        # Price card
        lines = [
            f"📈 【{name} ({code}) 实时行情】",
            f"• 最新现价：{price:,.2f} {cur} ({sign}{pct:.2f}%)",
        ]

        if "high" in q and "low" in q:
            lines.append(f"• 今日区间：{q['low']:,.2f} ~ {q['high']:,.2f}")

        # Turnover
        if q.get("turnover_yi"):
            tr_str = f" | 换手率 {q.get('turnover_rate', 0.0):.2f}%" if q.get("turnover_rate") else ""
            lines.append(f"• 成交数据：成交额 {q['turnover_yi']:.2f} 亿{cur}{tr_str}")
        elif q.get("volume"):
            lines.append(f"• 成交量：{int(q['volume']):,} 股")

        # Valuation
        val_parts = []
        if q.get("pe_ttm"):
            val_parts.append(f"PE {q['pe_ttm']:.1f}")
        if q.get("pb"):
            val_parts.append(f"PB {q['pb']:.2f}")
        if q.get("market_cap_yi"):
            val_parts.append(f"市值 {q['market_cap_yi']:.0f}亿")
        elif q.get("market_cap_usd"):
            val_parts.append(f"市值 ${q['market_cap_usd']:.0f}亿")
        if val_parts:
            lines.append("• 估值数据：" + " | ".join(val_parts))

        # Pan-kou
        if "bid1" in q and "ask1" in q:
            lines.append(f"• 盘口数据：买一 {q['bid1']['price']:.2f} ({q['bid1']['volume']}手) | 卖一 {q['ask1']['price']:.2f} ({q['ask1']['volume']}手)")

        # Quick tech indicator preview if available
        if quant and quant.get("ma"):
            sr = quant.get("support_resistance", {})
            chips = quant.get("chips", {})
            lines.append(f"• 量化简评：{quant.get('rating_stars', '')} {quant.get('diagnosis_stance', '')}")
            lines.append(f"• 支撑阻力：支撑位 {sr.get('s1', '-')} | 压力位 {sr.get('r1', '-')} | 获利盘 {chips.get('profit_ratio_pct', '-')}%")

        lines.append(f"💡 发送 “分析一下 {name}” 可获取完整 AI 量化操盘深度研报")
        return "\n".join(lines)
