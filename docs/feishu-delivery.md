# 飞书交付：白天提醒，每晚一份总结

只有09:30、13:30主动发送编号提醒；取消18点复盘，晚间由用户自行规划。21:00只静默归档当天总结，`notification:null`，不发飞书或当前聊天复盘。周报到期后仍可弹性补跑、始终并入目标周五唯一文档；非提醒窗口只存文档，待下一09:30/13:30通知与完成确认。周末没有待补则静默。它不需要Dot，不自动改日历或例行新建项目、目标、学习、知识、导航文件。

## 用户看到什么

| 时段 | 格式与保存方式 |
| --- | --- |
| 09:30/13:30 | 编号1–3条、约80–160汉字，一条一结论；无变化只1条下一步。仅消息，不落新云文档或本地MD/ICS |
| 21:00 | 静默归档一份`YYYY-MM-DD 工作总结`：成果≤3、问题≤2、已确认明日事项≤3，约250–400字；不发飞书或当前聊天复盘，不安排晚间工作 |
| 周报弹性窗口 | 默认未启用；周五18点起有机会就生成，未成功则周末/下次在线运行补跑。同周一份、每日最多一份。简短保留目标周五成果，周部分约400–600字：本周成果≤3、风险/未完成≤2、下周重点≤3 |

总结正文不放方法细节、源码路径、表格、证据、ID、冗长学习推荐或来源覆盖表，云标题已有日期时不必在正文重复标题。只有影响决策的重要缺口可在末尾补一句。技术细节维护在适用且已有授权的现有需求/技术文档；不要为转移冗余另建一堆文件或擅改无关文档。内部journal、完整机器JSON、审阅快照和manifest仍可私有追溯，不算用户可见文档。每天一份只限制例行助理输出；用户另行明确要求创建/更新需求或技术文档时，按独立请求处理，不影响研发文档正常维护，已有需求文档保留。

日终文档每日唯一key为`brief:<date>:review`，目录沿用本人专属根下的`01 每日工作/YYYY/MM`，同key重跑更新原文档。已有同日期review key时，沿用manifest实际title/folder/token，不因改标题重建；新日期可用“YYYY-MM-DD 工作总结”，含周报可标“（含周报）”。21点发布`notification:null`，回读成功后静默结束，不向飞书或本聊天发复盘。普通提醒仅用`notify:<date>:morning|changes`；周报固定目标周五`notify:<report_date>:review`，但只在09:30/13:30发送。历史文件保留，不更新导航。

自动周报需在私有配置设置`output.weekly_enabled=true`、`weekly_day=5`（ISO周五），并更新宿主现有助理任务以检查待补周报，配置不会自行调度。聚合目标周配置工作日的私有journal事实，跨周补跑不混入新一周；周五提前生成说明实际采集截止。`report_date`固定为目标周配置周报日（默认周五），即使周末或跨周补跑也沿用`brief:<report_date>:review`、`notify:<report_date>:review`，同周不再选另一日期或创建第二份文件。文档和消息都成功核实才视为交付完成；未知结果先核实，不盲目重建/重发。后续同日普通日终保留周内容。缺覆盖如实说明，不把方案或仿真当已实现或真机验收，不上传每日流水、实现日志或凭据。

每轮先`weekly-status`，有待补才`daily-review --weekly-due`并使用返回的目标周日期/路径。认领后正文和hash冻结，未知结果先核实既有云文档与消息，不重写或换key。离线生成不算已投递；全文回读和本人通知收据均验证后，才用`weekly-complete --week-start YYYY-MM-DD --receipt data/真实投递收据.json`确认，该命令校验私有manifest、正文hash、文档URL和消息ID。完成后同日普通总结可更新原文档并保留周内容，不另发第二次周报。详细状态约定见[架构与数据约定](architecture.md)。

检查早于采集；周末无待补直接结束。`state=delivered_unacknowledged`或`needs_completion=true`时，将返回`receipt`原样存为私有JSON并直接`weekly-complete`，不发布重发；`delivery_check_required`停止写入/发送，先核实。仅`due=true`才生成，发布通知原样使用`weekly_delivery.notification_text`，发布器自行附文档URL；已完成后更新原文档也沿用固定文本，避免再次通知。

## 只消息bundle：不访问云文件或root

先创建被Git忽略的`data/`目录，把审阅输入存为`data/bundle.json`。以下是仅供离线格式检查的人工示例，不代表真实工作，不要直接发送：

```json
{
  "schema_version": 1,
  "documents": [],
  "notification": {
    "key": "notify:2026-11-11:morning",
    "text": "1. 人工示例：先推进已确认的最高优先级任务，完成当前可验证的最小一步。\n2. 下午再根据实际结果调整；没有重要变化就不增加新的安排。"
  }
}
```

`documents:[]`加合法通知可以发布。documents和notification都空/缺失时拒绝；没有recipient字段，不能指定任意人或群。仅消息模式不要求、检查或创建云root，不调用drive/docs接口，也不会新建root manifest项；仍会核验用户、应用和已选Bot身份，并私有保存通知去重收据。

白天需要确定性排程时用`python3 assistant.py plan --preview --date YYYY-MM-DD`消费stdout JSON；它不创建reports/plans/MD/ICS。普通plan仅在用户另外要求日历草案时使用，不能作为例行白天任务偷偷写文件。

## 日终bundle：只包含当天一份工作总结

下例也是人工格式示例。真实正文由Codex基于核实成果压缩而来；无证据不虚报，也不能据此推断用户全天没工作。

```json
{
  "schema_version": 1,
  "documents": [
    {
      "key": "brief:2026-11-11:review",
      "title": "2026-11-11 工作总结",
      "folder": ["01 每日工作", "2026", "11"],
      "content": "## 今日成果\n1. 人工示例：完成了本次已确认的小目标。\n\n## 已确认明日事项\n1. 人工示例：保留此前已确认的验证事项。"
    }
  ],
  "notification": null
}
```

21点例行包只装这一份文档、无通知，不附加晨间/变化/阶段文件、项目总览或根导航。待补周报在下一提醒窗口是日间仅消息规则的唯一例外：复用原pinned文档+原`weekly_delivery.notification_text`完整bundle，让发布器回读原文档并附核验URL；不重新生成正文，也不改用缺URL的notification-only包。手改冲突先停止，周报消息验证成功后才complete；仅文档包的all_delivered=true不等于周报通知成功。key以字母/数字起始，后续可用ASCII字母、数字和`_ . : -`，不含斜杠；folder逐段传入，最多6层。通用接口容量不改变例行每日一份的约定。

## 预览与执行

```sh
python3 assistant.py publish-feishu data/bundle.json
```

默认纯离线预览，不触网、不输出正文。`mode=preview`、`all_delivered=false`，预览通过不是已发送。仅消息预览有`root_required:false`，不会因缺云root阻止白天提醒。

对真实且已审阅、本人授权范围内的内容显式执行：

```sh
python3 assistant.py publish-feishu data/bundle.json --apply
```

Windows可把入口换为`./run-assistant.ps1 publish-feishu data/bundle.json`。执行需要`delivery.enabled=true`，消息还需`send_self_enabled=true`；默认均关闭。已有本人Bot提醒与唯一日终写入授权无需每轮再次批准，官方OAuth身份步骤仍由本人完成。待审批不反复认证或催促。

## 配置和权限

私有`config.json`的`integrations.feishu.delivery`包含：

- `enabled`、`send_self_enabled`：实际启用开关，默认false。
- `message_sender`：默认user；显式授权指定智能体时可用bot，并要求`expected_bot_name`。
- `expected_owner_id`：可选预期本人openid，只放私有配置。
- `root_name/root_token/root_url/folder_discovery`：只在文档发布时需要。`list`依赖目录列表权限；`managed_only`必须使用实际专属根的真实root_token，仅管理manifest里的自建文档，不调用列表或按名称猜目录。仅消息完全不依赖这些字段。

数据读取、日终云文档和目录使用用户身份。Bot通知须与所选用户profile同应用，通过官方`/open-apis/bot/v3/info`核验`app_name`与`expected_bot_name`精确匹配；收件人固定用户whoami本人openid，无群发、不因读取失败切Bot。

文档功能按需要使用`docx:document:create`、`docx:document:readonly`、`docx:document:write_only`、`space:folder:create`；列表发现另需`space:document:retrieve`。用户身份消息需相应`im:message.send_as_user`和`im:message`；指定Bot路线使用应用身份的`im:message:send_as_bot`，不靠本人OAuth取得Bot权限。以当前官方schema、明确功能清单和实际授权核实，不用`--recommend`或未知未来需求扩大到所有权限。邮件正文或全局搜索的权限缺口不会因开启交付而自动补足。

## 去重、回读和失败

私有映射为`data/feishu-delivery/manifest.json`，锁为同目录`publish.lock`，绑定本人、profile和appId。日终创建/更新后回读全文，规范已证实Markdown往返差异后完整比较；同hash重跑也重新核验。手工改动用fingerprint和revision检测冲突，不覆盖。pending/unknown停止核实，不自动重发；明确未发送的授权/校验失败修复后可用原幂等key恢复并保留attempt收据。

文档状态包括verified/unchanged/failed/unknown/partial/conflict，通知另有sent/unchanged/not_requested/skipped_missing_permission/blocked_documents/unknown/failed/conflict。只有所请求文档都已核实、通知也成功时all_delivered才true；仅消息时不要求文档或root。文档成功和通知成功分别报告，不能把API成功承诺成系统弹窗。

日终消息只附当日总结的真实verified URL，不每轮更新根导航，不按token拼链接。正文不放本机路径或证据ID；完整资料只留私有记录，必要技术内容放已有需求文档。原聊天保留同样简洁的兜底；不公开分享、不发同事、不移动旧资料、不全量上传邮箱、源码、聊天或凭据。
