"""Command-line entry points."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .app_paths import AppPaths
from .config import AppSettings, load_local_env, load_routes, load_settings
from .errors import ConfigError
from .models import LegConfig
from .scheduler import run_forever
from .service import MonitorService

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="个人多航程航价监控")
    parser.add_argument("--user-root", help="数据根目录；缺省与桌面客户端共用同一平台用户目录")
    parser.add_argument("--routes", help="航程配置；缺省 <数据根>/config/routes.yaml")
    parser.add_argument("--settings", help="运行配置；缺省 <数据根>/config/settings.yaml")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="仅校验配置，不启动浏览器")
    once = subparsers.add_parser("run-once", help="采集一次并生成 Excel")
    once.add_argument("--send-mail", action="store_true", help="按配置发送真实邮件")
    subparsers.add_parser("daemon", help="按配置间隔运行并发送邮件")
    opener = subparsers.add_parser("open", help="在默认浏览器打开航程对应来源网站的搜索结果页")
    opener.add_argument("route_id", nargs="?", help="航程 ID；缺省列出全部可选航程")
    price = subparsers.add_parser("price", help="查看各航程最近价格")
    price.add_argument("--leg", help="只看指定航程 ID")
    price.add_argument("--last", type=int, default=1, help="每个航程显示最近 N 次结果（默认 1）")
    history = subparsers.add_parser("history", help="查看价格历史明细")
    history.add_argument("--leg", help="只看指定航程 ID")
    history.add_argument("--hours", type=int, default=24, help="回溯小时数（默认 24）")
    routes_parser = subparsers.add_parser("routes", help="航程管理（列表/启停）")
    routes_sub = routes_parser.add_subparsers(dest="routes_command", required=True)
    routes_sub.add_parser("list", help="列出全部航程")
    enable_cmd = routes_sub.add_parser("enable", help="启用航程（下一轮生效）")
    enable_cmd.add_argument("route_id", help="航程 ID")
    disable_cmd = routes_sub.add_parser("disable", help="停用航程（下一轮生效）")
    disable_cmd.add_argument("route_id", help="航程 ID")
    subparsers.add_parser("status", help="运行状态总览（锁/最近轮次/各航程最新价）")
    subparsers.add_parser("doctor", help="环境自检（配置/浏览器/数据库/签名身份）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        return _dispatch(args)
    except ConfigError as exc:
        # 配置类错误打印单行中文提示，不抛裸 traceback（试用验收 F2）。
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    paths = AppPaths.discover(user_root=args.user_root)
    load_local_env(Path.cwd() / ".env")
    routes_path = Path(args.routes) if args.routes else paths.routes_path
    settings_path = Path(args.settings) if args.settings else paths.settings_path
    # project_root 留空：settings.yaml 里的相对路径（data/、outputs/ 等）
    # 锚定到该配置文件所在的 <数据根>，与桌面客户端的解析口径一致。
    # allow_empty 与桌面客户端对齐：0 条启用时 daemon 允许空转等待配置，
    # run-once 会在执行期给出明确报错（试用验收 F3）。
    legs = load_routes(routes_path, allow_empty=True)
    settings = load_settings(settings_path)
    if args.command == "validate":
        enabled = [leg for leg in legs if leg.enabled]
        round_trips = sum(leg.is_round_trip for leg in enabled)
        print(f"配置有效：{len(enabled)} 个启用行程，其中 {round_trips} 组往返")
        return 0

    if args.command == "run-once":
        if not any(leg.enabled for leg in legs):
            print("没有启用的航程；请先在配置或桌面客户端中启用至少一条。", file=sys.stderr)
            return 2
        service = MonitorService(legs, settings)
        _attach_desktop_mail(service, settings_path)
        try:
            report, workbook = service.run_once(send_email=args.send_mail)
            print(f"运行完成：{report.status}；Excel：{workbook}")
            return 0
        finally:
            service.close()

    if args.command == "open":
        return _open_route(legs, settings, args.route_id)

    if args.command == "price":
        return _price_command(settings, legs, leg_id=args.leg, last=args.last)

    if args.command == "history":
        return _history_command(settings, legs, leg_id=args.leg, hours=args.hours)

    if args.command == "routes":
        return _routes_command(routes_path, legs, args.routes_command, getattr(args, "route_id", None))

    if args.command == "status":
        return _status_command(settings, legs, routes_path)

    if args.command == "doctor":
        return _doctor_command(settings, routes_path, settings_path)

    lock_path = settings.storage.sqlite_path.parent / "airfare-monitor.lock"
    run_forever(_daemon_cycle_factory(routes_path, settings_path), lock_path)
    return 0


def _open_route(legs: list[LegConfig], settings: AppSettings, route_id: str | None) -> int:
    import webbrowser

    from .search_link import search_site_label, search_url_for

    if route_id is None:
        if not legs:
            print("还没有任何航程；先用桌面客户端或编辑 routes.yaml 添加。")
            return 0
        print("可选航程（airfare-monitor open <ID>）：")
        for leg in legs:
            arrow = "⇄" if leg.is_round_trip else "→"
            state = "启用" if leg.enabled else "暂停"
            print(
                f"  {leg.id:<24} {leg.origin_name_zh or leg.origin_airport_iata} {arrow} "
                f"{leg.destination_name_zh or leg.destination_airport_iata}  "
                f"{leg.departure_date.isoformat()}  {search_site_label(leg)}  {state}"
            )
        return 0
    leg = next((item for item in legs if item.id == route_id), None)
    if leg is None:
        print(f"未找到航程：{route_id}（不带参数运行 airfare-monitor open 查看全部 ID）")
        return 1
    url = search_url_for(leg, settings)
    print(f"{search_site_label(leg)}：{url}")
    webbrowser.open(url)
    return 0


def _price_command(settings: AppSettings, legs: list[LegConfig], *, leg_id: str | None, last: int) -> int:
    from .storage import SQLiteStore

    selected = [leg for leg in legs if leg_id is None or leg.id == leg_id]
    if not selected:
        print(f"未找到航程：{leg_id}")
        return 1
    store = SQLiteStore(settings.storage.sqlite_path)
    latest = {str(row["leg_id"]): row for row in store.latest_leg_results([leg.id for leg in selected])}
    since_rows = {} if last <= 1 else _recent_rows(store, since_hours=24 * max(last, 1) * 3)
    for leg in selected:
        row = latest.get(leg.id)
        arrow = "⇄" if leg.is_round_trip else "→"
        header = (
            f"{leg.id}  {leg.origin_name_zh or leg.origin_airport_iata} {arrow} "
            f"{leg.destination_name_zh or leg.destination_airport_iata}  {leg.departure_date.isoformat()}"
        )
        print(header)
        if row is None:
            print("  尚无结果（等下一轮采集，或用 run-once 立即跑一次）")
            continue
        for line in _result_lines(row, since_rows.get(leg.id, []), last):
            print(f"  {line}")
    return 0


def _history_command(settings: AppSettings, legs: list[LegConfig], *, leg_id: str | None, hours: int) -> int:
    from .storage import SQLiteStore

    selected = [leg for leg in legs if leg_id is None or leg.id == leg_id]
    if not selected:
        print(f"未找到航程：{leg_id}")
        return 1
    store = SQLiteStore(settings.storage.sqlite_path)
    rows = _recent_rows(store, since_hours=hours)
    total = 0
    for leg in selected:
        leg_rows = rows.get(leg.id, [])
        total += len(leg_rows)
        arrow = "⇄" if leg.is_round_trip else "→"
        print(f"{leg.id}  {leg.origin_name_zh or leg.origin_airport_iata} {arrow} "
              f"{leg.destination_name_zh or leg.destination_airport_iata}  近 {hours} 小时 {len(leg_rows)} 条")
        for row in leg_rows:
            status = str(row.get("status", ""))
            price = row.get("minimum_total_price_cny")
            price_text = f"¥{Decimal(str(price)):,.0f}" if price not in (None, "") else "—"
            previous = row.get("previous_min_total_cny")
            delta = ""
            if price not in (None, "") and previous not in (None, ""):
                diff = Decimal(str(price)) - Decimal(str(previous))
                delta = f"  较上次 {'+' if diff >= 0 else ''}{diff:,.0f}"
            print(f"  {str(row.get('captured_at', ''))[:16]}  {price_text}{delta}  {status}")
    if total == 0:
        print(f"近 {hours} 小时没有记录（历史只保留有效入库轮次）。")
    return 0


def _routes_command(routes_path: Path, legs: list[LegConfig], command: str, route_id: str | None) -> int:
    from .search_link import search_site_label

    if command == "list":
        if not legs:
            print("还没有任何航程；用桌面客户端向导添加最方便。")
            return 0
        for leg in legs:
            arrow = "⇄" if leg.is_round_trip else "→"
            state = "启用" if leg.enabled else "暂停"
            print(
                f"  {leg.id:<24} {leg.origin_name_zh or leg.origin_airport_iata} {arrow} "
                f"{leg.destination_name_zh or leg.destination_airport_iata}  "
                f"{leg.departure_date.isoformat()}  {search_site_label(leg)}  {state}"
            )
        return 0

    from .desktop_app.route_repository import RouteRepository

    leg = next((item for item in legs if item.id == route_id), None)
    if leg is None:
        print(f"未找到航程：{route_id}（airfare-monitor routes list 查看全部 ID）")
        return 1
    desired = command == "enable"
    if leg.enabled == desired:
        print(f"{route_id} 已经是{'启用' if desired else '暂停'}状态，无需修改。")
        return 0
    RouteRepository(routes_path).save([replace(leg, enabled=desired) if item.id == route_id else item for item in legs])
    print(f"{route_id} 已{'启用' if desired else '停用'}；GUI 或 daemon 下一轮自动生效。")
    return 0


def _status_command(settings: AppSettings, legs: list[LegConfig], routes_path: Path) -> int:
    from .scheduler import AlreadyRunningError, ProcessLock
    from .storage import SQLiteStore

    print(f"数据根：{routes_path.resolve().parent.parent}")
    lock_path = settings.storage.sqlite_path.parent / "airfare-monitor.lock"
    lock = ProcessLock(lock_path)
    try:
        lock.acquire()
        print("调度锁：空闲（GUI / daemon 均未在运行）")
    except AlreadyRunningError:
        print("调度锁：占用中（GUI 或 daemon 正在运行，两者互斥）")
    finally:
        lock.release()

    store = SQLiteStore(settings.storage.sqlite_path)
    run = store.latest_run()
    success = store.latest_successful_run()
    if run:
        print(f"最近一轮：{str(run.get('started_at', ''))[:16]} 开始 · {run.get('status', '?')}")
    else:
        print("最近一轮：尚无记录")
    if success:
        print(f"最近成功：{str(success.get('finished_at', ''))[:16]}")
    latest = {str(row["leg_id"]): row for row in store.latest_leg_results([leg.id for leg in legs])}
    for leg in legs:
        row = latest.get(leg.id)
        if row is None:
            print(f"  {leg.id:<24} 尚无结果")
            continue
        price = row.get("minimum_total_price_cny")
        price_text = f"¥{Decimal(str(price)):,.0f}" if price not in (None, "") else "—"
        print(f"  {leg.id:<24} {price_text}  {row.get('status', '?')}  {str(row.get('captured_at', ''))[:16]}")
    for event in store.recent_app_events(limit=3):
        print(f"  动态 {str(event.get('occurred_at', ''))[:16]}  {event.get('message', '')}")
    return 0


def _doctor_command(settings: AppSettings, routes_path: Path, settings_path: Path) -> int:
    import subprocess
    import sys as _sys

    failures = 0

    def report(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        suffix = f"  {detail}" if detail else ""
        print(f"[{mark}] {name}{suffix}")

    try:
        load_routes(routes_path, allow_empty=True)
        report("routes.yaml 解析", True, str(routes_path))
    except ConfigError as exc:
        report("routes.yaml 解析", False, str(exc))
    try:
        load_settings(settings_path)
        report("settings.yaml 解析", True, str(settings_path))
    except ConfigError as exc:
        report("settings.yaml 解析", False, str(exc))

    from .desktop_app.browser_detector import BrowserDetector

    browsers = BrowserDetector().detect(preferred_path=settings.browser.executable_path)
    if browsers:
        names = ", ".join(f"{b.kind} {b.version or ''}".strip() for b in browsers)
        report("浏览器检测", True, names)
    else:
        report("浏览器检测", False, "未找到 Chrome/Chromium/Edge；请在系统设置页选择浏览器")

    database = settings.storage.sqlite_path
    try:
        from .storage import SQLiteStore

        SQLiteStore(database).initialize()
        report("价格数据库", True, str(database))
    except Exception as exc:  # noqa: BLE001 - 自检需要兜住一切存储层异常
        report("价格数据库", False, f"{type(exc).__name__}: {exc}")

    if getattr(_sys, "frozen", False):
        signing = subprocess.run(
            ["codesign", "-dv", _sys.executable], capture_output=True, text=True
        ).stderr
        adhoc = "Signature=adhoc" in signing
        authority = next(
            (line.split("=")[1] for line in signing.splitlines() if line.startswith("Authority=")),
            "",
        )
        if adhoc:
            report(
                "签名身份",
                False,
                "ad-hoc 签名：macOS 隐私授权会随每次升级重弹；运行 packaging/make_signing_identity.sh 生成固定身份",
            )
        else:
            report("签名身份", True, authority or "固定证书")
    else:
        print("[SKIP] 签名身份（源码运行，仅打包产物需要）")

    return 1 if failures else 0


def _recent_rows(store: object, *, since_hours: int) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = {}
    for row in store.history(since=datetime.now() - timedelta(hours=since_hours)):  # type: ignore[attr-defined]
        rows.setdefault(str(row.get("leg_id")), []).append(dict(row))
    return rows


def _result_lines(row: dict[str, object], recent: list[dict[str, object]], last: int) -> list[str]:
    lines: list[str] = []
    price = row.get("minimum_total_price_cny")
    if price not in (None, ""):
        lines.append(f"最新价 ¥{Decimal(str(price)):,.0f}  {str(row.get('captured_at', ''))[:16]}  {row.get('status', '')}")
    else:
        lines.append(f"最新轮 {row.get('status', '?')}  {str(row.get('captured_at', ''))[:16]}（无有效价格）")
    previous = row.get("previous_min_total_cny")
    if price not in (None, "") and previous not in (None, ""):
        diff = Decimal(str(price)) - Decimal(str(previous))
        lines.append(f"较上次 {'+' if diff >= 0 else ''}{diff:,.0f}")
    if last > 1:
        for history_row in recent[-last:]:
            captured = str(history_row.get("captured_at", ""))[:16]
            history_price = history_row.get("minimum_total_price_cny")
            value = f"¥{Decimal(str(history_price)):,.0f}" if history_price not in (None, "") else "—"
            lines.append(f"  {captured}  {value}  {history_row.get('status', '')}")
    return lines


def _daemon_cycle_factory(routes_path: Path, settings_path: Path) -> Callable[[], MonitorService]:
    """每轮重读 routes/settings 构造 service，与桌面客户端热更新语义一致。"""

    def factory() -> MonitorService:
        service = MonitorService(load_routes(routes_path, allow_empty=True), load_settings(settings_path))
        _attach_desktop_mail(service, settings_path)
        return service

    return factory


def _attach_desktop_mail(service: MonitorService, settings_path: Path) -> None:
    """挂接与桌面客户端同源的邮件通道：desktop_mail（钥匙串授权码）优先。

    未启用或读取失败时保持 mail_delivery 为空，run_once 会回退到
    settings.mail 的环境变量通道（enabled=false 时静默跳过）。
    """
    from .desktop_app.credential_store import CredentialStore
    from .desktop_app.mail_profile import MailProfileRepository
    from .mail import send_report_with_credentials

    try:
        profile = MailProfileRepository(settings_path, user_root=settings_path.resolve().parent.parent).load()
    except Exception as exc:
        logger.warning("读取桌面邮件配置失败，本轮回退环境变量邮件通道：%s", exc)
        return
    if not profile.enabled:
        return
    try:
        secret = CredentialStore().get_secret(profile.username)
    except Exception as exc:
        logger.warning("读取邮箱授权码失败，本轮跳过桌面邮件：%s", exc)
        return
    service.mail_delivery = lambda report, workbook: send_report_with_credentials(
        report,
        profile.mail_settings(service.settings.mail),
        profile.credentials(secret or ""),
        workbook,
    )
