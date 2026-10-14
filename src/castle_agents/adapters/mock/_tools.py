from __future__ import annotations

import json
import zlib
from typing import Any


class MockTools:
    async def call(self, name: str, args: dict[str, Any]) -> str:
        ticker = str(args["ticker"]).upper().strip()
        seed = zlib.crc32(ticker.encode())
        match name:
            case "get_previous_close":
                close = round(20 + seed % 900 + (seed % 100) / 100, 2)
                return json.dumps({"ticker": ticker, "close": close, "mock": True})
            case "get_company_details":
                return json.dumps({"ticker": ticker, "name": f"{ticker} (mock)", "mock": True})
            case _:
                raise ValueError(f"unknown tool {name}")
