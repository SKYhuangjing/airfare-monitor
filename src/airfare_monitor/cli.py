"""Command-line entry points."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path

from .app_paths import AppPaths
from .config import load_local_env, load_routes, load_settings
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    paths = AppPaths.discover(user_root=args.user_root)
    load_local_env(Path.cwd() / ".env")
    routes_path = Path(args.routes) if args.routes else paths.routes_path
    settings_path = Path(args.settings) if args.settings else paths.settings_path
    # project_root 留空：settings.yaml 里的相对路径（data/、outputs/ 等）
    # 锚定到该配置文件所在的 <数据根>，与桌面客户端的解析口径一致。
    legs = load_routes(routes_path)
    settings = load_settings(settings_path)
    if args.command == "validate":
        enabled = [leg for leg in legs if leg.enabled]
        round_trips = sum(leg.is_round_trip for leg in enabled)
        print(f"配置有效：{len(enabled)} 个启用行程，其中 {round_trips} 组往返")
        return 0

    if args.command == "run-once":
        service = MonitorService(legs, settings)
        _attach_desktop_mail(service, settings_path)
        try:
            report, workbook = service.run_once(send_email=args.send_mail)
            print(f"运行完成：{report.status}；Excel：{workbook}")
            return 0
        finally:
            service.close()

    lock_path = settings.storage.sqlite_path.parent / "airfare-monitor.lock"
    run_forever(_daemon_cycle_factory(routes_path, settings_path), lock_path)
    return 0


def _daemon_cycle_factory(routes_path: Path, settings_path: Path) -> Callable[[], MonitorService]:
    """每轮重读 routes/settings 构造 service，与桌面客户端热更新语义一致。"""

    def factory() -> MonitorService:
        service = MonitorService(load_routes(routes_path), load_settings(settings_path))
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
