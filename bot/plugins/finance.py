#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finance Plugin: Currency Exchange & Crypto Prices
Provides real-time currency conversion and cryptocurrency prices with zero API key requirement.
"""

import urllib.parse
import urllib.request
import json
import re
from .base import BasePlugin

CURRENCY_ALIASES = {
    "美元": "USD",
    "美金": "USD",
    "人民币": "CNY",
    "日元": "JPY",
    "欧元": "EUR",
    "英镑": "GBP",
    "港币": "HKD",
    "港元": "HKD",
    "韩元": "KRW",
    "新币": "SGD",
    "新加坡元": "SGD",
    "加元": "CAD",
    "澳元": "AUD",
    "瑞士法郎": "CHF",
}

CRYPTO_ALIASES = {
    "比特币": "bitcoin",
    "btc": "bitcoin",
    "以太坊": "ethereum",
    "以太币": "ethereum",
    "eth": "ethereum",
    "索拉纳": "solana",
    "sol": "solana",
    "狗狗币": "dogecoin",
    "doge": "dogecoin",
    "币安币": "binancecoin",
    "bnb": "binancecoin",
    "瑞波币": "ripple",
    "xrp": "ripple",
}


class ExchangeRatePlugin(BasePlugin):
    name = "get_exchange_rate"
    description = "查询全球主流法定货币（如美元、欧元、日元、英镑、港币、韩元、人民币等）的最新实时汇率及换算结果"
    parameters = {
        "type": "object",
        "properties": {
            "from_currency": {
                "type": "string",
                "description": "原币种（名称或三位字母代码），如：美元、USD、欧元、EUR、日元、JPY、人民币、CNY",
            },
            "to_currency": {
                "type": "string",
                "description": "目标币种（名称或三位字母代码），默认为 CNY（人民币）",
                "default": "CNY",
            },
            "amount": {
                "type": "number",
                "description": "兑换金额，默认为 1.0",
                "default": 1.0,
            },
        },
        "required": ["from_currency"],
    }

    fast_patterns = [
        r'^(?:查一下?|算下?|查询?)?\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<from_currency>美元|美金|欧元|日元|英镑|港币|港元|韩元|usd|eur|jpy|gbp|hkd)\s*(?:等于|换|兑换|折合)?\s*(?:多少)?\s*(?P<to_currency>人民币|cny)?$',
        r'^(?:查一下?|看下?|查询?)?\s*(?P<from_currency>美元|美金|欧元|日元|英镑|港币|港元|韩元|usd|eur|jpy|gbp|hkd)\s*(?:对|兑)?\s*(?P<to_currency>人民币|cny)?\s*汇率$',
    ]

    def _normalize_currency(self, cur_str: str) -> str:
        s = (cur_str or "").strip().lower()
        if s in CURRENCY_ALIASES:
            return CURRENCY_ALIASES[s]
        for k, v in CURRENCY_ALIASES.items():
            if k in s:
                return v
        return s.upper()

    def execute(self, from_currency: str = "", to_currency: str = "CNY", amount: float = 1.0, **kwargs) -> dict:
        from_cur = from_currency or kwargs.get("from_curr", "")
        to_cur = to_currency or kwargs.get("to_curr", "CNY")
        base = self._normalize_currency(from_cur)
        target = self._normalize_currency(to_cur or "CNY")
        try:
            amt = float(amount)
        except (ValueError, TypeError):
            amt = 1.0

        url = f"https://open.er-api.com/v6/latest/{base}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"汇率接口请求失败: {e}"}

        if data.get("result") != "success":
            return {"error": f"未能找到币种 {base} 的有效汇率数据"}

        rates = data.get("rates", {})
        if target not in rates:
            return {"error": f"未能找到 {base} 兑 {target} 的汇率"}

        rate = rates[target]
        converted = round(amt * rate, 4)
        update_time = data.get("time_last_update_utc", "")

        return {
            "from": base,
            "to": target,
            "amount": amt,
            "rate": rate,
            "converted_amount": converted,
            "update_time_utc": update_time,
            "rates_summary": {
                "CNY": rates.get("CNY"),
                "USD": rates.get("USD"),
                "EUR": rates.get("EUR"),
                "JPY": rates.get("JPY"),
                "HKD": rates.get("HKD"),
            }
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询汇率出错：{result['error']}"

        b = result["from"]
        t = result["to"]
        amt = result["amount"]
        converted = result["converted_amount"]
        rate = result["rate"]

        return (
            f"💱 【实时外汇牌价】\n"
            f"• 兑换结果：{amt} {b} = {converted} {t}\n"
            f"• 当前汇率：1 {b} ≈ {rate:.4f} {t}\n"
            f"• 常用汇率：1 {b} ≈ {result['rates_summary'].get('CNY', '-')} 人民币 | {result['rates_summary'].get('USD', '-')} 美元 | {result['rates_summary'].get('EUR', '-')} 欧元"
        )


class CryptoPlugin(BasePlugin):
    name = "get_crypto_price"
    description = "查询主流加密货币（BTC 比特币、ETH 以太坊、SOL 索拉纳、BNB 等）的最新实时美元价格、人民币估值及 24 小时涨跌幅"
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "加密货币代码或名称，例如：BTC、比特币、ETH、以太坊、SOL、BNB、DOGE",
            }
        },
        "required": ["symbol"],
    }

    fast_patterns = [
        r'^(?:查一下?|看下?)?(?P<symbol>btc|eth|sol|doge|bnb|xrp|比特币|以太坊|以太币|索拉纳|狗狗币|币安币)\s*(?:实时|最新)?\s*(?:价格|行情|多少钱)$',
    ]

    def _normalize_crypto(self, sym: str) -> tuple:
        s = (sym or "").strip().lower()
        coin_id = CRYPTO_ALIASES.get(s, s)
        display_name = sym.upper()
        for k, v in CRYPTO_ALIASES.items():
            if v == coin_id:
                display_name = f"{v.upper()} ({k})"
                break
        return coin_id, display_name

    def execute(self, symbol: str, **kwargs) -> dict:
        coin_id, display_name = self._normalize_crypto(symbol)
        raw_sym = (symbol or "").strip().upper()

        # Map coin_id back to standard ticker symbol for Binance primary feed
        id_to_ticker = {
            "bitcoin": "BTC",
            "ethereum": "ETH",
            "solana": "SOL",
            "binancecoin": "BNB",
            "ripple": "XRP",
            "dogecoin": "DOGE",
            "cardano": "ADA",
            "tron": "TRX",
            "avalanche-2": "AVAX",
            "polkadot": "DOT",
            "chainlink": "LINK",
            "litecoin": "LTC",
            "sui": "SUI",
            "pepe": "PEPE",
        }
        ticker = id_to_ticker.get(coin_id) or (raw_sym if raw_sym.isalpha() and 2 <= len(raw_sym) <= 8 else "")

        # 1. Primary source: Binance Spot 24hr Ticker API (fast, keyless, reliable in SG)
        if ticker and ticker not in ("USDT", "USDC"):
            b_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={ticker}USDT"
            try:
                req = urllib.request.Request(b_url, headers={"User-Agent": "WeChatBot/2.1"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    b_data = json.loads(r.read(65536).decode("utf-8"))
                if b_data and "lastPrice" in b_data:
                    price_usd = float(b_data["lastPrice"])
                    change_24h = float(b_data.get("priceChangePercent", 0.0))
                    high_24h = float(b_data.get("highPrice", 0.0))
                    low_24h = float(b_data.get("lowPrice", 0.0))
                    quote_vol_yi = round(float(b_data.get("quoteVolume", 0.0)) / 1e8, 2)
                    return {
                        "symbol": ticker,
                        "name": coin_id.capitalize() if coin_id != ticker.lower() else ticker,
                        "price_usd": round(price_usd, 2 if price_usd >= 1 else 6),
                        "change_24h_percent": round(change_24h, 2),
                        "high_24h_usd": round(high_24h, 2 if high_24h >= 1 else 6),
                        "low_24h_usd": round(low_24h, 2 if low_24h >= 1 else 6),
                        "volume_24h_usd_100m": quote_vol_yi,
                        "source": "Binance",
                    }
            except Exception:
                pass

        # 2. Secondary fallback: CoinGecko public markets endpoint
        cg_url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={urllib.parse.quote(coin_id)}"
        try:
            req = urllib.request.Request(cg_url, headers={"User-Agent": "WeChatBot/2.1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                arr = json.loads(r.read(131072).decode("utf-8"))
            if isinstance(arr, list) and arr:
                item = arr[0]
                price_usd = float(item.get("current_price") or 0.0)
                change_24h = float(item.get("price_change_percentage_24h") or 0.0)
                mcap_yi = round(float(item.get("market_cap") or 0.0) / 1e8, 2)
                return {
                    "symbol": str(item.get("symbol") or symbol).upper(),
                    "name": item.get("name") or coin_id.capitalize(),
                    "price_usd": round(price_usd, 2 if price_usd >= 1 else 6),
                    "change_24h_percent": round(change_24h, 2),
                    "market_cap_usd": mcap_yi,
                    "source": "CoinGecko",
                }
        except Exception:
            pass

        return {"error": f"加密货币行情接口暂无法访问或找不到代码 '{symbol}'"}

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询币价出错：{result['error']}"

        sym = result["symbol"]
        name = result["name"]
        price = result["price_usd"]
        chg = result["change_24h_percent"]
        chg_sign = "+" if chg > 0 else ""
        price_fmt = f"{price:,.2f}" if price >= 1 else f"{price:.6f}"

        extra_line = ""
        if "high_24h_usd" in result and "low_24h_usd" in result:
            h_fmt = f"{result['high_24h_usd']:,.2f}" if result["high_24h_usd"] >= 1 else f"{result['high_24h_usd']:.6f}"
            l_fmt = f"{result['low_24h_usd']:,.2f}" if result["low_24h_usd"] >= 1 else f"{result['low_24h_usd']:.6f}"
            extra_line = f"• 24h 高/低：${h_fmt} / ${l_fmt} USD（24h 成交额约 ${result.get('volume_24h_usd_100m', 0)} 亿）"
        elif "market_cap_usd" in result:
            extra_line = f"• 总市值：约 ${result['market_cap_usd']} 亿美元"

        lines = [
            f"🪙 【{name} ({sym}) 实时行情】",
            f"• 当前价格：${price_fmt} USD",
            f"• 24小时涨跌：{chg_sign}{chg:.2f}%",
        ]
        if extra_line:
            lines.append(extra_line)
        return "\n".join(lines)
