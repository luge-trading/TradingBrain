# TASK-011D：每日复盘邮件通知

## 目标与流程

用户级 LaunchAgent 每天 15:30 启动 `src.engine.scheduled_review`。通过交易日和收盘门控后，系统更新行情、生成个股报告与每日汇总，再通过 Gmail SMTP SSL 发送复盘邮件。非交易日和收盘前正常跳过，不发送邮件，避免无报告通知和无意义打扰。

发送规则：`completed` 发送正常通知；`completed_with_errors` 发送“部分失败”通知；调度流程 `failed` 时尝试发送无附件失败通知；`skipped_non_trading_day` 和 `skipped_before_close` 不发送。显式 `--force` 完成后仍按完成状态发送。

## 配置与安全边界

非敏感配置位于 `config/email.toml`：SMTP `smtp.gmail.com:465`、SMTP SSL、20 秒超时、发件人与收件人、标题前缀、附件开关及 Keychain service。更改收件邮箱时只修改 `message.recipients`。设置 `enabled = false` 可长期关闭邮件；单次运行增加 `--no-email` 可临时关闭。

Gmail 应用专用密码只存于当前用户 macOS Keychain，account 为 `louislu0008@gmail.com`，service 为 `com.luge.tradingbrain.smtp`。配置、plist、环境变量、日志、数据库和报告均不存储密码。可安全确认条目是否存在，但不要输出其值：

```bash
security find-generic-password \
  -a "louislu0008@gmail.com" \
  -s "com.luge.tradingbrain.smtp" \
  >/dev/null \
  && echo "SMTP_KEYCHAIN_OK"
```

生产模块使用绝对路径 `/usr/bin/security` 在内存中读取凭据，随后以标准库 `smtplib.SMTP_SSL` 和系统默认 TLS 校验连接 Gmail。不会关闭证书校验，不使用第三方邮件依赖，也不会重试 SMTP 发送。

## 手动验证与自动发送

先验证配置和 LaunchAgent：

```bash
./scripts/manage_launchd.sh validate
./scripts/manage_launchd.sh reload
./scripts/manage_launchd.sh status
```

正式 launchd plist 显式传递项目绝对路径的 `--email-config`。下一次满足交易日和收盘条件的 15:30 复盘会自动发送。若需手动运行但不发送邮件：

```bash
.venv/bin/python -m src.engine.scheduled_review --no-email --help
```

## 内容、附件和失败隔离

邮件正文包含市场日期、复盘状态、起止时间、成功/失败股票数、高风险提示、股票代码、汇总路径和免责声明。汇总 Markdown 存在且 `attach_summary = true` 时以 UTF-8 Markdown 附件发送；失败通知没有汇总时仍可发送。

邮件通知在复盘 JSONL 写入后执行。Keychain、TLS、认证或 SMTP 失败只输出安全错误，不包含凭据、邮件正文或附件内容；它不会删除或回滚行情数据、SQLite 数据库、个股报告或每日汇总，也不会改变原始复盘状态和退出码。

日志位置：

```text
logs/launchd-stdout.log
logs/launchd-stderr.log
logs/scheduled-review.jsonl
```

## 故障排查与卸载

- Gmail 认证失败：确认应用专用密码仍有效、Keychain account/service 匹配，并检查 Gmail 账户安全设置；不要把密码复制到配置或日志。
- Keychain 权限问题：在当前登录用户会话中执行上面的存在性检查，检查“钥匙串访问”的访问控制提示。
- TLS 或网络失败：确认可连接 `smtp.gmail.com:465`，查看 stderr 中经过安全处理的 host/sender 信息。
- 未收到邮件：先检查 JSONL；跳过状态本来就不会发送，再查看 stdout 的 sent/skipped 和 stderr 的 failed 信息。
- 永久卸载整个自动任务：执行 `./scripts/manage_launchd.sh uninstall`。若只永久关闭邮件而保留复盘调度，将 `config/email.toml` 的 `enabled` 改为 `false` 后执行 `reload`。

邮件只用于研究复盘，不构成投资建议。系统不会自动下单，也不会通过邮件暴露数据库、日志或认证信息。
