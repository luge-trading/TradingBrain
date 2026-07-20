# TradingBrain 项目状态

## 1. 文档用途

本文档是 TradingBrain 跨会话协作的长期状态入口，用于记录已经进入 Git 正式基线的能力、当前架构、运行边界和下一步任务。具体实现以代码、测试和对应 TASK 文档为准；本机 LaunchAgent、日志、数据库和报告属于运行状态，不应仅凭本文档推断其当前内容。

每个任务完成 commit 和 push 后，应同步更新正式基线、任务状态、验证记录和下一步。计划中的能力必须明确标注为“尚未实现”，不得与已完成能力混写。

## 2. 项目目标与不做事项

TradingBrain 是个人 A 股研究与复盘系统，当前链路覆盖行情获取、SQLite 存储、技术指标、风险分析、个股复盘、自选股批量复盘、每日汇总、交易日与收盘门控、macOS 自动调度和邮件通知。

项目明确不做：

- 自动下单或连接实盘交易执行；
- 收益保证或确定性买卖结论；
- 无法解释的黑箱涨跌预测；
- 用单一指标替代完整的风险判断；
- 在报告、日志、配置或 Git 中保存认证凭据。

所有输出仅用于研究和复盘，不构成投资建议。

## 3. 正式 Git 基线

- 分支：`main`
- Commit：`5ed912c63d81e5ad9e826195a60896dad62f5878`
- Commit message：`add daily review email notifications`
- 对应状态：TASK-011D 已完成、提交并推送。

开始任何新任务前必须重新检查本地 `HEAD`、`origin/main` 和工作区，不能仅依赖本文记录。

## 4. 当前架构与核心模块

```text
EastMoney 行情
  → src/data：标准化、有限重试、SQLite 更新与读取
  → src/analysis：技术指标、趋势、成交量和风险信号
  → src/report：个股 Markdown 报告、每日汇总
  → src/engine：批量编排、交易日及收盘门控、JSONL 运行记录
  → src/notification：Keychain 凭据读取、SMTP_SSL 邮件通知
  → macOS 用户级 LaunchAgent：每天 15:30 触发
```

主要模块：

- `src/data/providers/eastmoney.py`：EastMoney 日 K 数据获取、可重试错误判断和有限退避。
- `src/data/database.py`：`stock_daily` SQLite 表的初始化、幂等保存和查询。
- `src/data/update.py`：单只股票日线的增量更新协调。
- `src/analysis/technical.py`：基础技术指标计算。
- `src/analysis/signal.py`：趋势、成交量和风险标签等可解释规则。
- `src/config/watchlist.py`：自选股 TOML 加载、校验和代码规范化。
- `src/market/calendar.py`：XSHG 交易日、收盘时间和 Asia/Shanghai 市场时间。
- `src/report/stock_report.py`：个股 Markdown 复盘报告。
- `src/report/daily_summary.py`：批量结果、风险概览和失败信息汇总。
- `src/engine/daily_review.py`：自选股批量复盘协调。
- `src/engine/scheduled_review.py`：交易日/收盘门控、JSONL 记录和邮件集成。
- `src/notification/email_sender.py`：非敏感配置校验、Keychain 读取、TLS 邮件发送和附件构建。
- `scripts/manage_launchd.sh`：LaunchAgent 的验证、安装、卸载、重载、状态查询和手动触发。

## 5. 已完成任务与能力

- TASK-001 至 TASK-010：完成项目骨架、股票模型、EastMoney 日线、SQLite 存储、增量更新、技术指标、信号规则、个股报告、自选股批量复盘和每日汇总。
- TASK-011A：完成 XSHG 交易日与收盘门控、调度入口和 JSONL 运行日志。
- TASK-011B：完成 EastMoney 有限重试和失败保护。
- TASK-011C：完成 macOS 用户级 LaunchAgent、每天 15:30 调度和管理脚本。
- TASK-011D：完成 Gmail SMTP_SSL 邮件通知、Keychain 凭据、certifi CA、失败隔离、LaunchAgent 邮件参数和真实通道验证。
- TASK-012A：实现并验证四个核心指数的日线基础设施；最终提交哈希以当前 `HEAD` 为准，尚未在本文中预填。

TASK-012A 已完成：支持 `SH000001`（上证指数，`1.000001`）、`SZ399001`（深证成指，`0.399001`）、`SZ399006`（创业板指，`0.399006`）和 `SH000688`（科创50，`1.000688`）。已实现指数日K获取、严格标准化、独立 SQLite 保存/读取/latest、单指数增量更新及现有技术指标包装。尚未实现市场宽度、行业/概念板块、个股-板块联动、市场环境报告、daily summary、邮件或 LaunchAgent 集成。

`index_daily` 使用独立表，业务主键为 `(index_code, trade_date)`，字段包含 OHLC、volume、nullable amount、source 和 updated_at。`save_index_daily_kline()` 使用幂等 upsert；`update_index_daily()` 对本次所有有效记录 upsert，`new_rows` 仅统计新增交易日，因此可接收数据源对历史记录的修订。`analyze_index_daily()` 仅复用已有技术指标计算，不新增预测或确定性交易信号。

volume 与 amount 目前只定义为 EastMoney provider-native 数值。真实响应没有提供权威单位元数据；无证据表明具体单位，后续报告不得擅自标为“股、手、元”等。amount 可缺失，数据库保存为 SQLite `NULL`，不得伪造为 0。

`get_index_daily_kline()` 和 `update_index_daily()` 不自行判断是否收盘。收盘前调用可能获得当日尚未最终形成的日K。正式自动复盘的最终性依赖现有 `scheduled_review` 交易日/收盘门控；TASK-012A 尚未接入该调度链路。后续 TASK-012E 接入市场环境报告时，必须保持该门控，或显式标记为盘中预览。

TASK-012A 离线全量测试通过；随后完成四个指数各一次请求、`limit=5`、`retries=1` 的受控真实 provider 探针，均成功，并通过临时 SQLite 往返和指标包装验证。探针未写入 `data/trading_brain.db`，未进入 engine、邮件或 LaunchAgent。四个样本的 amount 均非缺失，但不代表所有历史或未来响应都不会缺失。

## 6. 自动化运行链路

1. 用户级 LaunchAgent 每天系统本地时间 15:30 触发。
2. 直接调用项目 `.venv/bin/python -m src.engine.scheduled_review`，不经过 shell 激活环境。
3. `scheduled_review` 使用 XSHG 日历判断交易日和收盘时间。
4. 非交易日记录 `skipped_non_trading_day`；收盘前记录 `skipped_before_close`，两者均不发送邮件。
5. 满足门控后更新行情、写入 SQLite、生成个股报告和每日汇总。
6. 运行结果写入 `logs/scheduled-review.jsonl`，launchd stdout/stderr 分别写入项目日志。
7. `completed` 发送正常邮件；`completed_with_errors` 发送部分失败邮件；`failed` 尝试发送失败通知。
8. 邮件失败只记录安全错误，不改变原复盘状态或退出码，也不回滚数据库和报告。

正式 plist 不传 `--force`。手动运行如需禁用邮件，应显式使用 `--no-email`。

## 7. LaunchAgent 与邮件状态

- Label：`com.luge.tradingbrain.scheduled-review`
- 类型：当前用户 GUI 会话中的用户级 LaunchAgent
- 安装位置：`~/Library/LaunchAgents/com.luge.tradingbrain.scheduled-review.plist`
- 调度：每天 `Hour=15`、`Minute=30`
- 工作目录：项目根目录的绝对路径
- 邮件配置参数：`--email-config <项目根目录>/config/email.toml`

LaunchAgent 只有在相应用户会话存在时可用。Mac 关机时不会执行；睡眠或错过触发时间后的实际补跑行为以本机 launchd 和日志为准。

邮件通道使用 `smtp.gmail.com:465` 和 `SMTP_SSL`。真实测试已在 TASK-011D 验收阶段完成，不应在普通回归测试中重复发送。

## 8. 安全与本地产物边界

- Gmail 应用专用密码只存在于当前用户 macOS Keychain；account 和 service 用于定位条目，密码值不得输出。
- `config/email.toml` 只保存 SMTP 地址、发件人、收件人、超时、附件开关和 Keychain service 等非敏感信息。
- 生产 TLS 使用 `ssl.create_default_context(cafile=certifi.where())`，保持 hostname 校验和 `CERT_REQUIRED`。
- 禁止未验证 SSL context、`check_hostname=False`、`CERT_NONE` 或绕过证书错误。
- 邮件错误不得包含密码、完整认证内容、正文全文或附件全文。
- `data/*.db`、`data/*.sqlite*`、`reports/`、`logs/`、`.venv/`、`__pycache__/` 和 `*.pyc` 不进入 Git。
- SQLite 当前生产 schema 仅覆盖个股日线 `stock_daily`；新增市场数据必须独立设计和测试。

## 9. 常用 CLI 与运维命令

在仓库根目录使用项目虚拟环境：

```bash
.venv/bin/python -m src.report --help
.venv/bin/python -m src.engine --help
.venv/bin/python -m src.engine.daily_review --help
.venv/bin/python -m src.engine.scheduled_review --help
```

LaunchAgent 的只读检查与配置验证：

```bash
./scripts/manage_launchd.sh validate
./scripts/manage_launchd.sh status
```

以下命令会改变本机服务状态或触发真实复盘，只能在对应任务明确授权时使用：

```bash
./scripts/manage_launchd.sh install
./scripts/manage_launchd.sh reload
./scripts/manage_launchd.sh uninstall
./scripts/manage_launchd.sh run
```

## 10. 测试与质量检查

常用验证命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -W error -m src.engine --help >/dev/null
.venv/bin/python -W error -m src.engine.daily_review --help >/dev/null
.venv/bin/python -W error -m src.engine.scheduled_review --help >/dev/null
.venv/bin/python -m pip check
git diff --check
```

`190 passed` 是 TASK-011D 完成时的历史全量验证记录，不代表阅读本文时的实时测试状态。新任务必须重新运行与风险相称的测试并记录实际结果。自动化单元测试不得访问真实邮件、Keychain、LaunchAgent 或外部网络。

## 11. Git 工作规范

1. 开始前核验路径、分支、工作区、`HEAD` 和 `origin/main`。
2. 不回滚或覆盖来源不明的用户改动。
3. 每次只实施一个明确任务；先设计和验收标准，再修改代码。
4. 先运行静态检查、相关测试和全量回归，再进行受控真实验证。
5. 仅逐个暂存任务文件，禁止使用 `git add .`。
6. 提交前核对 cached diff、敏感信息和精确文件集合。
7. 每个子任务独立 commit 和普通 push；禁止 amend、force push 和破坏性 reset。

## 12. 当前下一步

下一阶段是 TASK-012B“两市成交额与市场宽度”，先完成设计与口径确认，再开始编码。
