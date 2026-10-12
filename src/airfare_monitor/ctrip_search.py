"""Ctrip URL construction and correlation of naturally observed browser packets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlencode, urlsplit

from .ctrip_parser import CABIN_SEARCH, check_ctrip_attention, city_code, is_completed_ctrip_payload
from .errors import CollectionError, ParseError
from .models import LegConfig

SEARCH_PREFIX = "/international/search/api/search/"


def build_ctrip_search_url(leg: LegConfig) -> str:
    dates = leg.departure_date.isoformat()
    path = "oneway"
    params = {"cabin": CABIN_SEARCH[leg.cabin_class].lower(),
              "adult": leg.adult_count, "child": leg.child_count, "infant": 0}
    if leg.return_date is not None:
        dates += "_" + leg.return_date.isoformat()
        path = "round"
        params["isbuildup"] = 1  # Native complete-combination mode, not outbound selection.
    query = urlencode({"depdate": dates, **params})
    suffix = "0" if leg.is_round_trip else ""
    return (f"https://flights.ctrip.com/online/list/{path}-"
            f"{city_code(leg.origin_airport_iata).lower()}-{city_code(leg.destination_airport_iata).lower()}{suffix}?{query}")


class CtripSearchCapture:
    """Follow a search transaction, including server-side searchId rotation.

Only the latest list is kept. The frontend retains its previous list when a
poll omits the list or sends an empty list; an explicitly finished poll is still
required. Request/response credentials remain transient in memory.
"""

    def __init__(self, leg: LegConfig, transaction_id: str, *, city_pair: tuple[str, str] | None = None):
        if not transaction_id:
            raise CollectionError("携程页面缺少本轮搜索标识")
        self.leg = leg
        self.transaction_id = transaction_id
        self.city_pair = city_pair or (city_code(leg.origin_airport_iata), city_code(leg.destination_airport_iata))
        self.search_ids: set[str] = set()
        self.latest_rows: list[dict] | None = None

    def _matches(self, request: object) -> bool:
        if not isinstance(request, dict) or request.get("transactionID") != self.transaction_id:
            return False
        if any(type(request.get(key)) is not int or request[key] != count for key, count in (
            ("adultCount", self.leg.adult_count), ("childCount", self.leg.child_count), ("infantCount", 0)
        )):
            return False
        expected_way, expected_number = ("D", 0) if self.leg.is_round_trip else ("S", 1)
        if (request.get("flightWay") != expected_way or request.get("scope") != "i"
                or type(request.get("segmentNo")) is not int or request["segmentNo"] != expected_number):
            return False
        if request.get("cabin") != CABIN_SEARCH[self.leg.cabin_class]:
            return False
        segments = request.get("flightSegments")
        expected = [(self.city_pair[0], self.city_pair[1], self.leg.departure_date)]
        if self.leg.return_date is not None:
            expected.append((self.city_pair[1], self.city_pair[0], self.leg.return_date))
        if not isinstance(segments, list) or len(segments) != len(expected):
            return False
        return all(isinstance(segment, dict)
                   and segment.get("departureDate") == day.isoformat()
                   and segment.get("departureCityCode") == origin
                   and segment.get("arrivalCityCode") == destination
                   for segment, (origin, destination, day) in zip(segments, expected))

    def accept(self, url: str, request: object, payload: object, *, http_status: int = 200) -> dict[str, Any] | None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "flights.ctrip.com" or not self._matches(request):
            return None
        path = parsed.path
        batch = path == SEARCH_PREFIX + "batchSearch"
        pull_id = path.removeprefix(SEARCH_PREFIX + "pull/") if path.startswith(SEARCH_PREFIX + "pull/") else None
        if not batch and (not pull_id or pull_id not in self.search_ids):
            return None
        if http_status != 200:
            raise CollectionError("携程本轮搜索请求未成功")
        if not isinstance(payload, dict):
            raise ParseError("未能读取携程搜索响应正文，请检查网络监听缓存或响应格式")
        check_ctrip_attention(payload)
        if type(payload.get("status")) is not int or payload["status"] != 0:
            raise CollectionError("携程本轮搜索返回业务错误")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("context"), dict):
            raise ParseError("携程搜索响应缺少 context")
        context = data["context"]
        search_id = context.get("searchId")
        if not isinstance(search_id, str) or not search_id:
            raise ParseError("携程搜索响应缺少 searchId")
        self.search_ids.add(search_id)
        if "flightItineraryList" in data:
            rows = data["flightItineraryList"]
            if not isinstance(rows, list):
                raise ParseError("携程 flightItineraryList 不是列表")
            if rows or self.latest_rows is None:
                self.latest_rows = rows
        if is_completed_ctrip_payload(payload):
            if self.latest_rows is None:
                raise ParseError("携程搜索结束但没有可识别的结果列表")
            final = {"status": 0, "data": {"context": {
                "finished": True, "flag": context.get("flag", 0),
                "showAuthCode": context.get("showAuthCode", False)},
                "needUserLogin": data.get("needUserLogin", False), "lgn": data.get("lgn", False),
                "flightItineraryList": deepcopy(self.latest_rows)}}
            return final
        return None
