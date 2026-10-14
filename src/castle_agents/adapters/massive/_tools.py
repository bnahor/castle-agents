from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from ._config import MassiveConfig


class MassiveTools:
    def __init__(self, api_key: str, config: MassiveConfig) -> None:
        from massive import RESTClient  # pyright: ignore[reportMissingTypeStubs]

        # Transport timeouts matter: cancelling the awaiting task cannot stop a call in a thread.
        self._client: Any = RESTClient(
            api_key=api_key,
            connect_timeout=config.connect_timeout_s,
            read_timeout=config.read_timeout_s,
            retries=config.retries,
        )

    async def call(self, name: str, args: dict[str, Any]) -> str:
        ticker = str(args["ticker"]).upper().strip()
        if name == "get_previous_close":
            return await asyncio.to_thread(self._previous_close, ticker)
        if name == "get_company_details":
            return await asyncio.to_thread(self._details, ticker)
        raise ValueError(f"unknown tool {name}")

    def _previous_close(self, ticker: str) -> str:
        agg = _first(self._client.get_previous_close_agg(ticker))
        if agg is None:
            return json.dumps({"ticker": ticker, "error": "no data"})
        return json.dumps(
            {
                "ticker": ticker,
                "open": agg.open,
                "high": agg.high,
                "low": agg.low,
                "close": agg.close,
                "volume": agg.volume,
                "timestamp_ms": agg.timestamp,
            }
        )

    def _details(self, ticker: str) -> str:
        d: Any = self._client.get_ticker_details(ticker)
        return json.dumps(
            {
                "ticker": ticker,
                "name": d.name,
                "market_cap": getattr(d, "market_cap", None),
                "industry": getattr(d, "sic_description", None),
                "type": getattr(d, "type", None),
                "description": (getattr(d, "description", None) or "")[:600],
            }
        )


def _first(result: Any) -> Any:
    if isinstance(result, list):
        items = cast(list[Any], result)
        return items[0] if items else None
    return result
