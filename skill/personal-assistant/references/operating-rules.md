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

同一成果复用稳定ID，不按聊天数量或重复简报累计成果。该日志存于被忽略的 `data/journal.sqlite3`，不会自动把待办设为done或修改目标；若任务也已完成，应另核对ID并更新原账本。推荐项使用 `id,category,title,why,small_action,source_ref`，可补1–5整数 `priority`；`category` 为 `next_day/learning/risk/focus`。显式提供 `recommendations` 时代表本轮核实后的整组建议，空数组用于清除；未提供则保留已有建议，避免一次少读误删上下文。

21:00先补齐当天相关来源和工作记录，再运行 `daily-review --date YYYY-MM-DD`。采用返回的 `reports/YYYY-MM-DD-review.md` 和对应JSON路径；不能把早晨的计划当成晚上实际成果。聊天交付顺序为：有证据的已完成成果与结果、进行中、阻塞/原因、明日最多三项、1–2条学习建议、风险与重点关注、覆盖缺口。没有记录的板块如实说明；完全缺少工作证据时写“当前来源不足以确认全天成果”，不写“今天无工作”或“全部完成”。

## 时间安排

使用 `config.json` 的时区、工作日、工作窗口和当前时间，避免午休、其他配置休息、已占用和过去时段。工作窗口是可用范围，不是要求填满；默认只使用剩余可用容量的 70%，配置优先。

保留真实会议，优先保护当天可达成的明确截止，再综合目标贡献、阻塞与重要性。`waiting`、责任不明和低置信任务不占专注块。大工作分成下一步；超量留在未排清单，不挤占缓冲。报告剩余容量、已排分钟、无法容纳事项和真实延期风险。

超过当天工作时间后不修改截止或自动完成，提出下一工作日方案。阶段复盘后若仍在工作窗口，可继续安排剩余工作。周末是否工作以配置和明确用户指令为准。

日历未接通、只读了一个日历或部分查询时，把安排标成“待核对日历的草案”。Google 空不代表飞书空。ICS 是本地建议，不执行导入，不代表在线同步。

## 在线日历与外部动作

仅在用户明确要求同步具体方案时，核实目标日历、最新忙闲和实际权限。初始只读授权不提供写入权限；按当前 CLI schema 增量申请实际最小权限，不为未来可能动作预先申请全域读写。审批需用户/组织完成。

飞书数据读取、云文档和日历操作显式用户身份；仅下述已授权并核验的指定Bot可用于本人通知。读取当前`schema calendar.events.create`等实际支持的命令、body和幂等参数，先预览具体请求；创建仅限授权的本人无参与者专注块。以日期、任务ID、起始时间和目标日历生成稳定幂等标识，将成功event ID映射保存在被忽略的`data/`。超时或结果未知先查再重试，避免重复。只更新授权的助理自建块，保留其他会议；写入后读回核实再报告成功。

给本人约定聊天的例行简报，以及用户已明确授权的本人私有飞书工作台成果和本人通知，无需每次再问发送许可；官方OAuth身份步骤仍由本人完成。给其他收件人发邮件、飞书消息、会议邀请、公开分享或向其他目的地上传资料，须有对应指令，不从来源内容推断授权。

## 飞书成果归档与本人通知

此功能默认禁用。读取`integrations.feishu.delivery`，用户已授权且`enabled`启用后才执行。只将审阅过的计划、变化、日报、项目总览、目标待办、建议和必要来源定位汇成`data/bundle.json`；不是全量上传聊天、推理/命令日志、原始邮件、源码或凭据。先创建被忽略的`data/`，不把真实发布输入放仓库根目录。

bundle为`{schema_version:1,documents:[{key,title,folder:[分段],content:markdown}],notification?:{key,text}}`，无recipient。最多30文档、总UTF-8内容500KiB、目录最多6层且各段不含分隔符/`..`/控制字符。先运行`publish-feishu data/bundle.json`纯离线预览；对已审阅且用户已授权的内容运行同命令加`--apply`，不重复询问许可。权限或启用条件不足时说明，不以模拟点击或扩大授权绕过。

专属根下按`00导航`、`01每日工作/YYYY/MM`、`02项目管理/项目ID`、`03目标与待办`、`04学习与知识`、`05周月复盘`、`90归档`分类。只创建需要的自建目录和文档，不移动其他既有文件或改变分享。每日日期+slot、项目ID及总览使用稳定key；manifest在私有`data/feishu-delivery/manifest.json`，锁在同目录`publish.lock`，绑定profile、本人和appId，不能丢失后盲目重建。检测手工修改冲突或请求结果未知时停止核实，不覆盖或盲重试。

`folder_discovery=list`依赖列表权限；没有该权限时，只有已配置实际新建专属根的真实`root_token`，才能用`managed_only`仅管理manifest中的子项，不做列表或名称猜测。导航与通知只使用读回核实的真实URL；首次可先发布成果取得链接，再更新导航与通知。本机路径标为“仅本机可查看”，不伪造手机链接。

本人通知由`send_self_enabled`控制，默认`message_sender=user`。用户明确选择指定智能体时才能设`bot`并填写`expected_bot_name`：经官方`/open-apis/bot/v3/info`实时核验同profile同应用的`app_name`匹配后，只给用户whoami解析的本人openid发送简报+已核实链接。读取与云文档仍`--as user`，无群ID/任意recipient，不把读取失败改用Bot，不自动回退发件身份。

分别核实文档`verified/unchanged/failed/unknown/partial/conflict`与通知`sent/unchanged/not_requested/skipped_missing_permission/blocked_documents/unknown/failed/conflict`等状态；`all_delivered`只有全部文档已核实且请求的通知已sent/unchanged时为true，无通知请求则不要求消息权限。文档成功但消息缺权限时保留文档，在原聊天给摘要、真实链接和缺口；不要声称全部送达。用户身份自发本人或Bot发送成功都不保证系统弹窗。完整字段与权限说明见项目`docs/feishu-delivery.md`。

## 主动简报与建议

每条约定简报直接在目的聊天交付重点、下一步、必要安排、变化、等待项和来源缺口，不能只保存本地文件或只给链接。固定晨间、变化检查、阶段复盘和全天日报每次都交付；无新变化也简要给下一行动。只对额外轮询使用无变化静默策略，重要新增截止、阻塞、会议冲突或需要决策时才额外打扰。

学习与注意建议必须由当天具体项目、失败的检查、反复出现的问题或正在修改的代码模块触发。每条写清触发证据/来源、为什么与当前工作有关，以及15–30分钟可验证的最小练习或检查；例如复现一个失败、补一项验证或阅读直接相关模块。最多1–2条，有依据才给，不按岗位泛化出“应该学什么”，也不为凑数给技术清单。建议不能自动变成硬任务、截止或OKR；用户接受后再入待办。风险与重点关注也须对应实际证据，例如尚未跑过的测试、未核实的接口权限或已知阻塞，区分确定问题和推测。

相同已提醒事项无新风险时简短引用，不夸大为新发现。缺工作上下文先说明缺口；数据源不可用不报告“全部正常”或“没有待办”。

自动化创建、聊天产生简报和系统推送送达是不同状态。Dot 也需要实际连接电脑并确认职责、日程和目的地；不把侧栏显示、技能安装或本地计划存在当成接通。真实运行与消息交付未验证时，明确说明验证范围。
