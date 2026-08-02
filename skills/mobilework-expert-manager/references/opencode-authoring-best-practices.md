# OpenCode 原生专家、Agent 与 Skill 编写规范

创建专家、专家团、agent 或 skill 时读取本文件。这里吸收通用插件开发中的触发设计、结构组织、配置安全和渐进披露原则，但最终产物必须使用 MobileWork 当前兼容的 OpenCode 格式。

需要判断 `opencode.json` 根字段、agent 投影或官方字段是否归专家包所有时，同时读取
`opencode-json-spec.md`。

## 目录

1. [转译边界](#转译边界)
2. [Agent 编写](#agent-编写)
3. [Skill 编写](#skill-编写)
4. [Command 编写](#command-编写)
5. [运行资源选择](#运行资源选择)
6. [Structure 与配置](#structure-与配置)
7. [Authoring 检查](#authoring-检查)

## 转译边界

- 使用 `expert.json` 管理业务结构和资源所有权，并由生成器派生 `opencode.json`、`.opencode/agents/`、`.opencode/skills/` 与其他运行资源。
- 不生成 `.claude-plugin/plugin.json`、`.claude/*.local.md` 或 `${CLAUDE_PLUGIN_ROOT}`。这些属于其他宿主的格式，不能进入 MobileWork 专家包。
- 包内资源使用 POSIX 相对路径。OpenCode 配置使用 `{env:VARIABLE}` 引用运行环境，不写真实 secret 或开发机绝对路径。
- 保留 MobileWork 需要的 `name`、`displayName`、`profession`、`avatar_url` 等扩展字段，同时保证 OpenCode 官方字段 `description`、`steps`、`mode`、`color`、`permission` 有效。

## Agent 编写

### Description

让 `description` 同时回答“做什么”和“何时使用”。从以下输入生成简短、可路由的描述：

1. 角色 `description`；
2. `route_triggers[]`；
3. 顶层 `quick_prompts[]`；
4. 专家团 `workflows[].trigger`。

单专家描述面向用户请求；团长描述面向跨角色编排、验收和集成；团员描述面向团长的专业委派。不要把其他宿主使用的 XML `<example>` 块塞进 OpenCode frontmatter；具体触发示例放在 agent Markdown 正文。

### System prompt

让 agent 正文具备以下信息，并避免只写人格设定：

- 角色身份、职责边界和不适用范围；
- 典型触发场景和路由方式；
- 可执行的工作流程；
- 分配给该角色的完整 skill 列表；
- 分配给该角色的 Reference、使用时机和角色规则；
- 输出格式、证据要求和质量门控；
- 输入不足、工具不可用、越权、验证失败时的处理方式。

团长只做编排、验收、返工和最终集成；团员只完成被委派的专业任务。继续使用 OpenCode `task`、
`subagent_type` 和返回的 `task_id`，不生成其他宿主的团队 API。parallel Phase 的 `agents[]`
只列唯一、必参与角色；团长可为多个角色分别发起多个 fresh task 实例，每个实例保存独立
`task_id`、Todo 和验收状态，实例数与分片按本次输入动态决定。

### Runtime options

OpenCode 正式步数字段只使用正整数 `steps`；新角色与新文档不得使用其他写法。`max_turns`、
`maxTurns` 不是 OpenCode Agent 选项，只在读取既有 MobileWork `expert.json` 时兼容，多个输入同时
出现时值必须一致，派生配置仍只写 `steps`。只有业务需求明确涉及模型能力、成本、确定性或创造性时，才在设计确认中询问并声明
`model`、`variant`、`temperature`、`top_p`、`options`；通常只调 `temperature` 与 `top_p` 中一个。
团员可声明 `hidden`，单专家和团长不可声明。省略表示继承 OpenCode、模型或 provider 默认值，
generator 不推断参数。`reasoningEffort`、`textVerbosity` 等 provider-specific 参数放在非空
`options` 对象中，不提升为角色顶层字段。

### Permissions

优先在 `permission` 中表达无法由 execution 与 ownership 推导的能力，`tools` 只保留为旧 manifest
的布尔兼容输入。统一技能池 manifest 未声明 Workflow 时使用 `no-workflow-bounded-default`；
启用 workflow autonomy 后，默认 permission 按
`references/permission-policy-spec.md` 的双轴最小权限策略生成；显式提权必须有
`permission_reason`。Agent Markdown 与 `opencode.json.agent.<id>` 必须完全一致。

## Skill 编写

### Frontmatter

每个 `.opencode/skills/<name>/SKILL.md` 先满足 Agent Skills 官方规范，再应用 MobileWork
安全与便携规则。允许的顶层字段只有 `name`、`description`、`license`、`compatibility`、
`metadata`、`allowed-tools`：

```yaml
---
name: contract-clause-review
description: 当合同审查专家需要定位高风险条款、引用证据并形成修改建议时使用。
compatibility: opencode
metadata:
  author: mobilework
---
```

- `name` 必须与 skill 目录名一致，使用 1–64 字符的 ASCII kebab-case，不能包含连续连字符。
- `description` 长度为 1–1024 个字符，写明能力和触发条件。
- `compatibility` 可以按原技能声明保留，但存在时必须是 1–500 字符的字符串；旧兼容生成器仍
  固定输出 `opencode`。
- `license` 存在时为非空字符串；`metadata` 只能映射字符串到字符串；实验性
  `allowed-tools` 只能是空格分隔的非空字符串。
- 未知 frontmatter 字段失败；自定义字符串属性放到 `metadata`。
- 上传技能的 frontmatter 和正文默认逐字节保留，不增加 package、role 或类型 metadata。
- 诊断和导入不做类型强制转换或 YAML 规范化；不合规内容在写入专家包前阻断。

### Progressive disclosure

把 agent 身份、路由和协作规则留在 agent Markdown；把可复用的方法、SOP、输出合同和质量门控放入 skill。详细资料和可执行资源按需放入 skill 子树：

- `references/`：领域资料、规则、API 或长说明；仅在相关任务需要时读取。
- `scripts/`：确定性或重复执行逻辑；先查看帮助和输入输出合同，再运行。
- `assets/`、`templates/`：用于最终交付的模板或静态资源，不作为说明全文加载。
- `examples/`：需要理解格式或边界时读取的完整示例。

统一技能池把包括 `SKILL.md` 在内的所有文件通过 `package_resources[]` 声明并记录 SHA-256。
`preserved` 技能不得为补充资源导航而改写；它自己的导航不完整时阻止或报告，不自动修复。

### Writing style

使用命令式、面向执行的语言。解释关键约束背后的原因，避免堆叠没有验收意义的绝对措辞。输出格式只有在业务确实要求固定结构时才强制。

## Command 编写

把 command 作为用户进入已确认 workflow 的稳定快捷入口。顶层 Workflow 本身可选；没有稳定
执行与验收合同的专家不创建 Workflow command，仍可使用独立 `runtime_extensions.commands[]`。
每个可由用户直接触发、会重复使用的 workflow 默认推荐一个 command；多个 workflow 使用多个 Markdown 文件。单专家 command 默认
路由到该专家，专家团默认路由到团长，避免绕过团队编排与最终验收。

模板用 `$ARGUMENTS` 接收用户文字，并提醒 agent 同时处理本次调用中可访问的图片、PDF 或其他
附件。附件是宿主消息层输入，不为它发明 command 占位符或本机路径。完整字段、`@path`、位置
参数和文件投影规则见 `runtime-extensions-spec.md`。

命名冲突按 MobileWork 固定的 OpenCode `v1.16.2` 服务端 command 注册表判断：`init` 和
`review` 是默认 command，而用户配置会覆盖同名项，因此 workflow 与
`runtime_extensions.commands` 均禁止声明这两个名称。`help` 是 TUI palette command，
不属于服务端注册表，允许生成。OpenCode 版本升级时必须先读回新版本的服务端默认注册表并更新
共享校验器与回归测试，不能凭文档示例扩大或缩小禁用清单。

## 运行资源选择

在设计确认阶段根据能力边界选择资源，不从附件或描述自动推断生成：

| 需求 | 选择 | 运行时关系 |
|---|---|---|
| 随包领域资料、规范、案例、知识库或操作手册 | `reference_files[]` + 本地 `references` | 文件放入 `.opencode/references/<slug>/<alias>/`，namespaced alias 写入 `opencode.json.references`。 |
| Git 仓库资料 | Git `references` | `repository` 必需，可选 `branch`、`description`、`hidden`；namespaced alias 写入 `opencode.json.references`，不生成 backing file。 |
| 指定角色或专家团内部始终遵守的规则 | `role_instructions` + 角色 `instructions[]` | 本地 Markdown 只写入分配角色的 Agent Markdown，不进入根级 instructions。 |
| 事件订阅、工具拦截或运行时行为修改 | `plugins.local[]` | 文件放入 `.opencode/plugins/` 并自动发现；第三方依赖放 `.opencode/package.json`。 |
| 智能体直接调用的 JavaScript/TypeScript 能力 | `custom_tools[]` | 文件放入 `.opencode/tools/` 并自动发现，不生成根级 `tools`。 |
| 已有 Python/Shell 确定性脚本 | Skill `scripts/` + `skill-script` executor | 脚本随 Skill 声明、分配并按执行合同调用。 |
| 整个 workspace 都要遵守的专家包指令 | `instruction_files[]` + `instructions[]` | 文件放入 `.opencode/instructions/<slug>/`，文件或 glob 写入 `opencode.json.instructions`。 |
| 主动读写外部软件、服务或数据库 | `mcp_servers[]` | MCP 写入根配置，角色通过 `mcp[]` 获得使用关系。 |

只有 npm plugin package name 写入 `opencode.json.plugin`，本地 plugin 路径不写入。本地 reference
文件只接受 UTF-8 文本；二进制资料先转换为 Markdown 或文本。Git reference 不声明本地文件，
也不在确认前自动 clone。Reference 的 `hidden` 和角色使用关系都不是访问控制。专家包不开发根级 `AGENTS.md`。

## Structure 与配置

使用 OpenCode 项目结构：

```text
<slug>/
├── expert.json
├── opencode.json
├── README.md
├── .env.example              # 可选
└── .opencode/
    ├── agents/
    ├── skills/
    ├── commands/
    ├── tools/
    ├── plugins/
    ├── references/<slug>/
    └── instructions/<slug>/
```

`opencode.json` 写入 `https://opencode.ai/config.json` schema。用户可配置值通过现有 OpenCode 配置层和 `{env:VARIABLE}` 传入；生成器从最终运行配置中提取环境变量名并生成 placeholder-only `.env.example`。该文件只记录变量名，不承载真实值，也不代替运行环境注入。

## Authoring 检查

生成前确认：

- 每个 agent 的职责与触发场景能区分，团员之间没有重叠到无法路由。
- 每个 skill 的描述足以让 agent 决定是否加载，正文只包含重复可用的流程和知识。
- package resources 被正确归属并在对应 skill 中导航。
- 每个用户可直接触发的稳定 workflow 已评估 command；已声明 command 能接收动态文字和同次调用附件，并路由到正确入口 agent。
- 已明确是否需要顶层 Workflow；不需要时保持省略并使用普通 Todo。需要时每个 Workflow 都有
  autonomy、Phase 和 acceptance，Phase 与 Agent override 只在边界确实不同时覆盖。
- parallel Phase 的角色均为必参与角色；每个角色可有多个动态实例，实例之间无共享写入冲突，
  两级 fan-in 与返工 `task_id` 已写入团长合同。
- 能使用 skill script、custom tool、MCP tool 或受控 programming tool 的稳定阶段已固定执行器，不允许 Agent 临时现写替代实现。
- 随包资料、plugins/hooks、custom tools 与 workspace instructions 已按真实需求评估，并写入设计确认稿；不依赖 generator 或 validator 自动补建。
- 每个 Reference 和角色规则至少分配给一个角色；未分配角色的 Agent Markdown 不泄露对应路径或
  规则正文。需要强隔离的资料没有误用 Reference 路由代替权限控制。
- 对计划同时安装的包完成 Agent/MCP/LSP/command/plugin/tool 跨包冲突审计；plugin/tool 文件使用 slug 命名空间，并在同一临时 workspace 顺序安装读回 receipts 与配置。
- workspace 指令只通过 `.opencode/instructions/<slug>/` 与 `opencode.json.instructions` 声明，包根目录没有 `AGENTS.md`。
- 配置只使用 OpenCode 字段或明确的 MobileWork 扩展字段。
- secret 使用 `{env:VARIABLE}`，包内没有 `.claude*` 结构、其他宿主路径变量或开发机路径。

生成后运行 `validate_expert.py`，再做 install、package、解压后二次校验和运行态配置读回。
