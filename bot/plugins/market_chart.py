#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Financial & Macroeconomic Data Chart Plugin (Zero-AI Local Script Renderer)
Integrates directly with Buffett Terminal (https://buffett.lmlu.de/) for US equity
valuation / safety-margin overlays & OHLCV data, plus Yahoo Finance v8, EastMoney,
Tencent Smartbox, and World Bank APIs for global equities (US/KR/JP/TW/HK/CN/EU),
indices, commodities, FX, treasury yields, crypto, and macroeconomic indicators.

Renders crisp 2x-supersampled dark-mode charts locally via Pillow (zero AI generation)
and dispatches the PNG directly into WeChat via X11 clipboard.
"""

import io
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .base import BasePlugin
from .image_tools import get_image_sender
from .stock import NAME_TO_CODE, StockDataEngine, StockQuantPlugin

TZ_BJ = timezone(timedelta(hours=8))
BUFFETT_BASE_URL = "https://buffett.lmlu.de"

# Global equities (KR/JP/TW/EU/US), commodities, yields, FX & crypto aliases -> (yahoo_symbol, display_name)
GLOBAL_ASSET_ALIASES: Dict[str, Tuple[str, str]] = {
    # Korea
    "sk海力士": ("000660.KS", "SK海力士"),
    "海力士": ("000660.KS", "SK海力士"),
    "hynix": ("000660.KS", "SK Hynix"),
    "三星电子": ("005930.KS", "三星电子"),
    "三星": ("005930.KS", "三星电子"),
    "现代汽车": ("005380.KS", "现代汽车"),
    "韩国综合指数": ("^KS11", "韩国 KOSPI 指数"),
    "kospi": ("^KS11", "韩国 KOSPI 指数"),
    # Japan
    "丰田汽车": ("7203.T", "丰田汽车"),
    "丰田": ("7203.T", "丰田汽车"),
    "索尼": ("6758.T", "索尼集团"),
    "东京电子": ("8035.T", "东京电子"),
    "软银集团": ("9984.T", "软银集团"),
    "软银": ("9984.T", "软银集团"),
    "任天堂": ("7974.T", "任天堂"),
    "日经225": ("^N225", "日经 225 指数"),
    "日经指数": ("^N225", "日经 225 指数"),
    "日经": ("^N225", "日经 225 指数"),
    # Taiwan
    "台积电台股": ("2330.TW", "台积电 (台股)"),
    "鸿海": ("2317.TW", "鸿海精密"),
    "富士康": ("2317.TW", "鸿海精密"),
    "联发科": ("2454.TW", "联发科"),
    # Europe / Global Giants
    "诺和诺德": ("NVO", "诺和诺德"),
    "阿斯麦": ("ASML", "阿斯麦 ASML"),
    "路威酩轩": ("MC.PA", "LVMH 集团"),
    "lvmh": ("MC.PA", "LVMH 集团"),
    # Commodities
    "国际金价": ("GC=F", "COMEX 黄金期货"),
    "现货黄金": ("GC=F", "COMEX 黄金期货"),
    "黄金价格": ("GC=F", "COMEX 黄金期货"),
    "黄金": ("GC=F", "COMEX 黄金期货"),
    "金价": ("GC=F", "COMEX 黄金期货"),
    "白银价格": ("SI=F", "COMEX 白银期货"),
    "白银": ("SI=F", "COMEX 白银期货"),
    "银价": ("SI=F", "COMEX 白银期货"),
    "wti原油": ("CL=F", "WTI 原油期货"),
    "国际油价": ("CL=F", "WTI 原油期货"),
    "原油价格": ("CL=F", "WTI 原油期货"),
    "原油": ("CL=F", "WTI 原油期货"),
    "油价": ("CL=F", "WTI 原油期货"),
    "布伦特原油": ("BZ=F", "布伦特原油期货"),
    "布油": ("BZ=F", "布伦特原油期货"),
    "国际铜": ("HG=F", "COMEX 铜期货"),
    "铜价": ("HG=F", "COMEX 铜期货"),
    "天然气": ("NG=F", "NYMEX 天然气期货"),
    # Treasury Yields, FX & Volatility
    "10年期美债收益率": ("^TNX", "美国10年期国债收益率"),
    "十年期美债收益率": ("^TNX", "美国10年期国债收益率"),
    "10年期美债": ("^TNX", "美国10年期国债收益率"),
    "十年期美债": ("^TNX", "美国10年期国债收益率"),
    "美债10年": ("^TNX", "美国10年期国债收益率"),
    "美债收益率": ("^TNX", "美国10年期国债收益率"),
    "美债": ("^TNX", "美国10年期国债收益率"),
    "30年期美债": ("^TYX", "美国30年期国债收益率"),
    "美债30年": ("^TYX", "美国30年期国债收益率"),
    "美元指数": ("DX-Y.NYB", "美元指数 DXY"),
    "美指": ("DX-Y.NYB", "美元指数 DXY"),
    "dxy": ("DX-Y.NYB", "美元指数 DXY"),
    "美元兑人民币": ("CNY=X", "美元兑人民币汇率"),
    "离岸人民币": ("CNY=X", "美元兑人民币汇率"),
    "人民币汇率": ("CNY=X", "美元兑人民币汇率"),
    "美元兑日元": ("JPY=X", "美元兑日元汇率"),
    "日元汇率": ("JPY=X", "美元兑日元汇率"),
    "欧元兑美元": ("EURUSD=X", "欧元兑美元汇率"),
    "恐慌指数": ("^VIX", "CBOE VIX 恐慌指数"),
    "vix": ("^VIX", "CBOE VIX 恐慌指数"),
    # Indices & Crypto
    "费城半导体指数": ("^SOX", "费城半导体指数"),
    "费城半导体": ("^SOX", "费城半导体指数"),
    "半导体指数": ("^SOX", "费城半导体指数"),
    "标普500": ("^GSPC", "标普 500 指数"),
    "标普": ("^GSPC", "标普 500 指数"),
    "美股大盘": ("^GSPC", "标普 500 指数"),
    "美股": ("^GSPC", "标普 500 指数"),
    "纳斯达克": ("^IXIC", "纳斯达克综合指数"),
    "纳指": ("^IXIC", "纳斯达克综合指数"),
    "道琼斯": ("^DJI", "道琼斯工业指数"),
    "道指": ("^DJI", "道琼斯工业指数"),
    "恒生指数": ("^HSI", "香港恒生指数"),
    "恒指": ("^HSI", "香港恒生指数"),
    "港股大盘": ("^HSI", "香港恒生指数"),
    "港股": ("^HSI", "香港恒生指数"),
    "恒生科技": ("3033.HK", "恒生科技 ETF"),
    "上证指数": ("000001.SS", "上证综合指数"),
    "上证综指": ("000001.SS", "上证综合指数"),
    "上证": ("000001.SS", "上证综合指数"),
    "沪指": ("000001.SS", "上证综合指数"),
    "大盘": ("000001.SS", "上证综合指数"),
    "a股大盘": ("000001.SS", "上证综合指数"),
    "大a": ("000001.SS", "上证综合指数"),
    "a股": ("000001.SS", "上证综合指数"),
    "沪深300": ("000300.SS", "沪深 300 指数"),
    "中证500": ("000905.SS", "中证 500 指数"),
    "中证1000": ("000852.SS", "中证 1000 指数"),
    "科创50": ("000688.SS", "科创 50 指数"),
    "科创板": ("000688.SS", "科创 50 指数"),
    "北证50": ("899050.BJ", "北证 50 指数"),
    "深证成指": ("399001.SZ", "深证成指"),
    "深成指": ("399001.SZ", "深证成指"),
    "创业板指数": ("399006.SZ", "创业板指"),
    "创业板指": ("399006.SZ", "创业板指"),
    "创业板": ("399006.SZ", "创业板指"),
    "比特币": ("BTC-USD", "比特币 BTC"),
    "大饼": ("BTC-USD", "比特币 BTC"),
    "btc": ("BTC-USD", "比特币 BTC"),
    "以太坊": ("ETH-USD", "以太坊 ETH"),
    "二饼": ("ETH-USD", "以太坊 ETH"),
    "eth": ("ETH-USD", "以太坊 ETH"),
    "solana": ("SOL-USD", "Solana SOL"),
    "狗狗币": ("DOGE-USD", "狗狗币 DOGE"),
}

# Macroeconomic series definitions
MACRO_ALIASES: Dict[str, Dict[str, Any]] = {
    "中国cpi": {"source": "eastmoney", "report": "RPT_ECONOMY_CPI", "col": "NATIONAL_SAME", "name": "中国居民消费价格指数 (CPI 同比)", "unit": "%", "ref_line": 0.0},
    "国内cpi": {"source": "eastmoney", "report": "RPT_ECONOMY_CPI", "col": "NATIONAL_SAME", "name": "中国居民消费价格指数 (CPI 同比)", "unit": "%", "ref_line": 0.0},
    "中国通胀": {"source": "eastmoney", "report": "RPT_ECONOMY_CPI", "col": "NATIONAL_SAME", "name": "中国居民消费价格指数 (CPI 同比)", "unit": "%", "ref_line": 0.0},
    "cpi": {"source": "eastmoney", "report": "RPT_ECONOMY_CPI", "col": "NATIONAL_SAME", "name": "中国居民消费价格指数 (CPI 同比)", "unit": "%", "ref_line": 0.0},
    "中国ppi": {"source": "eastmoney", "report": "RPT_ECONOMY_PPI", "col": "BASE_SAME", "name": "中国工业品出厂价格指数 (PPI 同比)", "unit": "%", "ref_line": 0.0},
    "国内ppi": {"source": "eastmoney", "report": "RPT_ECONOMY_PPI", "col": "BASE_SAME", "name": "中国工业品出厂价格指数 (PPI 同比)", "unit": "%", "ref_line": 0.0},
    "ppi": {"source": "eastmoney", "report": "RPT_ECONOMY_PPI", "col": "BASE_SAME", "name": "中国工业品出厂价格指数 (PPI 同比)", "unit": "%", "ref_line": 0.0},
    "中国pmi": {"source": "eastmoney", "report": "RPT_ECONOMY_PMI", "col": "MAKE_INDEX", "name": "中国官方制造业 PMI 指数", "unit": "点", "ref_line": 50.0},
    "制造业pmi": {"source": "eastmoney", "report": "RPT_ECONOMY_PMI", "col": "MAKE_INDEX", "name": "中国官方制造业 PMI 指数", "unit": "点", "ref_line": 50.0},
    "pmi": {"source": "eastmoney", "report": "RPT_ECONOMY_PMI", "col": "MAKE_INDEX", "name": "中国官方制造业 PMI 指数", "unit": "点", "ref_line": 50.0},
    "中国m2": {"source": "eastmoney", "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "col": "BASIC_CURRENCY_SAME", "name": "中国广义货币供应量 (M2 同比增速)", "unit": "%", "ref_line": None},
    "货币供应量": {"source": "eastmoney", "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "col": "BASIC_CURRENCY_SAME", "name": "中国广义货币供应量 (M2 同比增速)", "unit": "%", "ref_line": None},
    "m2增速": {"source": "eastmoney", "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "col": "BASIC_CURRENCY_SAME", "name": "中国广义货币供应量 (M2 同比增速)", "unit": "%", "ref_line": None},
    "m2": {"source": "eastmoney", "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "col": "BASIC_CURRENCY_SAME", "name": "中国广义货币供应量 (M2 同比增速)", "unit": "%", "ref_line": None},
    "中国gdp增速": {"source": "eastmoney", "report": "RPT_ECONOMY_GDP", "col": "SUM_SAME", "name": "中国季度 GDP 同比增速", "unit": "%", "ref_line": None},
    "中国gdp": {"source": "eastmoney", "report": "RPT_ECONOMY_GDP", "col": "SUM_SAME", "name": "中国季度 GDP 同比增速", "unit": "%", "ref_line": None},
    "国内gdp": {"source": "eastmoney", "report": "RPT_ECONOMY_GDP", "col": "SUM_SAME", "name": "中国季度 GDP 同比增速", "unit": "%", "ref_line": None},
    "美国cpi": {"source": "worldbank", "country": "USA", "indicator": "FP.CPI.TOTL.ZG", "name": "美国年度 CPI 通胀率 (World Bank)", "unit": "%", "ref_line": 2.0},
    "美国通胀": {"source": "worldbank", "country": "USA", "indicator": "FP.CPI.TOTL.ZG", "name": "美国年度 CPI 通胀率 (World Bank)", "unit": "%", "ref_line": 2.0},
    "美国失业率": {"source": "worldbank", "country": "USA", "indicator": "SL.UEM.TOTL.ZS", "name": "美国年度失业率 (World Bank)", "unit": "%", "ref_line": None},
    "美国gdp增速": {"source": "worldbank", "country": "USA", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "美国年度实际 GDP 增速 (World Bank)", "unit": "%", "ref_line": 0.0},
    "美国gdp": {"source": "worldbank", "country": "USA", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "美国年度实际 GDP 增速 (World Bank)", "unit": "%", "ref_line": 0.0},
    "日本cpi": {"source": "worldbank", "country": "JPN", "indicator": "FP.CPI.TOTL.ZG", "name": "日本年度 CPI 通胀率 (World Bank)", "unit": "%", "ref_line": 2.0},
    "日本gdp": {"source": "worldbank", "country": "JPN", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "日本年度实际 GDP 增速 (World Bank)", "unit": "%", "ref_line": 0.0},
}

_CN_NUM_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}


def _http_get_json(url: str, timeout: int = 7, headers: Optional[Dict[str, str]] = None) -> Any:
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def cleanup_temp_charts(max_age_sec: int = 86400) -> None:
    tmp_dir = "/tmp" if os.path.isdir("/tmp") else os.environ.get("TEMP", ".")
    now = time.time()
    try:
        for fn in os.listdir(tmp_dir):
            if fn.startswith("wechat_search_chart_") and fn.endswith(".png"):
                fp = os.path.join(tmp_dir, fn)
                if now - os.path.getmtime(fp) > max_age_sec:
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
    except Exception:
        pass


def parse_period_spec(period_str: str) -> Tuple[str, str, Optional[int], str]:
    """Parse natural or code period into (yahoo_range, yahoo_interval, slice_days, label)."""
    s = (period_str or "").strip().lower()
    if not s:
        return "1y", "1d", 366, "近1年"

    if s in ("ytd", "今年", "今年以来", "年初至今", "年内", "本年"):
        now = datetime.now(TZ_BJ)
        days = max(5, (now - datetime(now.year, 1, 1, tzinfo=TZ_BJ)).days + 1)
        return "ytd", "1d", days, "今年以来"
    if s in ("max", "all", "全部", "历史", "上市以来", "全周期"):
        return "max", "1mo", None, "历史全周期"
    if "半年" in s:
        return "6mo", "1d", 185, "近半年"

    # Match explicit code like 1mo, 3mo, 6mo, 1y, 2y, 3y, 5y, 10y, 30d
    m_code = re.search(r'(\d+)\s*(mo|m|y|yr|w|wk|d)\b', s)
    if m_code:
        num = int(m_code.group(1))
        unit = m_code.group(2)
        if unit in ("d",):
            days = max(5, num)
            yr = "1mo" if days <= 31 else ("3mo" if days <= 93 else ("6mo" if days <= 186 else "1y"))
            return yr, "1d", days, f"近{days}天"
        if unit in ("w", "wk"):
            days = num * 7
            yr = "3mo" if days <= 90 else "1y"
            return yr, "1d", days, f"近{num}周"
        if unit in ("m", "mo"):
            days = num * 31
            if num <= 1:
                return "1mo", "1d", 32, "近1个月"
            if num <= 3:
                return "3mo", "1d", 93, f"近{num}个月"
            if num <= 6:
                return "6mo", "1d", 186, f"近{num}个月"
            return "1y", "1d", min(366, days), f"近{num}个月"
        if unit in ("y", "yr"):
            days = int(num * 365.25) + 2
            if num <= 1:
                return "1y", "1d", days, "近1年"
            if num <= 2:
                return "2y", "1d", days, "近2年"
            if num <= 5:
                return "5y", "1d", days, f"近{num}年"
            if num <= 10:
                return "10y", "1wk", days, f"近{num}年"
            return "max", "1mo", days, f"近{num}年"

    # Match Chinese period e.g. 最近5年 / 近三年 / 过去6个月 / 30天
    m_cn = re.search(r'(\d+|[一二两三四五六七八九十半])\s*个?\s*(年|月|周|星期|天|日)', s)
    if m_cn:
        raw_n, unit = m_cn.group(1), m_cn.group(2)
        num_f = float(raw_n) if raw_n.isdigit() else float(_CN_NUM_MAP.get(raw_n, 1))
        if unit == "年":
            days = int(num_f * 365.25) + 2
            n_lbl = f"{int(num_f)}" if num_f == int(num_f) else f"{num_f}"
            if num_f <= 1:
                return "1y", "1d", days, f"近{n_lbl}年"
            if num_f <= 2:
                return "2y", "1d", days, f"近{n_lbl}年"
            if num_f <= 5:
                return "5y", "1d", days, f"近{n_lbl}年"
            if num_f <= 10:
                return "10y", "1wk", days, f"近{n_lbl}年"
            return "max", "1mo", days, f"近{n_lbl}年"
        if unit == "月":
            m_int = max(1, int(round(num_f)))
            days = m_int * 31
            if m_int <= 1:
                return "1mo", "1d", days, "近1个月"
            if m_int <= 3:
                return "3mo", "1d", days, f"近{m_int}个月"
            if m_int <= 6:
                return "6mo", "1d", days, f"近{m_int}个月"
            return "1y", "1d", min(366, days), f"近{m_int}个月"
        if unit in ("周", "星期"):
            w_int = max(1, int(round(num_f)))
            days = w_int * 7
            return ("1mo" if days <= 31 else "3mo"), "1d", days, f"近{w_int}周"
        if unit in ("天", "日"):
            d_int = max(5, int(round(num_f)))
            return ("1mo" if d_int <= 31 else ("3mo" if d_int <= 93 else "1y")), "1d", d_int, f"近{d_int}天"

    return "1y", "1d", 366, "近1年"


class MarketChartDataEngine:
    """Multi-source market & macro series fetcher integrated with buffett.lmlu.de."""

    @staticmethod
    def _tc_to_yahoo(tc_or_code: str, market_type: str, clean_code: str) -> str:
        if market_type == "cn_index":
            return f"{clean_code}.SS" if tc_or_code.startswith("sh") else f"{clean_code}.SZ"
        if market_type == "hk_index":
            return "^HSI" if clean_code == "HSI" else "3033.HK"
        if market_type == "us_index":
            return "^IXIC" if clean_code == "IXIC" else "^DJI"
        if market_type == "a_share":
            if tc_or_code.startswith("sh"):
                return f"{clean_code}.SS"
            if tc_or_code.startswith("sz"):
                return f"{clean_code}.SZ"
            return f"{clean_code}.BJ"
        if market_type == "hk_share":
            # Yahoo HK uses 4 digits for <10000 (e.g. 0700.HK, 9992.HK) or 5 digits for >=10000
            hk_int = int(clean_code)
            return f"{hk_int:04d}.HK" if hk_int < 10000 else f"{hk_int}.HK"
        if market_type == "us_share":
            return clean_code.replace(".", "-")
        return ""

    @classmethod
    def fetch_buffett_terminal_data(cls, ticker: str) -> Optional[Dict[str, Any]]:
        """Query https://buffett.lmlu.de/api/chart/{ticker} and /api/pool for valuation & 5y bars."""
        t_clean = ticker.strip().upper().replace(".", "-")
        if not re.fullmatch(r'[A-Z\-]{1,7}', t_clean):
            return None
        try:
            url = f"{BUFFETT_BASE_URL}/api/chart/{urllib.parse.quote(t_clean)}"
            data = _http_get_json(url, timeout=6)
            if not isinstance(data, dict) or not data.get("bars"):
                return None
            # Enrich quality_score & moat from /api/pool if available
            try:
                pool = _http_get_json(f"{BUFFETT_BASE_URL}/api/pool?q={urllib.parse.quote(t_clean)}", timeout=4)
                for it in (pool.get("items") or []):
                    if str(it.get("ticker", "")).upper() == t_clean:
                        summary = it.get("analysis_summary") or {}
                        if summary.get("quality_score") is not None:
                            data["quality_score"] = summary.get("quality_score")
                        break
            except Exception:
                pass
            return data
        except Exception:
            return None

    @classmethod
    def search_buffett_pool(cls, query: str) -> Optional[str]:
        q = (query or "").strip()
        if not q or re.search(r'[\u4e00-\u9fff]', q):
            return None
        try:
            pool = _http_get_json(f"{BUFFETT_BASE_URL}/api/pool?q={urllib.parse.quote(q)}", timeout=4)
            items = pool.get("items") or []
            if items:
                return str(items[0].get("ticker") or "").upper() or None
        except Exception:
            pass
        return None

    @classmethod
    def fetch_yahoo_chart(cls, yf_symbol: str, yf_range: str = "1y", yf_interval: str = "1d") -> Optional[Dict[str, Any]]:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yf_symbol)}"
            f"?range={yf_range}&interval={yf_interval}&includePrePost=false"
        )
        try:
            raw = _http_get_json(url, timeout=8)
            results = (raw.get("chart") or {}).get("result") or []
            if not results:
                return None
            r0 = results[0]
            meta = r0.get("meta") or {}
            timestamps = r0.get("timestamp") or []
            quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            vols = quote.get("volume") or []

            bars = []
            for i, ts in enumerate(timestamps):
                c_val = closes[i] if i < len(closes) else None
                if c_val is None:
                    continue
                dt_str = datetime.fromtimestamp(ts, tz=TZ_BJ).strftime("%Y-%m-%d")
                o_val = opens[i] if (i < len(opens) and opens[i] is not None) else c_val
                h_val = highs[i] if (i < len(highs) and highs[i] is not None) else c_val
                l_val = lows[i] if (i < len(lows) and lows[i] is not None) else c_val
                v_val = vols[i] if (i < len(vols) and vols[i] is not None) else 0
                bars.append({
                    "ts": ts,
                    "date": dt_str,
                    "open": float(o_val),
                    "high": float(h_val),
                    "low": float(l_val),
                    "close": float(c_val),
                    "volume": float(v_val or 0),
                })
            if len(bars) < 2:
                return None
            return {
                "symbol": meta.get("symbol", yf_symbol),
                "name": meta.get("shortName") or meta.get("longName") or yf_symbol,
                "currency": meta.get("currency") or "",
                "exchange": meta.get("exchangeName") or "",
                "regularMarketPrice": meta.get("regularMarketPrice") or bars[-1]["close"],
                "bars": bars,
                "source": "Yahoo Finance",
            }
        except Exception:
            return None

    @classmethod
    def fetch_macro_series(cls, macro_cfg: Dict[str, Any], slice_days: Optional[int]) -> Optional[Dict[str, Any]]:
        src = macro_cfg["source"]
        cutoff_dt = None
        if slice_days is not None:
            # For macro data (monthly/quarterly/annual), enforce a sensible minimum window so chart has enough points
            eff_days = max(slice_days, 365 * 2 if src == "eastmoney" else 365 * 10)
            cutoff_dt = (datetime.now(TZ_BJ) - timedelta(days=eff_days)).strftime("%Y-%m-%d")

        if src == "eastmoney":
            report = macro_cfg["report"]
            col = macro_cfg["col"]
            url = (
                f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
                f"reportName={report}&columns=REPORT_DATE,{col}&pageSize=120&sortColumns=REPORT_DATE&sortTypes=-1"
            )
            data = _http_get_json(url, timeout=8)
            rows = ((data.get("result") or {}).get("data")) or []
            bars = []
            for r in reversed(rows):
                val = r.get(col)
                rd = str(r.get("REPORT_DATE") or "")[:10]
                if val is None or not rd:
                    continue
                if cutoff_dt and rd < cutoff_dt:
                    continue
                fval = float(val)
                bars.append({"date": rd[:7], "open": fval, "high": fval, "low": fval, "close": fval, "volume": 0.0})
            if len(bars) < 2:
                return None
            return {
                "symbol": report.replace("RPT_ECONOMY_", ""),
                "name": macro_cfg["name"],
                "currency": macro_cfg.get("unit", "%"),
                "bars": bars,
                "is_macro": True,
                "ref_line": macro_cfg.get("ref_line"),
                "source": "EastMoney Macro",
            }

        if src == "worldbank":
            country = macro_cfg["country"]
            ind = macro_cfg["indicator"]
            url = f"https://api.worldbank.org/v2/country/{country}/indicator/{ind}?format=json&per_page=40"
            raw = _http_get_json(url, timeout=8)
            if not isinstance(raw, list) or len(raw) < 2 or not raw[1]:
                return None
            min_year = int(cutoff_dt[:4]) if cutoff_dt else 1995
            bars = []
            for item in reversed(raw[1]):
                val = item.get("value")
                yr = str(item.get("date") or "")
                if val is None or not yr.isdigit():
                    continue
                if int(yr) < min_year:
                    continue
                fval = float(val)
                bars.append({"date": yr, "open": fval, "high": fval, "low": fval, "close": fval, "volume": 0.0})
            if len(bars) < 2:
                return None
            return {
                "symbol": f"{country}.{ind}",
                "name": macro_cfg["name"],
                "currency": macro_cfg.get("unit", "%"),
                "bars": bars,
                "is_macro": True,
                "ref_line": macro_cfg.get("ref_line"),
                "source": "World Bank Macro",
            }
        return None

    @classmethod
    def resolve_and_fetch(cls, raw_symbol: str, period_str: str = "1y") -> Dict[str, Any]:
        q = (raw_symbol or "").strip()
        if not q:
            return {"error": "未提供股票或经济指标名称"}

        yf_range, yf_interval, slice_days, period_label = parse_period_spec(period_str)
        q_low = q.lower().strip()

        # 1. Check Macroeconomic Indicators first
        for mk in sorted(MACRO_ALIASES.keys(), key=len, reverse=True):
            if q_low == mk or (len(mk) >= 3 and mk in q_low):
                m_res = cls.fetch_macro_series(MACRO_ALIASES[mk], slice_days)
                if m_res:
                    m_res["period_label"] = period_label if slice_days and slice_days >= 730 else f"{m_res['bars'][0]['date']} ~ {m_res['bars'][-1]['date']}"
                    return m_res

        # 2. Check Global & Commodity / FX / Bond / Crypto Aliases
        yf_sym = ""
        disp_name = ""
        for ak in sorted(GLOBAL_ASSET_ALIASES.keys(), key=len, reverse=True):
            if q_low == ak or (len(ak) >= 2 and ak in q_low):
                yf_sym, disp_name = GLOBAL_ASSET_ALIASES[ak]
                break

        # 3. Direct Yahoo suffix code (e.g. 000660.KS, 7203.T, 0700.HK, 600519.SS, BTC-USD, ^TNX)
        if not yf_sym and re.fullmatch(r'[\^A-Za-z0-9]{1,8}(?:[\.\-][A-Za-z0-9]{1,4})?', q):
            if "." in q or "-" in q or q.startswith("^"):
                yf_sym = q.upper()
                disp_name = yf_sym

        # 4. StockDataEngine normalization (covers NAME_TO_CODE + A-share 6-digit + HK 5-digit + Tencent Smartbox Chinese lookup)
        tc_quote_name = ""
        clean_code, mkt_type, tc_code, kline_code, secid = "", "unknown", "", "", ""
        if not yf_sym:
            clean_code, mkt_type, tc_code, kline_code, secid = StockDataEngine.normalize_symbol(q)
            if mkt_type != "unknown" and clean_code:
                yf_sym = cls._tc_to_yahoo(tc_code, mkt_type, clean_code)
                try:
                    q_rt = StockDataEngine.fetch_realtime_quote(tc_code, mkt_type, clean_code)
                    if isinstance(q_rt, dict) and q_rt.get("name"):
                        tc_quote_name = q_rt["name"]
                except Exception:
                    pass

        # 5. Buffett Terminal Pool search fallback for English company names
        if not yf_sym:
            bt_found = cls.search_buffett_pool(q)
            if bt_found:
                yf_sym = bt_found

        if not yf_sym:
            return {"error": f"未能识别股票或经济指标‘{q}’"}

        # 6. Check Buffett Terminal (https://buffett.lmlu.de) when US equity ticker
        bt_data = None
        if re.fullmatch(r'[A-Z\-]{1,6}', yf_sym) and not yf_sym.endswith(("-USD", "=F", "=X")):
            bt_data = cls.fetch_buffett_terminal_data(yf_sym)

        bars: List[Dict[str, Any]] = []
        currency = ""
        final_name = disp_name or tc_quote_name or ""
        source_label = ""

        # Use Buffett Terminal's own 5y adjusted daily bars directly when period <= 5y
        if bt_data and bt_data.get("bars") and yf_range in ("1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y"):
            bars = bt_data["bars"]
            currency = bt_data.get("currency") or "USD"
            final_name = final_name or bt_data.get("company_name") or yf_sym
            source_label = "Buffett Terminal (buffett.lmlu.de)"
        else:
            yf_res = cls.fetch_yahoo_chart(yf_sym, yf_range=yf_range, yf_interval=yf_interval)
            cn_idx_syms = {"000001.SS", "000300.SS", "000905.SS", "000852.SS", "000688.SS", "399001.SZ", "399006.SZ", "899050.BJ"}
            if yf_res and len(yf_res.get("bars") or []) >= 2:
                bars = yf_res["bars"]
                if yf_sym in ("^TNX", "^TYX"):
                    currency = "%"
                elif yf_sym.startswith("^") or yf_sym in cn_idx_syms or yf_sym == "DX-Y.NYB":
                    currency = "点"
                else:
                    currency = yf_res.get("currency") or ""
                final_name = final_name or yf_res.get("name") or yf_sym
                source_label = "Buffett Terminal + Yahoo Finance" if bt_data else "Yahoo Finance"
            else:
                if mkt_type == "unknown":
                    q_norm_in = re.sub(r'\.(?:SS|SZ|BJ)$', '', q, flags=re.IGNORECASE)
                    clean_code, mkt_type, tc_code, kline_code, secid = StockDataEngine.normalize_symbol(q_norm_in)
                if mkt_type != "unknown" and kline_code:
                    # Fallback to Tencent / EastMoney K-lines for CN indices / A-shares / BJ-shares
                    limit_n = min(1500, max(60, int((slice_days or 365) * 0.75)))
                    kl = StockDataEngine.fetch_klines(kline_code, secid, mkt_type, limit=limit_n)
                    if kl:
                        bars = kl
                        currency = "点" if "index" in mkt_type else ("CNY" if mkt_type == "a_share" else ("HKD" if "hk" in mkt_type else "USD"))
                        final_name = final_name or disp_name or clean_code
                        source_label = "Tencent / EastMoney Quote"

        if not bars or len(bars) < 2:
            return {"error": f"暂无‘{q}’({yf_sym}) 的有效历史价格序列数据"}

        # Slice bars to exact requested calendar window if needed
        if slice_days is not None and len(bars) > 5:
            try:
                last_dt = datetime.strptime(bars[-1]["date"][:10], "%Y-%m-%d")
                cutoff_str = (last_dt - timedelta(days=slice_days)).strftime("%Y-%m-%d")
                sliced = [b for b in bars if b["date"][:10] >= cutoff_str]
                if len(sliced) >= 2:
                    bars = sliced
            except Exception:
                pass

        return {
            "symbol": yf_sym,
            "name": final_name or yf_sym,
            "currency": currency,
            "bars": bars,
            "period_label": period_label,
            "buffett": bt_data if (bt_data and bt_data.get("has_analysis")) else None,
            "is_macro": False,
            "ref_line": None,
            "source": source_label,
        }


class BuffettChartRenderer:
    """2x-supersampled Pillow renderer styled after Buffett Terminal V1.0 (https://buffett.lmlu.de/)."""

    FONT_CANDIDATES_REGULAR = [
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/winfonts/msyh.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    FONT_CANDIDATES_BOLD = [
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/winfonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]

    @classmethod
    def _load_font(cls, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        cands = cls.FONT_CANDIDATES_BOLD if bold else cls.FONT_CANDIDATES_REGULAR
        for fp in cands:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _fmt_num(val: float, currency: str = "") -> str:
        abs_v = abs(val)
        if currency == "%":
            return f"{val:.2f}%"
        prefix = "$" if currency == "USD" else ("¥" if currency in ("CNY", "RMB") else ("HK$" if currency == "HKD" else ""))
        suffix = f" {currency}" if (currency and not prefix and currency not in ("点",)) else ("%" if currency == "%" else "")
        if abs_v >= 1_000_000:
            return f"{prefix}{val:,.0f}{suffix}"
        if abs_v >= 1000:
            return f"{prefix}{val:,.1f}{suffix}" if abs_v < 10000 else f"{prefix}{val:,.0f}{suffix}"
        if abs_v >= 10:
            return f"{prefix}{val:.2f}{suffix}"
        return f"{prefix}{val:.3f}{suffix}"

    @staticmethod
    def _moving_avg(vals: List[float], win: int) -> List[Optional[float]]:
        out: List[Optional[float]] = [None] * len(vals)
        if len(vals) < win or win <= 1:
            return out
        s = sum(vals[:win])
        out[win - 1] = s / win
        for i in range(win, len(vals)):
            s += vals[i] - vals[i - win]
            out[i] = s / win
        return out

    @staticmethod
    def _compute_stats(bars: List[Dict[str, Any]], is_macro: bool = False) -> Dict[str, Any]:
        closes = [b["close"] for b in bars]
        highs = [b.get("high", b["close"]) for b in bars]
        lows = [b.get("low", b["close"]) for b in bars]
        first_c = closes[0]
        last_c = closes[-1]
        diff = last_c - first_c
        pct = (diff / abs(first_c) * 100.0) if abs(first_c) > 1e-9 else 0.0

        max_idx = max(range(len(highs)), key=lambda i: highs[i])
        min_idx = min(range(len(lows)), key=lambda i: lows[i])

        # Max drawdown
        peak = closes[0]
        max_dd = 0.0
        for c in closes:
            if c > peak:
                peak = c
            if peak > 0:
                dd = (c - peak) / peak * 100.0
                if dd < max_dd:
                    max_dd = dd

        # CAGR if span >= 300 days
        cagr = None
        try:
            d0 = datetime.strptime(bars[0]["date"][:10], "%Y-%m-%d")
            d1 = datetime.strptime(bars[-1]["date"][:10], "%Y-%m-%d")
            yrs = (d1 - d0).days / 365.25
            if yrs >= 0.95 and first_c > 0 and last_c > 0 and not is_macro:
                cagr = ((last_c / first_c) ** (1.0 / yrs) - 1.0) * 100.0
        except Exception:
            pass

        return {
            "first_close": first_c,
            "last_close": last_c,
            "diff": diff,
            "pct_change": pct,
            "high_val": highs[max_idx],
            "high_date": bars[max_idx]["date"],
            "high_idx": max_idx,
            "low_val": lows[min_idx],
            "low_date": bars[min_idx]["date"],
            "low_idx": min_idx,
            "max_drawdown_pct": max_dd,
            "cagr_pct": cagr,
            "mean_val": sum(closes) / len(closes),
        }

    @classmethod
    def render(cls, chart_data: Dict[str, Any], out_path: str) -> Dict[str, Any]:
        bars = chart_data["bars"]
        is_macro = bool(chart_data.get("is_macro"))
        bt = chart_data.get("buffett")
        currency = chart_data.get("currency") or ""
        has_vol = (not is_macro) and any(b.get("volume", 0) > 0 for b in bars)

        stats = cls._compute_stats(bars, is_macro=is_macro)

        # Downsample dense series to <= 420 plot points while preserving exact global high/low indices
        plot_bars = bars
        if len(bars) > 420:
            step = len(bars) / 400.0
            keep_indices = set([0, len(bars) - 1, stats["high_idx"], stats["low_idx"]])
            for k in range(400):
                keep_indices.add(min(len(bars) - 1, int(k * step)))
            plot_bars = [bars[i] for i in sorted(keep_indices)]

        # 2x supersampling canvas
        S = 2
        W = 1280 * S
        H = (860 if bt else 760) * S

        bg_img = Image.new("RGBA", (W, H), (6, 10, 18, 255))
        draw = ImageDraw.Draw(bg_img)

        # Fonts
        f_title = cls._load_font(28 * S, bold=True)
        f_ticker = cls._load_font(15 * S, bold=True)
        f_sub = cls._load_font(13 * S, bold=False)
        f_price = cls._load_font(32 * S, bold=True)
        f_pill = cls._load_font(15 * S, bold=True)
        f_kpi_lbl = cls._load_font(12 * S, bold=False)
        f_kpi_val = cls._load_font(18 * S, bold=True)
        f_kpi_sub = cls._load_font(11 * S, bold=False)
        f_axis = cls._load_font(12 * S, bold=False)
        f_pin = cls._load_font(12 * S, bold=True)
        f_bt_hdr = cls._load_font(13 * S, bold=True)
        f_bt_txt = cls._load_font(13 * S, bold=False)

        pad_x = 34 * S
        # Outer main card
        draw.rounded_rectangle(
            [16 * S, 14 * S, W - 16 * S, H - 14 * S],
            radius=18 * S,
            fill=(13, 20, 34, 255),
            outline=(38, 52, 75, 255),
            width=1 * S,
        )

        # 1. Header section
        title_str = str(chart_data.get("name") or chart_data.get("symbol"))
        sym_str = str(chart_data.get("symbol") or "")
        y_hdr = 34 * S
        draw.text((pad_x, y_hdr), title_str, font=f_title, fill=(248, 250, 252, 255))
        tb = draw.textbbox((pad_x, y_hdr), title_str, font=f_title)
        if sym_str and sym_str.lower() not in title_str.lower():
            bx1 = tb[2] + 12 * S
            by1 = y_hdr + 6 * S
            sb = draw.textbbox((0, 0), sym_str, font=f_ticker)
            bw = (sb[2] - sb[0]) + 18 * S
            bh = 24 * S
            draw.rounded_rectangle([bx1, by1, bx1 + bw, by1 + bh], radius=6 * S, fill=(30, 41, 59, 255), outline=(71, 85, 105, 255), width=1 * S)
            draw.text((bx1 + 9 * S, by1 + 3 * S), sym_str, font=f_ticker, fill=(252, 211, 77, 255))

        date_span = f"{bars[0]['date']} ~ {bars[-1]['date']}"
        sub_line = f"周期: {chart_data.get('period_label', '')} ({date_span})   ·   单位/币种: {currency or '-'}   ·   来源: {chart_data.get('source', '')}"
        draw.text((pad_x, y_hdr + 40 * S), sub_line, font=f_sub, fill=(148, 163, 184, 255))

        # Right side price & period change badge
        is_up = stats["diff"] >= 0
        main_accent = (16, 185, 129) if is_up else (239, 68, 68)
        pill_bg = (16, 185, 129, 38) if is_up else (239, 68, 68, 38)
        pill_fg = (110, 231, 183, 255) if is_up else (252, 165, 165, 255)

        price_str = cls._fmt_num(stats["last_close"], currency)
        if is_macro:
            sign = "+" if stats["diff"] >= 0 else ""
            chg_str = f"{sign}{stats['diff']:.2f} ({ '▲' if is_up else '▼' })"
        else:
            sign = "+" if stats["pct_change"] >= 0 else ""
            chg_str = f"{sign}{stats['pct_change']:.2f}% {'▲' if is_up else '▼'}"

        pb = draw.textbbox((0, 0), chg_str, font=f_pill)
        pw = (pb[2] - pb[0]) + 22 * S
        ph = 28 * S
        px2 = W - pad_x
        px1 = px2 - pw
        py1 = y_hdr + 6 * S

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        odraw.rounded_rectangle([px1, py1, px2, py1 + ph], radius=8 * S, fill=pill_bg, outline=main_accent + (140,), width=1 * S)
        odraw.text((px1 + 11 * S, py1 + 4 * S), chg_str, font=f_pill, fill=pill_fg)

        pr_b = draw.textbbox((0, 0), price_str, font=f_price)
        pr_w = pr_b[2] - pr_b[0]
        draw.text((px1 - pr_w - 14 * S, y_hdr - 2 * S), price_str, font=f_price, fill=(248, 250, 252, 255))

        # 2. KPI Strip (4 cards)
        kpi_y1 = 104 * S
        kpi_h = 66 * S
        kpi_gap = 14 * S
        kpi_w = (W - 2 * pad_x - 3 * kpi_gap) // 4

        if is_macro:
            kpis = [
                ("最新读数", cls._fmt_num(stats["last_close"], currency), f"期初 {cls._fmt_num(stats['first_close'], currency)}", (248, 250, 252)),
                ("区间峰值", cls._fmt_num(stats["high_val"], currency), f"出现于 {stats['high_date']}", (110, 231, 183)),
                ("区间谷值", cls._fmt_num(stats["low_val"], currency), f"出现于 {stats['low_date']}", (252, 165, 165)),
                ("区间均值", cls._fmt_num(stats["mean_val"], currency), f"样本数 {len(bars)} 期", (147, 197, 253)),
            ]
        else:
            sign = "+" if stats["pct_change"] >= 0 else ""
            c4_title = "年化复合 (CAGR)" if stats["cagr_pct"] is not None else "最大回撤"
            c4_val = (f"{'+' if stats['cagr_pct'] >= 0 else ''}{stats['cagr_pct']:.2f}%" if stats["cagr_pct"] is not None else f"{stats['max_drawdown_pct']:.2f}%")
            c4_sub = f"最大回撤 {stats['max_drawdown_pct']:.1f}%" if stats["cagr_pct"] is not None else f"样本 {len(bars)} 个交易周期"
            kpis = [
                ("区间涨跌幅", f"{sign}{stats['pct_change']:.2f}%", f"期初 {cls._fmt_num(stats['first_close'], currency)}", pill_fg[:3]),
                ("区间最高价", cls._fmt_num(stats["high_val"], currency), f"日期 {stats['high_date']}", (110, 231, 183)),
                ("区间最低价", cls._fmt_num(stats["low_val"], currency), f"日期 {stats['low_date']}", (252, 165, 165)),
                (c4_title, c4_val, c4_sub, (252, 211, 77)),
            ]

        for idx_k, (klbl, kval, ksub, kcol) in enumerate(kpis):
            kx1 = pad_x + idx_k * (kpi_w + kpi_gap)
            kx2 = kx1 + kpi_w
            odraw.rounded_rectangle([kx1, kpi_y1, kx2, kpi_y1 + kpi_h], radius=10 * S, fill=(19, 28, 46, 230), outline=(51, 65, 85, 160), width=1 * S)
            odraw.text((kx1 + 14 * S, kpi_y1 + 8 * S), klbl, font=f_kpi_lbl, fill=(148, 163, 184, 255))
            odraw.text((kx1 + 14 * S, kpi_y1 + 25 * S), kval, font=f_kpi_val, fill=kcol + (255,))
            ks_b = odraw.textbbox((0, 0), ksub, font=f_kpi_sub)
            odraw.text((kx2 - (ks_b[2] - ks_b[0]) - 12 * S, kpi_y1 + 46 * S), ksub, font=f_kpi_sub, fill=(100, 116, 139, 255))

        # 3. Plot Geometry
        plot_left = pad_x + 76 * S
        plot_right = W - pad_x - 18 * S
        plot_top = kpi_y1 + kpi_h + 20 * S
        footer_top = H - (118 * S if bt else 44 * S)
        avail_h = footer_top - plot_top - 32 * S

        if has_vol:
            price_h = int(avail_h * 0.76)
            vol_top = plot_top + price_h + 14 * S
            vol_bot = plot_top + avail_h
        else:
            price_h = avail_h
            vol_top = vol_bot = plot_top + price_h
        price_bot = plot_top + price_h

        # Plot background panel
        odraw.rounded_rectangle([plot_left, plot_top, plot_right, price_bot], radius=8 * S, fill=(8, 13, 24, 210), outline=(38, 52, 75, 190), width=1 * S)
        if has_vol:
            odraw.rounded_rectangle([plot_left, vol_top, plot_right, vol_bot], radius=6 * S, fill=(8, 13, 24, 210), outline=(38, 52, 75, 190), width=1 * S)

        # Compute Y range (incorporating Buffett Buy Zone if reasonably close)
        p_closes = [b["close"] for b in plot_bars]
        p_highs = [b.get("high", b["close"]) for b in plot_bars]
        p_lows = [b.get("low", b["close"]) for b in plot_bars]
        y_min = min(min(p_lows), min(p_closes))
        y_max = max(max(p_highs), max(p_closes))

        bz_low = bt.get("buy_zone_lower") if bt else None
        bz_high = bt.get("buy_zone_upper") if bt else None
        bz_same_ccy = bool(bt and (not bt.get("buy_zone_currency") or bt.get("buy_zone_currency") == currency))
        draw_bz = False
        if bz_same_ccy and bz_low is not None and bz_high is not None and bz_high > 0:
            # Include Buy Zone upper in Y range if within 55% of min price so viewer sees distance to Buy Zone
            if bz_high >= y_min * 0.45 and bz_low <= y_max * 1.6:
                y_min = min(y_min, bz_high * 0.92)
                if bz_low >= y_min * 0.7:
                    y_min = min(y_min, bz_low * 0.95)
                y_max = max(y_max, min(bz_high * 1.05, y_max * 1.25))
                draw_bz = True

        ref_line = chart_data.get("ref_line")
        if ref_line is not None:
            y_min = min(y_min, float(ref_line))
            y_max = max(y_max, float(ref_line))

        span = y_max - y_min
        if span <= 1e-9:
            span = max(abs(y_max) * 0.1, 1.0)
        raw_min_floor = min(min(p_lows), min(p_closes))
        y_min -= span * 0.08
        if not is_macro and raw_min_floor >= 0:
            y_min = max(0.0, y_min)
        y_max += span * 0.11
        y_span = y_max - y_min

        def val_to_y(v: float) -> int:
            ratio = (v - y_min) / y_span
            return int(price_bot - ratio * (price_bot - plot_top))

        n_pts = len(plot_bars)
        pw_inner = plot_right - plot_left - 16 * S

        def idx_to_x(i: int) -> int:
            if n_pts <= 1:
                return plot_left + pw_inner // 2
            return int(plot_left + 8 * S + (i / (n_pts - 1)) * pw_inner)

        # Horizontal grid lines & Y-axis labels
        grid_steps = 5
        for g in range(grid_steps + 1):
            gv = y_min + (g / grid_steps) * y_span
            gy = val_to_y(gv)
            if plot_top + 4 * S < gy < price_bot - 4 * S:
                odraw.line([(plot_left, gy), (plot_right, gy)], fill=(51, 65, 85, 95), width=1 * S)
            glbl = cls._fmt_num(gv, "%" if currency == "%" else "")
            gb = odraw.textbbox((0, 0), glbl, font=f_axis)
            odraw.text((plot_left - (gb[2] - gb[0]) - 10 * S, gy - 8 * S), glbl, font=f_axis, fill=(148, 163, 184, 230))

        # Vertical grid lines & X-axis date ticks
        tick_count = min(6, n_pts)
        y_xtick = (vol_bot if has_vol else price_bot) + 6 * S
        for t_i in range(tick_count):
            b_idx = int(round(t_i * (n_pts - 1) / max(1, tick_count - 1)))
            tx = idx_to_x(b_idx)
            if 0 < t_i < tick_count - 1:
                odraw.line([(tx, plot_top), (tx, price_bot)], fill=(51, 65, 85, 65), width=1 * S)
            dlbl = plot_bars[b_idx]["date"][:10]
            if len(dlbl) == 10 and n_pts > 90:
                dlbl = dlbl[:7]
            db = odraw.textbbox((0, 0), dlbl, font=f_axis)
            dw = db[2] - db[0]
            dx = max(plot_left, min(plot_right - dw, tx - dw // 2))
            odraw.text((dx, y_xtick), dlbl, font=f_axis, fill=(148, 163, 184, 230))

        # Buffett Buy Zone Band (if applicable)
        if draw_bz and bz_low is not None and bz_high is not None:
            by_top = max(plot_top + 2 * S, min(price_bot - 2 * S, val_to_y(bz_high)))
            by_bot = max(plot_top + 2 * S, min(price_bot - 2 * S, val_to_y(max(bz_low, y_min))))
            if by_bot > by_top + 3 * S:
                odraw.rectangle([plot_left + 2 * S, by_top, plot_right - 2 * S, by_bot], fill=(16, 185, 129, 34))
            odraw.line([(plot_left + 2 * S, by_top), (plot_right - 2 * S, by_top)], fill=(16, 185, 129, 195), width=2 * S)
            bz_txt = f"Buffett 安全击球上限 {cls._fmt_num(bz_high, currency)}"
            bz_b = odraw.textbbox((0, 0), bz_txt, font=f_pin)
            bz_x = plot_right - (bz_b[2] - bz_b[0]) - 14 * S
            odraw.text((bz_x, max(plot_top + 6 * S, by_top - 20 * S)), bz_txt, font=f_pin, fill=(110, 231, 183, 240))

        # Macro Reference Line (e.g., 0% CPI or 50.0 PMI boom-bust line)
        if ref_line is not None:
            ry = val_to_y(float(ref_line))
            if plot_top < ry < price_bot:
                for sx in range(plot_left + 4 * S, plot_right - 8 * S, 14 * S):
                    odraw.line([(sx, ry), (min(plot_right - 4 * S, sx + 8 * S), ry)], fill=(245, 158, 11, 185), width=2 * S)

        # Gradient fill under price curve
        pts = [(idx_to_x(i), val_to_y(b["close"])) for i, b in enumerate(plot_bars)]
        line_rgb = (59, 130, 246) if is_macro else main_accent

        if len(pts) >= 2:
            grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(grad)
            poly = [(pts[0][0], price_bot - 1 * S)] + pts + [(pts[-1][0], price_bot - 1 * S)]
            gdraw.polygon(poly, fill=line_rgb + (255,))

            # Vertical alpha ramp mask
            alpha_col = Image.new("L", (1, H), 0)
            apx = alpha_col.load()
            top_y = min(p[1] for p in pts)
            denom = max(1, price_bot - top_y)
            for yy in range(top_y, price_bot):
                t = 1.0 - (yy - top_y) / denom
                apx[0, yy] = int(78 * (t ** 1.45))
            alpha_mask = alpha_col.resize((W, H))
            r_ch, g_ch, b_ch, a_ch = grad.split()
            from PIL import ImageChops
            grad.putalpha(ImageChops.multiply(a_ch, alpha_mask))
            bg_img = Image.alpha_composite(bg_img, overlay)
            bg_img = Image.alpha_composite(bg_img, grad)
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)

        # Moving averages for non-macro series with sufficient bars
        legend_items = [("指标读数" if is_macro else "收盘价", line_rgb)]
        if is_macro and ref_line is not None:
            legend_items.append((f"参考基准 ({cls._fmt_num(float(ref_line), currency)})", (245, 158, 11)))
        if not is_macro and len(plot_bars) >= 25:
            ma_short_w = 20
            ma_long_w = 60 if len(plot_bars) >= 65 else 0
            ma_s = cls._moving_avg(p_closes, ma_short_w)
            s_pts = [(idx_to_x(i), val_to_y(v)) for i, v in enumerate(ma_s) if v is not None]
            if len(s_pts) >= 2:
                odraw.line(s_pts, fill=(245, 158, 11, 215), width=2 * S, joint="curve")
                legend_items.append((f"MA{ma_short_w}", (245, 158, 11)))
            if ma_long_w:
                ma_l = cls._moving_avg(p_closes, ma_long_w)
                l_pts = [(idx_to_x(i), val_to_y(v)) for i, v in enumerate(ma_l) if v is not None]
                if len(l_pts) >= 2:
                    odraw.line(l_pts, fill=(96, 165, 250, 210), width=2 * S, joint="curve")
                    legend_items.append((f"MA{ma_long_w}", (96, 165, 250)))

        # Main price polyline
        if len(pts) >= 2:
            odraw.line(pts, fill=line_rgb + (255,), width=3 * S, joint="curve")
        if is_macro and len(pts) <= 60:
            for px_i, py_i in pts:
                odraw.ellipse([px_i - 3 * S, py_i - 3 * S, px_i + 3 * S, py_i + 3 * S], fill=line_rgb + (255,), outline=(15, 23, 42, 255), width=1 * S)

        # Latest price horizontal dotted guide + end dot
        lx, ly = pts[-1]
        odraw.ellipse([lx - 5 * S, ly - 5 * S, lx + 5 * S, ly + 5 * S], fill=line_rgb + (255,), outline=(248, 250, 252, 255), width=2 * S)

        # Plot Legend (top-left inside plot)
        leg_x = plot_left + 14 * S
        leg_y = plot_top + 10 * S
        for lname, lrgb in legend_items:
            odraw.rounded_rectangle([leg_x, leg_y + 5 * S, leg_x + 14 * S, leg_y + 9 * S], radius=2 * S, fill=lrgb + (255,))
            odraw.text((leg_x + 19 * S, leg_y), lname, font=f_kpi_lbl, fill=(203, 213, 225, 235))
            lb = odraw.textbbox((0, 0), lname, font=f_kpi_lbl)
            leg_x += 19 * S + (lb[2] - lb[0]) + 18 * S

        # High / Low callout pins inside plot
        p_max_i = max(range(len(plot_bars)), key=lambda i: plot_bars[i].get("high", plot_bars[i]["close"]))
        p_min_i = min(range(len(plot_bars)), key=lambda i: plot_bars[i].get("low", plot_bars[i]["close"]))
        for is_hi, idx_pin, pin_val, pin_col in [
            (True, p_max_i, stats["high_val"], (110, 231, 183, 255)),
            (False, p_min_i, stats["low_val"], (252, 165, 165, 255)),
        ]:
            cx = idx_to_x(idx_pin)
            cy = val_to_y(plot_bars[idx_pin]["close"])
            lbl = f"{'最高' if is_hi else '最低'} {cls._fmt_num(pin_val, '%' if currency == '%' else '')}"
            bb = odraw.textbbox((0, 0), lbl, font=f_pin)
            lw = (bb[2] - bb[0]) + 12 * S
            lh = 20 * S
            tx = max(plot_left + 6 * S, min(plot_right - lw - 6 * S, cx - lw // 2))
            ty = (cy - lh - 8 * S) if is_hi else (cy + 8 * S)
            ty = max(plot_top + 6 * S, min(price_bot - lh - 6 * S, ty))
            if tx < leg_x + 6 * S and ty < leg_y + 24 * S:
                ty = min(price_bot - lh - 6 * S, max(cy + 10 * S, leg_y + 26 * S))
            odraw.ellipse([cx - 4 * S, cy - 4 * S, cx + 4 * S, cy + 4 * S], fill=pin_col)
            odraw.rounded_rectangle([tx, ty, tx + lw, ty + lh], radius=5 * S, fill=(15, 23, 42, 225), outline=pin_col[:3] + (140,), width=1 * S)
            odraw.text((tx + 6 * S, ty + 2 * S), lbl, font=f_pin, fill=pin_col)

        # 4. Volume Sub-panel
        if has_vol:
            vols = [b.get("volume", 0.0) for b in plot_bars]
            max_v = max(vols) or 1.0
            bar_w = max(1 * S, int(pw_inner / max(n_pts * 1.45, 1)))
            for i, b in enumerate(plot_bars):
                v = b.get("volume", 0.0)
                if v <= 0:
                    continue
                vx = idx_to_x(i)
                vh = int((v / max_v) * (vol_bot - vol_top - 10 * S))
                vy1 = vol_bot - 2 * S - max(2 * S, vh)
                up_bar = b["close"] >= b.get("open", b["close"])
                vcol = (16, 185, 129, 155) if up_bar else (239, 68, 68, 155)
                odraw.rectangle([vx - bar_w // 2, vy1, vx + max(1, bar_w // 2), vol_bot - 2 * S], fill=vcol)
            odraw.text((plot_left + 10 * S, vol_top + 4 * S), "成交量 (Volume)", font=f_kpi_sub, fill=(148, 163, 184, 210))

        # 5. Buffett Terminal Footer Card or Attribution Strip
        if bt:
            fy1 = footer_top + 6 * S
            fy2 = H - 24 * S
            odraw.rounded_rectangle([pad_x, fy1, W - pad_x, fy2], radius=10 * S, fill=(19, 28, 46, 240), outline=(245, 158, 11, 130), width=1 * S)
            dec = bt.get("investment_decision") or "Tracked"
            q_score = bt.get("quality_score")
            q_str = f"  ·  质量分: {q_score}/100" if q_score is not None else ""
            bz_str = ""
            if bz_low is not None and bz_high is not None:
                bz_ccy = bt.get("buy_zone_currency") or currency
                bz_str = f"  ·  安全边际击球区: {cls._fmt_num(bz_low, bz_ccy)} – {cls._fmt_num(bz_high, bz_ccy)}"
            margin_pct = bt.get("margin_to_upper_pct")
            m_str = f" ({margin_pct:+.1f}%)" if isinstance(margin_pct, (int, float)) else ""
            hdr_line = f"◆ BUFFETT TERMINAL (buffett.lmlu.de)   |   评级: {dec}{q_str}{bz_str}{m_str}"
            odraw.text((pad_x + 14 * S, fy1 + 10 * S), hdr_line, font=f_bt_hdr, fill=(252, 211, 77, 255))

            summary = str(bt.get("conclusion_summary") or "").replace("\n", " ").strip()
            if len(summary) > 78:
                summary = summary[:77] + "…"
            odraw.text((pad_x + 14 * S, fy1 + 38 * S), f"核心结论：{summary}", font=f_bt_txt, fill=(219, 228, 240, 245))
        else:
            wm = "Buffett Terminal Visual Engine · https://buffett.lmlu.de  |  Real-Data Script Render (Zero-AI)"
            wb = odraw.textbbox((0, 0), wm, font=f_kpi_sub)
            odraw.text((W - pad_x - (wb[2] - wb[0]), H - 34 * S), wm, font=f_kpi_sub, fill=(100, 116, 139, 220))

        final_rgba = Image.alpha_composite(bg_img, overlay)
        resample_mode = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        final_rgb = final_rgba.convert("RGB").resize((W // S, H // S), resample=resample_mode)
        final_rgb.save(out_path, "PNG", optimize=True)
        return stats

    COMPARISON_PALETTE: List[Tuple[int, int, int]] = [
        (56, 189, 248),   # #38BDF8 Sky Cyan
        (245, 158, 11),   # #F59E0B Amber Gold
        (16, 185, 129),   # #10B981 Emerald Green
        (244, 63, 94),    # #F43F5E Rose Coral
        (168, 85, 247),   # #A855F7 Vivid Violet
    ]

    @staticmethod
    def _parse_bar_ordinal(date_raw: str) -> Optional[Tuple[int, str]]:
        s = (date_raw or "").strip()[:10]
        if not s:
            return None
        try:
            if len(s) == 10 and s[4] == "-" and s[7] == "-":
                dt = datetime.strptime(s, "%Y-%m-%d")
            elif len(s) == 7 and s[4] == "-":
                dt = datetime.strptime(s + "-01", "%Y-%m-%d")
            elif len(s) == 4 and s.isdigit():
                dt = datetime.strptime(s + "-01-01", "%Y-%m-%d")
            else:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return dt.toordinal(), dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    @classmethod
    def render_comparison(
        cls,
        series_list: List[Dict[str, Any]],
        period_label: str,
        out_path: str,
    ) -> Dict[str, Any]:
        """
        Render 2~5 assets on a single aligned canvas:
        - Common start date t_0 anchored at 0.00% baseline so prices of 1 and 1000 align cleanly.
        - N == 2 (positive prices): Proportional-locked Dual Y-Axis (Left = Asset 1 real price, Right = Asset 2 real price).
        - N >= 3 (or rate diff): Unified cumulative % Y-Axis + collision-free right callout pills with real prices.
        """
        S = 2
        W, H = 1280 * S, 860 * S

        prepped: List[Dict[str, Any]] = []
        for idx, item in enumerate(series_list[:5]):
            by_ord: Dict[int, Tuple[str, float]] = {}
            for b in (item.get("bars") or []):
                parsed = cls._parse_bar_ordinal(str(b.get("date") or ""))
                c_val = b.get("close")
                if parsed is None or c_val is None:
                    continue
                fv = float(c_val)
                if math.isnan(fv) or math.isinf(fv):
                    continue
                by_ord[parsed[0]] = (parsed[1], fv)
            pts = [(k, by_ord[k][0], by_ord[k][1]) for k in sorted(by_ord.keys())]
            if len(pts) < 2:
                continue
            raw_name = str(item.get("name") or item.get("symbol") or f"Asset{idx+1}")
            short_name = re.sub(r'\s*\(.*?\)\s*', '', raw_name).strip() or raw_name
            if len(short_name) > 10:
                short_name = short_name[:10]
            prepped.append({
                "raw": item,
                "symbol": str(item.get("symbol") or ""),
                "name": raw_name,
                "short_name": short_name,
                "currency": str(item.get("currency") or ""),
                "color": cls.COMPARISON_PALETTE[idx % len(cls.COMPARISON_PALETTE)],
                "pts": pts,
            })

        if len(prepped) < 2:
            raise ValueError("多标的同图对比至少需要 2 组有效历史序列数据")

        # Determine common overlap start anchor t_0
        max_first_ord = max(s["pts"][0][0] for s in prepped)
        min_last_ord = min(s["pts"][-1][0] for s in prepped)
        anchor_ord = max_first_ord if max_first_ord < min_last_ord - 1 else min(s["pts"][0][0] for s in prepped)
        anchor_date_str = datetime.fromordinal(anchor_ord).strftime("%Y-%m-%d")

        for s in prepped:
            pts = s["pts"]
            le_pts = [p for p in pts if p[0] <= anchor_ord]
            ge_pts = [p for p in pts if p[0] >= anchor_ord]
            if le_pts and (anchor_ord - le_pts[-1][0] <= 45):
                base_val = le_pts[-1][2]
            elif ge_pts:
                base_val = ge_pts[0][2]
            else:
                base_val = pts[0][2]

            active = list(ge_pts) if ge_pts else [pts[-1]]
            if active[0][0] > anchor_ord:
                active.insert(0, (anchor_ord, anchor_date_str, base_val))

            # Downsample very dense series while preserving start, end, peak, and trough
            if len(active) > 380:
                max_i = max(range(len(active)), key=lambda j: active[j][2])
                min_i = min(range(len(active)), key=lambda j: active[j][2])
                must_keep = {0, len(active) - 1, max_i, min_i}
                step = len(active) / 360.0
                picked = sorted(must_keep | {min(len(active) - 1, int(k * step)) for k in range(360)})
                active = [active[j] for j in picked]

            peak_v = active[0][2]
            max_dd = 0.0
            for _, _, cv in active:
                if cv > peak_v:
                    peak_v = cv
                if peak_v > 0:
                    dd = (cv - peak_v) / peak_v * 100.0
                    if dd < max_dd:
                        max_dd = dd

            s["base_val"] = base_val
            s["last_val"] = active[-1][2]
            s["last_date"] = active[-1][1]
            s["high_val"] = max(p[2] for p in active)
            s["low_val"] = min(p[2] for p in active)
            s["max_dd_pct"] = max_dd
            s["active"] = active

        use_pct_rebase = (
            all(s["base_val"] > 1e-9 and min(p[2] for p in s["active"]) > 0 for s in prepped)
            and not any(s["raw"].get("is_macro") and s["currency"] == "%" for s in prepped)
        )

        for s in prepped:
            b0 = s["base_val"]
            rel_pts: List[Tuple[int, str, float, float]] = []
            for d_ord, d_str, cv in s["active"]:
                rv = ((cv - b0) / b0 * 100.0) if use_pct_rebase else (cv - b0)
                rel_pts.append((d_ord, d_str, cv, rv))
            s["rel_pts"] = rel_pts
            s["ret_val"] = rel_pts[-1][3]

        is_dual_axis = (len(prepped) == 2 and use_pct_rebase)
        alignment_mode = (
            "proportional_dual_axis"
            if is_dual_axis
            else ("rebased_pct_axis" if use_pct_rebase else "rebased_diff_axis")
        )

        # Canvas & Fonts
        bg_img = Image.new("RGBA", (W, H), (6, 10, 18, 255))
        draw = ImageDraw.Draw(bg_img)
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)

        f_title = cls._load_font(26 * S, bold=True)
        f_badge = cls._load_font(13 * S, bold=True)
        f_sub = cls._load_font(13 * S, bold=False)
        f_card_name = cls._load_font((14 if len(prepped) <= 3 else 12) * S, bold=True)
        f_card_val = cls._load_font((20 if len(prepped) <= 3 else 17) * S, bold=True)
        f_card_sub = cls._load_font((11 if len(prepped) <= 3 else 10) * S, bold=False)
        f_axis = cls._load_font(12 * S, bold=False)
        f_axis_hdr = cls._load_font(12 * S, bold=True)
        f_pin = cls._load_font(12 * S, bold=True)

        pad_x = 34 * S
        draw.rounded_rectangle(
            [16 * S, 14 * S, W - 16 * S, H - 14 * S],
            radius=18 * S,
            fill=(13, 20, 34, 255),
            outline=(38, 52, 75, 255),
            width=1 * S,
        )

        # 1. Header
        y_hdr = 32 * S
        joined_names = " vs ".join(s["short_name"] for s in prepped)
        if len(joined_names) > 34:
            joined_names = joined_names[:33] + "…"
        draw.text((pad_x, y_hdr), joined_names, font=f_title, fill=(248, 250, 252, 255))

        mode_txt = "双轴等比例对标 · 起点 0% 锚定" if is_dual_axis else ("多标的同图对标 · 起点 0% 归一化" if use_pct_rebase else "多标的同图变动对标")
        mb = draw.textbbox((0, 0), mode_txt, font=f_badge)
        mw = (mb[2] - mb[0]) + 22 * S
        mh = 28 * S
        mx1 = W - pad_x - mw
        my1 = y_hdr + 2 * S
        odraw.rounded_rectangle([mx1, my1, W - pad_x, my1 + mh], radius=8 * S, fill=(30, 41, 59, 240), outline=(245, 158, 11, 155), width=1 * S)
        odraw.text((mx1 + 11 * S, my1 + 5 * S), mode_txt, font=f_badge, fill=(252, 211, 77, 255))

        end_date_str = max(s["last_date"] for s in prepped)
        ranked = sorted(prepped, key=lambda x: x["ret_val"], reverse=True)
        leader = ranked[0]
        laggard = ranked[-1]
        spread_val = leader["ret_val"] - laggard["ret_val"]
        unit_sym = "%" if use_pct_rebase else " pp"
        sub_line = (
            f"周期: {period_label} (共同起点: {anchor_date_str} ~ {end_date_str})   ·   "
            f"领跑: {leader['short_name']} ({leader['ret_val']:+.2f}{unit_sym})   ·   "
            f"强弱剪刀差: {spread_val:.2f}{unit_sym}"
        )
        draw.text((pad_x, y_hdr + 39 * S), sub_line, font=f_sub, fill=(148, 163, 184, 255))

        # 2. Multi-Asset Comparison KPI Cards
        n_ser = len(prepped)
        kpi_y1 = 102 * S
        kpi_h = 74 * S
        kpi_gap = (12 if n_ser <= 3 else 8) * S
        kpi_w = (W - 2 * pad_x - (n_ser - 1) * kpi_gap) // n_ser

        for i, s in enumerate(prepped):
            kx1 = pad_x + i * (kpi_w + kpi_gap)
            kx2 = kx1 + kpi_w
            col = s["color"]
            odraw.rounded_rectangle([kx1, kpi_y1, kx2, kpi_y1 + kpi_h], radius=10 * S, fill=(19, 28, 46, 232), outline=col + (135,), width=1 * S)
            # Left color bar
            odraw.rounded_rectangle([kx1 + 2 * S, kpi_y1 + 8 * S, kx1 + 7 * S, kpi_y1 + kpi_h - 8 * S], radius=2 * S, fill=col + (255,))

            axis_tag = (" [左轴]" if i == 0 else " [右轴]") if is_dual_axis else ""
            sym_tag = f" ({s['symbol']})" if (n_ser <= 3 and s["symbol"] and s["symbol"].lower() not in s["short_name"].lower()) else ""
            hdr_lbl = f"{s['short_name']}{sym_tag}{axis_tag}"
            max_chars = 26 if n_ser == 2 else (18 if n_ser == 3 else (13 if n_ser == 4 else 10))
            if len(hdr_lbl) > max_chars:
                hdr_lbl = hdr_lbl[:max_chars - 1] + "…"
            odraw.text((kx1 + 14 * S, kpi_y1 + 8 * S), hdr_lbl, font=f_card_name, fill=col + (255,))

            rv = s["ret_val"]
            up_c = (110, 231, 183, 255) if rv >= 0 else (252, 165, 165, 255)
            rv_str = f"{rv:+.2f}%" if use_pct_rebase else f"{rv:+.2f}"
            odraw.text((kx1 + 14 * S, kpi_y1 + 27 * S), rv_str, font=f_card_val, fill=up_c)

            last_s = cls._fmt_num(s["last_val"], s["currency"])
            first_s = cls._fmt_num(s["base_val"], s["currency"])
            if n_ser <= 2:
                sub_s = f"现价 {last_s}  (期初 {first_s} · 最大回撤 {s['max_dd_pct']:.1f}%)"
            elif n_ser == 3:
                sub_s = f"现价 {last_s} (期初 {first_s})"
            else:
                sub_s = f"现 {last_s} / 初 {first_s}"
            odraw.text((kx1 + 14 * S, kpi_y1 + 53 * S), sub_s, font=f_card_sub, fill=(148, 163, 184, 240))

        # 3. Plot Geometry
        plot_left = pad_x + 88 * S
        plot_right = (W - pad_x - 94 * S) if is_dual_axis else (W - pad_x - 166 * S)
        plot_top = kpi_y1 + kpi_h + 28 * S
        footer_top = H - 46 * S
        price_bot = footer_top - 28 * S

        odraw.rounded_rectangle([plot_left, plot_top, plot_right, price_bot], radius=8 * S, fill=(8, 13, 24, 215), outline=(38, 52, 75, 195), width=1 * S)

        # Axis Unit Headers above plot (avoid ◀/▶ glyphs which render as boxes in some CJK fonts)
        if is_dual_axis:
            c0, c1 = prepped[0]["color"], prepped[1]["color"]
            lh_txt = f"左轴刻度: {prepped[0]['short_name']} ({prepped[0]['currency'] or '价格'})"
            rh_txt = f"右轴刻度: {prepped[1]['short_name']} ({prepped[1]['currency'] or '价格'})"
            odraw.ellipse([plot_left, plot_top - 17 * S, plot_left + 10 * S, plot_top - 7 * S], fill=c0 + (255,))
            odraw.text((plot_left + 15 * S, plot_top - 21 * S), lh_txt, font=f_axis_hdr, fill=c0 + (245,))
            rb = odraw.textbbox((0, 0), rh_txt, font=f_axis_hdr)
            rx_txt = plot_right - (rb[2] - rb[0]) - 15 * S
            odraw.text((rx_txt, plot_top - 21 * S), rh_txt, font=f_axis_hdr, fill=c1 + (245,))
            odraw.ellipse([plot_right - 10 * S, plot_top - 17 * S, plot_right, plot_top - 7 * S], fill=c1 + (255,))
        else:
            lh_txt = "▲ 区间累计涨跌幅 (共同起点 = 0.00%)" if use_pct_rebase else "▲ 相对期初变动值 (起点 = 0.00)"
            odraw.text((plot_left, plot_top - 21 * S), lh_txt, font=f_axis_hdr, fill=(148, 163, 184, 235))

        # Y-range envelope (always contains 0.0 baseline; clamp > -100% for positive prices)
        raw_lo = min(0.0, min(min(p[3] for p in s["rel_pts"]) for s in prepped))
        raw_hi = max(0.0, max(max(p[3] for p in s["rel_pts"]) for s in prepped))
        r_span_raw = max(2.0, raw_hi - raw_lo)
        bottom_pad = min(r_span_raw * 0.09, 18.0) if (use_pct_rebase and raw_lo >= -5.0) else (r_span_raw * 0.10)
        r_min = raw_lo - bottom_pad
        if use_pct_rebase:
            r_min = max(-96.0, r_min)
        r_max = raw_hi + r_span_raw * 0.12
        r_span = max(1.0, r_max - r_min)

        def val_to_y(rv: float) -> int:
            ratio = (rv - r_min) / r_span
            return int(price_bot - ratio * (price_bot - plot_top))

        min_ord = anchor_ord
        max_ord = max(s["rel_pts"][-1][0] for s in prepped)
        ord_span = max(1, max_ord - min_ord)
        right_inner_pad = (86 * S) if is_dual_axis else (14 * S)
        pw_inner = plot_right - plot_left - 12 * S - right_inner_pad

        def ord_to_x(d_ord: int) -> int:
            return int(plot_left + 12 * S + ((d_ord - min_ord) / ord_span) * pw_inner)

        # Horizontal Grid & Proportional-Locked Y-Axis Labels
        grid_steps = 5
        for g in range(grid_steps + 1):
            rg = r_min + (g / grid_steps) * r_span
            gy = val_to_y(rg)
            if plot_top + 4 * S < gy < price_bot - 4 * S:
                odraw.line([(plot_left, gy), (plot_right, gy)], fill=(51, 65, 85, 88), width=1 * S)
            if is_dual_axis:
                p0_v = max(0.0, prepped[0]["base_val"] * (1.0 + rg / 100.0))
                p1_v = max(0.0, prepped[1]["base_val"] * (1.0 + rg / 100.0))
                l_lbl = cls._fmt_num(p0_v, prepped[0]["currency"])
                r_lbl = cls._fmt_num(p1_v, prepped[1]["currency"])
                lb = odraw.textbbox((0, 0), l_lbl, font=f_axis)
                odraw.text((plot_left - (lb[2] - lb[0]) - 10 * S, gy - 8 * S), l_lbl, font=f_axis, fill=(125, 211, 252, 235))
                odraw.text((plot_right + 10 * S, gy - 8 * S), r_lbl, font=f_axis, fill=(252, 211, 77, 235))
            else:
                l_lbl = f"{rg:+.1f}%" if use_pct_rebase else f"{rg:+.2f}"
                lb = odraw.textbbox((0, 0), l_lbl, font=f_axis)
                odraw.text((plot_left - (lb[2] - lb[0]) - 10 * S, gy - 8 * S), l_lbl, font=f_axis, fill=(186, 200, 218, 230))

        # X-Axis Calendar Ticks
        tick_count = 6
        y_xtick = price_bot + 7 * S
        for t_i in range(tick_count):
            t_ord = int(round(min_ord + (t_i / (tick_count - 1)) * ord_span))
            tx = ord_to_x(t_ord)
            if 0 < t_i < tick_count - 1:
                odraw.line([(tx, plot_top), (tx, price_bot)], fill=(51, 65, 85, 60), width=1 * S)
            dlbl = datetime.fromordinal(t_ord).strftime("%Y-%m-%d" if ord_span <= 120 else "%Y-%m")
            db = odraw.textbbox((0, 0), dlbl, font=f_axis)
            dw = db[2] - db[0]
            dx = max(plot_left, min(plot_right - dw, tx - dw // 2))
            odraw.text((dx, y_xtick), dlbl, font=f_axis, fill=(148, 163, 184, 230))

        # 0.00% Common Origin Baseline (line first; pill badge rendered on top of curves below)
        y_zero = val_to_y(0.0)
        if plot_top + 4 * S <= y_zero <= price_bot - 4 * S:
            for sx in range(plot_left + 4 * S, plot_right - 8 * S, 14 * S):
                odraw.line([(sx, y_zero), (min(plot_right - 4 * S, sx + 8 * S), y_zero)], fill=(226, 232, 240, 175), width=2 * S)

        # Draw All Series Curves
        end_markers: List[Dict[str, Any]] = []
        for s in prepped:
            col = s["color"]
            pts_xy = [(ord_to_x(p[0]), val_to_y(p[3])) for p in s["rel_pts"]]
            if len(pts_xy) >= 2:
                odraw.line(pts_xy, fill=col + (46,), width=7 * S, joint="curve")
                odraw.line(pts_xy, fill=col + (250,), width=3 * S, joint="curve")
            sx0, sy0 = pts_xy[0]
            ex, ey = pts_xy[-1]
            odraw.ellipse([sx0 - 4 * S, sy0 - 4 * S, sx0 + 4 * S, sy0 + 4 * S], fill=(248, 250, 252, 240), outline=col + (255,), width=2 * S)
            odraw.ellipse([ex - 5 * S, ey - 5 * S, ex + 5 * S, ey + 5 * S], fill=col + (255,), outline=(248, 250, 252, 255), width=2 * S)
            end_markers.append({"s": s, "ex": ex, "ey": ey, "adj_y": float(ey)})

        # Render 0% origin tag in a crisp pill so curves never strike through text
        if plot_top + 12 * S <= y_zero <= price_bot - 12 * S:
            z_lbl = "0% 起点"
            zb = odraw.textbbox((0, 0), z_lbl, font=f_card_sub)
            zw = (zb[2] - zb[0]) + 10 * S
            zh = 17 * S
            zx = plot_left + 16 * S
            zy = max(plot_top + 6 * S, y_zero - zh - 5 * S)
            odraw.rounded_rectangle([zx, zy, zx + zw, zy + zh], radius=4 * S, fill=(15, 23, 42, 225), outline=(148, 163, 184, 140), width=1 * S)
            odraw.text((zx + 5 * S, zy + 1 * S), z_lbl, font=f_card_sub, fill=(226, 232, 240, 235))

        # 1D Repulsion Anti-Overlap for Endpoint Callout Pills
        end_markers.sort(key=lambda m: m["adj_y"])
        min_gap = 28 * S
        y_lo = plot_top + 16 * S
        y_hi = price_bot - 16 * S
        for k in range(len(end_markers)):
            if k == 0:
                end_markers[k]["adj_y"] = max(float(y_lo), end_markers[k]["adj_y"])
            else:
                end_markers[k]["adj_y"] = max(end_markers[k]["adj_y"], end_markers[k - 1]["adj_y"] + min_gap)
        if end_markers[-1]["adj_y"] > y_hi:
            end_markers[-1]["adj_y"] = float(y_hi)
            for k in range(len(end_markers) - 2, -1, -1):
                end_markers[k]["adj_y"] = min(end_markers[k]["adj_y"], end_markers[k + 1]["adj_y"] - min_gap)

        for m in end_markers:
            s = m["s"]
            col = s["color"]
            ex, ey = m["ex"], m["ey"]
            ay = int(round(m["adj_y"]))
            rv = s["ret_val"]
            rv_txt = f"{rv:+.2f}%" if use_pct_rebase else f"{rv:+.2f}"
            if is_dual_axis:
                # Dedicated right-inner lane [ex + 8*S, plot_right - 5*S]: never overlaps curves or right axis ticks
                pill_txt = rv_txt
                tb = odraw.textbbox((0, 0), pill_txt, font=f_pin)
                pw_p = (tb[2] - tb[0]) + 12 * S
                ph_p = 22 * S
                px1 = min(plot_right - pw_p - 5 * S, ex + 9 * S)
                py1 = ay - ph_p // 2
                odraw.line([(ex + 5 * S, ey), (px1, ay)], fill=col + (180,), width=1 * S)
                odraw.rounded_rectangle([px1, py1, px1 + pw_p, py1 + ph_p], radius=6 * S, fill=(15, 23, 42, 238), outline=col + (210,), width=1 * S)
                odraw.text((px1 + 6 * S, py1 + 2 * S), pill_txt, font=f_pin, fill=col + (255,))
            else:
                sn = s["short_name"][:6]
                pill_txt = f"{sn} {rv_txt}"
                tb = odraw.textbbox((0, 0), pill_txt, font=f_pin)
                pw_p = min(154 * S, (tb[2] - tb[0]) + 14 * S)
                ph_p = 22 * S
                px1 = plot_right + 10 * S
                py1 = ay - ph_p // 2
                odraw.line([(ex + 4 * S, ey), (px1, ay)], fill=col + (175,), width=1 * S)
                odraw.rounded_rectangle([px1, py1, px1 + pw_p, py1 + ph_p], radius=6 * S, fill=(15, 23, 42, 238), outline=col + (210,), width=1 * S)
                odraw.text((px1 + 7 * S, py1 + 2 * S), pill_txt, font=f_pin, fill=col + (255,))

        # Footer attribution
        wm = "Buffett Terminal Multi-Asset Rebase Engine · https://buffett.lmlu.de  |  Real-Data Script Render (Zero-AI)"
        wb = odraw.textbbox((0, 0), wm, font=f_card_sub)
        odraw.text((W - pad_x - (wb[2] - wb[0]), H - 34 * S), wm, font=f_card_sub, fill=(100, 116, 139, 220))

        final_rgba = Image.alpha_composite(bg_img, overlay)
        resample_mode = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        final_rgb = final_rgba.convert("RGB").resize((W // S, H // S), resample=resample_mode)
        final_rgb.save(out_path, "PNG", optimize=True)

        series_stats = []
        for i, s in enumerate(prepped):
            bt = s["raw"].get("buffett") or {}
            item_stat: Dict[str, Any] = {
                "symbol": s["symbol"],
                "name": s["name"],
                "currency": s["currency"],
                "axis": ("left_price" if i == 0 else "right_price") if is_dual_axis else "rebased_pct",
                "anchor_date": anchor_date_str,
                "end_date": s["last_date"],
                "start_value": round(s["base_val"], 4),
                "latest_value": round(s["last_val"], 4),
                "period_change_pct": round(s["ret_val"], 2),
                "period_high": round(s["high_val"], 4),
                "period_low": round(s["low_val"], 4),
                "max_drawdown_pct": round(s["max_dd_pct"], 2),
                "source": s["raw"].get("source", ""),
            }
            if bt and bt.get("has_analysis"):
                item_stat["buffett_terminal"] = {
                    "investment_decision": bt.get("investment_decision"),
                    "buy_zone": [bt.get("buy_zone_lower"), bt.get("buy_zone_upper")],
                    "margin_to_upper_pct": bt.get("margin_to_upper_pct"),
                }
            series_stats.append(item_stat)

        return {
            "alignment_mode": alignment_mode,
            "period_label": period_label,
            "date_range": f"{anchor_date_str} ~ {end_date_str}",
            "leader": {"name": leader["name"], "symbol": leader["symbol"], "period_change_pct": round(leader["ret_val"], 2)},
            "laggard": {"name": laggard["name"], "symbol": laggard["symbol"], "period_change_pct": round(laggard["ret_val"], 2)},
            "spread_pct": round(spread_val, 2),
            "series_stats": series_stats,
        }


class FinancialChartPlugin(BasePlugin):
    name = "generate_financial_chart"
    description = (
        "通过本机真实金融/宏观经济数据脚本直接绘制高清走势图（非AI生图）并发送到微信聊天中。"
        "支持单标的走势图与 2~5 支多标的同图叠加对比走势图（自动按共同起点 0% 归一化对齐，双标的使用等比例锁定左右双价格轴，股价 1 与 1000 也可同图精准对比）。"
        "融合 https://buffett.lmlu.de/ 巴菲特安全边际估值击球区数据，支持全球股票（美股/韩股如海力士000660.KS/日股/港股/A股）、"
        "大盘指数、黄金原油大宗商品、汇率、美债收益率、加密货币以及中国/美国 CPI、PPI、PMI、M2、GDP 等宏观经济指标的多周期（1月~10年）价格图表。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "单支股票/指数/宏观指标名称或代码（如 '海力士'、'000660.KS'、'NVDA'、'黄金'、'中国CPI'）；多支同图对比也可用英文逗号分隔传入（如 '上证指数,纳指'）",
            },
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "多支股票/指数/资产同图对比列表（2~5支），例如 ['上证指数', '纳指'] 或 ['NVDA', 'TSM', '000660.KS']；系统会自动按共同起点归一化并对标双轴同图绘制",
            },
            "period": {
                "type": "string",
                "description": "时间跨度，例如：'1m'(1个月)、'3m'(3个月)、'6m'(半年)、'ytd'(今年以来)、'1y'(1年，默认)、'3y'(3年)、'5y'(5年)、'10y'(10年)、'max'(全部历史)",
                "default": "1y",
            },
        },
        "required": [],
    }

    _PERIOD_EXTRACT_RE = re.compile(
        r'(今年以来|年初至今|今年|本年|年内|上市以来|全部历史|历史全周期|'
        r'(?:最近|近|过去)?\s*(?:\d+|[一二两三四五六七八九十半])\s*个?\s*(?:年|月|周|星期|天|日)|'
        r'半年|\bytd\b|\bmax\b|\d+\s*(?:y|yr|mo|m|w|wk|d)\b)',
        flags=re.IGNORECASE,
    )

    _DEICTIC_SYM_RE = re.compile(
        r'^(?:对应的?|相对应的?|它的?|这个|这只|这支|该|该股|该标的|该指数|'
        r'上面(?:那个|那只|这只|这个|说的|提到的|的)?|刚才(?:那个|那只|说的|提到的|聊的|的)?|'
        r'前面(?:那个|说的|的)?|同上|相关|上述)?$'
    )

    _MULTI_FOLLOWUP_RE = re.compile(
        r'(?:(?:两|俩|二|三|四|五|几)[个支只条]?(?:标的|股票|指数)?(?:一起|一块|放一[起张个]|合[在并]?一[起张个]|画在一[起张个]|同图)|'
        r'(?:他俩|它俩|它们|这俩|那俩|二者|两者|双方).{0,10}(?:图|画|对比|走势|一起)|'
        r'(?:放|合|画|叠|塞)[在到]?同一?[个张幅]图|同图(?:对比|走势|展示|绘制)?|(?:和|与|跟|同).{1,12}(?:放同一?[个张]图|一起画|同图对比))'
    )

    _MULTI_SPLIT_RE = re.compile(
        r'(?:\s+(?:vs\.?|VS\.?|versus|pk|PK)\s+|(?<![A-Za-z])(?:vs\.?|VS\.?|pk|PK)(?![A-Za-z])|[、,，/／&＆]|(?:和|与|跟|对比|对标|相比|叠加))'
    )

    _PAREN_TICKER_RE = re.compile(
        r'[（\(]((?:sh|sz|bj)?\d{6}|\d{4,5}\.HK|\d{4,6}\.(?:KS|KQ|SS|SZ|BJ|TW|T)|[A-Z]{1,5}(?:\-[A-Z]{1,3})?|\^[A-Z0-9]+)[）\)]'
    )

    @staticmethod
    def _clean_query_mentions(text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r'^\[(?:引用|附图|分享文章卡片|文件)[^\]]*\]\s*', '', t).strip()
        t = re.sub(r'^(?:@\S+[\s\u2005\u00a0\u3000]*|bot\s*|albot\s*|aibot\s*|小号\s*|：|:|,|，)+', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'(?:[\s\u2005\u00a0\u3000]*@\s*(?:aibot|albot|bot|微信小号)\S*)+$', '', t, flags=re.IGNORECASE).strip()
        return t

    @classmethod
    def _canonical_symbol_key(cls, raw_sym: str) -> str:
        """Map synonyms (e.g. '大A' / '上证指数' / 'sh000001' / '000001.SS') to a single canonical key."""
        s = (raw_sym or "").strip()
        s_low = s.lower()
        if s_low in MACRO_ALIASES:
            m = MACRO_ALIASES[s_low]
            return f"macro:{m.get('report') or m.get('indicator')}:{m.get('country', 'CN')}"
        if s_low in GLOBAL_ASSET_ALIASES:
            return f"yf:{GLOBAL_ASSET_ALIASES[s_low][0].upper()}"
        if s in NAME_TO_CODE:
            c = NAME_TO_CODE[s]
            c_up = c.upper()
            remap = {
                "SH000001": "000001.SS", "SH000300": "000300.SS", "SH000905": "000905.SS",
                "SH000852": "000852.SS", "SH000688": "000688.SS", "SZ399001": "399001.SZ",
                "SZ399006": "399006.SZ", "HKHSI": "^HSI", "HKHSTECH": "3033.HK",
                "USIXIC": "^IXIC", "USDJI": "^DJI", "SPY": "^GSPC",
            }
            if c_up in remap:
                return f"yf:{remap[c_up]}"
            if re.fullmatch(r'\d{6}', c):
                suf = ".SS" if c.startswith(("60", "68")) else (".SZ" if c.startswith(("00", "30")) else ".BJ")
                return f"yf:{c}{suf}"
            if re.fullmatch(r'\d{5}', c):
                hk_i = int(c)
                return f"yf:{hk_i:04d}.HK" if hk_i < 10000 else f"yf:{hk_i}.HK"
            return f"yf:{c_up.replace('.', '-')}"
        if re.fullmatch(r'(?:sh|sz|bj)\d{6}', s_low):
            code6 = s_low[2:]
            suf = ".SS" if s_low.startswith("sh") else (".SZ" if s_low.startswith("sz") else ".BJ")
            return f"yf:{code6}{suf}"
        return f"raw:{s_low}"

    @classmethod
    def is_chart_generation_request(cls, text: str) -> bool:
        """Return True if the message asks the bot to generate/send a financial or macro data chart."""
        t = cls._clean_query_mentions(text)
        if not t:
            return False

        # Never intercept questions asking ABOUT an already-uploaded screenshot/image or AI photo remixing
        if re.search(
            r'(?:这张|这个|上面这[个张]?|我的这[个张]?|图里|图上|截图中|截图里).{0,8}(?:走势图|k线图|K线图|图|截图|照片)',
            t,
        ) and not re.search(r'(?:给我|发给我|重画|画一|生成|放同一|一起画|同图)', t):
            return False
        if any(w in t for w in ["重制", "P图", "p图", "把图里", "把照片", "壁纸", "表情包", "头像", "长啥样", "照片"]):
            return False

        explicit_chart_word = bool(re.search(
            r'(?:走势图|股价图|价格图|行情图|k线图|K线图|折线图|曲线图|估值图|分时图|对比图)',
            t,
        ))
        multi_followup_cue = bool(cls._MULTI_FOLLOWUP_RE.search(t))
        has_chart_noun = explicit_chart_word or multi_followup_cue or bool(re.search(
            r'(?:给我|发给我|发一?[张个幅下]?|来一?[张个幅]?|画一?[张个幅下]?|绘制一?[张个幅下]?|对比一?[下张]?|看一?[下张]?|生成一?[张个幅]?|拉一?[张个下]?|贴一?[张个下]?|补一?[张个下]?).{0,26}(?:图|图表|曲线)',
            t,
        ))
        if not has_chart_noun:
            return False

        fin_cues = [
            "股价", "股票", "走势", "行情", "k线", "K线", "价格", "市值", "估值", "买点", "击球区",
            "金价", "黄金", "白银", "原油", "油价", "汇率", "人民币", "日元", "美元", "美债", "收益率",
            "指数", "纳指", "标普", "道指", "恒指", "上证", "沪深", "创业板", "科创", "大a", "a股", "比特币", "以太坊",
            "cpi", "ppi", "pmi", "gdp", "m2", "通胀", "失业率", "利率",
        ]
        t_low = t.lower()
        has_fin_cue = (
            explicit_chart_word
            or multi_followup_cue
            or any(fc.lower() in t_low for fc in fin_cues)
            or any(ak in t_low for ak in GLOBAL_ASSET_ALIASES)
            or any(mk in t_low for mk in MACRO_ALIASES)
            or any(nk in t for nk in NAME_TO_CODE)
            or bool(cls._PERIOD_EXTRACT_RE.search(t))
        )
        return has_fin_cue

    @classmethod
    def _find_all_aliases_in_text(cls, text_snippet: str) -> List[str]:
        """Find all non-shadowed known macro/global/stock aliases in left-to-right order, deduplicated canonically."""
        if not text_snippet:
            return []
        s_low = text_snippet.lower()
        spans: List[Tuple[int, int, str]] = []

        def _collect(keys_iter, target_str: str, is_lower: bool) -> None:
            for k in keys_iter:
                if len(k) < 2:
                    continue
                needle = k.lower() if is_lower else k
                if needle.isascii():
                    for m in re.finditer(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', target_str):
                        spans.append((m.start(), m.end(), k))
                else:
                    start_pos = 0
                    while True:
                        idx = target_str.find(needle, start_pos)
                        if idx < 0:
                            break
                        spans.append((idx, idx + len(needle), k))
                        start_pos = idx + len(needle)

        _collect(MACRO_ALIASES.keys(), s_low, True)
        _collect(GLOBAL_ASSET_ALIASES.keys(), s_low, True)
        _collect(NAME_TO_CODE.keys(), text_snippet, False)
        if not spans:
            return []

        filtered: List[Tuple[int, int, str]] = []
        for s1, e1, k1 in spans:
            shadowed = any(
                (s2 <= s1 and e2 >= e1 and (e2 - s2) > (e1 - s1))
                for s2, e2, _ in spans
            )
            if not shadowed:
                filtered.append((s1, e1, k1))
        filtered.sort(key=lambda item: (item[0], -(item[1] - item[0])))

        out: List[str] = []
        seen_canon = set()
        used_ranges: List[Tuple[int, int]] = []
        for s1, e1, k1 in filtered:
            if any(not (e1 <= us or s1 >= ue) for us, ue in used_ranges):
                continue
            ck = cls._canonical_symbol_key(k1)
            if ck not in seen_canon:
                seen_canon.add(ck)
                out.append(k1)
                used_ranges.append((s1, e1))
        return out

    @classmethod
    def _find_alias_in_text(cls, text_snippet: str) -> Optional[str]:
        all_found = cls._find_all_aliases_in_text(text_snippet)
        return all_found[0] if all_found else None

    @classmethod
    def _strip_to_symbol_candidate(cls, sym_text: str) -> str:
        s = re.sub(
            r'[，,。；;\s]*(?:放[在到]?同一?[个张幅]图[里上中]?|合[在并到]?一[张个幅]图[里上中]?|'
            r'画[在到]?一[起张个](?:图[里上中]?)?|放一起(?:对比)?|同图(?:对比|展示|绘制|走势)?|一[起块]画).*$',
            '',
            sym_text or '',
        ).strip()
        s = re.sub(
            r'^(?:请|麻烦|帮我|劳驾|能不能|可以)?(?:把|将|用)?'
            r'(?:给我|发给我|发一下|发下|发个|发|来一下|来个|来|画一下|画个|画|绘制一下|绘制|'
            r'对比一下|对比下|对比|比较一下|比较|叠加一下|叠加|生成一下|生成|看下|看看|查下|查一下|拉一下|贴一下|补一下)?'
            r'(?:一[张个幅份])?',
            '',
            s,
        ).strip()
        s = re.sub(
            r'(?:最近|近|过去)?(?:的)?(?:历史|实时|最新)?(?:巴菲特)?'
            r'(?:估值|安全边际|股价|股票|收盘价|价格|行情|走势|k线|K线|折线|曲线|数据|对比)?(?:的)?'
            r'(?:走势图|股价图|价格图|行情图|k线图|K线图|折线图|曲线图|估值图|分时图|对比图|图表|图片|图)?'
            r'(?:发出来|发群里|发一下|发下|发给我|给我看下|给我看看|画出来|生成出来|拉出来)?[吗呢吧呀啊呗！!？?。～~]*$',
            '',
            s,
        ).strip()
        s = re.sub(r'^(?:以及|还有|再加|加上)\s*', '', s).strip()
        return s.strip("的 　,，:：")

    @classmethod
    def _extract_multi_symbols(cls, sym_text: str, full_t: str) -> List[str]:
        """Extract 2~5 distinct target symbols when query asks to compare multiple assets on one chart."""
        core = cls._strip_to_symbol_candidate(sym_text)
        results: List[str] = []
        seen_canon = set()

        if cls._MULTI_SPLIT_RE.search(core):
            raw_parts = cls._MULTI_SPLIT_RE.split(core)
            for p in raw_parts:
                pc = cls._strip_to_symbol_candidate(p)
                if not pc or cls._DEICTIC_SYM_RE.match(pc):
                    continue
                known = cls._find_alias_in_text(pc)
                cand = known if known else (pc if 1 <= len(pc) <= 18 else "")
                if not cand:
                    continue
                ck = cls._canonical_symbol_key(cand)
                if ck not in seen_canon:
                    seen_canon.add(ck)
                    results.append(cand)
            if len(results) >= 2:
                return results[:5]

        # Fallback: scan known aliases directly if explicit comparison/conjunction cue exists
        if re.search(r'(?:和|与|跟|同|vs|VS|pk|PK|对比|对标|相比|叠加|同一|同图|一起|[、,，/／&＆])', full_t):
            all_aliases = cls._find_all_aliases_in_text(sym_text)
            if len(all_aliases) < 2:
                all_aliases = cls._find_all_aliases_in_text(full_t)
            if len(all_aliases) >= 2:
                return all_aliases[:5]
        return []

    @classmethod
    def _build_history_candidates(
        cls,
        current_clean: str,
        recent_history: Optional[List[Dict[str, Any]]] = None,
        quote_info: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, bool]]:
        now = time.time()
        candidates: List[Tuple[str, bool]] = []
        if isinstance(quote_info, dict) and quote_info.get("has_quote"):
            qc = cls._clean_query_mentions(str(quote_info.get("quoted_content") or ""))
            if qc and qc != current_clean:
                candidates.append((qc, False))

        for h in reversed((recent_history or [])[-8:]):
            if not isinstance(h, dict):
                continue
            h_ts = float(h.get("ts") or 0.0)
            if h_ts > 0 and (now - h_ts > 1200):
                continue
            raw_hc = str(h.get("content") or "")
            is_bot = bool(h.get("is_bot")) or str(h.get("sender") or "") == "bot"
            hc = cls._clean_query_mentions(raw_hc)
            if not hc or hc == current_clean:
                continue
            if is_bot and any(p in hc for p in ["这次真发了", "发不了图"]):
                continue
            candidates.append((hc, is_bot))
        return candidates

    @classmethod
    def _resolve_from_history(
        cls,
        current_clean: str,
        recent_history: Optional[List[Dict[str, Any]]] = None,
        quote_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve single deictic symbol & recent period from quoted message or recent chat history."""
        candidates = cls._build_history_candidates(current_clean, recent_history=recent_history, quote_info=quote_info)
        resolved_sym: Optional[str] = None
        for hc, is_bot in candidates:
            probe = hc[:180] if is_bot else hc
            found = cls._find_alias_in_text(probe)
            if found:
                resolved_sym = found
                break
            m_tk = cls._PAREN_TICKER_RE.search(probe)
            if m_tk:
                resolved_sym = m_tk.group(1)
                break
            if not is_bot and StockQuantPlugin:
                sq = StockQuantPlugin.extract_stock_intent(probe)
                if sq:
                    resolved_sym = sq
                    break

        resolved_per: Optional[str] = None
        for hc, is_bot in candidates[:4]:
            probe_p = hc[:100] if is_bot else hc
            mp = cls._PERIOD_EXTRACT_RE.search(probe_p)
            if mp:
                resolved_per = mp.group(1).strip()
                break

        return resolved_sym, resolved_per

    @classmethod
    def _resolve_multi_from_history(
        cls,
        current_clean: str,
        seed_symbols: List[str],
        recent_history: Optional[List[Dict[str, Any]]] = None,
        quote_info: Optional[Dict[str, Any]] = None,
        desired_count: int = 2,
    ) -> Tuple[List[str], Optional[str]]:
        """Collect 2~5 distinct canonical symbols from recent context when user asks to plot them together."""
        candidates = cls._build_history_candidates(current_clean, recent_history=recent_history, quote_info=quote_info)
        collected: List[str] = []
        seen_canon = set()

        for s0 in seed_symbols:
            ck0 = cls._canonical_symbol_key(s0)
            if ck0 not in seen_canon:
                seen_canon.add(ck0)
                collected.append(s0)

        hist_found: List[str] = []
        for hc, is_bot in candidates:
            probe = hc[:240] if is_bot else hc
            for al in cls._find_all_aliases_in_text(probe):
                ck = cls._canonical_symbol_key(al)
                if ck not in seen_canon:
                    seen_canon.add(ck)
                    hist_found.append(al)
                    if len(collected) + len(hist_found) >= desired_count:
                        break
            if len(collected) + len(hist_found) >= desired_count:
                break
            for m_tk in cls._PAREN_TICKER_RE.finditer(probe):
                tk = m_tk.group(1)
                ck = cls._canonical_symbol_key(tk)
                if ck not in seen_canon:
                    seen_canon.add(ck)
                    hist_found.append(tk)
                    if len(collected) + len(hist_found) >= desired_count:
                        break
            if len(collected) + len(hist_found) >= desired_count:
                break

        # Put older context symbols first, followed by newly added seed symbol (chronological order)
        hist_found.reverse()
        merged = (hist_found + collected)[:max(2, min(5, desired_count))]

        resolved_per: Optional[str] = None
        for hc, is_bot in candidates[:5]:
            probe_p = hc[:120] if is_bot else hc
            mp = cls._PERIOD_EXTRACT_RE.search(probe_p)
            if mp:
                resolved_per = mp.group(1).strip()
                break

        return merged, resolved_per

    @classmethod
    def extract_chart_intent(
        cls,
        text: str,
        recent_history: Optional[List[Dict[str, Any]]] = None,
        quote_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Detect if user asks for a single or multi-asset comparison chart and extract {symbol|symbols, period}."""
        if not cls.is_chart_generation_request(text):
            return None
        t = cls._clean_query_mentions(text)

        # Extract period string if present
        period = "1y"
        m_per = cls._PERIOD_EXTRACT_RE.search(t)
        has_explicit_period = bool(m_per)
        if m_per:
            period = m_per.group(1).strip()

        sym_text = t.replace(m_per.group(0), " ") if m_per else t

        # 0. Multi-asset comparison in a single message (e.g. "绘制一下大A和纳指今年的走势图，放在同一个图")
        multi_syms = cls._extract_multi_symbols(sym_text, t)
        if len(multi_syms) >= 2:
            if not has_explicit_period and (recent_history or quote_info):
                _, ctx_per = cls._resolve_from_history(t, recent_history=recent_history, quote_info=quote_info)
                if ctx_per:
                    period = ctx_per
            return {"symbols": multi_syms, "period": period}

        # 0.5 Multi-asset follow-up (e.g. "两个一起画你是不是不行？" / "跟纳指放同一张图对比")
        if cls._MULTI_FOLLOWUP_RE.search(t):
            desired = 3 if "三" in t else (4 if "四" in t else (5 if "五" in t else 2))
            seed_one = cls._find_all_aliases_in_text(sym_text)[:1]
            ctx_multi, ctx_per = cls._resolve_multi_from_history(
                t,
                seed_symbols=seed_one,
                recent_history=recent_history,
                quote_info=quote_info,
                desired_count=desired,
            )
            if len(ctx_multi) >= 2:
                final_per = period if has_explicit_period else (ctx_per or "1y")
                return {"symbols": ctx_multi, "period": final_per}
            # If user asked "两个一起画" without 2 financial symbols in history, do not emit bogus single symbol
            if not seed_one:
                return None

        # 1. Single-asset position-aware priority match against macro, global aliases, and NAME_TO_CODE
        known = cls._find_alias_in_text(sym_text) or cls._find_alias_in_text(t)
        if known:
            return {"symbol": known, "period": period}

        # 2. Strip request verbs & chart suffixes to isolate entity name or deictic pronoun
        sym_clean = cls._strip_to_symbol_candidate(sym_text)
        if not sym_clean or cls._DEICTIC_SYM_RE.match(sym_clean):
            ctx_sym, ctx_per = cls._resolve_from_history(t, recent_history=recent_history, quote_info=quote_info)
            if ctx_sym:
                final_per = period if has_explicit_period else (ctx_per or "1y")
                return {"symbol": ctx_sym, "period": final_per}
            return None

        if 1 <= len(sym_clean) <= 20:
            return {"symbol": sym_clean, "period": period}
        return None

    def execute(
        self,
        symbol: str = "",
        period: str = "1y",
        symbols: Optional[List[str]] = None,
        **kwargs,
    ) -> dict:
        per = (period or kwargs.get("range") or "1y").strip()
        raw_list: List[str] = []
        if isinstance(symbols, list):
            for item in symbols:
                if item and str(item).strip():
                    raw_list.append(str(item).strip())
        elif isinstance(symbols, str) and symbols.strip():
            raw_list.extend([x.strip() for x in re.split(r'[,，、;；]+', symbols) if x.strip()])

        sym_single = (symbol or kwargs.get("query") or kwargs.get("ticker") or "").strip()
        if sym_single:
            if re.search(r'[,，、]|\s+vs\.?\s+', sym_single, flags=re.IGNORECASE):
                for part in re.split(r'[,，、]|\s+vs\.?\s+', sym_single, flags=re.IGNORECASE):
                    if part.strip():
                        raw_list.append(part.strip())
            elif not raw_list:
                raw_list.append(sym_single)

        # Deduplicate input list preserving order
        sym_list: List[str] = []
        seen_in = set()
        for s_item in raw_list:
            ck = self._canonical_symbol_key(s_item)
            if ck not in seen_in:
                seen_in.add(ck)
                sym_list.append(s_item)
        sym_list = sym_list[:5]

        if not sym_list:
            return {"error": "请提供至少一个股票代码、资产名称或宏观经济指标"}

        cleanup_temp_charts()
        tmp_dir = "/tmp" if os.path.isdir("/tmp") else os.environ.get("TEMP", ".")

        # Multi-Asset Same-Chart Comparison Path (2~5 symbols)
        if len(sym_list) >= 2:
            with ThreadPoolExecutor(max_workers=min(5, len(sym_list))) as pool:
                fetched_list = list(pool.map(lambda q_sym: MarketChartDataEngine.resolve_and_fetch(q_sym, per), sym_list))

            valid_series: List[Dict[str, Any]] = []
            seen_resolved = set()
            failed_items: List[str] = []
            for req_s, f_res in zip(sym_list, fetched_list):
                if isinstance(f_res, dict) and not f_res.get("error") and f_res.get("bars"):
                    rk = f"{f_res.get('symbol')}::{f_res.get('name')}"
                    if rk not in seen_resolved:
                        seen_resolved.add(rk)
                        valid_series.append(f_res)
                else:
                    err_msg = f_res.get("error") if isinstance(f_res, dict) else "未知错误"
                    failed_items.append(f"{req_s}: {err_msg}")

            if len(valid_series) < 2:
                if len(valid_series) == 1 and not failed_items:
                    # Two synonyms resolved to the exact same asset -> fall through to single chart
                    sym_list = [valid_series[0]["symbol"]]
                else:
                    return {
                        "error": f"多标的同图对比需要至少2个有效数据序列（当前成功 {len(valid_series)} 个）；失败详情: {'; '.join(failed_items)}"
                    }
            else:
                tag_slug = "_vs_".join(
                    re.sub(r'[^A-Za-z0-9]', '', str(v.get("symbol", "S")))[:6] or "S"
                    for v in valid_series[:4]
                )
                out_png = os.path.join(tmp_dir, f"wechat_search_chart_cmp_{tag_slug}_{int(time.time() * 1000)}.png")
                period_lbl = valid_series[0].get("period_label") or parse_period_spec(per)[3]
                try:
                    comp_meta = BuffettChartRenderer.render_comparison(valid_series, period_lbl, out_png)
                except Exception as ce:
                    return {"error": f"本机绘制多标的对比走势图异常: {ce}"}

                sender_fn = get_image_sender()
                sent_ok = False
                if sender_fn:
                    res_send = sender_fn(out_png)
                    sent_ok = res_send[0] if isinstance(res_send, tuple) else bool(res_send)

                names_joined = " vs ".join(str(v.get("name") or v.get("symbol")) for v in valid_series)
                syms_joined = " vs ".join(str(v.get("symbol")) for v in valid_series)
                out_summary: Dict[str, Any] = {
                    "status": "success",
                    "type": "financial_comparison_chart",
                    "alignment_mode": comp_meta["alignment_mode"],
                    "image_sent_to_wechat": sent_ok,
                    "image_path": out_png,
                    "symbol": syms_joined,
                    "name": names_joined,
                    "period": comp_meta["period_label"],
                    "common_date_range": comp_meta["date_range"],
                    "leader": comp_meta["leader"],
                    "laggard": comp_meta["laggard"],
                    "spread_pct": comp_meta["spread_pct"],
                    "series": comp_meta["series_stats"],
                }
                if failed_items:
                    out_summary["partial_warnings"] = failed_items
                return out_summary

        # Single-Asset Chart Path
        sym = sym_list[0]
        chart_data = MarketChartDataEngine.resolve_and_fetch(sym, per)
        if "error" in chart_data:
            return chart_data

        safe_sym = re.sub(r'[^A-Za-z0-9_\u4e00-\u9fa5]', '_', str(chart_data.get("symbol", "chart")))[:24]
        out_png = os.path.join(tmp_dir, f"wechat_search_chart_{safe_sym}_{int(time.time() * 1000)}.png")

        try:
            stats = BuffettChartRenderer.render(chart_data, out_png)
        except Exception as re_err:
            return {"error": f"本机绘制走势图异常: {re_err}"}

        sender_fn = get_image_sender()
        sent_ok = False
        if sender_fn:
            res_send = sender_fn(out_png)
            sent_ok = res_send[0] if isinstance(res_send, tuple) else bool(res_send)

        bt = chart_data.get("buffett") or {}
        bars = chart_data["bars"]
        ccy = chart_data.get("currency") or ""
        summary_dict = {
            "status": "success",
            "type": "financial_chart",
            "image_sent_to_wechat": sent_ok,
            "image_path": out_png,
            "symbol": chart_data["symbol"],
            "name": chart_data["name"],
            "period": chart_data.get("period_label", per),
            "date_range": f"{bars[0]['date']} ~ {bars[-1]['date']}",
            "bar_count": len(bars),
            "currency": ccy,
            "start_value": round(stats["first_close"], 4),
            "latest_value": round(stats["last_close"], 4),
            "period_change_pct": round(stats["pct_change"], 2),
            "period_high": {"value": round(stats["high_val"], 4), "date": stats["high_date"]},
            "period_low": {"value": round(stats["low_val"], 4), "date": stats["low_date"]},
            "max_drawdown_pct": round(stats["max_drawdown_pct"], 2),
            "cagr_pct": round(stats["cagr_pct"], 2) if stats["cagr_pct"] is not None else None,
            "source": chart_data.get("source", ""),
        }
        if bt:
            summary_dict["buffett_terminal"] = {
                "url": f"{BUFFETT_BASE_URL}/",
                "investment_decision": bt.get("investment_decision"),
                "quality_score": bt.get("quality_score"),
                "buy_zone": [bt.get("buy_zone_lower"), bt.get("buy_zone_upper")],
                "margin_to_upper_pct": bt.get("margin_to_upper_pct"),
                "conclusion_summary": bt.get("conclusion_summary"),
            }
        return summary_dict
