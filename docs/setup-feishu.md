# 飞书官方 CLI 接入

本项目使用飞书团队维护的 [lark-cli](https://github.com/larksuite/cli)。只通过官方 CLI 访问已授权的服务端数据，不依赖桌面点击、客户端数据库或 Cookie。初始功能是只读采集；企业是否允许使用、应用上线和权限审批由组织规则决定。

## 1. 安装并检查

需要 Node.js 与 npm 安装官方 CLI。本项目集成时核验的版本为 `1.0.99`；如采用更新版本，请先查看其 help/schema，尤其是权限与 JSON envelope 的变化。

```sh
npm install -g @larksuite/cli@1.0.99
lark-cli --version
```

采集器使用已安装的原生二进制。找不到时会报告不可用，不会自行安装或登录。

## 2. 使用独立 profile 配置应用

先查看是否已有你要使用的配置，避免替换现有应用。新建独立 profile 的官方流程：

```sh
lark-cli config init --new --name codex-assistant --brand feishu --lang zh
```

CLI 输出飞书官方页面地址，需本人在浏览器完成设置。不要把密码、App Secret 或授权页面中的敏感数据贴进聊天、配置示例或 Issue。应用配置成功还不等于用户 OAuth 已完成。

## 3. 请求明确的只读用户权限

先根据所需业务查当前 CLI 的 help/schema。以下是本项目初始读取范围的示例：

```sh
lark-cli --profile codex-assistant auth login \
  --scope 'search:message im:message:readonly im:message.group_msg:get_as_user im:message.p2p_msg:get_as_user im:chat:read calendar:calendar.event:read calendar:calendar:read task:task:read mail:user_mailbox.message:readonly' \
  --no-wait --json
```

这些 scopes 面向消息搜索/上下文、主日历、本人任务及邮箱读取，不包含发送邮件、创建日程或修改任务的权限。只需部分来源时可以进一步缩小范围；其他功能应在真正需要时增量授权。

**不要用 `--recommend` 代替最小权限。** 集成时核验的 CLI 把它等同于全部业务域，并非“只读推荐集”。权限以所装版本的 help/schema 和实际授权页面为准。

本人完成官方页面授权后，继续 CLI 输出约定的 device flow，并核验状态：

```sh
lark-cli --profile codex-assistant auth login --device-code <本次临时设备码>
lark-cli --profile codex-assistant auth status --json
```

`<本次临时设备码>` 是说明占位符，不要原样执行，也不要提交到文件或 Git。临时链接失效时启动新流程，不能长期缓存设备码。由 Codex 协助时应由当前会话在内存中处理本次授权，不把认证值写进助理账本。

`auth status` 显示 Bot ready 不能证明个人数据可读。业务调用固定 `--as user`；用户身份缺失就报告缺口，不改用 Bot。切换组织或 profile 后应再次核对本人和组织。

### 需要管理员审批时

如果组织要求审批，等待管理员处理即可，不要反复创建应用或扩大到全域读写权限。可以在本地 `config.json` 中记录：

```json
{
  "integrations": {
    "feishu": {
      "profile": "codex-assistant",
      "authorization_status": "pending_admin_approval",
      "grant_mode": "read_only"
    }
  }
}
```

这是需要合并到现有配置的局部片段，不是完整配置文件。该字段供助理工作流识别等待状态；手动运行采集命令仍会真实尝试 CLI。例行运行应跳过重复认证，继续 Gmail 和本地计划。管理员批准后仍需完成用户身份授权和真实接口验证；审批通过本身不能证明所有来源都已接通。

## 4. 验证当前命令和真实读取

```sh
lark-cli im +messages-search --help
lark-cli calendar +agenda --help
lark-cli task +get-my-tasks --help
lark-cli schema mail.user_mailbox.messages.list
```

再用 `assistant.py collect-feishu` 检查一个有限窗口。所有业务命令使用所选 profile 和 `--as user`。输出包含每个来源状态、范围、数量和快照路径；来源独立失败，不会清空原有任务和日程。

| 来源 | 采集内容与边界 |
| --- | --- |
| `feishu_messages` | 指定窗口的可访问消息搜索；搜索命中不是完整会话 |
| `feishu_calendar` | 指定窗口的本人主日历 agenda；不代表共享或其他工作日历 |
| `feishu_tasks` | 本人未完成任务；不套用消息的时间过滤 |
| `feishu_mail` | 本人邮箱消息列表；需要邮箱实际开通，正文须另读 |

未确认分页耗尽的成功结果保持 `partial`。达到页数/输出限制不能称为全量同步；核实重要前后文需要后续精确读取。

## 5. 日历写入另行处理

项目默认只生成本地 ICS。只有用户明确要求把具体计划写到在线日历时，才按当前 schema 申请最小写权限，重新检查忙闲并预览具体标题、时间和日历。发布幂等的本人专注块与发会议邀请是不同操作；初始只读授权不允许代替用户向同事发消息。

当前回归测试验证本地和模拟采集行为。真实飞书用户数据和在线日历写入依赖实际账户、企业审批与权限，未完成这些步骤时不能宣称端到端验证成功。
