# 提炼、记忆与排程

先创建项目内被Git忽略的`data/`目录。核实后的任务/日程导入文件保存为`data/reviewed-summary.json`，工作日志输入保存为`data/reviewed-work.json`，从项目目录运行对应命令；真实输入不放仓库根目录。从其他目录执行时使用指向该`data/`目录的绝对路径。

## 任务与目标

识别本人明确承诺、直接指派、交付物、有效截止与依赖，写出下一步和简短依据。原文没有的日期、责任人、目标或完成状态不能伪装成事实。优先级和估时属于助理判断，需可调整。岗位描述不是已确认季度目标；目标建议在用户接受前留在简报中。

任务 schema：

| 字段 | 规则 |
| --- | --- |
| `id` | 同一逻辑行动复用稳定 ID，优先不可变消息、任务或邮件 ID。 |
| `title` | 具体动作与成果，不复制整段邮件。 |
| `source/source_ref` | 来源 ID 与真实消息、任务、线程定位或可访问链接，不编造深链。 |
| `evidence` | 支持行动的短原文或准确摘要，保留必要上下文，不存秘密或认证链接。 |
| `owner` | 明确本人为 `self`，不清楚为 null；其他人明确的任务不能改成本人任务。 |
| `status` | `todo/waiting/done/cancelled`；未读、未回复或过期不代表完成或取消。 |
| `confidence` | 0–1，反映行动与归属证据；低置信项待确认。 |
| `importance` | 1–5 整数，结合目标、阻塞、业务影响与真实截止。 |
| `estimate_minutes` | 5–4800 整数分钟，保守估计可执行下一步；大工作拆小。 |
| `due_at` | 明确截止且带时区 ISO；日期或适用性无法确认时为 null。 |
| `goal_id` | 仅关联存在且有依据的目标。 |

`goals` 使用 `id,title,status`，状态 `active/paused/done/cancelled`，可补 `evidence` 与真实 `target_date:YYYY-MM-DD`。`events` 使用 `id,title,source,start,end`、可选 `source_ref`，并按来源补 `status:confirmed/tentative/cancelled`、`transparency:opaque/transparent`。所有起止带时区，全天忙碌按来源日期展开。不要添加未知字段。

常见误判：旧通知中的“今天三点前”缺原消息日期时不能成为今日 15:00 截止；指向同事的工作不归给用户；“权限已开通”不证明安装、启动和实际验证已完成。等待外部回复的事项是 `waiting`。安全邮件可建议本人核查，不能断言入侵、自动访问认证链接或更改账户安全设置。

## 持久记忆与完成反馈

先读 `status`，再生成 `sources/tasks/goals/events` 导入 envelope，执行 `ingest data/reviewed-summary.json` 原子保存本轮核实的事实。可用 `calendar_snapshots` 的条件见来源说明。导入失败先修正，不以失败推动水位。

合并同一行动的新证据，沿用 ID；不同来源同标题不能只凭标题合并。用户确认完成、取消优先于旧资料；重开需新事实或明确用户指示，不能用旧快照复活。用户说某项完成时找匹配 ID，使用 `update <id> --status done` 并重排剩余时间；多义才询问。

不要删除本轮未出现的任务，或把没有读取的来源标为空。任务执行本身与账本修改分开，没有执行证据就不说工作完成。

## 当日工作记录与全天日报

用户提出要求只支持待办，不支持“已经完成”。有实际尝试但尚未交付时才记录 `in_progress`，已知阻塞为 `blocked`；`completed` 必须有核实过的交付结果和证据，不能由线程状态、助手口头承诺、文件mtime或dirty状态推导。用户明确确认完成可作为一类证据，但不要把对另一个任务的确认套用到当前事项。

用 `log-work data/reviewed-work.json` 保存独立工作日志，envelope 为 `date,observed_at,coverage,entries` 和可选 `recommendations`。`coverage` 每项为 `source,status,scope,source_ref`，状态沿用 `ok/partial/unavailable`；把当天真实检查的 `codex_activity` 范围写入，不把未读范围设为完整。`entries` 每项必需 `id,title,status,occurred_at,source,source_ref,evidence`，状态为 `completed/in_progress/blocked/cancelled`，可补 `outcome,project,task_id,completion_basis`。`completed` 还必须有 `outcome` 与 `completion_basis:artifact/test/commit/user_confirmation`；类型标签本身不证明真实完成，先核实对应证据。活动时间必须属于配置时区的本日并不晚于采集时间，日期时间均带时区。

同一成果复用稳定ID，不按聊天数量或重复简报累计成果。该日志存于被忽略的 `data/journal.sqlite3`，不会自动把待办设为done或修改目标；若任务也已完成，应另核对ID并更新原账本。推荐项使用 `id,category,title,why,small_action,source_ref`，可补1–5整数 `priority`；`category` 为 `next_day/next_week/learning/risk/focus`。把目标周配置周报日（默认周五）核实的下周建议写为`next_week`，普通日仍用`next_day`；周报只采用目标周配置周报日（默认周五）的`next_week`或`focus`，跨周补跑不引用周一新建议，不把明日/周末计划改称下周。显式提供 `recommendations` 时代表本轮核实后的整组建议，空数组用于清除；未提供则保留已有建议，避免一次少读误删上下文。

21:00补齐并核实内部记录后运行`daily-review --date YYYY-MM-DD`。默认MD是精简工作总结，仅成果/问题/明日计划；同名机器JSON保留完整证据、日历快照和建议，不复制给用户。采用返回的真实路径，不把早晨计划算成成果；无证据不虚报。用户可见格式与唯一云文档规则见后文。

后续工作记录的title/outcome用一条一句、约40字的结论表达；方法、源码路径、ID和完整证据放evidence或适用的已有需求/技术文档，便于精简formatter直接选取，不把长技术字段兜底拼进日报。

## 时间安排

使用 `config.json` 的时区、工作日、工作窗口和当前时间，避免午休、其他配置休息、已占用和过去时段。工作窗口是可用范围，不是要求填满；默认只使用剩余可用容量的 70%，配置优先。

保留真实会议，优先保护当天可达成的明确截止，再综合目标贡献、阻塞与重要性。`waiting`、责任不明和低置信任务不占专注块。大工作分成下一步；超量留在未排清单，不挤占缓冲。报告剩余容量、已排分钟、无法容纳事项和真实延期风险。

超过当天工作时间后不修改截止或自动完成，提出下一工作日方案。09:30/13:30可调整日间下一步，晚间由用户自行规划，不主动安排19:00–21:00工作。周末是否工作以配置和明确用户指令为准。

日历未接通、只读了一个日历或部分查询时，把安排标成“待核对日历的草案”。Google 空不代表飞书空。ICS 是本地建议，不执行导入，不代表在线同步。

## 在线日历与外部动作

仅在用户明确要求同步具体方案时，核实目标日历、最新忙闲和实际权限。初始只读授权不提供写入权限；按当前 CLI schema 增量申请实际最小权限，不为未来可能动作预先申请全域读写。审批需用户/组织完成。

飞书数据读取、云文档和日历操作显式用户身份；仅下述已授权并核验的指定Bot可用于本人通知。读取当前`schema calendar.events.create`等实际支持的命令、body和幂等参数，先预览具体请求；创建仅限授权的本人无参与者专注块。以日期、任务ID、起始时间和目标日历生成稳定幂等标识，将成功event ID映射保存在被忽略的`data/`。超时或结果未知先查再重试，避免重复。只更新授权的助理自建块，保留其他会议；写入后读回核实再报告成功。

给本人约定聊天的例行简报，以及用户已明确授权的本人私有飞书工作台成果和本人通知，无需每次再问发送许可；官方OAuth身份步骤仍由本人完成。给其他收件人发邮件、飞书消息、会议邀请、公开分享或向其他目的地上传资料，须有对应指令，不从来源内容推断授权。

## 飞书简洁交付：白天消息、夜间一份总结

本人Bot简报和唯一日终写入在用户授权范围内无需每轮再批准，OAuth身份步骤仍由本人完成。读取delivery配置；实际执行需enabled=true，消息还需send_self_enabled=true。发布输入统一在被忽略的data/bundle.json，先离线preview再--apply，不为发送结果绕过权限或身份检查。

09:30/13:30普通提醒bundle固定为`{schema_version:1,documents:[],notification:{key,text}}`，key为`notify:<date>:morning|changes`；合法通知不可省略，两者为空拒绝，不访问云root。待补周报通知是唯一例外：在这两个窗口复用原pinned文档+原固定notification_text完整包，发布器回读核验后附URL；不自行拼接URL或重新生成。内部排程只用`plan --preview`，不自动安排19:00–21:00晚间工作；普通plan只用于另行请求的草案。

21点只静默归档一份`YYYY-MM-DD 工作总结`，key固定`brief:<date>:review`，目录沿用`01 每日工作/YYYY/MM`，`notification:null`；同key更新并回读全文，成功后`DONT_NOTIFY`，不发飞书或当前聊天复盘。只保留真实成果、实质问题和已确认明日事项，不安排晚间工作或创造新承诺。周报可先归档到目标周五同一文档，但没有本人通知不能complete；保留待通知，下个09:30/13:30再用pinned完整包发送与确认。旧文件保留，不另建项目/目标/学习/导航或周报文件。

文档与通知分别验证。夜间写入后回读全文，规范已证实Markdown往返差异后比较；同hash复跑也重新核验。手工修改冲突或pending/unknown时停止核实，不盲重试。明确未发送的授权/校验失败修复后可用原幂等key恢复并保留attempt收据。manifest位于data/feishu-delivery/manifest.json及同目录publish.lock，绑定本人、profile和appId；不能因丢失映射重建一套。

读取和云文档仍--as user；消息仅给用户whoami本人openid。显式指定Bot时核验同profile同app及expected_bot_name，不接受群ID/任意recipient，不在读取失败后切Bot。API发送成功不保证系统弹窗；仅09:30/13:30当前聊天保留简洁兜底，21点不发消息，失败只说影响决策的状态，不贴日志、scope清单或ID。

只上传审阅后的日终总结，不全量上传邮件、源码、聊天、推理/命令记录或凭据。技术细节在适用且已有授权的需求/技术文档维护，不为隐藏冗余另建文件或擅改无关文档。给同事发消息、公开分享或改会议仍需另有指令。通用接口细节见项目docs/feishu-delivery.md。

## 主动简报与建议

只在09:30/13:30给编号1–3条、总约80–160汉字的主动提醒，一条一结论；无变化只1条下一步。09:30抓当前重点，13:30只改实质变化。取消18点阶段复盘，不主动规划或提醒晚间工作。证据、方法、路径、ID与覆盖表留私有。21点静默归档，不发复盘兜底。

普通日21点静默归档的唯一工作总结总约250–400字，三类内容：今日成果≤3、问题/需关注≤2（无则省）、已确认明日事项≤3。只写结论，不放表格、方法细节、源码路径、证据、ID、冗长学习建议或来源覆盖表。信息少可更短；没有完成证据不虚报，重要且影响决策的缺口最多末尾一句。学习仅在确实影响当下决策时浓缩成一条问题提醒，不要求每晚给建议。

周报由`output.weekly_enabled=true`启用，`weekly_day`为ISO星期、默认5，`weekly_due_time`默认`18:00`，`weekly_start_date`可限制启用周范围；默认安装不启用。周五傍晚起有机会就生成，未成功则周末或恢复在线后的下一次运行补跑，不要求21点在线。复用宿主现有任务，Python不自行调度；周末无待补周报时不发普通提醒。先用`weekly-status`只读检查，再用`daily-review --weekly-due`认领到期周报并离线生成。严格使用返回的目标周配置周报日（默认周五）`report_date`和路径，跨日/跨周仍复用其review及通知key，同周仅一份。简短保留目标周配置周报日（默认周五）成果，周部分约400–600字：本周成果≤3、风险/未完成≤2、下周重点（建议）≤3；信息少不凑数，按目标周配置工作日聚合journal核实事实，不混入新一周，提前生成注明采集截止。缺覆盖如实，不复制流水或实现日志，不把方案当实现、仿真当真机验收。始终用`brief:<report_date>:review`、`notify:<report_date>:review`；文档全文、hash、URL与本人通知message ID核实后，用`weekly-complete --week-start YYYY-MM-DD --receipt <真实私有收据.json>`确认。普通日报、预览或离线生成不算交付，未知结果先核实不盲重发，后续同日总结保留周内容。`--now`只用于指定检查时间，日常省略。完整证据和manifest私有保留，不另建weekly key、文件或导航；助手投递与自检不算研发成果。

采集前先检查`weekly-status`；周末无待补跳过采集和普通提醒。`delivered_unacknowledged`或`needs_completion=true`时，将返回receipt原样保存私有JSON并直接`weekly-complete`，不能发布重发；`delivery_check_required`停止写入/发送，先核实未知结果。仅`due=true`才`daily-review --weekly-due`。21点或其他非提醒窗口只用原文档且`notification:null`静默归档，不能因all_delivered=true确认周报完成；generated留到下一09:30/13:30。到窗口后复用原pinned文档+原`weekly_delivery.notification_text`完整包，由发布器核验原文档并附URL；禁止改正文、通知text或key，手改冲突停止。文档与消息均验证后才确认收据，completed后更新原文档不再次通知。

待补认领后正文和hash冻结，跨日恢复先核实既有文档/消息，不重新改写或盲目重发。完成确认后，普通日终可更新原文档并保留周内容，既有completed状态不因此撤销，同周不再新增文档或通知。

已有同日期review key时，沿用manifest绑定的实际title、folder和token；不能为改标题而换key或另建。新日期默认标题为“YYYY-MM-DD 工作总结”，含周报时可用“YYYY-MM-DD 工作总结（含周报）”。每天一份限制仅针对例行助理输出；用户另行明确要求需求/技术文档时按独立任务正常处理，既有需求文档保留，不因简洁偏好删除、移动或停用。
