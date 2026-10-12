"""Parse the final Ctrip China international list; never retain browser tokens.

Contract observed in the user-supplied 2026-10-12 HAR. A nonempty final list
replaces provisional lists. Prices are CNY passenger-group totals including tax.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from .errors import IncompleteResponseError, ManualAttentionRequired, ParseError
from .models import FlightSnapshot, ItinerarySnapshot, LegConfig, SeatAvailability, Segment
from .parser import _return_leg_config, _signature
from .ranking import rank_flights

# City aliases observed in the supplied recordings. Other airports retain their
# airport code in the search URL; eligibility always uses actual flight airports.
CITY_ALIASES = {"PVG": "SHA", "PKX": "BJS", "ICN": "SEL", "GMP": "SEL"}
CABIN_SEARCH = {"economy": "Y_S", "premium_economy": "Y_S", "business": "C_F", "first": "C_F"}
CABIN_CODES = {"economy": "Y", "premium_economy": "S", "business": "C", "first": "F"}


def city_code(airport: str) -> str:
    return CITY_ALIASES.get(airport.upper(), airport.upper())


def check_ctrip_attention(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    context = data.get("context") or {}
    for key in ("needUserLogin", "lgn"):
        if key in data and type(data[key]) is not bool:
            raise ParseError("携程登录状态字段类型已变化")
    if isinstance(context, dict):
        if "showAuthCode" in context and type(context["showAuthCode"]) is not bool:
            raise ParseError("携程验证状态字段类型已变化")
        if "flag" in context and type(context["flag"]) is not int:
            raise ParseError("携程访问状态字段类型已变化")
    if data.get("needUserLogin") is True:
        raise ManualAttentionRequired("携程要求登录，本工具不自动登录；请人工检查查询页面")
    if isinstance(context, dict) and (context.get("showAuthCode") is True or context.get("flag", 0) != 0):
        raise ManualAttentionRequired("携程返回验证或异常访问标记，需要人工检查")
    if data.get("lgn") is True:
        raise ManualAttentionRequired("携程返回受限价格标记，需要人工检查")


def is_completed_ctrip_payload(payload: object) -> bool:
    if not isinstance(payload, dict) or type(payload.get("status")) is not int or payload["status"] != 0:
        return False
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("context"), dict):
        return False
    context = data["context"]
    return (
        context.get("finished") is True
        and type(context.get("flag", 0)) is int and context.get("flag", 0) == 0
        and context.get("showAuthCode", False) is False
        and data.get("needUserLogin", False) is False
        and data.get("lgn", False) is False
    )


def _amount(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ParseError(f"携程缺少有效的 {name}")
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise ParseError(f"携程 {name} 不是有效金额") from exc
    if not result.is_finite() or result < 0:
        raise ParseError(f"携程 {name} 不是非负有限金额")
    # Match the recorded frontend's per-passenger price/tax rounding.
    return result.to_integral_value(rounding=ROUND_CEILING)


def _minutes(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise ParseError(f"携程 {name} 缺少有效分钟数")
    return value


def _airport(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ParseError("携程航段缺少实际机场 IATA")
    return value


def _segment(raw: dict[str, Any]) -> Segment:
    flight = raw.get("flightNo")
    carrier = raw.get("marketAirlineCode")
    if not isinstance(carrier, str) or not re.fullmatch(r"[A-Z0-9]{2,3}", carrier):
        raise ParseError("携程航段缺少航司代码")
    if not isinstance(flight, str) or not flight.startswith(carrier) or not re.fullmatch(r"[A-Z0-9]{2,3}\d{1,4}[A-Z]?", flight):
        raise ParseError("携程航段缺少有效航班号")
    try:
        etd = datetime.fromisoformat(raw["departureDateTime"])
        eta = datetime.fromisoformat(raw["arrivalDateTime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError("携程航段缺少有效本地起降时间") from exc
    if etd.tzinfo is not None or eta.tzinfo is not None:
        raise ParseError("携程本地起降时间格式已变化")
    return Segment(flight, carrier, _airport(raw.get("departureAirportCode")), _airport(raw.get("arrivalAirportCode")), etd, eta)


def _price(price: dict[str, Any], leg: LegConfig) -> tuple[Decimal, Decimal] | None:
    # Conditional fares cannot be assumed available to a general passenger.
    if price.get("eligibility") != "NOR":
        return None
    currency = price.get("currencyCode", price.get("currency", "CNY"))
    if currency not in {"CNY", "RMB"}:
        raise ParseError("携程报价币种不是 CNY/RMB")
    cabin = price.get("cabin")
    if not isinstance(cabin, str) or not re.fullmatch(r"@?[YSCF](?:[-|]@?[YSCF])*", cabin):
        raise ParseError("携程报价舱位结构无法识别")
    if any(part.lstrip("@") != CABIN_CODES[leg.cabin_class] for part in re.split(r"[-|]", cabin)):
        return None
    seats = price.get("ticketCount")
    if seats is not None:
        if type(seats) is not int or seats < 0:
            raise ParseError("携程余票提示无效")
        if seats < leg.adult_count + leg.child_count:
            return None
    base = _amount(price.get("adultPrice"), "adultPrice") * leg.adult_count
    tax = _amount(price.get("adultTax"), "adultTax") * leg.adult_count
    if leg.child_count:
        base += _amount(price.get("childPrice"), "childPrice") * leg.child_count
        tax += _amount(price.get("childTax"), "childTax") * leg.child_count
    if base + tax <= 0:
        raise ParseError("携程含税总价必须大于零")
    return base, tax


def _direction(itinerary: dict[str, Any], leg: LegConfig) -> tuple[ItinerarySnapshot, list[Segment]] | None:
    raw_segments = itinerary.get("flightList")
    if not isinstance(raw_segments, list) or not raw_segments or not all(isinstance(raw, dict) for raw in raw_segments):
        raise ParseError("携程行程缺少 flightList")
    segments = [_segment(raw) for raw in raw_segments]
    if any(a.destination_airport_iata != b.origin_airport_iata for a, b in zip(segments, segments[1:])):
        return None  # Ground transfers between airports are not supported.
    if segments[0].origin_airport_iata != leg.origin_airport_iata or segments[-1].destination_airport_iata != leg.destination_airport_iata:
        return None
    if segments[0].etd_local.date() != leg.departure_date or not leg.etd_window.contains(segments[0].etd_local.time()):
        return None
    stops = _minutes(itinerary.get("stopCount"), "stopCount")
    direct = len(segments) == 1 and stops == 0
    if leg.direct_only and not direct:
        return None
    duration = _minutes(itinerary.get("duration"), "duration", positive=True)
    # Use platform durations, not differences between local times in different zones.
    layover = sum(_minutes(raw.get("transferDuration"), "transferDuration") for raw in raw_segments[:-1]) if len(segments) > 1 else None
    if leg.max_layover_minutes is not None and layover is not None and layover > leg.max_layover_minutes:
        return None
    return ItinerarySnapshot(
        flight_signature=_signature(segments), flight_codes=tuple(s.flight_code for s in segments),
        carrier_codes=tuple(dict.fromkeys(s.carrier_code for s in segments)),
        origin_airport_iata=segments[0].origin_airport_iata, destination_airport_iata=segments[-1].destination_airport_iata,
        departure_date=leg.departure_date, etd_local=segments[0].etd_local, eta_local=segments[-1].eta_local,
        duration_minutes=duration, segment_count=len(segments), is_direct=direct,
        connection_airports=tuple(s.destination_airport_iata for s in segments[:-1]), layover_minutes=layover,
    ), segments


def parse_ctrip_payload(
    payload: dict[str, Any], leg: LegConfig, captured_at: datetime
) -> tuple[list[FlightSnapshot], list[FlightSnapshot | None], int, int]:
    check_ctrip_attention(payload)
    if not is_completed_ctrip_payload(payload):
        raise IncompleteResponseError("携程响应尚未报告成功且 data.context.finished == true")
    rows = payload["data"].get("flightItineraryList")
    if not isinstance(rows, list):
        raise ParseError("携程最终响应缺少 flightItineraryList")
    flights: list[FlightSnapshot] = []
    return_leg = _return_leg_config(leg) if leg.is_round_trip else None
    for row in rows:
        if not isinstance(row, dict):
            raise ParseError("携程行程不是对象")
        itineraries = row.get("flightSegments")
        expected_count = 2 if leg.is_round_trip else 1
        if not isinstance(itineraries, list) or len(itineraries) != expected_count or not all(isinstance(i, dict) for i in itineraries):
            raise ParseError("携程行程方向数量与单程/往返配置不一致")
        if leg.is_round_trip:
            if any(type(i.get("segmentNo")) is not int for i in itineraries) or {i["segmentNo"] for i in itineraries} != {1, 2}:
                raise ParseError("携程往返组合必须包含去程 1 和返程 2")
            itineraries = sorted(itineraries, key=lambda i: i["segmentNo"])
        outbound = _direction(itineraries[0], leg)
        inbound = _direction(itineraries[1], return_leg) if return_leg is not None else None
        if outbound is None or (return_leg is not None and inbound is None):
            continue
        itinerary, segments = outbound
        inbound_snapshot, inbound_segments = inbound if inbound is not None else (None, [])
        if inbound_snapshot is not None and inbound_snapshot.etd_local < itinerary.eta_local:
            raise ParseError("携程返程起飞时间早于去程抵达时间")
        prices = row.get("priceList")
        if not isinstance(prices, list) or not all(isinstance(price, dict) for price in prices):
            raise ParseError("携程行程缺少有效 priceList")
        for price in prices:
            amounts = _price(price, leg)
            if amounts is None:
                continue
            base, tax = amounts
            seats = price.get("ticketCount")
            flights.append(FlightSnapshot(
                flight_signature=_signature(segments + inbound_segments),
                flight_codes=tuple(segment.flight_code for segment in segments),
                carrier_codes=tuple(dict.fromkeys(segment.carrier_code for segment in segments)),
                origin_airport_iata=segments[0].origin_airport_iata,
                destination_airport_iata=segments[-1].destination_airport_iata,
                departure_date=leg.departure_date, etd_local=segments[0].etd_local,
                eta_local=segments[-1].eta_local, duration_minutes=itinerary.duration_minutes,
                segment_count=len(segments), is_direct=itinerary.is_direct,
                base_price_cny=base, tax_cny=tax, total_price_cny=base + tax,
                currency_code="CNY", remaining_seats=str(seats) if seats is not None else None,
                free_baggage_piece=None, free_baggage_weight=None,
                source_domain="ctrip.com", captured_at=captured_at,
                connection_airports=tuple(segment.destination_airport_iata for segment in segments[:-1]),
                layover_minutes=itinerary.layover_minutes,
                return_itinerary=inbound_snapshot,
                seat_availability=SeatAvailability(count_hint=seats) if seats is not None else None,
                # Only a small business allowlist is persisted, never routeSearchToken,
                # searchCriteriaToken, policyToken or request headers.
                raw_item={"provider": "ctrip", "eligibility": "NOR", "cabin": price["cabin"],
                          "adultPrice": price["adultPrice"], "adultTax": price["adultTax"],
                          "childPrice": price.get("childPrice"), "childTax": price.get("childTax"),
                          "has_service_package": bool(price.get("servicePackage")),
                          "product_name": (price.get("productType") or {}).get("name")},
            ))
    ranked, preferred, eligible = rank_flights(flights, leg)
    return ranked, preferred, len(rows), eligible
