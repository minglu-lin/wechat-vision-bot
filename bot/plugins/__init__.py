#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plugin Manager: Central Registry & Dispatcher
Manages all bot API plugins, exports OpenAI-compatible tool schemas,
and executes Fast-Path zero-latency matches.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple, Any

from .base import BasePlugin
from .weather import WeatherPlugin
from .finance import ExchangeRatePlugin, CryptoPlugin
from .stock import StockQuantPlugin
from .network import NetworkPlugin, WebSearchPlugin
from .calculator import CalculatorPlugin
from .utilities import UtilitiesPlugin
from .map_service import FoodSearchPlugin, LocalPlaceSearchPlugin, TransitRoutePlugin
from .image_tools import GenerateImagePlugin, SearchImagePlugin, SendWebpageImagePlugin, register_image_sender


class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, BasePlugin] = {}
        self._register_default_plugins()

    def register(self, plugin: BasePlugin):
        """Register a plugin instance."""
        if not plugin.name:
            raise ValueError("Plugin must have a non-empty name")
        self.plugins[plugin.name] = plugin

    def _register_default_plugins(self):
        self.register(WeatherPlugin())
        self.register(StockQuantPlugin())
        self.register(ExchangeRatePlugin())
        self.register(CryptoPlugin())
        self.register(NetworkPlugin())
        self.register(WebSearchPlugin())
        self.register(CalculatorPlugin())
        self.register(UtilitiesPlugin())
        self.register(LocalPlaceSearchPlugin())
        self.register(FoodSearchPlugin())
        self.register(TransitRoutePlugin())
        self.register(GenerateImagePlugin())
        self.register(SearchImagePlugin())
        self.register(SendWebpageImagePlugin())


    def get_openai_tools(self) -> List[dict]:
        """Export all registered plugins as OpenAI-compatible function tools."""
        return [p.to_openai_tool() for p in self.plugins.values()]

    def execute_tool(self, name: str, arguments: Any) -> dict:
        """Execute a tool by name with arguments (dict or JSON string)."""
        if name not in self.plugins:
            return {"error": f"Tool '{name}' not found in registry"}

        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments.strip() else {}
            except Exception as e:
                return {"error": f"Failed to parse arguments JSON: {e}"}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            args = {}

        plugin = self.plugins[name]
        try:
            return plugin.execute(**args)
        except Exception as e:
            return {"error": f"Plugin execution exception: {e}"}

    def match_fast_path(self, user_content: str) -> Tuple[bool, Optional[str]]:
        """Check if user query matches any plugin's fast path regex.
        Returns (True, formatted_reply) if matched, (False, None) otherwise.
        """
        if not user_content:
            return False, None

        # Clean leading bot / @bot prefix for cleaner pattern matching
        c = user_content.strip()
        c = re.sub(r'^(?:@\S+\s*|bot\s*|albot\s*|aibot\s*|小号\s*|：|:|,|，)+', '', c, flags=re.IGNORECASE).strip()
        if not c:
            return False, None

        for p_name, plugin in self.plugins.items():
            kwargs = plugin.match_fast_path(c)
            if kwargs is not None:
                try:
                    res = plugin.execute(**kwargs)
                    reply = plugin.format_fast_reply(res)
                    if reply:
                        return True, reply
                except Exception:
                    pass

        return False, None


# Global singleton instance
plugin_manager = PluginManager()
