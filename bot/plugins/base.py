#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base Plugin Interface for WeChat Bot
All plugins implement this interface for both:
1. OpenAI-compatible Function Calling (LLM autonomous invocation)
2. Fast-Path direct regex execution (0-token, zero-latency direct reply)
"""

import re
import json


class BasePlugin:
    name: str = ""
    description: str = ""
    parameters: dict = {}
    fast_patterns: list = []  # List of regex pattern strings for fast matching

    def to_openai_tool(self) -> dict:
        """Export OpenAI-compatible function calling tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

    def match_fast_path(self, text: str):
        """Check if user query matches any fast path regex pattern.
        Returns kwargs dict if matched, None otherwise.
        """
        if not self.fast_patterns or not text:
            return None
        t = text.strip()
        for pat in self.fast_patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return m.groupdict()
        return None

    def execute(self, **kwargs) -> dict:
        """Execute the API call and return structured data dict."""
        raise NotImplementedError

    def format_fast_reply(self, result: dict) -> str:
        """Format the execution result into plain text for WeChat direct fast-path reply."""
        raise NotImplementedError
