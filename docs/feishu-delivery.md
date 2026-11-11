# 飞书交付：白天提醒，每晚一份总结

默认例行输出保持简洁：09:00、14:00、18:00只向本人飞书AI助理对话发编号提醒；21:00只保存一份当天工作总结，再给本人简报和已核实链接。它不需要Dot，不自动改日历，也不再例行新建/更新项目、目标、学习、知识、导航或周报文件。

## 用户看到什么

| 时段 | 格式与保存方式 |
| --- | --- |
| 09/14/18 | 编号1–3条、约80–160汉字，一条一结论；无变化只1条下一步。仅消息，不落新云文档或本地MD/ICS |
| 21:00 | 一份`YYYY-MM-DD 工作总结`：今日成果≤3、问题/需关注≤2（无则省）、明日计划≤3，总约250–400字；信息少可更短，不凑字数 |
| 用户要求周报 | 约400–600字，成果/风险/下周计划各≤3项；不增加定时文件或周报云文件。若当晚生成，合并当晚同一份总结 |

总结正文不放方法细节、源码路径、表格、证据、ID、冗长学习推荐或来源覆盖表，云标题已有日期时不必在正文重复标题。只有影响决策的重要缺口可在末尾补一句。技术细节维护在适用且已有授权的现有需求/技术文档；不要为转移冗余另建一堆文件或擅改无关文档。内部journal、完整机器JSON、审阅快照和manifest仍可私有追溯，不算用户可见文档。每天一份只限制例行助理输出；用户另行明确要求创建/更新需求或技术文档时，按独立请求处理，不影响研发文档正常维护，已有需求文档保留。

日终文档每日唯一key为`brief:<date>:review`，目录沿用本人专属根下的`01 每日工作/YYYY/MM`，同key重跑更新原文档。已有同日期review key时，沿用manifest实际绑定的title、folder和token，不能因旧标题叫“工作日报”就换key重建；新日期默认标题才是“YYYY-MM-DD 工作总结”。历史项目/目标/知识/导航等云文件默认保留，不擅删、移动或批量整理，也不再为了分类每天新增或更新。通知key固定`notify:<date>:morning|changes|stage|review`，不随机换key重复发。

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
      "content": "## 今日成果\n1. 人工示例：完成了本次已确认的小目标。\n\n## 明日计划\n1. 先验证下一步结果，再决定后续安排。"
    }
  ],
  "notification": {
    "key": "notify:2026-11-11:review",
    "text": "1. 人工示例：今天的工作总结已整理。实际发送时仅附读回核实的当日总结链接。"
  }
}
```

例行工作只装这一份文档，不附加晨间/变化/阶段文件、项目总览或根导航。key以字母/数字起始，后续可用ASCII字母、数字和`_ . : -`，不含斜杠；folder逐段传入，最多6层，不含路径分隔符、`.`/`..`或控制字符。通用接口上限仍为30文档/总内容500KiB，但这不改变例行只发布一份日终文档的约定。

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
