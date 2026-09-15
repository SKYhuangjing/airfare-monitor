# 航价守望 R4（0.6.0）内部试用发布说明

本版聚焦稳定性与安装交付，不修改去哪儿、同程采集和价格解析规则。

## 用户可见变化

- 安装器提供当前用户安装、开始菜单入口、可选桌面快捷方式和卸载项。程序目录与 `%LOCALAPPDATA%/AirfareMonitor` 用户数据目录分离；覆盖安装和普通卸载不删除航程、历史、浏览器 Profile、Excel 或 Windows 凭据。
- 上次桌面程序异常中断时，下次启动给出提示；正常退出会等待监控线程和测试邮件线程结束后清除会话标记。
- 应用、采集和启动错误日志按单文件 2 MB、最多 5 个备份轮转，并在写盘前隐藏个人邮箱、常见凭据字段、URL 和 Windows 用户路径。
- 系统状态页导出 ZIP 脱敏诊断包，包含版本、运行摘要、数据库版本、机场目录版本和最近日志片段；不打包配置、数据库、原始响应、浏览器 Profile 或授权码。
- 数据库设置 schema 版本号；旧版库原地初始化且保留数据，遇到较新版本创建的库时拒绝降级打开。

## 构建与验证

开发机安装项目的 `desktop`、`build` 依赖，以及 Inno Setup 7；然后运行：

```powershell
.venv\Scripts\python.exe packaging\build_release.py
```

脚本生成 `dist/release-0.6.0/AirfareMonitor` onedir 程序、`release/v0.6.0/AirfareMonitorSetup-0.6.0.exe` 安装器和同名 `.sha256` 校验文件。发布产物不签名；仅从内部可信渠道获取，并在安装前核对 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 .\release\v0.6.0\AirfareMonitorSetup-0.6.0.exe
```

运行离线测试：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
```

打包程序可以用临时用户目录进行不访问官网、不发送邮件的烟测：

```powershell
.\dist\release-0.6.0\AirfareMonitor\AirfareMonitor.exe --ui-smoke-test --user-root C:\Temp\AirfareMonitor-smoke --smoke-report C:\Temp\AirfareMonitor-smoke.json
```

## 试用安装检查

1. 先从托盘选择“退出并停止监控”，确认程序退出，再运行安装器。升级使用同一安装器，不需卸载旧版。Windows 可能提示“未知发布者”；先确认文件来源与 SHA-256，再继续。
2. 首次打开，检查 Chrome/Edge 检测、机场选择、航程保存和可见/隐藏浏览器设置。不使用日常浏览器 Profile。
3. 覆盖安装后检查原航程、历史、凭据设置与独立 Profile 仍可用；普通卸载后检查 `%LOCALAPPDATA%/AirfareMonitor` 仍存在。
4. 如确实要删除本机数据，须在退出程序后由本人另行备份并明确手动删除 `%LOCALAPPDATA%/AirfareMonitor`；普通卸载没有“顺手清空数据”动作。

本机打包与离线烟测不替代干净 Windows 10/11 虚拟机中的安装、覆盖升级、卸载保留数据验收。R4 内部扩大试用前仍需完成这一步；真实国内/国际查询与真实 SMTP 测试不由构建脚本触发。
