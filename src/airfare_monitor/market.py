"""Resolve which public search site should collect a configured route."""

from __future__ import annotations

from functools import lru_cache

import airportsdata

from .errors import CollectionError
from .models import LegConfig


@lru_cache(maxsize=1)
def _iata_airports() -> dict[str, dict]:
    return airportsdata.load("IATA")


def resolve_market(leg: LegConfig) -> str:
    """Return ``domestic`` or ``international`` for one leg.

    Explicit configuration wins. Automatic classification is deliberately
    strict: both endpoints must exist in the bundled IATA dataset, otherwise
    the operator must choose a market instead of silently querying the wrong
    website.
    """
    if leg.market in {"domestic", "international"}:
        return leg.market
    airports = _iata_airports()
    origin = airports.get(leg.origin_airport_iata)
    destination = airports.get(leg.destination_airport_iata)
    missing = [
        code
        for code, record in (
            (leg.origin_airport_iata, origin),
            (leg.destination_airport_iata, destination),
        )
        if record is None
    ]
    if missing:
        raise CollectionError(
            f"无法自动判断 {'/'.join(missing)} 所属国家；请在该航程配置 market: domestic 或 international"
        )
    return "domestic" if origin["country"] == destination["country"] == "CN" else "international"


def resolve_provider(leg: LegConfig) -> str:
    market = resolve_market(leg)
    provider = leg.provider
    if provider == "auto":
        return "tongcheng" if market == "domestic" else "ctrip"
    if provider not in {"tongcheng", "qunar", "ctrip"}:
        raise CollectionError("未知查询来源")
    if market == "domestic" and provider != "tongcheng":
        raise CollectionError("国内航程目前只支持同程")
    if market != "domestic" and provider == "tongcheng":
        raise CollectionError("同程目前只支持国内航程")
    if leg.is_round_trip and provider == "tongcheng":
        raise CollectionError("同程国内目前只支持单程")
    return provider


def source_label(leg: LegConfig) -> str:
    try:
        provider = resolve_provider(leg)
    except CollectionError:
        return "来源配置需检查"
    return {"tongcheng": "国内 · 同程", "qunar": "国际/跨境 · 去哪儿", "ctrip": "国际/跨境 · 携程"}[provider]
