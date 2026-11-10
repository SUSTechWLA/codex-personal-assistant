# 架构与数据约定

本项目把模型判断与可重复执行的本地操作分开：Codex 决定哪些信息值得行动，Python 验证并保存结果，然后按明确的工作时间和已知日程排程。

```mermaid
flowchart LR
    F[飞书官方 CLI] --> R[只读原始快照与来源覆盖]
    G[Gmail / Google Calendar 连接工具] --> C[Codex 核实上下文与提炼]
    A[当天相关 Codex 工作聊天 / 选定 Git 证据] --> C
    R --> C
    U[用户偏好与完成反馈] --> C
    C --> J[审阅后的 JSON]
    J --> I[原子 ingest]
    I --> D[(SQLite 本地账本)]
    D --> P[确定性 plan]
    P --> O[Markdown 日报与 ICS 草案]
    C --> W[log-work 验证工作记录]
    W --> JDB[(私有工作日志)]
    JDB --> E[daily-review 全天日报]
    E --> H[聊天简报 / 已配置的 Dot 工作流]
    O --> H[聊天简报 / 已配置的 Dot 工作流]
```

## 组件

| 文件 | 职责 |
| --- | --- |
| `assistant.py` | 配置验证、SQLite、导入、状态更新、排程及输出 |
| `collect_feishu.py` | 调用已有的官方原生 CLI，采集四类只读来源 |
| `daily_review.py` | 独立工作日志、成果证据校验、全天日报与结构化结果，不修改任务完成状态 |
| `run-assistant.ps1` | Windows Python 运行时选择，不修改 PATH |
| `install_skill.py` | 将可复用技能安装到选定位置并绑定项目根目录 |
| `skill/personal-assistant/` | Codex 的来源读取、事实提炼、主动简报与操作约定 |

没有额外模型服务或常驻进程。定时由宿主应用执行；安装技能不会自动创建定时任务。

## 本地状态

SQLite 的 `sources`、`goals`、`tasks`、`events` 表按稳定 ID 保存 JSON 记录，`history` 保存变更。`ingest` 在校验整个批次后通过事务提交；无效来源、目标引用或记录会使整批失败。

重复的相同 ID 更新原记录。导入旧消息不会把 `done` / `cancelled` 任务重新设为活动状态；重开需要明确的新事实和用户意图，由 `update --status todo` 或显式 `reopen:true` 表达。稳定 ID 防止同一事项重复，不意味着跨服务的同名任务可以盲目合并。

## Codex 活动与成果证据

工作流在运行时发现宿主提供的 `list_threads/read_thread` 等能力，不直接读取Codex私有数据库。只选择配置时区内当天相关工作聊天/项目，每轮最多20个，每个最近3个turn，核实最后结果需要时最多5个。标题、置顶、更新时间与运行状态只帮助筛选；无关私人旧聊天不读取、不归档。

从相关turn提取 `userMessage`、`final_answer` 与选定产物路径，忽略内部推理、命令文本与工具元数据；需要时只读核对相关测试结果、产物或Git提交。仅收到请求不等于已经尝试，尝试也不等于交付。idle/complete状态、助手口头承诺、文件mtime或dirty都不能单独支持完成。commit证明提交内容，不自动证明测试通过或用户验收。

`codex_activity`覆盖记录写明本轮日期、项目/聊天范围与限制；只检查部分项目时保持`partial`。助手自检、自动简报、重复提醒与助手自身排障不能当成用户业务研发成果；用户安排的助理工具建设可以独立归类，不改写成产品功能交付。日志只保存必要摘要和证据定位，工作资料不随公开代码发布或发给其他人。

## 独立工作日志与全天日报

`log-work data/reviewed-work.json`把核实的活动写入私有`data/journal.sqlite3`；`daily-review --date YYYY-MM-DD`生成`reports/YYYY-MM-DD-review.md`和同名JSON。它们独立于任务账本，不会自动把待办设为完成，也不会自行调用模型编造建议。

日志envelope：

| 字段 | 内容 |
| --- | --- |
| `date` | 配置时区的工作日期，`YYYY-MM-DD` |
| `observed_at` | 本次核实时间，带时区ISO |
| `coverage` | 每项`source,status,scope,source_ref`；状态`ok/partial/unavailable`，同来源按本日更新 |
| `entries` | 每项`id,title,status,occurred_at,source,source_ref,evidence`；可补`outcome,project,task_id,completion_basis` |
| `recommendations` | 可选数组，每项`id,category,title,why,small_action,source_ref`及可选1–5整数`priority` |

工作状态为`completed/in_progress/blocked/cancelled`。`completed`还必须提供`outcome`及`completion_basis:artifact/test/commit/user_confirmation`；这些类型标签只是校验字段，真实证据仍须由Codex核实。活动时间必须属于本批工作日期且不晚于`observed_at`；来源必须在本日已有或由本批coverage提供。同一成果ID不能跨日期复用，也不能为重复简报每天新建成果。

建议类别为`next_day/learning/risk/focus`。显式提供建议数组时替换本日整组，空数组清除；未提供则保留。学习建议由当天具体项目、失败检查、反复问题或代码模块触发，写清依据、为什么与当前工作相关，以及15–30分钟可验证练习。建议不自动成为硬任务、截止或OKR。

以下是**人工构造的工作日志示例**，不代表真实用户或真实测试结果，仅说明字段。先创建被Git忽略的`data/`目录，再保存为`data/reviewed-work.json`；真实工作导入文件不要放仓库根目录。

```json
{
  "date": "2026-11-11",
  "observed_at": "2026-11-11T21:00:00+08:00",
  "coverage": [
    {
      "source": "codex_activity",
      "status": "partial",
      "scope": "人工样例：一个工作聊天的最后三个turn及一个选定结果",
      "source_ref": "synthetic:sample-thread"
    }
  ],
  "entries": [
    {
      "id": "sample:2026-11-11:verification",
      "title": "人工样例：核实一项检查结果",
      "status": "completed",
      "occurred_at": "2026-11-11T17:00:00+08:00",
      "source": "codex_activity",
      "source_ref": "synthetic:sample-thread:final",
      "evidence": "人工样例中的明确检查结果，非真实测试记录。",
      "outcome": "人工样例中的一项检查通过。",
      "completion_basis": "test"
    }
  ],
  "recommendations": []
}
```

晨间计划依据明确截止、优先级、阻塞、会议与现实容量，最多列三项重点。全天日报按有证据的已完成、进行中、阻塞、明日最多三项、1–2条学习建议、风险/重点关注和覆盖缺口交付。无足够证据时应说明无法确认全天成果；不能把早晨计划直接算作晚上成果，也不能据此认定用户全天无工作。

## 导入 envelope

顶层支持 `sources`、`goals`、`tasks`、`events` 和可选 `calendar_snapshots`。日期时间必须包含时区，例如 `2026-11-11T10:00:00+08:00` 或 `2026-11-11T02:00:00Z`。缺失的任务截止是 `null`，不从模糊的“下午”“周报”猜日期。

以下是**人工构造的格式示例**，不代表任何人的真实工作。先创建被Git忽略的`data/`目录，保存为`data/reviewed-summary.json`后导入会产生一个示例任务；实际使用时替换成核实内容，真实导入文件不要放仓库根目录。

```json
{
  "sources": [
    {
      "id": "manual",
      "status": "ok",
      "observed_at": "2026-11-11T09:00:00+08:00",
      "scope": "本人在本次聊天中明确交代的一项工作"
    }
  ],
  "tasks": [
    {
      "id": "manual:sample-review",
      "title": "整理测试结果与一个待解决问题",
      "source": "manual",
      "source_ref": "人工样例：当前聊天中的本人任务",
      "evidence": "本人要求整理本次测试结果；估时属于助理建议。",
      "owner": "self",
      "status": "todo",
      "confidence": 0.95,
      "importance": 4,
      "estimate_minutes": 30,
      "due_at": null,
      "goal_id": null
    }
  ]
}
```

### 来源与任务

`sources` 必需字段为 `id,status,observed_at,scope`，可补 `details`。

| `status` | 含义 |
| --- | --- |
| `ok` | 明确写在 `scope` 中的有限范围完整成功 |
| `partial` | 页数、会话、时间或查询结构仍有未确认覆盖 |
| `unavailable` | 未授权、权限不足、认证失败、无法启动或读取失败 |

一次成功搜索只证明搜索范围，不证明读完所有会话。来源覆盖记录包含采集时间；消息/日程发生时间应保留在证据与对应字段中，不能混为一个时间。

任务需包含示例中的字段。`owner` 为 `self` 或 `profile.aliases` 中已确认的本人称呼时才可自动排程；未知责任人为 `null`。`status` 支持 `todo/waiting/done/cancelled`；`confidence` 为 0–1，`importance` 为 1–5 整数，估时为 5–4800 的整数分钟。低置信度、非本人及 `waiting` 事项不占自动专注块。

目标使用 `id,title,status`，其中状态为 `active/paused/done/cancelled`，可补 `evidence` 与真实的 `target_date:YYYY-MM-DD`。任务的 `goal_id` 只能引用已有目标。岗位描述不自动构成已承诺的季度目标。

### 日程与完整快照

`events` 必需 `id,title,source,start,end`，可补 `source_ref,status,transparency`。状态为 `confirmed/tentative/cancelled`，透明性为 `opaque/transparent`；取消或透明日程不占忙碌时间。全天事件由提炼者按来源时区转换成真实起止范围。

完整快照可撤销该来源、该窗口内已经不存在的旧忙碌项：

```json
{
  "source": "feishu_calendar",
  "start": "2026-11-11T00:00:00+08:00",
  "end": "2026-11-12T00:00:00+08:00",
  "complete": true
}
```

它应作为 `calendar_snapshots` 数组的一项，与该来源本批 `status=ok` 记录及整个窗口的完整 `events` 一起提交。只有确实读完相关日历、分页和重复实例时才使用；仅主日历、失败或部分结果不可清除其他未读日历的旧记录。程序验证状态一致性，采集范围是否真的完整仍取决于读取证据。

## 排程规则

配置决定时区、ISO 星期数字 `workdays`、非重叠 `work_windows`、容量比例与置信度门槛。默认预算为扣除已知忙碌后的可用时间的 70%，按五分钟对齐。任务按目标、重要性与明确截止选择，同时优先保护当天可达到的较早截止，避免长任务挤掉本能按时完成的工作。

计划不会排入过去、休息、非工作日或已导入的忙碌时间。单段最多 90 分钟不代表自动安排了额外休息；工作窗口和保留容量仍需根据实际情况配置。容量不足的工作进入未安排清单，明确的延期风险会提示。

## 采集约束

采集器寻找已安装的原生 `lark-cli`，不通过 JS 启动器自动下载程序，也不发起认证。它固定 `--as user`，不因用户令牌缺失切换为 Bot。每来源最多 45 秒、合计 stdout/stderr 2 MiB；消息、任务和邮件列表请求最多五页，agenda 无法确认完整结构时保持部分覆盖。四类来源最多四路并发。

成功 JSON 以原始资料快照落盘，已知凭据键和认证 URL 会脱敏；原始错误文本不保存，只保留有限、经过校验的错误分类。**脱敏不等于公开可分享**：正常消息正文、姓名和公司信息仍可能是私人数据，因此整个 `data/` 都应保持私有。

仅来源覆盖会自动导入。原始消息不会直接成为任务，邮箱列表不会直接产生邮件摘要，日历不明结构不会被当作完整快照。Gmail / Google Calendar 的读取由 Codex 插件承担，遵循相同的来源和提炼规则。
