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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .base import BasePlugin
from .image_tools import get_image_sender
from .stock import NAME_TO_CODE, StockDataEngine

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
    "纳斯达克": ("^IXIC", "纳斯达克综合指数"),
    "纳指": ("^IXIC", "纳斯达克综合指数"),
    "道琼斯": ("^DJI", "道琼斯工业指数"),
    "道指": ("^DJI", "道琼斯工业指数"),
    "恒生指数": ("^HSI", "香港恒生指数"),
    "恒指": ("^HSI", "香港恒生指数"),
    "恒生科技": ("3033.HK", "恒生科技 ETF"),
    "上证指数": ("000001.SS", "上证综合指数"),
    "上证": ("000001.SS", "上证综合指数"),
    "沪指": ("000001.SS", "上证综合指数"),
    "大盘": ("000001.SS", "上证综合指数"),
    "沪深300": ("000300.SS", "沪深 300 指数"),
    "深证成指": ("399001.SZ", "深证成指"),
    "深成指": ("399001.SZ", "深证成指"),
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

    if s in ("ytd", "今年", "今年以来", "年初至今"):
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
            if not bars:
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
            if yf_res and yf_res.get("bars"):
                bars = yf_res["bars"]
                currency = yf_res.get("currency") or ""
                final_name = final_name or yf_res.get("name") or yf_sym
                source_label = "Buffett Terminal + Yahoo Finance" if bt_data else "Yahoo Finance"
            elif mkt_type != "unknown" and kline_code:
                # Fallback to EastMoney / Tencent K-lines for A-shares / BJ-shares
                limit_n = min(1500, max(60, int((slice_days or 365) * 0.72)))
                kl = StockDataEngine.fetch_klines(kline_code, secid, mkt_type, limit=limit_n)
                if kl:
                    bars = kl
                    currency = "CNY" if mkt_type in ("a_share", "cn_index") else ("HKD" if "hk" in mkt_type else "USD")
                    final_name = final_name or clean_code
                    source_label = "EastMoney / Tencent Quote"

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


class FinancialChartPlugin(BasePlugin):
    name = "generate_financial_chart"
    description = (
        "通过本机真实金融/宏观经济数据脚本直接绘制高清走势图（非AI生图）并发送到微信聊天中。"
        "融合 https://buffett.lmlu.de/ 巴菲特安全边际估值击球区数据，支持全球股票（美股/韩股如海力士000660.KS/日股/港股/A股）、"
        "大盘指数、黄金原油大宗商品、汇率、美债收益率、加密货币以及中国/美国 CPI、PPI、PMI、M2、GDP 等宏观经济指标的多周期（1月~10年）价格图表。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "股票、资产或宏观经济指标名称/代码，例如：'海力士'、'000660.KS'、'AAPL'、'NVDA'、'茅台'、'泡泡玛特'、'黄金'、'原油'、'10年期美债'、'比特币'、'中国CPI'、'美国CPI'、'制造业PMI'",
            },
            "period": {
                "type": "string",
                "description": "时间跨度，例如：'1m'(1个月)、'3m'(3个月)、'6m'(半年)、'ytd'(今年以来)、'1y'(1年，默认)、'3y'(3年)、'5y'(5年)、'10y'(10年)、'max'(全部历史)",
                "default": "1y",
            },
        },
        "required": ["symbol"],
    }

    @classmethod
    def extract_chart_intent(cls, text: str) -> Optional[Dict[str, str]]:
        """Detect if user explicitly asks for a stock/asset/macro data chart image and extract {symbol, period}."""
        if not text:
            return None
        t = text.strip()
        t = re.sub(r'^(?:@\S+\s*|bot\s*|albot\s*|aibot\s*|小号\s*|：|:|,|，)+', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'(?:\s*@\s*(?:aibot|albot|bot|微信小号)\S*)+$', '', t, flags=re.IGNORECASE).strip()
        if not t:
            return None

        # Never intercept questions asking ABOUT an already-uploaded image or AI photo remixing
        if re.search(r'(?:这张图|这个图|上面这[个张]图|图里|截图中).*(?:是啥|是什么|真的假的|真假|意思|写了|谁)', t):
            return None
        if any(w in t for w in ["重制", "P图", "p图", "把图里", "把照片", "壁纸", "表情包", "头像", "长啥样", "照片"]):
            return None

        has_chart_noun = bool(re.search(
            r'(?:走势图|股价图|价格图|行情图|k线图|K线图|折线图|曲线图|估值图|'
            r'(?:给我|发一?[张个幅]?|来一?[张个幅]?|画一?[张个幅下]?|看一?[下张]?|生成一?[张个幅]?|拉一?[张个下]?).{0,22}(?:图|图表|曲线))',
            t,
        ))
        if not has_chart_noun:
            return None

        fin_cues = [
            "股价", "股票", "走势", "行情", "k线", "K线", "价格", "市值", "估值", "买点", "击球区",
            "金价", "黄金", "白银", "原油", "油价", "汇率", "人民币", "日元", "美元", "美债", "收益率",
            "指数", "纳指", "标普", "道指", "恒指", "上证", "沪深", "创业板", "比特币", "以太坊",
            "cpi", "ppi", "pmi", "gdp", "m2", "通胀", "失业率", "利率",
        ]
        t_low = t.lower()
        has_fin_cue = (
            any(fc.lower() in t_low for fc in fin_cues)
            or any(ak in t_low for ak in GLOBAL_ASSET_ALIASES)
            or any(mk in t_low for mk in MACRO_ALIASES)
            or any(nk in t for nk in NAME_TO_CODE)
            or bool(re.search(r'(?:最近|近|过去)\s*(?:\d+|[一二两三四五六七八九十半])\s*个?\s*(?:年|月|周|天)', t))
        )
        if not has_fin_cue:
            return None

        # Extract period string if present
        period = "1y"
        m_per = re.search(
            r'(今年以来|年初至今|上市以来|全部历史|(?:最近|近|过去)?\s*(?:\d+|[一二两三四五六七八九十半])\s*个?\s*(?:年|月|周|星期|天|日)|半年|\d+\s*(?:y|yr|mo|m|w|d)\b)',
            t,
            flags=re.IGNORECASE,
        )
        if m_per:
            period = m_per.group(1).strip()

        # Extract clean target symbol
        sym_text = t
        if m_per:
            sym_text = sym_text.replace(m_per.group(0), " ")

        # 1. Exact priority match against macro, global aliases, and NAME_TO_CODE
        for mk in sorted(MACRO_ALIASES.keys(), key=len, reverse=True):
            if len(mk) >= 2 and mk in t_low:
                return {"symbol": mk, "period": period}
        for ak in sorted(GLOBAL_ASSET_ALIASES.keys(), key=len, reverse=True):
            if len(ak) >= 2 and ak in t_low:
                return {"symbol": ak, "period": period}
        for nk in sorted(NAME_TO_CODE.keys(), key=len, reverse=True):
            if len(nk) >= 2 and nk in t:
                return {"symbol": nk, "period": period}

        # 2. Strip request verbs & chart suffixes to isolate the entity name (e.g. "给我一张海力士最近5年股价的图" -> "海力士")
        sym_clean = re.sub(
            r'^(?:请|麻烦|帮我)?(?:给我|发给我|发|来|画|绘制|生成|看下|看看|查下|查一下|拉一下)?(?:一[张个幅份])?',
            '',
            sym_text,
        ).strip()
        sym_clean = re.sub(
            r'(?:最近|近|过去)?(?:的)?(?:历史|实时|最新)?(?:巴菲特)?(?:估值|安全边际|股价|股票|收盘价|价格|行情|走势|k线|K线|折线|曲线|数据)?(?:的)?(?:走势图|折线图|曲线图|图表|图片|图)[吗呢吧呀啊！!？?。]*$',
            '',
            sym_clean,
        ).strip()
        sym_clean = sym_clean.strip("的 　,，:：")
        if 1 <= len(sym_clean) <= 20:
            return {"symbol": sym_clean, "period": period}
        return None

    def execute(self, symbol: str = "", period: str = "1y", **kwargs) -> dict:
        sym = (symbol or kwargs.get("query") or kwargs.get("ticker") or "").strip()
        per = (period or kwargs.get("range") or "1y").strip()
        if not sym:
            return {"error": "请提供股票代码、资产名称或宏观经济指标"}

        cleanup_temp_charts()
        chart_data = MarketChartDataEngine.resolve_and_fetch(sym, per)
        if "error" in chart_data:
            return chart_data

        tmp_dir = "/tmp" if os.path.isdir("/tmp") else os.environ.get("TEMP", ".")
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
