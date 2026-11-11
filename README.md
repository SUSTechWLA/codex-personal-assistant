# Codex Personal Assistant

[English](README.en.md)

在 Codex 中结合当天工作聊天、飞书、邮箱和日历，安排晨间重点，记录有证据的成果，晚上形成全天日报并提出针对实际问题的学习与风险建议。Codex负责理解和建议，Python标准库工具负责保存状态、验证数据与排程，不需要额外的OpenAI API Key或常驻服务。

这是一个可自行配置的社区项目。飞书接入使用飞书团队维护的 [lark-cli](https://github.com/larksuite/cli)；本项目并非 OpenAI 或飞书官方产品。

## 能做什么

| 能力 | 实现方式 |
| --- | --- |
| Codex工作活动 | 运行时发现聊天读取工具，限于当天相关工作项目，最多20个聊天、每个最近3–5个turn；核实最后结果和选定产物 |
| 飞书信息采集 | 官方 CLI 读取消息搜索或受限会话历史、本人主日历、本人未完成任务及收件箱 `INBOX` 的邮件 ID 列表，固定用户身份 |
| 邮件和日历理解 | Codex 使用已连接的 Gmail / Google Calendar 工具，再提炼行动与忙碌时间 |
| 持久记忆 | 本地 SQLite 保存任务、目标、来源覆盖及变更历史；稳定 ID 去重，保护已完成状态 |
| 每日安排 | 避开已知会议、过去时间、休息和非工作日；默认只安排可用时间的 70% |
| 可核查的输出 | 中文日报、时间块、待确认/等待项、截止风险与本地 ICS 草案 |
| 全天复盘与学习 | 独立工作日志区分已完成、进行中、阻塞；明日最多三项、1–2条有触发证据的学习建议及15–30分钟最小练习 |
| 自动例行工作 | 将技能放进 Codex / ChatGPT 定时任务；可进一步交给已连接电脑的 Dot 协调 |
| 本人飞书交付 | 可选保存审阅成果到专属云工作台、按项目/日期分类，再给本人简报+真实链接；默认禁用，支持明确选择且核验的指定Bot通知 |

**采集成功不等于已经生成待办。** `collect-feishu` 保存只读原始快照，并且只自动导入来源覆盖；Codex 还需要核实快照、提炼任务和日程后再 `ingest`。邮件采集限定收件箱 `INBOX`，只列消息 ID 等列表信息；相关主题、地址和正文须另行取得对应只读权限后读取。来源失败会明确显示缺口，不会被当作“没有工作”。

## 快速开始

需要 Python 3.9+。本地账本、排程和测试只用 Python 标准库。使用 AI 工作流还需要能够访问本项目目录的 Codex；Gmail、Google Calendar、飞书均可按需接入，单个来源未连接不妨碍本地功能。

```sh
git clone https://github.com/SUSTechWLA/codex-personal-assistant.git
cd codex-personal-assistant
python3 assistant.py init
python3 assistant.py status
python3 assistant.py plan
```

Windows PowerShell 可使用已安装的 Python，或使用项目启动器自动寻找可用 Python / 已安装的 Codex 运行时：

```powershell
.\run-assistant.ps1 init
.\run-assistant.ps1 status
.\run-assistant.ps1 plan
```

`init` 创建本地配置与空账本，不会生成演示任务、登录服务或创建定时任务。第一次没有任务时得到空计划是正常结果。打开生成的 `config.json`，设置时区、本人称呼、工作日、工作时段和所需来源；[config.example.json](config.example.json) 提供配置示例。默认工作时间为周一至周五 09:00–12:00、13:00–18:00，可自行添加晚间时段。

在 Codex 中打开这个目录，安装技能：

```sh
python3 install_skill.py --dry-run
python3 install_skill.py
```

默认安装到 `$CODEX_HOME/skills/personal-assistant`，未设置 `CODEX_HOME` 时使用 `~/.codex/skills/personal-assistant`。已有同名技能时安装器拒绝覆盖；可用 `--name my-work-assistant` 安装成另一个名字。安装器会绑定当前项目路径，只安装技能，不替你授权飞书或设置日程。参数以 `python3 install_skill.py --help` 为准。

随后可以在 Codex 里说：

> 使用 $personal-assistant 检查可用的信息来源，安排今天最重要的工作，并标明需要确认的事项。

如果使用了自定义技能名，把上面的名称替换成安装时的名字。

## 接入飞书、Gmail 和日历

飞书使用官方 CLI，无须模拟点击、读取客户端私有数据库或提取 Cookie。安装与授权详见 [飞书接入指南](docs/setup-feishu.md)。初始权限保持只读；企业账户可能需要管理员审批。配置应用成功、Bot 就绪和个人数据授权成功是三种不同状态。

消息快捷搜索和邮件正文权限按需追加，基础采集可以不补这四项权限。CLI `1.0.99` 的消息快捷搜索即使使用 `--no-reactions`，前置检查仍需要 `im:message.reactions:read`；仅缺这一权限时，采集器会用已有消息读取权限调用官方 CLI 的基础 GET 会话历史接口。它最多读取 10 个近期活跃的非机器人会话、每会话 20 条、总计 100 条，沿用消息来源 45 秒 / 2 MiB 限制，始终为 `partial`，不代表全局搜索。它不会授权、切换 Bot 或下载程序，也不绕过服务端权限检查。

邮件列表的 `mail:user_mailbox.message:readonly` 不包含正文所需的主题、地址、正文三个细分读取权限；不补这些权限时只保留 `INBOX` 邮件 ID，正文保持未读。[接入指南](docs/setup-feishu.md) 分别说明基础读取与可选增强权限，不使用请求全部业务域的 `--recommend`。

接通后可运行有时间范围的只读采集。以下日期仅为调用示例，请替换为实际需要的范围：

```sh
python3 assistant.py collect-feishu \
  --start '2026-11-09T00:00:00+08:00' \
  --end '2026-11-11T00:00:00+08:00' \
  --profile codex-assistant
```

Windows 可将 `python3 assistant.py` 换成 `.\run-assistant.ps1`，并按 PowerShell 语法换行。每个来源最多 45 秒、2 MiB，最多四路并发；消息、任务和收件箱邮件列表最多采集 5 页，agenda 未确认完整分页时保留部分覆盖。消息和日历采用指定窗口；任务列表与收件箱列表的覆盖范围以返回的 `scope` 为准，不假装套用了日期过滤。最多只接受 31 天的单次采集窗口。

Gmail 和 Google Calendar 使用 Codex 已连接的插件工具。本项目脚本没有单独实现 Google OAuth 或直接调用 Google API；在 Codex 里连接相应服务后，由技能组织读取与提炼。不要把插件连接状态等同于每次查询成功。

## 不用每次输入指令

**不需要 Dot。** 推荐先用本机 Codex 的定时任务，直接在约定聊天自动交付简报；Dot 是可选入口，其电脑访问状态不影响这套本地方案。

技能本身不会常驻运行。首次安装后，让 Codex 创建一个保存的定时任务，例如：

> 在我的本地时区，每个工作日09:00做晨间计划、14:00检查变化、18:00做阶段复盘、21:00出全天日报，使用$personal-assistant。结合当天相关Codex工作与可用来源，每次在这个聊天主动给我完整简报；晚报含有证据的成果、明日重点及针对实际工作的学习/风险建议。不只保存文件，不编造任务或完成状态。请确认保存的时间、时区和投递位置。

这些时间只是配置示例，不会在安装时自动生效。全天日报时间应与本人的下班时间对齐，例如工作到21:00时采用下表。任务需要在应用的Scheduled中实际保存。例行简报每次交付；额外轮询没有实质变化时才保持安静。桌面弹窗还取决于应用与操作系统通知设置，不能把创建任务成功当成已验证推送。

| 时点示例 | 交付内容 |
| --- | --- |
| 09:00晨间 | 明确截止与优先级、最多三项成果目标、第一步、已知会议和现实容量 |
| 14:00变化 | 新增指派、截止/会议变化、阻塞及下午调整 |
| 18:00阶段 | 核实阶段结果，安排剩余工作窗口，不直接当作下班 |
| 21:00全天 | 有证据的已完成、进行中、阻塞、明日最多三项、学习建议、风险注意点与来源缺口 |

Codex活动通过宿主提供的 `list_threads/read_thread` 等工具读取，脚本不直接解析应用私有数据库。只提取相关 `userMessage`、`final_answer` 和选定产物，忽略推理、命令文本与工具元数据；线程结束、文件mtime或dirty不代表完成。助手自检与自动简报不能计为用户研发成果，工作资料不随项目发布或外发。

学习建议由当天具体项目、失败检查、反复问题或修改模块触发，每条说明证据、为什么值得关注和15–30分钟可验证练习。无依据时不凑数；建议不会自动成为硬任务或OKR。当天没有成果证据时，晚报必须说明覆盖不足，不能虚报完成或认定全天无工作。

Dot 可以协调长期职责，但需要你先在 Dot 资料页连接这台电脑，并让 Dot 确认任务、时间和投递位置。详见 [Dot 与主动提醒](docs/dot-setup.md)。需要本地文件或 CLI 的步骤要求电脑在线、桌面应用运行；这不是一个关机后仍可访问本机的云服务。

## 把结果交付到本人飞书

可选交付功能把已审阅的计划、变化、日报、项目总览、目标待办、学习风险建议和必要来源定位保存到本人云空间，消息只发本人简报+链接；不是上传整段聊天、原始邮件、源码、推理/命令日志或凭据。原始资料与日志保持私有，不发布到GitHub。

只新建专属工作台，分类为根`00导航`、`01每日工作/YYYY/MM`、`02项目管理/项目ID`、`03目标与待办`、`04学习与知识`、`05周月复盘`、`90归档`。稳定key重跑更新原文档；只管理自己的manifest条目，不移动既有文件、不共享、不自动改日历。手工编辑冲突或请求结果未知时先停止核实，不盲目重试。

先创建被忽略的`data/`目录，把审阅后的发布输入保存为`data/bundle.json`，再运行：

```sh
python3 assistant.py publish-feishu data/bundle.json
```

默认纯离线预览，不输出正文、不触网；真实执行需用户已授权、配置启用并显式加`--apply`。默认本人通知用用户身份；用户明确选择指定智能体时，Bot必须与所选profile同应用，实时名称核验通过，收件人仍固定本人。数据读取/云文档始终使用用户身份，不因读取失败切Bot。消息缺权限时可先保存云文档，在原聊天返回核实链接；消息API成功也不保证系统弹窗。配置、最小权限、bundle人工例子和结果状态见[飞书成果交付](docs/feishu-delivery.md)。

## 数据、输出和操作范围

```text
config.json                 本人配置，默认不提交
assistant.sqlite3           任务、目标、日程、来源及历史
data/journal.sqlite3        有证据的每日工作活动与建议
data/feishu-delivery/        私有云文档映射、内容状态和发布锁
data/feishu/<timestamp>/     只读采集快照与来源导入文件
reports/YYYY-MM-DD.md        每日计划
reports/YYYY-MM-DD-review.md 全天日报；同名JSON提供结构化结果
plans/YYYY-MM-DD.ics         建议时间块草案
```

这些运行文件已加入 `.gitignore`，仓库只发布通用源码、技能和人工构造的说明示例。提交前仍应检查 `git status`；`.gitignore` 不会从 Git 中移除此前已追踪的文件。

本地计划只根据已读取并导入的会议避让。**生成ICS不会把日程写入飞书或Google日历。** 在线日历操作仍需具体授权、写权限、最新忙闲检查和结果验证；云文档/本人简报授权不包含改会议。已经明确授权的本人私有成果存储与本人简报无需每轮重复许可，官方OAuth身份步骤仍由本人完成。不会自动给同事发送邮件、飞书消息、会议邀请或公开共享资料。

常用命令：

先在项目内创建被Git忽略的`data/`目录，把核实后的任务摘要保存为`data/reviewed-summary.json`，工作记录保存为`data/reviewed-work.json`。不要把含真实工作资料的导入JSON放在仓库根目录。

```sh
mkdir -p data
python3 assistant.py ingest data/reviewed-summary.json
python3 assistant.py log-work data/reviewed-work.json
python3 assistant.py update 'source:stable-task-id' --status done
python3 assistant.py plan --date 2026-11-11
python3 assistant.py daily-review --date 2026-11-11
python3 assistant.py --help
python3 -m unittest discover -s tests -v
```

PowerShell中创建目录可用`New-Item -ItemType Directory -Force data | Out-Null`。任务导入与工作日志是两种独立记录，日志不自动把待办设为完成。`log-work`输入须由Codex核实后生成，`daily-review`不凭空推断工作或学习需求。字段和人工示例见[架构与数据约定](docs/architecture.md)。

## 验证范围与限制

回归测试覆盖原子导入、去重与完成状态、日历完整快照保护、时区截止、会议/休息避让、容量、ICS 转义，以及模拟 CLI 的独立失败、超时、输出上限、空结果结构和分页状态。日报测试验证完成证据、跨日隔离、旧记录保护、失败回滚、建议数量及日志不改变任务状态。真实账号须在所需审批与用户 OAuth 完成后逐来源验证；测试通过或一个入口成功不代表所有飞书内容和在线日历写入均已接通。

搜索结果不等于完整会话，主日历不等于所有工作日历。模型可能提取错误，因此低置信度、负责人不明和缺少真实日期的事项进入待确认。对于无 IANA 时区数据库的 Python 运行时，内置支持 Asia/Shanghai 与 UTC；其他时区可能需要系统时区数据或另装 `tzdata`。

## 项目资料

- [架构与数据约定](docs/architecture.md)
- [更新记录](CHANGELOG.md)
- [飞书接入指南](docs/setup-feishu.md)
- [Dot、定时任务与通知](docs/dot-setup.md)
- [飞书成果交付与项目分类](docs/feishu-delivery.md)
- [MIT License](LICENSE)

欢迎提交带最小复现的 Issue 或改进 PR。请使用人工样例，移除邮件正文、聊天记录、真实账号、令牌和授权链接后再提交。
