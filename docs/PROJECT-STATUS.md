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
- Commit：`c1fec1d2c8b8ea5237e04ed8ea3898a7ded264cd`
- Commit message：`add market environment data layer`
- 对应状态：TASK-012B 已完成、提交并推送。

开始任何新任务前必须重新检查本地 `HEAD`、`origin/main` 和工作区，不能仅依赖本文记录。

## 4. 当前架构与核心模块

```text
SSE/SZSE 官方统计与 EastMoney 行情
  → src/data：标准化、有限重试、SQLite 更新与读取
  → src/analysis：技术指标、趋势、成交量和风险信号
  → src/report：个股 Markdown 报告、每日汇总
  → src/engine：批量编排、交易日及收盘门控、JSONL 运行记录
  → src/notification：Keychain 凭据读取、SMTP_SSL 邮件通知
  → macOS 用户级 LaunchAgent：每天 15:30 触发
```

主要模块：

- `src/data/providers/eastmoney.py`：EastMoney 个股及指数日 K 数据获取、可重试错误判断和有限退避。
- `src/data/providers/exchange.py`：SSE、SZSE 官方股票日度成交额获取、日期校验和单位换算。
- `src/data/providers/eastmoney_market.py`：EastMoney 沪深市场广度快照获取、逐市场校验和汇总。
- `src/data/providers/eastmoney_sector.py`：EastMoney 三级行业注册表分页获取和行业不复权历史日 K 获取。
- `src/data/market.py`：市场日度事实模型、日期/金额/广度校验和读取时派生指标。
- `src/data/sector.py`：行业定义、注册表和行业日 K 的严格标准化与字段校验。
- `src/data/database.py`：股票、指数、市场和行业事实表的增量初始化、幂等保存和查询。
- `src/data/update.py`：股票、指数、市场及行业数据的相互独立更新协调。
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
- TASK-012A：实现并验证四个核心指数的日线基础设施，已提交并推送，commit 为 `aeb67307f7f2d25d2e0397a789d6ded77f7238c7`。
- TASK-012B：市场基础环境数据层已完成、提交并推送，commit 为 `c1fec1d2c8b8ea5237e04ed8ea3898a7ded264cd`。
- TASK-012C：东方财富三级行业注册表与不复权历史日线事实层已完成编码和离线验收；受控真实探针因环境代理链路阻塞获得验收豁免，待精确暂存、commit 和 push。

TASK-012A 已完成：支持 `SH000001`（上证指数，`1.000001`）、`SZ399001`（深证成指，`0.399001`）、`SZ399006`（创业板指，`0.399006`）和 `SH000688`（科创50，`1.000688`）。已实现指数日K获取、严格标准化、独立 SQLite 保存/读取/latest、单指数增量更新及现有技术指标包装。尚未实现市场宽度、行业/概念板块、个股-板块联动、市场环境报告、daily summary、邮件或 LaunchAgent 集成。

`index_daily` 使用独立表，业务主键为 `(index_code, trade_date)`，字段包含 OHLC、volume、nullable amount、source 和 updated_at。`save_index_daily_kline()` 使用幂等 upsert；`update_index_daily()` 对本次所有有效记录 upsert，`new_rows` 仅统计新增交易日，因此可接收数据源对历史记录的修订。`analyze_index_daily()` 仅复用已有技术指标计算，不新增预测或确定性交易信号。

volume 与 amount 目前只定义为 EastMoney provider-native 数值。真实响应没有提供权威单位元数据；无证据表明具体单位，后续报告不得擅自标为“股、手、元”等。amount 可缺失，数据库保存为 SQLite `NULL`，不得伪造为 0。

`get_index_daily_kline()` 和 `update_index_daily()` 不自行判断是否收盘。收盘前调用可能获得当日尚未最终形成的日K。正式自动复盘的最终性依赖现有 `scheduled_review` 交易日/收盘门控；TASK-012A 尚未接入该调度链路。后续 TASK-012E 接入市场环境报告时，必须保持该门控，或显式标记为盘中预览。

TASK-012A 离线全量测试通过；随后完成四个指数各一次请求、`limit=5`、`retries=1` 的受控真实 provider 探针，均成功，并通过临时 SQLite 往返和指标包装验证。探针未写入 `data/trading_brain.db`，未进入 engine、邮件或 LaunchAgent。四个样本的 amount 均非缺失，但不代表所有历史或未来响应都不会缺失。

TASK-012B 已建立 `market_daily` 市场基础环境数据层。成交额分别来自 SSE、SZSE 官方股票日度概况，官方亿元值使用十进制定点换算为整数人民币元；字段为 `sh_amount_yuan`、`sz_amount_yuan` 和仅在两者齐全时计算的 `total_amount_yuan`。SZSE 响应日期按 `conditions[name=txtQueryDate].defaultValue` 校验。深市范围已确认包含创业板和存托凭证；是否包含 B 股尚无权威证据，不作扩展声明。

市场广度来自 EastMoney `1.000001`、`0.399001` 两条记录，分别校验 `f104`、`f105`、`f106` 后汇总为上涨、下跌和平盘家数。三个广度字段整体成功或整体缺失；`advance_ratio` 只在读取时计算，字段缺失或分母不大于零时返回缺失。EastMoney 接口不提供可靠业务日期，`trade_date` 是系统采集归属日期，不是接口返回日期。

`market_daily` 以 `trade_date` 为主键，金额和广度事实字段均可为 SQLite `NULL`，并拆分保存 SSE、SZSE 和 EastMoney 来源。`update_market_daily()` 与股票、指数更新相互独立；成交额组和广度组分别保持原子性，采集失败不会用缺失值覆盖该组已有有效事实。TASK-012B 未接入 engine、报告、邮件、LaunchAgent 或 `scheduled_review`，数据层不自行判断是否收盘；未来正式使用市场广度必须由收盘门控赋予采集日期。

TASK-012B 原计划包含涨跌停统计。经数据源调研，目前没有同时满足完整沪深市场覆盖、明确计算口径和稳定历史查询的数据源，因此本任务不实现 `limit_up_count` 或 `limit_down_count`，也不以 EastMoney 非 ST 涨跌停专题池替代。完整涨跌停统计如需实现，应作为独立任务重新设计，不默认并入 TASK-012C。

TASK-012B 全量离线测试为 `307 passed`，provider 测试均使用 mock 且没有真实网络依赖。受控真实探针中 SSE、SZSE 各一次请求成功；EastMoney 请求因当前 Python `requests` 代理链路发生 `ProxyError`/`RemoteDisconnected`，真实响应结构验证被阻塞。该环境限制不证明 provider 正确或错误；项目已记录验收豁免，不通过放宽校验、增加重试或更换接口掩盖失败。探针未写正式数据库，也未运行任何自动化复盘链路。

TASK-012C 采用 `EASTMONEY_INDUSTRY` 单一行业体系，分别以 `m:90 s:2 f:!50`、`m:90 s:4 f:!50`、`m:90 s:8 f:!50` 获取东财一级、二级和三级行业。各层独立按 100 条分页，并严格核对 total、页间 total 一致性、代码唯一性和最终数量；行业代码当前严格限定为 `BK` 加四位数字，不根据名称猜测层级。

`sector_registry` 保存当前注册表名称、来源和 `is_active` 状态；完整快照只将退出或迁级的旧业务键标为 inactive，不删除历史记录。名称表示最新一次成功快照中的当前名称，不代表历史交易日名称。本任务不建立 `parent_sector_code`、父子关系、历史名称或分类有效期。

`sector_daily` 以 `(sector_type, sector_level, sector_code, trade_date)` 为主键，保存不复权 OHLC、nullable volume、amount、change_pct、source 和 updated_at。volume 与 amount 仅定义为 EastMoney provider-native 数值，缺少权威单位证据；缺失保存为 SQLite `NULL`。单行业更新会 upsert 本次全部有效记录以接收历史修订，`new_rows` 只统计新增交易日；批量更新串行执行并隔离单行业失败。

TASK-012C 不实现概念板块、行业成分股、个股行业映射、行业排名、相对收益、强弱判断、热点/主线/龙头标签、资金流、股票横截面、报告或自动化集成。定向离线验证为 `148 passed`，全量离线验证为 `412 passed`，`pip check` 为 `No broken requirements found.`；所有 provider 单元测试使用 mock 和临时 SQLite。

2026 年 7 月 21 日执行了 TASK-012C 受控真实探针，所有正式 provider 调用固定 `retries=1`。首个东财一级行业注册表第 1 页请求在取得 HTTP 或 JSON 响应前发生 `requests.exceptions.ProxyError`，底层为 `RemoteDisconnected`，因此未完成正式 provider 的三级注册表分页或行业日 K 验证，也未发生临时 SQLite 写入。临时目录在异常退出后自动删除，未写入正式数据库。该结果属于当前 Python `requests` 代理链路的环境阻塞，无证据表明 TASK-012C provider 实现正确或错误；验收豁免不通过增加重试、绕过代理、放宽校验或更换接口掩盖失败。

TASK-012C-0 接口侦察阶段曾独立取得三级行业分类接口参数、静态导航三级集合及 9 个行业的真实历史日 K 样本。这些事实支持本任务的接口设计和字段映射，但不能替代对本次正式 provider 实现的完整真实探针。

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
- SQLite 数据域使用相互独立的 `stock_daily`、`index_daily`、`market_daily`、`sector_registry` 和 `sector_daily`；初始化只增量执行 `CREATE TABLE IF NOT EXISTS`，不得重建数据库或破坏已有表。

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

当前下一步是完成 TASK-012C 最终静态审查和精确暂存；在 commit/push 闭环前不开始 TASK-012D。
