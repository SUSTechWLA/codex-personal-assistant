# Codex Personal Assistant

[English](README.en.md)

结合当天Codex工作、飞书、邮箱和日历，白天只给简洁编号提醒，每晚只保存一份当前成果与明日计划总结。Codex负责理解，Python标准库工具保留内部状态与证据，不需要额外OpenAI API Key或常驻服务。

这是一个可自行配置的社区项目。飞书接入使用飞书团队维护的 [lark-cli](https://github.com/larksuite/cli)；本项目并非 OpenAI 或飞书官方产品。

## 能做什么

- 用官方CLI和已连接工具读取授权范围内的飞书、Codex工作、邮箱和日历。
- 内部账本保护稳定ID、完成反馈、真实截止与现实容量，证据私有留存。
- 09/14/18只给本人编号1–3条、约80–160汉字的提醒；无变化只1条下一步，不保存新文档。
- 21点只保存一份`YYYY-MM-DD 工作总结`：成果≤3、问题≤2、明日计划≤3，约250–400字，信息少可更短。
- 不自动新增或更新项目、目标、学习、导航或周报文件；旧云文件保留。
- 本机定时即可，不需要Dot；消息与夜间写入默认关闭，需完成本人授权及实际配置。

原始采集不会直接成为成果。`collect-feishu`只保存私有raw和来源覆盖，Codex再核实行动；邮件采集限定INBOX ID列表，正文未授权不能总结。助手投递/自检不算业务研发，来源失败也不等于没有工作。

## 快速开始

需要 Python 3.9+。本地账本、排程和测试只用 Python 标准库。使用 AI 工作流还需要能够访问本项目目录的 Codex；Gmail、Google Calendar、飞书均可按需接入，单个来源未连接不妨碍本地功能。

```sh
git clone https://github.com/SUSTechWLA/codex-personal-assistant.git
cd codex-personal-assistant
python3 assistant.py init
python3 assistant.py status
python3 assistant.py plan --preview
```

Windows PowerShell 可使用已安装的 Python，或使用项目启动器自动寻找可用 Python / 已安装的 Codex 运行时：

```powershell
.\run-assistant.ps1 init
.\run-assistant.ps1 status
.\run-assistant.ps1 plan --preview
```

`init` 创建本地配置与空账本，不会生成演示任务、登录服务或创建定时任务。第一次没有任务时得到空计划是正常结果。打开生成的 `config.json`，设置时区、本人称呼、工作日、工作时段和所需来源；[config.example.json](config.example.json) 提供配置示例。默认工作时间为周一至周五 09:00–12:00、13:00–18:00，可自行添加晚间时段。

示例的`output`字段记录供技能和定时任务遵循的输出偏好；Python不会动态强制执行其中每个时间或字数字段，精简日报仍采用固定条数与短句规则。

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

本机Codex保存定时任务即可，不需要Dot。安装技能本身不创建日程，可以让Codex保存：

> 工作日09:00、14:00、18:00只在我的飞书AI助理对话发1–3条编号提醒，约80–160字，无变化只一条下一步；不要保存白天文档。21:00只保存一份“YYYY-MM-DD 工作总结”，成果≤3、问题≤2、明日计划≤3，约250–400字，内容不足不凑字数。不要方法细节、源码路径、表格、证据ID或长学习清单；重要缺口末尾一句。不要自动更新项目/目标/学习/导航。保留原聊天简洁兜底，请确认日程、时区和投递目标。

时间按实际工作窗口调整，去Scheduled确认确实保存。固定提醒每次交付，额外轮询无变化才静默。白天用`plan --preview`的stdout JSON在内部排程，不生成MD/ICS；普通plan只在用户另要日历草案时使用。需要本地资料时电脑须在线、桌面应用运行；系统弹窗仍受通知设置影响。

周报只在用户要求时给，约400–600字，成果/风险/下周计划各≤3项，不新增定时或周报文件；若当晚生成，合并当晚同一份总结。技术细节放适用且已有授权的现有需求/技术文档，不为隐藏冗余另建文件。内部证据和完整机器JSON仍私有保留，不复制到简报。

Dot只是可选协调入口，须另行连接电脑并确认目的地；不能把定时已保存说成Dot已接通。

## 把结果交付到本人飞书

白天用`documents:[]`加合法notification的只消息bundle，不访问云文件或root；两者都空则拒绝。21点bundle只包含一份日终总结，key固定`brief:<date>:review`，目录沿用`01 每日工作/YYYY/MM`，同key更新。通知key为`notify:<date>:<slot>`，避免复跑重复发。旧文件不擅删、移动或整理，根导航不每轮更新。

已有同日期review key时沿用manifest实际title/folder/token，不因旧标题换key重建；新日期默认标题为“YYYY-MM-DD 工作总结”。每日一份只约束例行助理输出，不限制你另行明确要求的需求/技术文档；已有需求文档保留，不擅删、移动或停用。

先创建被忽略的data目录，审阅输入存为`data/bundle.json`：

```sh
python3 assistant.py publish-feishu data/bundle.json
```

默认离线预览，不触网或输出正文；已授权、实际配置启用后加`--apply`执行。本人Bot须同profile同app和名称核验，收件人固定本人；读取和夜间文档仍为用户身份。冲突/未知结果停止核实；文档与消息分别确认，不能承诺系统弹窗。配置、仅消息/夜间示例见[飞书简洁交付](docs/feishu-delivery.md)。

## 数据、输出和操作范围

```text
config.json                 本人配置，默认不提交
assistant.sqlite3           任务、目标、日程、来源及历史
data/journal.sqlite3        有证据的每日工作活动与建议
data/feishu-delivery/        私有云文档映射、内容状态和发布锁
data/feishu/<timestamp>/     只读采集快照与来源导入文件
reports/YYYY-MM-DD.md        仅用户另要的手动计划草案
reports/YYYY-MM-DD-review.md 21点精简工作总结；同名JSON私有保留完整记录
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
python3 assistant.py plan --preview --date 2026-11-11
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
- [飞书编号提醒与唯一日终总结](docs/feishu-delivery.md)
- [MIT License](LICENSE)

欢迎提交带最小复现的 Issue 或改进 PR。请使用人工样例，移除邮件正文、聊天记录、真实账号、令牌和授权链接后再提交。
