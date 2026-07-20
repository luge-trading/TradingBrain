# TASK-011C：macOS launchd 自动调度

## 任务目标与设计

本任务用 macOS 用户级 LaunchAgent 在系统本地时间每天 15:30 启动项目虚拟环境中的 Python，并直接执行 `src.engine.scheduled_review`。`launchd` 只负责触发；交易日、法定休市日和收盘时间仍由 `scheduled_review` 使用 XSHG 日历判断。这样不会在 plist 中重复实现业务门控，也不改变现有门控语义。

这是用户级 LaunchAgent，Label 为 `com.luge.tradingbrain.scheduled-review`。它位于当前用户的 GUI launchd domain，仅在用户登录并存在该用户 GUI 会话时可用；它不是 LaunchDaemon，不使用系统级 `/Library/LaunchAgents`。

## 文件说明

- `config/launchd/com.luge.tradingbrain.scheduled-review.plist.template`：带 `__PROJECT_ROOT__` 占位符的合法 plist 模板。
- `scripts/manage_launchd.sh`：验证、安装、卸载、重载、查询和手动触发工具。
- `tests/test_launchd_config.py`：不调用真实 `launchctl` 的静态自动化测试。
- `docs/TASK-011C.md`：本说明。

安装时脚本根据自身位置计算项目绝对路径，安全渲染 plist，并安装到：

```text
~/Library/LaunchAgents/com.luge.tradingbrain.scheduled-review.plist
```

## 调度与运行参数

任务每天 15:30 触发，包括周末；`scheduled_review` 随后判断是否为交易日以及市场是否收盘。程序直接调用 `.venv/bin/python`，不经过 shell，也不激活虚拟环境。运行参数依次为：

```text
<项目根目录>/.venv/bin/python
-m
src.engine.scheduled_review
--watchlist
<项目根目录>/config/watchlist.toml
--database-path
<项目根目录>/data/trading_brain.db
--output-dir
<项目根目录>/reports
--log-path
<项目根目录>/logs/scheduled-review.jsonl
```

## 管理命令

在项目根目录执行：

```bash
./scripts/manage_launchd.sh validate
./scripts/manage_launchd.sh install
./scripts/manage_launchd.sh status
./scripts/manage_launchd.sh run
./scripts/manage_launchd.sh reload
./scripts/manage_launchd.sh uninstall
```

`validate` 只检查和临时渲染，不安装服务。`install` 安装并加载服务；`reload` 安全卸载后重新渲染和加载；`run` 通过 `kickstart` 手动触发；`uninstall` 只卸载服务并删除用户 LaunchAgents 目录中的生成 plist，不删除数据库、报告或日志。

## 日志与执行确认

相关路径：

```text
logs/launchd-stdout.log
logs/launchd-stderr.log
logs/scheduled-review.jsonl
reports/
data/trading_brain.db
```

查询服务状态和确认最近的结构化运行记录：

```bash
./scripts/manage_launchd.sh status
tail -n 1 logs/scheduled-review.jsonl
tail -n 20 logs/launchd-stdout.log
tail -n 20 logs/launchd-stderr.log
```

JSONL 最后一条记录应包含 `started_at`、`finished_at`、`market_date`、`calendar`、`status`、`exit_code`、`forced` 和 `message`。正式 plist 不传 `--force`，因此正常调度记录中的 `forced` 应为 `false`。结合 JSONL 时间、状态、stdout、stderr 及生成报告确认任务是否真实执行。

## 常见错误排查

- `service is not loaded`：先执行 `validate`，再执行 `install`，随后查询 `status`。
- plist 无效：运行 `validate` 查看模板渲染和 `plutil -lint` 错误。
- Python 或 watchlist 缺失：确认 `.venv/bin/python` 可执行，且 `config/watchlist.toml` 存在。
- 运行失败：查看 `launchd-stderr.log` 和 JSONL 最后一条记录的 `message`、`status`、`exit_code`。
- 没有报告：先确认 JSONL 状态；非交易日或收盘前会正常跳过，不产生当日复盘报告。
- 路径移动：项目根目录变化后执行 `reload`，以重新渲染绝对路径。

## 睡眠、关机、登出与错过时间

Mac 关机时任务不会执行；用户登出后不再处于该用户的 GUI launchd 会话中。根据当前 macOS `launchd.plist(5)` 手册，`StartCalendarInterval` 在睡眠期间错过的事件通常会在唤醒时触发，多个错过事件会合并为一次；但不得承诺机器睡眠时一定在 15:30 准时执行，也不得承诺所有错过时间都一定自动补跑。若错过调度，可在唤醒或重新登录并确认服务已加载后执行：

```bash
./scripts/manage_launchd.sh run
```

最终是否补跑，以本机当前 `launchd` 行为以及 stdout、stderr 和 JSONL 日志为准。
