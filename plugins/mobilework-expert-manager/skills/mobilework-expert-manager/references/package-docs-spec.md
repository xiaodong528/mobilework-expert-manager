# 专家包文档生成规范

标记区内 README 模板由生成器直接读取。四反引号围栏与本页说明不属于生成结果。

启用 `workflow.autonomy` 时，“工作流程”必须显示五档直观名称、workflow/phase/Agent override 的
声明值与生效值、提高自主度原因、执行器、标准和验收；“运行时扩展”列出由
`workflows[].command` 生成的入口。旧 manifest 没有自主度字段时保持原 README 投影。

“运行时扩展”必须包含 Agent 运行参数摘要：逐角色显示 `steps` 和已声明可选项；`options` 只列键名，
不在摘要中复制 provider-specific 值。未声明项标记为继承默认值；不得把仅供 manifest 输入兼容
的 `max_turns`、`maxTurns` 或已弃用的 `maxSteps` 列为运行参数。

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
- `.opencode/skills/`：MobileWork 运行时读取的通用和角色专属 playbook。
- `.opencode/commands/`：可选的自定义命令。
- `.opencode/tools/`：可选的自定义工具定义。
- `.opencode/plugins/`：可选的本地插件；依赖只通过 `.opencode/package.json` 声明，不携带 `node_modules`。
- `.opencode/references/<slug>/<alias>/`：可选的包内引用资料目录。
- `.opencode/instructions/<slug>/`：可选的 workspace 全局指令文件；必须由 `opencode.json.instructions` 索引。
- `package_resources[]`：声明 supplemental skill 内脚本、规则、模板和二进制资源；实际文件保留在对应 skill 子树。

本包保留 MobileWork 项目结构，不生成根级 `AGENTS.md`，也不包含非运行必需的根级配置或隐藏目录。

## 运行时扩展

$mcp_note

## 配置与环境变量

$settings

## 注意事项

$notes
````
<!-- mobilework-template:readme:end -->
