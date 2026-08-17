# 专家包文档生成规范

标记区内 README 模板由生成器直接读取。四反引号围栏与本页说明不属于生成结果。

启用 `workflow.autonomy` 时，“工作流程”必须显示五档直观名称、workflow/phase/Agent override 的
声明值与生效值、提高自主度原因、执行器、标准和验收；“运行时扩展”列出由
`workflows[].command` 生成的入口。权限摘要另按每个角色的自主度显示标签、内部值、来源、敏感动作
和外部 Skill 动作，不把 Workflow/Phase 自主度列为权限来源。旧角色缺少 autonomy 时保持原 README
文件，并在只读校验中报告临时 bounded 投影 warning。

“运行时扩展”必须包含 Agent 运行参数摘要：逐角色显示 `steps`、`model`、`variant`、`hidden` 和
`options`；`options` 只列键名，不在摘要中复制 provider-specific 值。未声明项标记为继承默认值；
不得列出 `temperature`、`top_p`、仅供 manifest 输入兼容的 `max_turns`、`maxTurns` 或已弃用的
`maxSteps`。

“内置技能”在零 Skill 时必须用“无”或等价中文明确零分配状态，不能留下空表。角色资源摘要
只列角色明确拥有的 custom tool 及调用用途；Plugin 作为 package-wide 运行行为列在本节，不声明为
角色所有。职责、流程、输出结构和质量门由 Agent 合同定义，不得声称必须由 Skill 承载。

## README.md

<!-- mobilework-template:readme:start -->
````markdown
# $expert_name

$display_description

## 类型

$type_label

## 功能

$feature_summary

## $roles_heading

$roles_content

## 工作流程

$workflow

## 内置技能

$skills_table

## 使用示例

$quick_prompts

## 包结构

- `expert.json`：本包的结构与资源所有权 manifest；修改能力、角色、展示字段或权限时先改这里。
- `opencode.json`：MobileWork 当前兼容的运行时配置文件，包含 agent、权限、MCP 和运行时扩展配置。
- `.env.example`：仅当 `opencode.json` 引用 `{env:VARIABLE}` 时生成的变量名清单；只含占位值，不会自动加载真实配置。
- `avatars/`：包内专家、团长和团员头像资源；本地相对 `avatar_url` 应能解析到这里的真实文件。
- `.opencode/agents/`：MobileWork 运行时读取的专家 / 专家团角色 Markdown 定义。
- `.opencode/skills/`：按已确认能力生成或导入的业务 Skill；无适合固化的能力时保留为空。
- `.opencode/commands/`：可选的自定义命令。
- `.opencode/tools/`：可选的自定义工具定义。
- `.opencode/plugins/`：可选的本地插件；依赖只通过 `.opencode/package.json` 声明，不携带 `node_modules`。
- `.opencode/references/<slug>/<alias>/`：可选的包内资料目录；Git Reference 只在 `expert.json` 和 `opencode.json` 声明仓库，不生成本地 backing file。
- `.opencode/instructions/<slug>/`：可选的指令文件；workspace 规则由 `opencode.json.instructions` 索引，`roles/` 下的角色规则只写入被分配角色的 Agent Markdown。
- `package_resources[]`：统一声明 skill 子树内包括 `SKILL.md` 在内的全部文件及 SHA-256；实际文件保留在对应 skill 子树。

本包保留 MobileWork 项目结构，不生成根级 `AGENTS.md`，也不包含非运行必需的根级配置或隐藏目录。
角色、职责、流程和质量门不会直接生成资源；普通运行只消费包内资源，不修改 `expert.json`、
Skill、custom tool 或 Plugin。

## 运行时扩展

$mcp_note

本地 Plugin 是整个专家包的运行行为，不是任何角色的私有能力；外部系统访问由 MCP 承担。

## 配置与环境变量

$settings

## 注意事项

$notes
````
<!-- mobilework-template:readme:end -->
