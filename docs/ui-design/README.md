# 航价守望 UI 设计稿

这些高保真设计稿依据以下文档生成，作为 P0 Windows 桌面端的视觉与交互参考：

- `docs/EXE_DEMO_PRODUCT_REQUIREMENTS.md`
- `docs/EXE_DEMO_DEVELOPMENT_GUIDE.md`
- `docs/DESIGN.md`
- `docs/EMAIL_AND_EXCEL.md`

| 文件 | 页面/交互覆盖 |
| --- | --- |
| `01-first-launch-onboarding.png` | 首次启动、浏览器检测、频率、显示模式、通知与开机启动 |
| `02-dashboard-overview.png` | 概览、监控状态、含税最低价、立即查询、异常入口、最近动态 |
| `03-my-routes.png` | 航程管理、容量上限、启停/复制/编辑、删除二次确认 |
| `04-route-wizard-step-1.png` | 新建航程第 1 步：机场搜索、起止交换、日期与时间窗 |
| `05-route-wizard-step-2.png` | 新建航程第 2 步：直达/中转、含税心理价位、重点班次、高级设置 |
| `06-route-wizard-step-3.png` | 新建航程第 3 步：配置确认、来源自动匹配、立即启用 |
| `07-price-history.png` | 历史价格、24 小时/7 天切换、心理价位线、未完成查询标记、Excel |
| `08-notification-settings.png` | 桌面通知、SMTP、凭据安全说明、显式测试邮件操作 |
| `09-system-status.png` | 服务健康、隔离浏览器 Profile、显示模式、运行记录、脱敏诊断 |
| `10-manual-verification.png` | 人工页面确认、打开验证页、完成后重试、其他航程继续运行 |
| `11-flight-results-detail.md` | 最近完整查询的候选航班表、筛选排序、信息缺失和同轮对比规则 |

## 实施约束

- 所有监控价格应显示为 CNY 含税总价。
- 机场 IATA 只由地点目录提供；不使用城市集合代码或航司代码替代机场。
- 浏览器始终使用应用隔离 Profile；人工验证必须由用户在可见的独立浏览器中完成。
- 设计稿不包含登录、预订、支付或验证码/设备验证绕过能力。
