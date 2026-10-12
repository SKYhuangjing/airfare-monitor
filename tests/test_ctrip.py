from __future__ import annotations

import itertools
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from airfare_monitor.collector import QunarBrowserSession
from airfare_monitor.config import BrowserSettings, load_routes
from airfare_monitor.ctrip_parser import is_completed_ctrip_payload, parse_ctrip_payload
from airfare_monitor.ctrip_search import CtripSearchCapture, SEARCH_PREFIX, build_ctrip_search_url
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.errors import CollectionError, ConfigError, IncompleteResponseError, ManualAttentionRequired, ParseError
from airfare_monitor.market import resolve_provider, source_label
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.storage import SQLiteStore

NOW = datetime(2026, 10, 12, 13, 45)
FIXTURE = Path(__file__).parent / "fixtures" / "ctrip_final_sanitized.json"
BATCH = "https://flights.ctrip.com" + SEARCH_PREFIX + "batchSearch"
PULL = "https://flights.ctrip.com" + SEARCH_PREFIX + "pull/search-a"


def leg(**kwargs):
    value = LegConfig("ctrip-test", True, "PVG", "CJU", date(2026, 10, 14),
                      EtdWindow(time(0), time(23, 59)), False, Decimal("400"), 200, 1, 0, "economy")
    return replace(value, **kwargs)


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def request(**kwargs):
    value = {"transactionID": "current-transaction", "adultCount": 1, "childCount": 0,
             "infantCount": 0, "flightWay": "S", "scope": "i", "segmentNo": 1,
             "cabin": "Y_S", "flightSegments": [{"departureCityCode": "SHA", "arrivalCityCode": "CJU", "departureDate": "2026-10-14"}]}
    value.update(kwargs)
    return value


def page_criteria():
    return {"transactionID": "current-transaction", **request()["flightSegments"][0]}


def payload(*, finished=True, rows=None, search_id="search-a"):
    value = fixture()
    value["data"]["context"].update(finished=finished, searchId=search_id)
    if rows is not None:
        value["data"]["flightItineraryList"] = rows
    return value


class CtripParserTests(unittest.TestCase):
    def test_observed_final_list_has_18_itineraries_and_9_direct(self):
        flights, _, observed, eligible = parse_ctrip_payload(fixture(), leg(), NOW)
        self.assertEqual((observed, eligible, len(flights)), (18, 18, 18))
        self.assertEqual(sum(f.is_direct for f in flights), 9)
        self.assertEqual(flights[0].total_price_cny, Decimal("330"))
        self.assertTrue(all(f.source_domain == "ctrip.com" for f in flights))
        self.assertTrue(all(f.currency_code == "CNY" for f in flights))
        self.assertTrue(all(f.total_price_cny == f.base_price_cny + f.tax_cny for f in flights))

    def test_city_alias_does_not_accept_pudong_for_hongqiao(self):
        flights, _, observed, eligible = parse_ctrip_payload(fixture(), leg(origin_airport_iata="SHA"), NOW)
        self.assertEqual((observed, eligible, flights), (18, 0, []))

    def test_direct_time_window_and_layover_filters(self):
        flights, _, _, eligible = parse_ctrip_payload(fixture(), leg(direct_only=True), NOW)
        self.assertEqual(eligible, 9)
        self.assertTrue(all(f.is_direct for f in flights))
        flights, _, _, _ = parse_ctrip_payload(fixture(), leg(max_layover_minutes=60), NOW)
        self.assertTrue(all(f.layover_minutes is None or f.layover_minutes <= 60 for f in flights))
        flights, _, _, _ = parse_ctrip_payload(fixture(), leg(etd_window=EtdWindow(time(8), time(10))), NOW)
        self.assertTrue(all(time(8) <= f.etd_local.time() <= time(10) for f in flights))

    def test_duration_uses_platform_minutes_across_time_zones(self):
        value = payload(rows=[fixture()["data"]["flightItineraryList"][0]])
        flights, _, _, _ = parse_ctrip_payload(value, leg(), NOW)
        self.assertEqual(flights[0].duration_minutes, 395)
        self.assertEqual(flights[0].layover_minutes, 200)

    def test_group_total_counts_adults_children_and_tax(self):
        value = payload(rows=[fixture()["data"]["flightItineraryList"][0]])
        value["data"]["flightItineraryList"][0]["priceList"] = [
            {"adultPrice": 100, "adultTax": 50, "childPrice": 80, "childTax": 20,
             "eligibility": "NOR", "cabin": "Y", "ticketCount": 9}]
        flights, _, _, _ = parse_ctrip_payload(value, leg(adult_count=2, child_count=1), NOW)
        self.assertEqual(flights[0].base_price_cny, Decimal("280"))
        self.assertEqual(flights[0].tax_cny, Decimal("120"))
        self.assertEqual(flights[0].total_price_cny, Decimal("400"))

    def test_restricted_cabin_or_insufficient_inventory_is_not_a_low_price(self):
        row = fixture()["data"]["flightItineraryList"][0]
        baseline = {"adultPrice": 1, "adultTax": 1, "eligibility": "NOR", "cabin": "Y"}
        row["priceList"] = [dict(baseline, eligibility="STU"), dict(baseline, cabin="@C-Y"),
                            dict(baseline, ticketCount=0), dict(baseline, adultPrice=100)]
        flights, _, _, _ = parse_ctrip_payload(payload(rows=[row]), leg(), NOW)
        self.assertEqual(flights[0].total_price_cny, Decimal("101"))

    def test_decimal_prices_follow_frontend_rounding_before_passenger_totals(self):
        row = fixture()["data"]["flightItineraryList"][0]
        row["priceList"] = [{"adultPrice": "100.1", "adultTax": "50.1", "eligibility": "NOR", "cabin": "Y"}]
        flights, _, _, _ = parse_ctrip_payload(payload(rows=[row]), leg(adult_count=2), NOW)
        self.assertEqual(flights[0].total_price_cny, Decimal("304"))

    def test_malformed_security_flags_cannot_be_completed(self):
        value = fixture()
        value["data"]["needUserLogin"] = 1
        self.assertFalse(is_completed_ctrip_payload(value))
        with self.assertRaises(ParseError):
            parse_ctrip_payload(value, leg(), NOW)

    def test_partial_unknown_or_invalid_amount_fails_closed(self):
        with self.assertRaises(IncompleteResponseError):
            parse_ctrip_payload(payload(finished=False), leg(), NOW)
        value = fixture()
        del value["data"]["flightItineraryList"]
        with self.assertRaises(ParseError):
            parse_ctrip_payload(value, leg(), NOW)
        for changes in [{"adultTax": None}, {"adultPrice": "NaN"}, {"adultPrice": -1},
                        {"adultPrice": True}, {"currencyCode": "USD"}]:
            value = fixture()
            value["data"]["flightItineraryList"][0]["priceList"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ParseError):
                parse_ctrip_payload(value, leg(), NOW)

    def test_verification_login_and_restricted_response_require_attention(self):
        for key in ["needUserLogin", "lgn", "showAuthCode", "flag"]:
            value = fixture()
            target = value["data"]["context"] if key in {"showAuthCode", "flag"} else value["data"]
            target[key] = 1 if key == "flag" else True
            self.assertFalse(is_completed_ctrip_payload(value))
            with self.assertRaises(ManualAttentionRequired):
                parse_ctrip_payload(value, leg(), NOW)

    def test_secrets_never_survive_as_snapshot_metadata(self):
        value = fixture()
        value["data"]["context"]["searchCriteriaToken"] = "PRIVATE_SENTINEL"
        for row in value["data"]["flightItineraryList"]:
            for price in row["priceList"]:
                price["routeSearchToken"] = "PRIVATE_SENTINEL"
                price.setdefault("servicePackage", {})["policyToken"] = "PRIVATE_SENTINEL"
        flights, _, _, _ = parse_ctrip_payload(value, leg(), NOW)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps([f.raw_item for f in flights]))


class CtripCaptureTests(unittest.TestCase):
    def test_terminal_list_replaces_provisional_and_allows_search_id_rotation(self):
        capture = CtripSearchCapture(leg(), "current-transaction")
        self.assertIsNone(capture.accept(BATCH, request(), payload(finished=False)))
        interim = payload(finished=False)
        del interim["data"]["flightItineraryList"]
        for _ in range(5):
            self.assertIsNone(capture.accept(PULL, request(), interim))
        final = capture.accept(PULL, request(), payload(rows=fixture()["data"]["flightItineraryList"][:2], search_id="rotated-final"))
        self.assertEqual(len(final["data"]["flightItineraryList"]), 2)
        self.assertTrue(final["data"]["context"]["finished"])

    def test_only_current_route_date_passengers_transaction_and_poll_chain(self):
        for changed in [request(transactionID="old"), request(adultCount=2), request(childCount=1),
                        request(infantCount=1), request(cabin="C_F"), request(flightWay="D"),
                        request(flightSegments=[{"departureCityCode": "SHA", "arrivalCityCode": "CJU", "departureDate": "2026-10-15"}]),
                        request(flightSegments=[{"departureCityCode": "SHA", "arrivalCityCode": "TYO", "departureDate": "2026-10-14"}])]:
            capture = CtripSearchCapture(leg(), "current-transaction")
            self.assertIsNone(capture.accept(BATCH, changed, payload()))
        capture = CtripSearchCapture(leg(), "current-transaction")
        self.assertIsNone(capture.accept(PULL, request(), payload()))
        self.assertIsNone(capture.accept(BATCH.replace("flights.ctrip.com", "example.test"), request(), payload()))

    def test_final_empty_or_missing_list_retains_previous_like_frontend(self):
        for omitted in [True, False]:
            capture = CtripSearchCapture(leg(), "current-transaction")
            capture.accept(BATCH, request(), payload(finished=False))
            final = payload(rows=[])
            if omitted:
                del final["data"]["flightItineraryList"]
            self.assertEqual(len(capture.accept(PULL, request(), final)["data"]["flightItineraryList"]), 18)
        capture = CtripSearchCapture(leg(), "current-transaction")
        final = capture.accept(BATCH, request(), payload(rows=[]))
        self.assertEqual(parse_ctrip_payload(final, leg(), NOW)[2:], (0, 0))

    def test_no_list_is_schema_failure_and_provisional_is_not_success(self):
        capture = CtripSearchCapture(leg(), "current-transaction")
        value = payload()
        del value["data"]["flightItineraryList"]
        with self.assertRaises(ParseError):
            capture.accept(BATCH, request(), value)

    def test_current_http_error_is_failure_and_page_city_normalization_is_bound(self):
        capture = CtripSearchCapture(leg(destination_airport_iata="NRT"), "current-transaction", city_pair=("SHA", "TYO"))
        self.assertIsNone(capture.accept(BATCH, request(), payload()))
        normalized = request(flightSegments=[{"departureCityCode": "SHA", "arrivalCityCode": "TYO", "departureDate": "2026-10-14"}])
        with self.assertRaises(CollectionError):
            capture.accept(BATCH, normalized, payload(), http_status=503)


class CtripIntegrationOfflineTests(unittest.TestCase):
    def test_verification_check_tolerates_drission_falsey_missing_body(self):
        class MissingBody:
            def __bool__(self):
                return False

            @property
            def text(self):
                raise AssertionError("Drission NoneElement text must not be accessed")

        session = QunarBrowserSession(Mock())
        session.tab = Mock(url="https://flights.ctrip.com", title="Flight search")
        session.tab.ele.return_value = MissingBody()
        self.assertFalse(session._verification_visible())

    def test_collector_waits_for_final_and_persists_only_business_metadata(self):
        def packet(url, value):
            return SimpleNamespace(url=url, request=SimpleNamespace(postData=request()),
                                   response=SimpleNamespace(body=value, status=200))
        with tempfile.TemporaryDirectory() as tmp:
            settings = BrowserSettings(False, Path(tmp) / "data/browser-profile", 9333, 45, 30, 2, "https://example.test")
            session = QunarBrowserSession(settings)
            session.start = Mock()
            session.tab = Mock()
            session.tab.run_js.return_value = page_criteria()
            session.tab.listen.wait.side_effect = [packet(BATCH, payload(finished=False)), packet(PULL, payload(search_id="rotated"))]
            result = session.collect(leg(top_n=3), now=lambda: NOW)
            self.assertTrue(result.completed_response)
            self.assertEqual((len(result.flights), len(result.candidate_flights), result.observed_count), (3, 18, 18))
            session.tab.listen.stop.assert_called_once()
            self.assertEqual(session.tab.mock_calls[0][0], "listen.start")
            self.assertEqual(session.tab.mock_calls[1][0], "get")
            self.assertNotIn("Token", json.dumps(result.raw_response))

    def test_collector_times_out_without_explicit_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = QunarBrowserSession(BrowserSettings(False, Path(tmp), 9333, 45, 5, 2, "https://example.test"))
            session.start = Mock()
            session.tab = Mock()
            session.tab.run_js.return_value = page_criteria()
            session.tab.listen.wait.return_value = False
            session._verification_visible = Mock(return_value=False)
            with patch("airfare_monitor.collector.time.monotonic", side_effect=itertools.count()), self.assertRaises(IncompleteResponseError):
                session.collect(leg(), now=lambda: NOW)
            session.tab.listen.stop.assert_called_once()

    def test_provider_config_roundtrip_and_legacy_paths(self):
        self.assertEqual(resolve_provider(leg()), "ctrip")
        self.assertEqual(resolve_provider(leg(provider="qunar")), "qunar")
        self.assertEqual(resolve_provider(leg(return_date=date(2026, 10, 20))), "ctrip")
        self.assertEqual(resolve_provider(leg(destination_airport_iata="CAN")), "tongcheng")
        self.assertEqual(resolve_provider(leg(provider="ctrip", return_date=date(2026, 10, 20))), "ctrip")
        self.assertEqual(resolve_provider(leg(provider="qunar", return_date=date(2026, 10, 20))), "qunar")
        self.assertIn("携程", source_label(leg()))
        self.assertIn("oneway-sha-cju?depdate=2026-10-14&cabin=y_s&adult=1&child=0&infant=0", build_ctrip_search_url(leg()))
        with tempfile.TemporaryDirectory() as tmp:
            repo = RouteRepository(Path(tmp) / "routes.yaml")
            repo.save([leg(provider="qunar")])
            self.assertEqual(repo.load()[0].provider, "qunar")
            repo.path.write_text(repo.path.read_text(encoding="utf-8").replace("provider: qunar", "provider: invalid"), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_routes(repo.path)

    def test_storage_records_platform_and_does_not_mix_price_references(self):
        flights, _, _, _ = parse_ctrip_payload(fixture(), leg(), NOW)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "prices.sqlite3")
            store.initialize()
            result = LegResult(leg(), LegStatus.SUCCESS, NOW, flights=flights, completed_response=True)
            store.save_report(RunReport("ctrip-run", NOW, NOW, RunStatus.SUCCESS, [result]))
            self.assertEqual(store.latest_leg_results([leg().id])[0]["collection_provider"], "ctrip")
            self.assertEqual(store.previous_minimum(leg().id, provider="ctrip"), Decimal("330"))
            self.assertIsNone(store.previous_minimum(leg().id, provider="qunar"))
            self.assertIsNone(store.flight_price_reference(leg().id, flights[0].flight_signature, provider="qunar").previous_total_price_cny)

    def test_legacy_database_upgrade_preserves_prices_without_guessing_provider(self):
        flights, _, _, _ = parse_ctrip_payload(fixture(), leg(), NOW)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.sqlite3"
            store = SQLiteStore(path)
            store.initialize()
            result = LegResult(leg(), LegStatus.SUCCESS, NOW, flights=flights, completed_response=True)
            store.save_report(RunReport("old-run", NOW, NOW, RunStatus.SUCCESS, [result]))
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("ALTER TABLE leg_results DROP COLUMN collection_provider")
                db.execute("PRAGMA user_version = 1")
            store.initialize()
            self.assertEqual(store.latest_leg_results([leg().id])[0]["collection_provider"], "unknown")
            self.assertEqual(store.previous_minimum(leg().id), Decimal("330"))
            self.assertIsNone(store.previous_minimum(leg().id, provider="ctrip"))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM flight_snapshots").fetchone()[0], 18)


if __name__ == "__main__":
    unittest.main()
