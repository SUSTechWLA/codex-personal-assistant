# Codex Personal Assistant

[English](README.en.md)

在 Codex 中把飞书、邮箱和日历信息整理成有来源的待办、每日重点、时间安排与复盘。Codex 负责理解和建议，Python 标准库工具负责保存状态、验证数据与排程，不需要额外的 OpenAI API Key 或常驻服务。

这是一个可自行配置的社区项目。飞书接入使用飞书团队维护的 [lark-cli](https://github.com/larksuite/cli)；本项目并非 OpenAI 或飞书官方产品。

## 能做什么

| 能力 | 实现方式 |
| --- | --- |
| 飞书信息采集 | 官方 CLI 读取消息搜索、本人主日历、本人未完成任务及邮箱消息列表，固定用户身份 |
| 邮件和日历理解 | Codex 使用已连接的 Gmail / Google Calendar 工具，再提炼行动与忙碌时间 |
| 持久记忆 | 本地 SQLite 保存任务、目标、来源覆盖及变更历史；稳定 ID 去重，保护已完成状态 |
| 每日安排 | 避开已知会议、过去时间、休息和非工作日；默认只安排可用时间的 70% |
| 可核查的输出 | 中文日报、时间块、待确认/等待项、截止风险与本地 ICS 草案 |
| 自动例行工作 | 将技能放进 Codex / ChatGPT 定时任务；可进一步交给已连接电脑的 Dot 协调 |

**采集成功不等于已经生成待办。** `collect-feishu` 保存只读原始快照，并且只自动导入来源覆盖；Codex 还需要核实快照、提炼任务和日程后再 `ingest`。邮箱列表只包含消息 ID 等列表信息，相关正文须另行读取。来源失败会明确显示缺口，不会被当作“没有工作”。

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

接通后可运行有时间范围的只读采集。以下日期仅为调用示例，请替换为实际需要的范围：

```sh
python3 assistant.py collect-feishu \
  --start '2026-11-09T00:00:00+08:00' \
  --end '2026-11-11T00:00:00+08:00' \
  --profile codex-assistant
```

Windows 可将 `python3 assistant.py` 换成 `.\run-assistant.ps1`，并按 PowerShell 语法换行。每个来源最多 45 秒、2 MiB，最多四路并发；消息、任务和邮件列表最多采集 5 页，agenda 未确认完整分页时保留部分覆盖。消息和日历采用指定窗口；任务列表与邮箱列表的覆盖范围以返回的 `scope` 为准，不假装套用了日期过滤。最多只接受 31 天的单次采集窗口。

Gmail 和 Google Calendar 使用 Codex 已连接的插件工具。本项目脚本没有单独实现 Google OAuth 或直接调用 Google API；在 Codex 里连接相应服务后，由技能组织读取与提炼。不要把插件连接状态等同于每次查询成功。

## 不用每次输入指令

**不需要 Dot。** 推荐先用本机 Codex 的定时任务，直接在约定聊天自动交付简报；Dot 是可选入口，其电脑访问状态不影响这套本地方案。

技能本身不会常驻运行。首次安装后，让 Codex 创建一个保存的定时任务，例如：

> 在我的本地时区，每个工作日 09:00 做晨间计划、14:00 检查变化、18:00 做阶段复盘，使用 $personal-assistant。每次都在这个聊天里主动给我一条简短简报，包括重点、下一步和必要的时间安排，不要只保存文件。没有新变化也说明下一步；没有证据时不要编造任务。请确认保存的时间、时区和投递位置。

这些时间只是配置示例，不会在安装时自动生效。任务需要在应用的 Scheduled 中实际保存。例行简报应每次交付；额外轮询没有实质变化时才保持安静。桌面弹窗还取决于应用和操作系统的通知设置，不能把创建任务成功当成已验证推送。

Dot 可以协调长期职责，但需要你先在 Dot 资料页连接这台电脑，并让 Dot 确认任务、时间和投递位置。详见 [Dot 与主动提醒](docs/dot-setup.md)。需要本地文件或 CLI 的步骤要求电脑在线、桌面应用运行；这不是一个关机后仍可访问本机的云服务。

## 数据、输出和操作范围

```text
config.json                 本人配置，默认不提交
assistant.sqlite3           任务、目标、日程、来源及历史
data/feishu/<timestamp>/     只读采集快照与来源导入文件
reports/YYYY-MM-DD.md        每日计划
plans/YYYY-MM-DD.ics         建议时间块草案
```

这些运行文件已加入 `.gitignore`，仓库只发布通用源码、技能和人工构造的说明示例。提交前仍应检查 `git status`；`.gitignore` 不会从 Git 中移除此前已追踪的文件。

本地计划只根据已读取并导入的会议避让。**生成 ICS 不会把日程写入飞书或 Google 日历。** 在线写入需要用户明确授权、所需写权限、最新忙闲检查和结果验证；初始只读配置不具备这些写权限。本项目不会自动给同事发送邮件、飞书消息或会议邀请。

常用命令：

```sh
python3 assistant.py ingest reviewed-summary.json
python3 assistant.py update 'source:stable-task-id' --status done
python3 assistant.py plan --date 2026-11-11
python3 assistant.py --help
python3 -m unittest discover -s tests -v
```

导入格式、目标和完整日历快照规则见 [架构与数据约定](docs/architecture.md)。

## 验证范围与限制

回归测试覆盖原子导入、去重与完成状态、日历完整快照保护、时区截止、会议/休息避让、容量、ICS 转义，以及模拟 CLI 的独立失败、超时、输出上限和分页状态。发布前未获企业审批的环境不能验证真实飞书用户数据读取；本项目不把这些测试描述成飞书端到端认证或在线日历写入已通过。

搜索结果不等于完整会话，主日历不等于所有工作日历。模型可能提取错误，因此低置信度、负责人不明和缺少真实日期的事项进入待确认。对于无 IANA 时区数据库的 Python 运行时，内置支持 Asia/Shanghai 与 UTC；其他时区可能需要系统时区数据或另装 `tzdata`。

## 项目资料

- [架构与数据约定](docs/architecture.md)
- [飞书接入指南](docs/setup-feishu.md)
- [Dot、定时任务与通知](docs/dot-setup.md)
- [MIT License](LICENSE)

欢迎提交带最小复现的 Issue 或改进 PR。请使用人工样例，移除邮件正文、聊天记录、真实账号、令牌和授权链接后再提交。
