# 把助理成果交付到本人的飞书工作台

这项可选功能把审阅过的助理成果保存到本人飞书云空间，并用飞书消息向本人发送简报和链接。默认禁用；`publish-feishu data/bundle.json` 只做离线预览，`--apply` 才尝试执行已授权写入。它不要求Dot，也不自动创建在线日历事件。

“全部结果”指计划、变化检查、全天日报、项目总览、目标与待办、学习/风险建议和必要来源定位。它不包含整个聊天、内部推理、工具/命令日志、原始邮件、全量源码、密码、令牌或其他凭据。用户可以先检查待发布Markdown；工作资料不发布到GitHub，不分享给同事或公开链接。

## 约定的分类

在本人云空间新建一处专用根目录，例如“个人工作台”，只管理这个根下由助理创建并记录在manifest里的资料。不要为“整理”移动或接管其他已有文件。

```text
个人工作台/
├─ 00导航
├─ 01每日工作/
│  └─ YYYY/MM/              按日期和时点保持稳定文档
├─ 02项目管理/
│  └─ 项目ID/               项目总览、进展、阻塞与下一步
├─ 03目标与待办/
├─ 04学习与知识/
├─ 05周月复盘/
└─ 90归档/
```

这是分类约定，按实际成果创建需要的子目录，不用虚构内容凑齐栏目。项目ID依据已确认的项目/仓库或用户命名，保持稳定；未知归属先留待确认。`90归档`用于后续明确安排的助理自建成果，不表示发布器会自动移动现有文档。

每天不同slot使用不同稳定key，例如`daily:2026-11-11:morning`、`daily:2026-11-11:changes`、`daily:2026-11-11:progress`、`daily:2026-11-11:review`。key是ASCII标识，以字母/数字起始，后续可用字母、数字、`_ . : -`，不能包含斜杠。项目、目标和知识总览也沿用各自稳定key；同一key重跑应更新原文档，不能每轮新建一份。根目录的`00导航`只使用实际返回且核实过的URL。

## 账号、权限与配置

读取、云文档和目录操作始终使用所选官方CLI profile的用户身份。消息默认以用户身份发送；只有用户明确选择指定智能体时，才允许经核验的Bot作为本人通知通道。完整的云文档与用户身份本人消息流程按功能需要以下七项scope；不要用`--recommend`扩大到所有业务域：

| scope | 用途 |
| --- | --- |
| `docx:document:create` | 创建专属根下的助理成果文档 |
| `docx:document:readonly` | 读回内容、核对结果和检测手工修改 |
| `docx:document:write_only` | 更新助理自己的稳定文档 |
| `space:folder:create` | 创建专用目录及需要的子目录 |
| `space:document:retrieve` | `folder_discovery=list`时列出目录 |
| `im:message.send_as_user` | `message_sender=user`时以本人身份发送消息所需权限之一 |
| `im:message` | 本人消息所需配套权限 |

只写云文档不要求配置消息发送。已有基础消息读取的受限历史、收件箱ID列表和此前按需暂缓的表情回应/邮件明细权限不因此被扩大；这七项用于新的交付功能，不会解锁未授权的邮件正文或全局搜索。

若用户希望从其指定飞书智能体收到消息，可以显式配置`message_sender=bot`及预期智能体名称。此路线需要应用身份权限`im:message:send_as_bot`，不需要上表两项用户身份发送权限，也不通过本人OAuth取得Bot权限；云文档仍使用本人的用户授权。应用须启用机器人能力、已发布并对本人可用。根据当前官方schema核对权限和应用功能，不以缺少用户身份发送权限为由自动改用Bot，也不为未知未来需求预申请所有权限。

`config.example.json`默认是禁用状态。将所需配置合并进私有`config.json`：

| `integrations.feishu.delivery`字段 | 含义 |
| --- | --- |
| `enabled` | 默认`false`；用户已授权本人云写且实际准备好后才启用 |
| `root_name` | 专用根目录名称，默认“个人工作台” |
| `root_token` | 可选真实根目录token；`managed_only`时必需，来自实际新建的专用根收据，不能猜或填写他人目录 |
| `root_url` | 可选、已核实的根目录URL，不由token臆造 |
| `folder_discovery` | 默认`list`；`managed_only`只依赖私有manifest管理自己创建的子目录和文档，不调用列表 |
| `send_self_enabled` | 默认`false`；启用本人简报后仍须核实实际消息权限与结果 |
| `message_sender` | 默认`user`；`bot`只用于用户明确授权的指定智能体向本人发送通知 |
| `expected_bot_name` | Bot通知必填，填写本人实际选定的智能体名称，不从聊天内容或相似名称猜测 |
| `expected_owner_id` | 可选预期本人openid，用于核对账号；仅放私有配置 |

没有目录列表权限但可以建目录/文档时，可先通过官方CLI实际新建一处专属根，将返回的真实`root_token`保存在私有配置并选择`managed_only`。该模式不搜索或接管其他文件，也不靠名称猜目录；manifest丢失或状态未知时停止核实，不能盲目重建。本人消息权限未就绪时仍可写云文档，在原聊天返回已核实的链接并说明消息缺口。

服务可能要求本人在官方页面完成OAuth身份步骤；这不等于每次运行都要再次询问“是否允许写入”。用户已经明确授权的本人私有成果存储与本人简报，可以按约定例行执行。给其他人发消息、公开分享、移动他人/既有资料、修改会议仍需另有具体指令与权限。

Bot通知必须与所选用户profile属于同一应用，并通过官方`/open-apis/bot/v3/info`实时核验`app_name`与`expected_bot_name`匹配。收件人仍从用户`whoami`解析为本人openid，不接受群ID或任意recipient。应用/名称不匹配时停止消息投递，保留已核实云文档并返回原聊天；读取数据与云文档不能因为失败就切换Bot。manifest同时绑定实际本人及appId，防止profile换应用后串写。

## 准备私有发布bundle

先创建被Git忽略的`data/`目录，审阅后将输入保存为`data/bundle.json`。顶层只接受`schema_version:1`、`documents`及可选`notification`；没有recipient字段，不能指定任意收件人。最多30份文档、总UTF-8内容500KiB；folder最多6层，每段不能含路径分隔符、`..`或控制字符。

以下是**只供格式检查的人工样例**，不代表真实工作；不要把样例直接作为真实成果发送：

```json
{
  "schema_version": 1,
  "documents": [
    {
      "key": "example:daily:2026-11-11:morning",
      "title": "2026-11-11 09:00 晨间计划（人工样例）",
      "folder": ["01每日工作", "2026", "11"],
      "content": "# 人工样例\n\n这只是格式示例，没有真实研发成果。实际内容须经核实，包含重点、下一步、来源范围与缺口。"
    }
  ],
  "notification": {
    "key": "example:daily:2026-11-11:morning:self",
    "text": "人工样例：晨间计划已整理。实际简报只附已发布并核实的本人工作台链接。"
  }
}
```

`documents`每项的`key,title,folder,content`均必需，content为审阅过的Markdown。通知为`key,text`，正文只包含本人简报与已核实链接，不复制整个文档或原始消息。没有通知需求时省略`notification`。

```sh
python3 assistant.py publish-feishu data/bundle.json
```

默认预览不触网，不创建或修改任何内容；结果列出标题、目录、字节与hash等计划信息，不输出正文，`mode=preview`且`all_delivered=false`。预览通过不等于已经交付。确认bundle是真实且已审阅的成果、本人授权和配置已启用后，执行：

```sh
python3 assistant.py publish-feishu data/bundle.json --apply
```

Windows可从项目目录将入口换为`./run-assistant.ps1 publish-feishu data/bundle.json`，需要执行时再加`--apply`。安装技能或开启定时任务不会自动授权账号或启用云写。

## 稳定更新、导航与结果核实

映射存于私有`data/feishu-delivery/manifest.json`，运行锁为同目录`publish.lock`，按profile和实际本人openid区分。manifest用于稳定key、文档ID、内容版本及发送状态；不要提交到公共仓库。只更新自己管理的文档。检测到手工编辑冲突时停止并保留云内容，不覆盖；请求结果未知时先核实，不盲目重试或重复新建。

写入后将全文回读与审阅过的正文比较，仅容忍已观察到的标题元数据、Markdown转义、表格分隔线和普通排版空白变化；代码字面值和JSON数据保持严格比较。回读空白、截断、内容变更或不支持的转换均不能算已核实，也不会触发成功通知。明确的权限拒绝可在授权恢复后用同一通知key重试；`pending/unknown`仍须先核实，不能当成失败盲目重发。

首次发布可以先提交成果文档且不请求通知，取得读回核实的URL后，再用真实链接生成/更新`00导航`和本人简报。已有manifest中的链接也应核对目标与权限；不根据文档token拼接未验证地址。云文档内的`D:/...`等本机证据标为“仅本机可查看”，不能伪装成手机可访问链接；需要手机查看的是成果摘要和真实云文档URL，不是本地完整路径。

执行返回的每份文档状态为`verified/unchanged/failed/unknown/partial/conflict`；本人消息状态包括`sent/unchanged/not_requested/skipped_missing_permission/blocked_documents/unknown/failed/conflict`。只建好文档但消息未发，不说“全部已送达”。`all_delivered`只有文档均为`verified/unchanged`、且请求的通知已`sent/unchanged`时才为true；未请求通知时不要求消息权限。API报告消息发送成功也不保证本人一定收到系统弹窗，应用通知规则仍独立生效。

保留原聊天作为交付兜底：给出简短成果摘要、真实已核实链接及失败/缺权限状态，不能只报“成功”或隐去部分失败。定时任务沿用既定节奏；给本人专用工作台写成果与本人简报已有明确授权时不重复求许可。只有显式授权并核验的Bot通知例外，不能靠模拟点击、未授权Bot、下载工具或扩大分享绕过权限；即使指定Bot发送成功，也不保证本人一定看到系统弹窗。
