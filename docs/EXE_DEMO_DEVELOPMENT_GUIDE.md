# 航价守望 P0：一键 EXE＋简单表单开发手册

| 项目 | 内容 |
| --- | --- |
| 文档状态 | 开发实施稿 |
| 版本 | v0.1 |
| 日期 | 2026-09-04 |
| 关联需求 | `docs/EXE_DEMO_PRODUCT_REQUIREMENTS.md` v0.4 |
| 目标平台 | Windows 10/11 x64 |
| 首批规模 | 5 名内部试用用户，每台设备最多 10 个启用航程 |

> 本手册描述如何在尽量复用现有代码的前提下实现桌面演示版。文中的“新增文件”和“修改点”是开发计划，不代表这些代码已经存在。

## 1. 开发目标

在当前命令行工具之外增加一个 Windows 桌面壳，使普通用户能够：

- 通过安装器完成安装，不安装 Python；
- 通过中文表单维护航程；
- 使用内置机场目录，不手工编辑 IATA 和 YAML；
- 启动、暂停或立即执行监控；
- 查看最低价、历史、状态和 Excel；
- 接收桌面通知和个人 SMTP 邮件；
- 控制浏览器可见或后台隐藏；
- 在验证码或设备验证出现时人工处理；
- 通过覆盖安装升级并保留本地数据。

P0 必须保持以下边界：

- 每台设备最多 10 个启用航程；
- 单个隔离 Chromium Profile；
- 单个浏览器会话；
- 航程严格串行；
- 最短调度间隔 30 分钟；
- 不提供批量导入、并发采集和高频模式；
- 不登录、不预订、不支付、不绕过验证码或设备挑战。

## 2. 当前代码基线

### 2.1 技术栈

- Python `>=3.11`；
- DrissionPage 4.x：浏览器控制与网络/页面状态观察；
- PyYAML：航程和运行设置；
- SQLite：运行、航程结果、航班快照、重点班次价格和原始响应；
- openpyxl：Excel；
- Python `smtplib`：SMTP 邮件；
- airportsdata：机场 IATA 和国家归属；
- `unittest`：现有 45 个离线测试。

### 2.2 现有模块职责

| 文件 | 当前职责 | P0 处理方式 |
| --- | --- | --- |
| `models.py` | 航程、航班、运行结果领域模型 | 直接复用；仅在确有需要时增加 UI 无关字段 |
| `config.py` | YAML、`.env` 读取和强校验 | 复用读取；增加启用上限、原子写入仓储放到新模块 |
| `market.py` | 根据真实机场 IATA 判断国内/国际 | 原样复用 |
| `collector.py` | 单浏览器会话、去哪儿/同程采集 | 复用；补浏览器路径、运行事件和人工处理入口 |
| `parser.py` | 去哪儿完整响应解析、过滤和排序 | 原样复用 |
| `tongcheng_parser.py` | 同程最终页面状态解析和含税价 | 原样复用 |
| `ranking.py` | 航班及重点时刻匹配 | 原样复用 |
| `service.py` | 一轮串行采集、重试、二次确认、入库、报表、邮件 | 复用；增加可选事件回调和邮件凭据注入 |
| `storage.py` | SQLite 表和历史查询 | 复用；增加桌面首页只读查询方法和版本迁移 |
| `excel_report.py` | 合并 Excel | 原样复用 |
| `mail.py` | 邮件主题、正文和发送 | 复用渲染；拆分凭据来源并增加测试邮件 |
| `scheduler.py` | 阻塞式命令行守护循环和运行锁 | CLI 保留；GUI 使用新的可控制运行协调器 |
| `cli.py` | `validate/run-once/daemon` | 保留，兼容开发和诊断 |

### 2.3 不应重写的规则

以下规则已经被现有测试覆盖或属于项目安全边界，桌面化不得复制另一份实现：

- 去哪儿最终响应必须满足 `result.ctrlInfo.completed == true`；
- 同程必须等待最终状态并校验航线和日期；
- 国内价格使用票面价＋机建燃油后的预计支付总价；
- 往返使用平台组合含税总价；
- 排序、历史和心理价位统一使用 CNY 含税总价；
- 失败重试一次；连续失败后重启隔离浏览器；
- 低价命中后二次确认；
- 不完整结果不得作为成功或触发提醒。

## 3. 技术选型

### 3.1 桌面框架：PySide6 Widgets

P0 使用 PySide6 Widgets＋QSS：

- 能实现现有高保真桌面稿；
- 提供窗口、表单、列表、图表容器、系统托盘和本地 IPC；
- Windows 10/11 可运行；
- 与现有同步 Python 内核集成成本低；
- 后续可逐页替换视觉，不影响业务服务。

P0 不使用 QML，避免引入第二套声明式运行层和打包复杂度。简单趋势图优先使用 Qt Charts；如果包体或授权评审不通过，则用自定义 `QWidget.paintEvent()` 绘制折线。

### 3.2 桌面通知：QSystemTrayIcon

使用 `QSystemTrayIcon` 实现：

- 系统托盘图标；
- 右键菜单；
- `showMessage()` 桌面通知；
- 点击通知后打开相关航程或系统状态页。

桌面通知可能被 Windows 用户设置抑制，因此不能作为唯一的关键告警渠道。所有提醒必须同时保留在应用内“最近动态”，邮件按用户设置独立发送。

### 3.3 凭据：keyring

使用 Python `keyring` 对接 Windows Credential Locker：

- YAML 只保存环境无关的 SMTP 主机、端口、安全方式和用户名；
- SMTP 授权码写入系统凭据库；
- UI 读取时只显示“已保存”，不回显明文；
- 删除邮件账号时同步删除凭据；
- 若系统没有可用的安全后端，禁用邮件保存并给出错误，不降级为明文文件。

建议凭据键：

```text
service = "AirfareMonitor/SMTP"
username = <SMTP 用户名>
secret = <SMTP 授权码>
```

### 3.4 打包：PyInstaller onedir

P0 先将桌面程序打包为 onedir，而不是 PyInstaller onefile：

- onedir 更容易定位 Qt 插件、DrissionPage 动态导入和资源缺失问题；
- 启动时不需要将全部依赖解压到临时目录；
- 用户最终仍只接触安装器 EXE，不会看到内部目录；
- 后续覆盖安装更直观。

### 3.5 安装器：Inno Setup

使用 Inno Setup 将 onedir 目录制作成一个 `AirfareMonitorSetup.exe`：

- 当前用户级安装，不要求管理员权限；
- 创建开始菜单和可选桌面快捷方式；
- 注册卸载项；
- 覆盖安装时保留 `%LOCALAPPDATA%/AirfareMonitor`；
- 使用固定 `AppId` 识别同一产品的升级；
- P0 不签名，但发布时同时提供 SHA-256 校验值。

### 3.6 建议新增依赖

精确版本应在打包技术验证后锁定，建议约束为同一主版本：

```toml
[project.optional-dependencies]
desktop = [
  "PySide6>=6.8,<7",
  "keyring>=25,<26",
]
build = [
  "PyInstaller>=6,<7",
]
```

不得在第一次实现时同时引入多个 GUI 框架、Web 服务框架或 Electron。

## 4. 目标架构

```text
┌──────────────────────────────────────────────┐
│ PySide6 UI                                   │
│ 页面 / 对话框 / 托盘 / 桌面通知             │
└───────────────────┬──────────────────────────┘
                    │ commands / events
┌───────────────────▼──────────────────────────┐
│ Desktop Application Layer                    │
│ AppController / MonitorCoordinator           │
│ RouteRepository / SettingsRepository         │
│ AirportCatalog / CredentialStore             │
│ BrowserDetector / DiagnosticsExporter        │
└───────────────────┬──────────────────────────┘
                    │ typed domain objects
┌───────────────────▼──────────────────────────┐
│ Existing Core                                │
│ MonitorService / Collector / Parser          │
│ SQLiteStore / Excel / Mail                    │
└───────────────────┬──────────────────────────┘
                    │
┌───────────────────▼──────────────────────────┐
│ Local Runtime                                │
│ Chrome or Edge / isolated profile / SQLite   │
└──────────────────────────────────────────────┘
```

### 4.1 架构原则

- UI 不直接调用 DrissionPage；
- UI 不直接拼装平台 URL；
- UI 不复制解析、排名、阈值或邮件正文规则；
- 浏览器和完整一轮采集只在一个后台工作线程运行；
- UI 主线程只处理界面和轻量状态转换；
- YAML 是内部持久化格式，不向普通用户展示；
- UI 写配置后必须再次调用现有 `load_routes/load_settings` 验证；
- 所有运行入口统一执行 10 条启用上限检查。

## 5. 建议目录结构

```text
src/airfare_monitor/
├── desktop.py                    # GUI 入口
├── app_paths.py                  # 安装资源和用户数据路径
├── desktop_app/
│   ├── __init__.py
│   ├── application.py            # QApplication 和组合根
│   ├── controller.py             # 顶层命令编排
│   ├── monitor_coordinator.py    # 后台线程、调度和状态机
│   ├── events.py                 # 运行事件协议和数据类
│   ├── route_repository.py       # 航程 YAML 原子读写
│   ├── settings_repository.py    # 设置 YAML 原子读写
│   ├── airport_catalog.py        # 机场目录加载和搜索
│   ├── browser_detector.py       # Chrome/Edge 检测
│   ├── credential_store.py       # Windows 凭据
│   ├── notifications.py          # 托盘消息策略
│   ├── diagnostics.py            # 脱敏诊断包
│   ├── single_instance.py        # 本地 IPC 单实例
│   └── autostart.py              # 当前用户开机启动
├── ui/
│   ├── main_window.py
│   ├── navigation.py
│   ├── pages/
│   │   ├── overview_page.py
│   │   ├── routes_page.py
│   │   ├── route_wizard.py
│   │   ├── history_page.py
│   │   ├── notification_page.py
│   │   └── system_status_page.py
│   ├── widgets/
│   │   ├── route_card.py
│   │   ├── status_card.py
│   │   ├── airport_picker.py
│   │   ├── price_chart.py
│   │   └── empty_state.py
│   └── resources/
│       ├── styles.qss
│       ├── icons/
│       └── resources.qrc
└── resources/
    ├── airports.zh.json
    ├── settings.default.yaml
    └── routes.default.yaml

packaging/
├── airfare-monitor.spec
├── installer.iss
└── assets/
    ├── app.ico
    └── installer.ico

tests/
├── desktop/
├── fixtures/
└── integration/
```

目录可在开发中小幅调整，但不得把 UI 文件与平台解析逻辑混合。

## 6. 路径与运行目录

当前 CLI 以 `Path.cwd()` 作为项目根目录。GUI 不能依赖当前工作目录。

### 6.1 AppPaths

新增不可变 `AppPaths`，至少提供：

```text
install_root       安装目录，只读
resource_root      随包资源目录，只读
user_root          %LOCALAPPDATA%/AirfareMonitor
config_dir         user_root/config
data_dir           user_root/data
browser_profile    user_root/data/browser-profile
logs_dir           user_root/logs
outputs_dir        user_root/outputs
routes_path        user_root/config/routes.yaml
settings_path      user_root/config/settings.yaml
database_path      user_root/data/airfare-monitor.sqlite3
lock_path          user_root/data/airfare-monitor.lock
```

### 6.2 初始化规则

- 第一次启动创建用户目录；
- 缺少配置时从只读默认模板复制；
- 已存在配置绝不覆盖；
- 安装目录只保存程序和静态资源；
- 覆盖安装只替换安装目录；
- 用户数据路径不得指向日常 Chrome Profile；
- GUI 和 CLI 在打包后应支持显式传入同一用户目录，便于诊断。

### 6.3 原子写入

航程和设置保存流程：

1. 在目标目录创建临时文件；
2. 写入完整 YAML；
3. 立即使用 `load_routes/load_settings` 重新读取验证；
4. `flush` 并在可用时执行 `fsync`；
5. 使用 `os.replace()` 原子替换目标；
6. 保留最近一个 `.bak`；
7. 失败时不破坏现有配置。

## 7. 机场目录

### 7.1 数据结构

`airports.zh.json` 建议结构：

```json
{
  "catalog_version": 1,
  "updated_at": "2026-09-04",
  "airports": [
    {
      "airport_iata": "PVG",
      "display_name_zh": "上海浦东",
      "city_name_zh": "上海",
      "airport_name_zh": "上海浦东国际机场",
      "display_name_en": "Shanghai Pudong International Airport",
      "country_code": "CN",
      "aliases": ["上海", "浦东", "shanghai", "pudong", "sh", "pd"],
      "supported_sources": ["tongcheng", "qunar"],
      "verified_at": "2026-09-04",
      "enabled": true
    }
  ]
}
```

### 7.2 目录校验

加载时必须校验：

- IATA 为三个大写英文字母；
- IATA 在 airportsdata 中存在；
- 不得使用航司代码或城市集合代码；
- `country_code` 与 airportsdata 一致；
- IATA 不重复；
- 中文显示名非空；
- `supported_sources` 只允许 `tongcheng/qunar`；
- 禁用记录不出现在普通下拉框；
- 目录版本是正整数。

目录错误属于发布错误：应用继续启动，但禁止新增航程并在系统状态中显示诊断。

### 7.3 搜索

搜索标准化：

- 去除首尾空格；
- 英文字母转小写；
- 中文、英文、拼音、拼音首字母、IATA 均参与匹配；
- 精确 IATA 和中文前缀优先；
- 最近使用地点次优先；
- 最多显示 20 条结果；
- 多机场城市显示多个具体机场；
- 不允许任意未收录文本直接提交。

拼音和首字母在目录生成阶段预计算，P0 不必为运行时增加拼音依赖。

### 7.4 与当前配置映射

```text
selected.airport_iata  → origin_airport_iata / destination_airport_iata
selected.city_name_zh  → origin_name_zh / destination_name_zh
market                 → auto
```

这样可以直接复用 `LegConfig`、`resolve_market()` 和当前 URL 构造逻辑。

### 7.5 目录候选采集工具

目录维护工具不打包给最终用户。建议后续新增 `tools/calibrate_airports.py`，规则如下：

- 只能使用项目隔离 Profile；
- 输入人工提供的候选城市/机场清单；
- 打开去哪儿或同程公开搜索入口；
- 只读观察联想项，不登录、不下单；
- 输出候选 JSON，不直接覆盖正式目录；
- 删除 Cookie、Token、请求标识等运行数据；
- 每条记录必须人工审核后才能合并；
- 真实查询验证使用专门集成入口并留有明确标识。

## 8. 配置层

### 8.1 航程上限

定义唯一常量：

```python
MAX_ENABLED_LEGS = 10
```

上限必须在四层同时执行，但判断函数只能有一份：

1. 表单：第 11 条的“保存并启用”不可用；
2. 仓储：保存前调用统一校验；
3. 应用控制器：读取配置后检测手工修改或旧配置；
4. 运行入口：MonitorService、CLI 和 GUI 启动一轮前最后校验。

统一校验建议：

```python
def validate_enabled_leg_limit(legs: Sequence[LegConfig]) -> None:
    enabled_count = sum(leg.enabled for leg in legs)
    if enabled_count > MAX_ENABLED_LEGS:
        raise ConfigError("每台设备最多同时启用 10 个监控航程")
```

不得自动选择前 10 条并修改用户配置。超过上限时保留原文件、停止调度，但 GUI 仍以“配置恢复模式”展示全部航程，使用户可以暂停到 10 条以内；恢复前不得启动浏览器。CLI 直接返回配置错误。

### 8.2 表单默认值

| 字段 | P0 默认 |
| --- | --- |
| 启用 | 是，但达到上限时否 |
| 单程/往返 | 单程 |
| 起飞时间窗 | 全天 `00:00-23:59` |
| 直达 | 是 |
| 心理价位 | 空，表示只观察 |
| 最低价候选数 | 10，不显示在普通表单 |
| 成人 | 1 |
| 儿童 | 0 |
| 舱位 | economy |
| 市场 | auto |

### 8.3 能力联动

- 同程国内：只允许单程＋仅直达；
- 去哪儿国际/跨境：允许单程/往返、直达/中转；
- 往返显示返程日期、返程时间窗、返程直达和中转设置；
- 心理价位为空写入 YAML `null`；
- 日期必须在今天之后或当天仍有有效航班时段；
- 起点和终点不能相同；
- 保存前显示自动选择的来源。

### 8.4 设置仓储

设置页只暴露：

- 查询间隔：30/60/120 分钟；
- 浏览器：自动/Chrome/Edge；
- 显示浏览器运行过程；
- 开机自动启动；
- 桌面通知开关；
- 邮件设置；
- Excel 保留天数；
- 原始响应保留天数。

平台 URL、端口、超时等进入“诊断高级设置”，默认折叠并提供恢复默认值。

现有 `AppSettings` 建议增加 `DesktopSettings`，仅保存非敏感桌面偏好：

```text
desktop_notifications_enabled
start_with_windows
close_to_tray
excel_retention_days
preferred_browser: auto | chrome | edge
```

浏览器是否可见继续使用现有 `browser.headless`，不要再创建含义相反的第二个持久化字段。邮件授权码只在 keyring 中，不进入 `DesktopSettings`。

## 9. GUI 页面实现

### 9.1 MainWindow

- 左侧固定导航；
- 右侧使用 `QStackedWidget` 切换页面；
- 标题栏显示产品名和窗口控制；
- 窗口关闭事件默认隐藏到托盘；
- 首次关闭提示“关闭窗口后仍会在后台运行”，允许记住选择；
- 真正退出必须走统一 shutdown 流程。

### 9.2 概览页

数据源：SQLiteStore 的只读查询＋MonitorCoordinator 当前内存状态。

展示：

- 监控运行/暂停/查询中/需要处理；
- 下次查询时间；
- 已启用 `N/10`；
- 最近成功时间；
- 需要处理数量；
- 上一轮耗时；
- 航程卡片：路线、来源、日期、时间窗、最低含税价、价差、趋势、更新时间；
- “添加航程”“立即查询”；
- 最近动态。

查询中禁止重复点击“立即查询”，按钮改为“正在查询”。

### 9.3 航程列表

- 卡片或表格均可，P0 推荐卡片；
- 支持新增、编辑、复制、启用、暂停、删除；
- 复制结果默认暂停；
- 达到 10 条时突出显示容量；
- 点击航程打开详情；
- 删除只删除配置，不自动删除历史；清理历史必须单独确认。

### 9.4 航程向导

使用一个 `QDialog/QWizard` 风格三步页面，但不要直接使用默认系统 Wizard 视觉。

第一步：

- `AirportPicker` 起点、终点；
- 交换按钮；
- 单程/往返；
- 日期；
- 时间预设和自定义时间窗。

第二步：

- 直达/允许中转；
- 最大中转等待；
- 心理价位；
- 重点关注班次；
- 高级设置：成人、儿童、舱位。

第三步：

- 中文摘要；
- 来源；
- 是否立即启用；
- 支持性警告；
- 保存。

所有页面使用同一个 `RouteDraft`，最后一步才转换为 `LegConfig` 并写入。

### 9.5 历史页

- 航程选择；
- 最近 24 小时/7 天；
- 当前、最低、最高；
- 心理价位线；
- 失败点不连成有效价格；
- 打开最新 Excel；
- 打开输出目录。

P0 一次最多绘制 500 个点，更多数据先按时间桶采样，避免阻塞 UI。

### 9.6 通知页

- 桌面通知独立开关；
- SMTP 主机、端口、SSL/STARTTLS、用户名、授权码、发件人、收件人；
- 授权码字段不回显；
- “保存并测试”；
- 测试过程在工作线程执行；
- 测试失败不清除用户输入；
- 发送测试邮件是用户显式操作；
- 普通采集成功默认不弹窗，低价命中、部分失败和人工处理才通知。

### 9.7 系统状态页

- 应用版本；
- 浏览器类型、路径和版本；
- 显示/隐藏模式；
- 浏览器 Profile 路径；
- 数据库状态；
- 最后成功和最后失败；
- 当前配置有效性；
- 启用航程数量；
- 日志和输出目录；
- 导出脱敏诊断；
- 人工验证处理卡片。

## 10. 后台工作线程与状态机

### 10.1 线程模型

推荐一个常驻 Python 工作线程：

```text
Qt 主线程
  ├─ 渲染 UI
  ├─ 发出 command
  └─ 接收 immutable event

MonitorWorker 线程
  ├─ 唯一 MonitorService
  ├─ 唯一 QunarBrowserSession
  ├─ 调度计时
  ├─ 串行采集
  ├─ Excel/邮件
  └─ 发布状态事件
```

不得为每条航程创建线程；不得在 UI 主线程执行 `run_once()`、SMTP 或浏览器操作。

### 10.2 状态

```text
STOPPED        尚未启动或已退出
IDLE           已启动，等待下次查询
RUNNING        正在执行一轮
PAUSED         用户暂停
ATTENTION      至少一个航程需要人工处理
RESTARTING     正在安全重建浏览器
SHUTTING_DOWN  正在退出
ERROR          无法继续运行的配置/运行错误
```

`ATTENTION` 可以和整体 `IDLE` 并存于展示层，但协调器内部应使用一个主状态＋受影响航程集合，避免状态组合爆炸。

### 10.3 命令

```text
START
PAUSE
RUN_NOW
RELOAD_CONFIG
SET_BROWSER_VISIBILITY
RETRY_LEG
OPEN_MANUAL_ATTENTION
SHUTDOWN
```

命令通过线程安全队列进入工作线程：

- `RUN_NOW` 在 RUNNING 时只保留一个待执行标记，不重复排队；
- `PAUSE` 不强杀当前页面，当前航程安全结束后停止下一程；
- `SHUTDOWN` 请求当前浏览器操作在合理超时内结束，然后关闭浏览器；
- 配置更新只在空闲点重建 MonitorService；
- 可见/隐藏切换只在空闲点重启浏览器。

建议使用一个 `queue.Queue` 传入命令、另一个 `queue.Queue` 输出事件。Qt 主线程用短周期 `QTimer` 批量排空事件队列并更新界面；工作线程不得直接访问任何 QWidget。事件队列需要合并高频进度事件，避免 UI 隐藏很久后积压。

### 10.4 事件

建议使用不可变数据类：

```text
CoordinatorStateChanged
CycleStarted(run_id, started_at, total_legs)
LegStarted(run_id, leg_id, index, total)
LegFinished(run_id, leg_id, status, minimum_total_cny, error)
CycleFinished(run_report, workbook_path, duration)
NextRunScheduled(at)
ManualAttentionRequested(leg_id, message)
BrowserModeChanged(headless)
FatalError(category, user_message, diagnostic_id)
```

UI 只消费事件，不读取工作线程中的可变对象。

### 10.5 对 MonitorService 的最小修改

增加可选 `event_sink`，默认空实现，保持 CLI 和现有测试不变：

```python
class MonitorEventSink(Protocol):
    def on_leg_started(self, leg: LegConfig, index: int, total: int) -> None: ...
    def on_leg_finished(self, result: LegResult, index: int, total: int) -> None: ...

class NullMonitorEventSink:
    ...
```

插入点仅限：

- 每条航程开始前；
- 每条航程产生最终 LegResult 后；
- 二次确认开始/结束；
- 整轮报告保存后。

事件回调异常必须被隔离，不能使采集失败。

`MonitorService` 已支持注入 `sleep`。GUI 必须注入可中断等待函数，用于低价二次确认延迟：

- 正常情况下等待配置的秒数；
- shutdown 事件发生时立即结束等待并取消未完成的二次确认；
- 取消不得把初次低价误标记为已确认；
- 调度的 30 分钟等待和 5 分钟冷却同样使用可唤醒事件，禁止用不可打断的长时间 `time.sleep()`；
- 普通 CLI 保持现有 `time.sleep` 行为。

## 11. 调度实现

### 11.1 时间计算

P0 下一轮时间：

```python
base_due = cycle_started + interval
cooldown_due = cycle_finished + timedelta(minutes=5)
next_due = max(base_due, cooldown_due) + random_jitter
```

意义：

- 正常短轮次仍以轮次开始时间计算间隔；
- 超长轮次不会结束后立刻连续查询；
- 至少冷却 5 分钟；
- 保留抖动，避免固定节奏。

### 11.2 负载保护

- 启用航程上限 10；
- 最短间隔 30 分钟；
- 串行执行；
- 一轮未结束时不重叠；
- 连续两轮耗时达到间隔的 80% 时显示黄色提示；
- 连续两轮超过间隔时建议切换 60/120 分钟或减少航程；
- 不自动开启第二个浏览器提速；
- 阈值二次确认属于当前轮次并计入耗时。

### 11.3 休眠和系统时间变化

- 保存下一轮绝对时间和单调时钟参考；
- 系统唤醒后若已过期，不连续补跑多轮，只执行一轮；
- 系统时间大幅变化时重新计算下一轮；
- UI 显示“电脑休眠期间未查询”，不是失败。

## 12. 浏览器管理

### 12.1 检测顺序

`BrowserDetector` 返回可用浏览器列表：

1. 用户已保存的浏览器路径；
2. Chrome 注册表 App Paths 和常见安装路径；
3. Edge 注册表 App Paths 和常见安装路径；
4. DrissionPage 已能发现的兼容 Chromium。

每个结果包含：

```text
kind: chrome | edge
path
version
available
```

检测过程不启动用户日常 Profile。

### 12.2 启动

现有 `QunarBrowserSession.start()` 增加可选浏览器路径：

```python
options = ChromiumOptions(read_file=False)
options.set_browser_path(browser_path)
options.set_local_port(local_port)
options.set_user_data_path(str(isolated_profile))
options.headless(headless)
```

端口首次安装时从 9333 开始寻找可用端口并持久化；运行中端口冲突时不能随机连接未知浏览器实例。

### 12.3 显示浏览器运行过程

UI 开关语义：

```text
开启：headless = false
关闭：headless = true
```

- 首次默认开启；
- 当前轮次结束后安全重启生效；
- UI 显示“本轮结束后生效”；
- 可见模式意外关闭浏览器时本轮失败并按现有重启策略恢复；
- 模式切换不改变 Profile 路径。

### 12.4 人工验证

隐藏模式出现 `ManualAttentionRequired` 时：

1. 保存受影响航程和错误和当前页面 URL；
2. UI 显示人工处理卡片；
3. 用户点击“打开验证页面”；
4. 空闲点关闭隐藏浏览器；
5. 使用相同隔离 Profile 以可见模式启动并打开安全的公开查询入口；
6. 用户人工完成页面提示；
7. 用户点击“我已完成，重新查询”；
8. 重新执行该航程；
9. 完成后恢复用户原来的 headless 设置。

不得自动点击验证、读取短信、调用识别服务或复用日常浏览器数据。

## 13. 邮件和桌面通知

### 13.1 邮件重构边界

当前 `send_report()` 从环境变量读取凭据。为了同时兼容 CLI 和 GUI：

- 保留 `send_report(report, settings, attachment)` 作为 CLI 包装器；
- 提取 `SmtpCredentials` 数据类；
- 提取 `SmtpSender.send(message, settings, credentials)`；
- GUI 从 keyring 取凭据后调用发送器；
- 邮件正文和主题构建函数不变。

示意：

```python
@dataclass(frozen=True)
class SmtpCredentials:
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]
```

密码不得出现在 `repr`、日志、错误或事件中。

### 13.2 测试邮件

“保存并测试”流程：

1. 校验表单；
2. 临时构建凭据对象；
3. 发送标题为“[航价守望] 邮件通知测试”的短邮件；
4. 成功后才保存非敏感设置和 keyring 密码；
5. 失败时保留表单内容但不保存密码；
6. UI 区分 DNS、连接、TLS、认证和发送阶段；
7. 不在错误信息中回显服务器响应里的敏感字段。

### 13.3 通知策略

| 事件 | 桌面通知 | 邮件 |
| --- | --- | --- |
| 普通成功 | 默认不通知 | 按现有设置发送汇总 |
| 确认低价命中 | 通知 | 发送低价邮件 |
| 部分失败 | 通知 | 发送部分失败邮件 |
| 全部失败 | 通知 | 配置有效时发送失败邮件 |
| 人工处理 | 通知并打开入口 | 在汇总中标记 |
| 邮件自身失败 | 通知 | 不递归发送 |

所有事件写入应用内最近动态。桌面通知被系统抑制时，应用内状态仍可追溯。

## 14. SQLite 与首页查询

### 14.1 新增只读方法

建议在 SQLiteStore 增加：

```text
latest_run()
latest_leg_results(enabled_leg_ids)
leg_price_series(leg_id, since, max_points)
recent_runs(limit=20)
attention_items()
```

不得让 UI 拼 SQL。查询返回普通字典或专用只读 DTO，不返回打开的连接。

### 14.2 数据库版本

当前表使用 `CREATE TABLE IF NOT EXISTS` 和局部列迁移。桌面版开始后使用 `PRAGMA user_version`：

- 每次迁移一个明确版本；
- 事务内执行；
- 迁移失败回滚；
- 首次覆盖升级前复制数据库备份；
- 不因 UI 改版修改历史含义。

### 14.3 应用事件

为了让“最近动态”在重启后仍存在，新增轻量 `app_events` 表：

```text
id INTEGER PRIMARY KEY
occurred_at TEXT NOT NULL
event_type TEXT NOT NULL
severity TEXT NOT NULL
leg_id TEXT NULL
message TEXT NOT NULL
details_json TEXT NULL
```

只保存面向用户的脱敏事件，例如一轮完成、需要人工处理、邮件失败、浏览器重启和设置变更。原始响应、SMTP 信息和浏览器令牌不得写入该表。默认保留 30 天。

### 14.4 保留策略

- 原始响应默认 7 天，沿用现有配置；
- Excel 默认 30 天；
- 日志默认 30 天或单目录上限；
- 价格历史 P0 不自动删除；
- 清理只在一轮结束后执行；
- 清理失败只记录警告，不影响本轮结果。

## 15. 单实例、托盘与退出

### 15.1 单实例

使用 `QLocalServer/QLocalSocket`：

- 第一个实例监听固定应用键；
- 第二个实例发送“activate”后退出；
- 第一个实例恢复并置前窗口；
- 保留现有 `ProcessLock` 作为监控运行的第二道保护；
- IPC 名称包含当前 Windows 用户标识，避免不同用户会话冲突。

### 15.2 托盘

菜单：

- 打开航价守望；
- 立即查询；
- 暂停/继续监控；
- 打开最新报告；
- 退出并停止监控。

托盘图标状态：蓝色正常、灰色暂停、橙色需要处理、红色错误。不要只依赖颜色，tooltip 同时显示文字。

### 15.3 安全退出

退出顺序：

1. UI 进入“正在退出”；
2. 禁止新命令；
3. 请求工作线程停止；
4. 关闭浏览器；
5. 释放运行锁；
6. 等待线程退出；
7. 关闭本地 IPC；
8. 退出 QApplication。

超过合理等待时间时提示用户，不直接让 UI 永久卡死。

## 16. 日志与诊断

### 16.1 日志

使用 `logging.handlers.TimedRotatingFileHandler` 或 `RotatingFileHandler`：

- `app.log`：应用、UI 和调度；
- `collection.log`：采集状态；
- 默认 INFO；
- 单文件大小和保留天数受控；
- 日志使用 UTF-8；
- UI 不显示堆栈，只显示用户文案和诊断 ID；
- DEBUG 只能通过系统状态页临时开启，重启后恢复 INFO。

禁止记录：

- SMTP 密码；
- Cookie、Token、Bella、queryId 全值；
- 原始请求头；
- 个人邮箱全文；
- 浏览器日常 Profile 路径。

### 16.2 诊断包

导出 ZIP 包含：

- 应用版本、Windows 版本、Python 打包版本；
- 浏览器类型和版本；
- 脱敏设置；
- 航程数量和机场代码，不包含个人备注；
- 最近日志；
- 数据库 schema/version，不包含完整数据库；
- 最近错误类别；
- 目录版本。

导出前执行敏感字段扫描；发现疑似密码、Cookie 或 Token 时终止导出并记录本地错误。

## 17. 自动启动

P0 提供设置开关，默认值在需求评审完成前为关闭：

- 仅注册当前用户；
- 不安装 Windows 服务；
- 启动参数使用 `--background`；
- 自动启动后只显示托盘，不抢焦点；
- 用户关闭开关时清理注册项；
- 覆盖安装保持用户选择。

可以使用当前用户 Startup 快捷方式或 HKCU Run；选定一种实现并集中在 `autostart.py`，不要同时维护两套。

## 18. 打包与安装

### 18.1 GUI 入口

新增脚本入口：

```toml
[project.gui-scripts]
airfare-monitor-gui = "airfare_monitor.desktop:main"
```

`desktop.main()` 负责：

1. 初始化 AppPaths；
2. 初始化文件日志；
3. 检查单实例；
4. 创建 QApplication；
5. 加载静态资源；
6. 创建 Application/Controller；
7. 创建托盘和主窗口；
8. 按配置启动或保持暂停；
9. 进入事件循环。

异常必须写入文件日志，并显示最小可理解的启动错误窗口。

### 18.2 PyInstaller spec

`packaging/airfare-monitor.spec` 至少包含：

- GUI 入口；
- `console=False`；
- 应用图标；
- PySide6 Qt plugins；
- DrissionPage 动态导入；
- airportsdata 数据文件；
- `airports.zh.json`；
- QSS、图标和默认 YAML；
- 版本信息资源；
- 输出名称 `AirfareMonitor`。

先用 onedir 构建并测试：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm packaging\airfare-monitor.spec
```

不得在 spec 中包含 `.env`、真实 `routes.yaml/settings.yaml`、数据库、日志、输出或浏览器 Profile。

### 18.3 Inno Setup

建议关键配置：

```text
AppName=航价守望
AppVersion=<与 pyproject 一致>
AppId=<固定 GUID>
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\AirfareMonitor
OutputBaseFilename=AirfareMonitorSetup-<version>
```

安装器：

- 复制完整 onedir；
- 创建开始菜单；
- 桌面快捷方式作为可选任务；
- 覆盖安装前尝试关闭正在运行的应用；
- 不删除 `%LOCALAPPDATA%/AirfareMonitor` 用户数据；
- 卸载默认保留用户数据；
- 提供单独明确的“删除本地数据”操作，不在普通卸载中静默删除。

### 18.4 发布产物

```text
AirfareMonitorSetup-0.x.y.exe
AirfareMonitorSetup-0.x.y.exe.sha256
RELEASE_NOTES-0.x.y.md
```

P0 未签名，只能内部可信渠道分发。发布说明必须提醒 Windows 可能显示未知发布者，并给出 SHA-256 核对方式。

## 19. 测试策略

### 19.1 测试分层

| 层级 | 内容 | 是否访问真实网站 |
| --- | --- | --- |
| 现有单元测试 | parser/config/service/report/storage | 否 |
| 新增单元测试 | 机场目录、上限、路径、凭据接口、调度计算 | 否 |
| UI 测试 | 表单联动、按钮状态、页面切换、事件渲染 | 否 |
| 协调器测试 | 假浏览器、假时钟、命令队列、暂停/重启 | 否 |
| 打包烟测 | 干净 Windows VM 启动和目录检查 | 否 |
| 集成测试 | 国内、国际单程、国际往返 | 是，已授权代表性运行 |
| SMTP 测试 | 用户点击“保存并测试” | 是，显式操作 |

### 19.2 必测用例

航程上限：

- 9 条时可新增并启用；
- 10 条时只能保存为暂停；
- 暂停一条后可启用另一条；
- 手工配置 11 条时 GUI 和 CLI 均拒绝运行；
- 往返按一条计数；
- 复制默认暂停。

浏览器：

- 只安装 Chrome；
- 只安装 Edge；
- 两者都有并切换；
- 两者都没有；
- 显示→隐藏和隐藏→显示；
- 查询中切换延迟到本轮结束；
- 隐藏模式出现人工验证；
- 可见浏览器被手工关闭；
- 端口冲突；
- Profile 路径始终隔离。

调度：

- 正常轮次按开始时间计算；
- 超长轮次至少冷却 5 分钟；
- RUN_NOW 不重叠；
- 休眠唤醒只补一轮；
- 暂停不再调度；
- shutdown 关闭浏览器并释放锁。

配置：

- 原子写入失败保留原文件；
- 覆盖安装保留配置；
- 空心理价位写入 null；
- 国内禁用往返和中转；
- 国际往返字段完整；
- 未知机场不能保存；
- 多机场城市展示具体机场。

通知：

- 桌面和邮件独立开关；
- 授权码不进入 YAML 和日志；
- 邮件测试成功/认证失败/TLS 失败；
- 邮件失败不影响报告保存；
- 桌面通知被禁用时应用内事件仍存在。

### 19.3 集成测试门控

集成测试必须满足：

- 放在 `tests/integration/`；
- 默认 discover 不运行；
- 需要显式环境变量 `AIRFARE_MONITOR_INTEGRATION=1`；
- 需要明确参数指定国内、国际单程或国际往返；
- 使用 `data/browser-profile/` 隔离目录；
- 不使用日常 Chrome Profile；
- 不自动发送邮件；
- 不绕过验证码；
- 记录测试时间、来源、完成标志和脱敏诊断；
- 测试完成后人工确认结果路线、日期、机场和含税总价。

用户已授权代表性的三类真实查询，但该授权不等于允许持续压测、批量采集或真实邮件发送。

### 19.4 回归命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\airfare-monitor.exe validate
```

新增桌面测试应继续被同一 unittest discover 收集，或提供一个统一 `scripts/test.ps1`，但不能默认包含集成测试。

## 20. 开发顺序

### 阶段 0：打包技术验证（2～3 人日）

- PySide6 空窗口；
- 托盘；
- PyInstaller onedir；
- Inno Setup；
- Chrome/Edge 检测；
- DrissionPage 能从打包程序启动隔离浏览器；
- SQLite、airportsdata、QSS 和图标资源可加载。

只有该阶段通过后再大规模写 UI。

### 阶段 1：应用骨架（3～5 人日）

- AppPaths；
- 日志；
- 单实例；
- MainWindow 和导航；
- 托盘及退出；
- 系统状态基础页。

### 阶段 2：配置与航程表单（5～8 人日）

- 机场目录；
- AirportPicker；
- 航程列表和三步向导；
- 原子配置写入；
- 国内/国际联动；
- 10 条上限；
- 设置页和 headless 开关。

### 阶段 3：运行协调器（4～6 人日）

- MonitorWorker；
- 状态机、命令、事件；
- MonitorService 最小事件回调；
- 调度、冷却、休眠恢复；
- 浏览器安全重启和人工验证。

### 阶段 4：结果和通知（4～7 人日）

- 首页和历史查询；
- 价格图；
- 桌面通知；
- keyring；
- SMTP 测试；
- 诊断包。

### 阶段 5：回归、集成和演示（4～7 人日）

- 全量离线测试；
- 三类已授权真实查询；
- 干净 VM 安装；
- 覆盖升级；
- 5 名试用准备；
- 演示脚本和已知问题。

## 21. 分支与提交建议

当前开发分支为 `codex/exe-desktop`。建议按可回滚的小提交推进：

```text
chore: add desktop dependencies and build spike
feat: add application paths and local repositories
feat: add airport catalog and picker
feat: add desktop navigation and route wizard
feat: add monitor coordinator and runtime events
feat: add browser visibility control
feat: enforce ten enabled route limit
feat: add tray notifications and smtp settings
feat: add dashboard and history views
build: add pyinstaller and inno setup packaging
test: add desktop and packaging coverage
```

不要在同一个提交中同时重构 parser、重做 UI 和加入安装器。

## 22. 编码约定

- 继续使用类型注解、dataclass 和 pathlib；
- UI 文案集中管理，避免散落在业务层；
- 领域层不得 import PySide6；
- UI 层不得 import 平台 parser 私有函数；
- 线程之间传递不可变 DTO；
- 不用裸 `except Exception` 吞掉 UI 运行错误；
- 用户文案和开发日志分离；
- 密码类型禁止默认 repr；
- 时间和价格继续使用 datetime/Decimal，不用浮点价格；
- 路径一律由 AppPaths 生成；
- 不在 tracked 文件中保存 SMTP、邮箱、Cookie、Token 或真实 Profile。

## 23. 主要风险与控制

| 风险 | 控制 |
| --- | --- |
| Qt/DrissionPage 打包缺动态依赖 | 阶段 0 先做 onedir 技术验证和干净 VM 烟测 |
| UI 阻塞 | 浏览器、SMTP、报表统一在工作线程 |
| 浏览器切换破坏 Profile | 单一 AppPaths，模式切换只重启实例不换目录 |
| 100 条配置造成压力 | UI、仓储、读取和运行四层统一限制 10 条 |
| 超长轮次连续运行 | 结束后至少冷却 5 分钟并提示延长间隔 |
| 官网地点名称变化 | 内置机场目录版本化，维护工具只生成候选 |
| 官网结构变化 | 失败关闭、脱敏诊断、真实集成检查 |
| 桌面通知被系统关闭 | 应用内最近动态＋可选邮件，不以托盘消息为唯一渠道 |
| SMTP 密码泄漏 | Windows Credential Locker；日志和诊断扫描 |
| 覆盖安装丢数据 | 程序和用户目录分离；安装/升级测试 |
| 无代码签名产生警告 | 仅内部可信分发＋SHA-256，扩大范围前再签名 |

## 24. Definition of Done

P0 完成需同时满足：

- 干净 Windows 10/11 x64 VM 可通过一个安装器安装；
- 无 Python 环境仍可启动；
- Chrome/Edge 检测和隔离 Profile 正常；
- 用户无需命令行/YAML即可维护航程；
- 国内/国际来源与当前内核一致；
- 10 条上限无法从 GUI、CLI 或配置加载绕过；
- 单浏览器串行执行，无重叠轮次；
- 可见/隐藏浏览器模式可切换；
- 隐藏模式人工验证流程可用；
- 桌面通知、邮件设置和测试通路可用；
- 最低价、历史、Excel 和应用内状态可查看；
- 覆盖安装保留配置、数据库、凭据和 Profile；
- 现有 45 个离线测试全部通过；
- 新增桌面测试通过；
- 已授权的国内、国际单程、国际往返各完成一次并人工核验；
- 日志、诊断包和安装产物通过敏感信息检查；
- 无登录、预订、支付或验证码绕过能力。

## 25. 官方技术参考

- Qt for Python 系统托盘与通知：<https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html>
- Qt for Python 本地单实例通信基础：<https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html>
- PyInstaller onedir/onefile 运行方式：<https://pyinstaller.org/en/stable/operating-mode.html>
- PyInstaller spec 与资源文件：<https://pyinstaller.org/en/stable/spec-files.html>
- keyring 与 Windows Credential Locker：<https://keyring.readthedocs.io/en/latest/index.html>
- Inno Setup 官方帮助：<https://jrsoftware.org/ishelp/contents.htm>
