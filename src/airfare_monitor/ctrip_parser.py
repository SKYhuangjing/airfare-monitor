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
    base = _amount(price.get("adultPrice"), "adultPrice")
    tax = _amount(price.get("adultTax"), "adultTax")
    if base + tax <= 0:
        raise ParseError("携程含税总价必须大于零")
    return base, tax


def _parse_baggage_content(content: str | None) -> tuple[int | None, str | None]:
    if not content:
        return None, None
    text = content.strip()
    if not text:
        return None, None
    if "无免费" in text or "无行李" in text or text in ("0", "0件", "0kg", "0KG"):
        return 0, "0kg"
    pieces = None
    m_piece = re.search(r"每人(\d+)件", text)
    if m_piece:
        pieces = int(m_piece.group(1))
    weight = None
    m_weight = re.search(r"(?:每件|总重不超过|不超过)?(\d+(?:\.\d+)?)\s*(?:公斤|KG|kg)", text)
    if m_weight:
        weight = f"{m_weight.group(1)}kg"
    return pieces, weight


def _extract_ctrip_baggage(baggage: Any, is_round_trip: bool) -> tuple[int | None, str | None]:
    if not isinstance(baggage, dict):
        return None, None
    data_list = baggage.get("dataList")
    if not isinstance(data_list, list) or not data_list:
        return None, None

    by_seg: dict[int, list[dict[str, Any]]] = {}
    for item in data_list:
        if isinstance(item, dict):
            seg_no = item.get("segmentNo", 1)
            by_seg.setdefault(seg_no, []).append(item)

    def extract_seg(items: list[dict[str, Any]]) -> tuple[int | None, str | None]:
        for it in items:
            adult = it.get("adultBaggage")
            if isinstance(adult, dict):
                checked = adult.get("checkedBaggage")
                if isinstance(checked, dict):
                    content = checked.get("baggageContent")
                    if content:
                        return _parse_baggage_content(str(content))
        return None, None

    out_piece, out_weight = extract_seg(by_seg.get(1, []))
    if not is_round_trip:
        return out_piece, out_weight

    in_piece, in_weight = extract_seg(by_seg.get(2, []))
    if in_piece is None and in_weight is None:
        return out_piece, out_weight

    if out_piece == in_piece and out_weight == in_weight:
        return out_piece, out_weight

    if (out_piece == 0 or out_weight in ("0", "0kg")) and (in_piece == 0 or in_weight in ("0", "0kg")):
        return 0, "0kg"

    def desc(p: int | None, w: str | None) -> str:
        if p == 0 or w in ("0", "0kg"):
            return "无额度"
        parts: list[str] = []
        if p is not None and p > 0:
            parts.append(f"{p}件")
        if w and w not in ("0", "0kg"):
            parts.append(w)
        return "".join(parts) or "未知"

    out_desc = desc(out_piece, out_weight)
    in_desc = desc(in_piece, in_weight)
    return None, f"去程{out_desc}/返程{in_desc}"


def _direction(itinerary: dict[str, Any], leg: LegConfig) -> tuple[ItinerarySnapshot, list[Segment]] | None:
    raw_segments = itinerary.get("flightList")
    if not isinstance(raw_segments, list) or not raw_segments or not all(isinstance(raw, dict) for raw in raw_segments):
        raise ParseError("携程行程缺少 flightList")
    segments = [_segment(raw) for raw in raw_segments]
    if any(a.destination_airport_iata != b.origin_airport_iata for a, b in zip(segments, segments[1:])):
        return None  # Ground transfers between airports are not supported.
    if (
        segments[0].origin_airport_iata not in leg.allowed_origin_airports
        or segments[-1].destination_airport_iata not in leg.allowed_destination_airports
    ):
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
        parsed_prices: list[tuple[dict[str, Any], Decimal, Decimal, int | None, int | None, str | None]] = []
        for price in prices:
            amounts = _price(price, leg)
            if amounts is None:
                continue
            base, tax = amounts
            seats = price.get("ticketCount")
            piece, weight = _extract_ctrip_baggage(price.get("baggage"), leg.is_round_trip)
            parsed_prices.append((price, base, tax, seats, piece, weight))

        luggage_prices = [
            base + tax for _, base, tax, _, piece, weight in parsed_prices
            if (piece is not None and piece > 0) or (weight and weight not in ("0", "0kg") and "无" not in weight)
        ]
        min_luggage_price = min(luggage_prices) if luggage_prices else None

        for price, base, tax, seats, piece, weight in parsed_prices:
            total = base + tax
            is_zero = (piece == 0) or (weight in ("0", "0kg"))
            inclusive_price = (
                min_luggage_price
                if (is_zero and min_luggage_price is not None and min_luggage_price > total)
                else None
            )
            flights.append(FlightSnapshot(
                flight_signature=_signature(segments + inbound_segments),
                flight_codes=tuple(segment.flight_code for segment in segments),
                carrier_codes=tuple(dict.fromkeys(segment.carrier_code for segment in segments)),
                origin_airport_iata=segments[0].origin_airport_iata,
                destination_airport_iata=segments[-1].destination_airport_iata,
                departure_date=leg.departure_date, etd_local=segments[0].etd_local,
                eta_local=segments[-1].eta_local, duration_minutes=itinerary.duration_minutes,
                segment_count=len(segments), is_direct=itinerary.is_direct,
                base_price_cny=base, tax_cny=tax, total_price_cny=total,
                currency_code="CNY", remaining_seats=str(seats) if seats is not None else None,
                free_baggage_piece=piece, free_baggage_weight=weight,
                source_domain="ctrip.com", captured_at=captured_at,
                connection_airports=tuple(segment.destination_airport_iata for segment in segments[:-1]),
                layover_minutes=itinerary.layover_minutes,
                return_itinerary=inbound_snapshot,
                seat_availability=SeatAvailability(count_hint=seats) if seats is not None else None,
                luggage_inclusive_price_cny=inclusive_price,
                # Only a small business allowlist is persisted, never routeSearchToken,
                # searchCriteriaToken, policyToken or request headers.
                raw_item={"provider": "ctrip", "eligibility": "NOR", "cabin": price["cabin"],
                          "adultPrice": price["adultPrice"], "adultTax": price["adultTax"],
                          "childPrice": price.get("childPrice"), "childTax": price.get("childTax"),
                          "has_service_package": bool(price.get("servicePackage")),
                          "product_name": (price.get("productType") or {}).get("name"),
                          "baggage": price.get("baggage")},
            ))
    ranked, preferred, eligible = rank_flights(flights, leg)
    return ranked, preferred, len(rows), eligible
