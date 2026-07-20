# `opencode.json` 派生配置规范

生成、检查或修改 MobileWork 专家包的 `opencode.json` 时读取本文件。

OpenCode 官方实时 JSON Schema 位于 `https://opencode.ai/config.json`，它定义 OpenCode
配置字段的通用语义。本文只定义 MobileWork 专家包拥有并能从 `expert.json` 稳定重建的子集。
一个字段被官方 schema 接受，不代表它自动成为专家包可拥有的字段。

## 1. 真相源与验证边界

- `expert.json` 是结构、运行能力和资源所有权的唯一真相源。
- `opencode.json` 是生成器输出；不要直接编辑它来增加模型、provider、权限或运行能力。
- 生成器输出严格 JSON。官方解析器允许 comments 与 trailing commas，不改变专家包的严格 JSON 合同。
- 核心 validator 使用本地 allowlist 和 manifest 投影做离线校验，不下载或内置完整官方 schema。
- 官方 schema 只用于设计时核对和可选的联网兼容性 smoke；网络不可用不得影响核心校验。

## 2. 包级支持字段

生成的 `opencode.json` 根对象只允许以下字段：

```text
$schema
agent
mcp
plugin
references
instructions
lsp
```

| 字段 | 必需性 | 上游来源 |
|---|---|---|
| `$schema` | 必需 | 固定为 `https://opencode.ai/config.json`。 |
| `agent` | 必需 | `agent` 或 `primary_agent`、`subagents[]`。 |
| `mcp` | 可选 | `mcp_servers[]`；没有声明时省略。 |
| `plugin` | 可选 | `runtime_extensions.plugins.npm[]`；没有声明时省略。 |
| `references` | 可选 | `runtime_extensions.references`；alias 统一生成为 `<slug>-<alias>`。 |
| `instructions` | 可选 | `runtime_extensions.instructions[]`。 |
| `lsp` | 可选 | `runtime_extensions.lsp` 的 `true`、`false` 或非空 server mapping。 |

可选段没有真实声明时必须省略，不生成空对象、空数组或占位配置。

`opencode.json.references` 的每个生成 alias 都带 `<slug>-` 前缀。local 对象必需 `path`，可选
`description`、`hidden`；Git 对象必需 `repository`，可选 `branch`、`description`、`hidden`。
`path` 与 `repository` 必须恰好声明一个，`branch` 只允许用于 Git。MobileWork 为保证 ownership
和精确回读不接受上游字符串简写；local `path` 必须等于
`.opencode/references/<slug>/<alias>`，并至少匹配一个 `reference_files[]`。

`mcp` 的 package-owned 子集与 MobileWork 固定 OpenCode sidecar v1.18.3 对齐：local 支持
`type/command/environment/enabled/timeout`，remote 支持
`type/url/headers/oauth/enabled/timeout`。remote `oauth` 为 `false` 或只包含
`clientId/clientSecret/scope/callbackPort/redirectUri` 的对象；生成器和 validator 必须与
`expert.json.mcp_servers[]` 做完整等值投影，不能只检查 `enabled`。

`lsp` 与 bundled OpenCode v1.18.3 的官方 union 对齐：根值为布尔值或 server mapping。mapping
中的 server 要么精确为 `{"disabled": true}`，要么声明非空 `command: string[]` 和非空
`extensions: string[]`，并可带布尔 `disabled`、字符串映射 `env`、对象 `initialization`。
未知 server 字段拒绝；`extensions` 不额外要求点前缀。未声明 `runtime_extensions.lsp` 时根键
必须省略，显式 `false` 必须保留，显式空 mapping `{}` 拒绝，生成态必须与 manifest 完全相等。

## 3. Agent 投影

字段语义以 [OpenCode Agent 选项](https://opencode.ai/docs/zh-cn/agents/#选项)、
[实时配置 Schema](https://opencode.ai/config.json) 和
[官方 AgentConfig 实现](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/v1/config/agent.ts)
为上游依据；本文收紧 MobileWork 专家包的声明、投影和校验合同。

OpenCode 当前正式步数字段只有 `steps`；遗留 `maxSteps` 已弃用。`max_turns`、`maxTurns` 都不是
OpenCode Agent 配置字段，只是 MobileWork 为读取既有 `expert.json` 保留的输入兼容名。它们不得
出现在 Agent Markdown、`opencode.json`、README 参数名或任何“官方支持字段”清单中。

`opencode.json.agent.<id>` 只拥有以下字段：

```text
mode
description
steps
model
variant
temperature
top_p
hidden
options
permission
```

- `mode` 从专家类型与角色位置派生，单专家和团长为 `primary`，团员为 `subagent`。
- `description` 组合角色能力与触发条件，并与 agent Markdown frontmatter 一致。
- `steps` 从 `expert.json` 的正式输入 `steps`，或仅限 MobileWork 的历史输入 `max_turns`、
  `maxTurns` 归一化得到；输出键始终只有 `steps`。
- `model`、`variant`、`temperature`、`top_p`、`hidden`、`options` 只在对应角色显式声明时原样投影。
- `permission` 从角色权限、skills、MCP 和团队委派关系派生，并与 agent Markdown 一致。

`title` 只作为旧 MobileWork manifest 缺少 `name` 时的输入回退；新 manifest 使用 `name`。
兼容读取后只派生标准 `displayName`/`description` 等字段，Agent Markdown 和
`opencode.json.agent.<id>` 都不得生成 `title`。

`variant` 要求同一角色声明 `model`；`hidden` 只允许团员。采样参数必须是 `0.0–1.0` 的有限数字。
未声明字段必须省略，不能由 generator 猜测默认值。provider-specific 参数统一放入非空
`options` 对象；角色顶层 `prompt`、`disable`、已弃用的 `maxSteps` 和未知字段拒绝。旧 manifest 的 `tools`
只转换到 `permission`，不得出现在派生配置。

## 4. 文件型扩展与根配置的关系

不是所有 OpenCode 能力都写入根配置。以下扩展由真实文件承载：

| Manifest 声明 | 包内输出 | 根配置投影 |
|---|---|---|
| `runtime_extensions.commands[]` | `.opencode/commands/<name>.md` | 无 `command` 根键。 |
| `runtime_extensions.custom_tools[]` | `.opencode/tools/<path>` | 无 `tools` 根键。 |
| `runtime_extensions.plugins.local[]` | `.opencode/plugins/<path>` | 不加入 `plugin`。 |
| `runtime_extensions.plugins.package_json` | `.opencode/package.json` | 不加入根配置。 |
| `runtime_extensions.plugins.npm[]` | 无文件 | 加入 `plugin`。 |
| `reference_files[]` | `.opencode/references/<slug>/<alias>/...` | 作为 local `references.<alias>.path` 的 backing file，不加入 `instructions`。 |
| `instruction_files[]` | `.opencode/instructions/<slug>/...` | 由 `instructions` 建立索引。 |

文件内容、路径、hash 与配置索引都必须能回到 `expert.json` 的声明输入。
OpenCode 自动发现 `.opencode/plugins/` 与 `.opencode/tools/`，所以本地 plugin 和 custom tool
不需要也不得伪造根配置字段。MobileWork 固定的 OpenCode v1.18.3 正式支持根级 `references`：
local entry 使用 namespaced alias 和包内目录 `path`，并由 `reference_files[]` 提供真实文件；Git
entry 使用 `repository`，可带 `branch`、`description`、`hidden`。`instructions` 只索引显式
`instruction_files[]`，不再承载 local reference 文件。
OpenCode 官方也支持根级 `command`，但专家包为保持文件 ownership 与安装冲突边界，固定使用
`.opencode/commands/*.md`；workflow-to-command 编写规则见 `runtime-extensions-spec.md`，官方语法见
[OpenCode Commands](https://opencode.ai/docs/commands/)。

这些文件安装后与其他包共享 workspace 目录；可共存包的 plugin/tool 路径必须使用 slug
命名空间，Agent、MCP、LSP 和 command key 也必须经过跨包冲突审计。独立安装与同 workspace
顺序安装是两个不同验收项，不能用前者替代后者。

## 5. Workspace 或用户级字段

以下官方字段通常控制整个 OpenCode workspace、用户环境或宿主进程，不归单个 MobileWork
专家包所有：

- 模型和 provider：`model`、`small_model`、`provider`、`enabled_providers`、
  `disabled_providers`、`default_agent`；
- 宿主运行：`server`、`shell`、`logLevel`、`share`、`autoupdate`、`username`；
- 全局行为：根级 `permission`、`tools`、`formatter`、`watcher`、`snapshot`、
  `compaction`、`experimental`、`attachment`、`tool_output`；
- 全局资源发现：`skills`、根级 `command` 和其他未由 `expert.json` 映射的配置。

需要这些能力时，应在 workspace 或用户配置层设置。若确实要让专家包拥有某个新字段，先同时
扩展 `expert.json` 规范、manifest normalizer、生成器、validator、installer 合并与 ownership
冲突规则，再增加正负回归；不要先手改 `opencode.json`。

OpenCode 官方支持项目根级 `AGENTS.md`，但 MobileWork 专家包不拥有或生成该文件。专家或专家团
需要在整个 workspace 生效的自定义指令时，使用 `runtime_extensions.instruction_files[]` 生成
`.opencode/instructions/<slug>/`，并由 `runtime_extensions.instructions[]` 投影到本文件的
`instructions` 字段。

## 6. 官方弃用字段

截至 2026-07-17 的官方 schema 中，以下字段已标记弃用或被新字段替代：

- 根级 `mode` 改用 `agent`；
- 根级单数 `reference` 不属于当前合同；复数 `references` 是 OpenCode v1.18.3 的正式包配置字段；
- `autoshare` 改用 `share`；
- agent `maxSteps` 改用 `steps`；
- agent `tools` 改用 `permission`；
- `layout` 已不再控制实际布局。

专家包不得为了兼容旧示例重新生成这些弃用字段。

## 7. 上游更新检查

需要新增 OpenCode 能力或怀疑上游发生变化时：

1. 现场读取 `https://opencode.ai/config.json`，确认字段、required、enum、deprecated 与
   `additionalProperties`。
2. 区分“OpenCode 官方支持”和“MobileWork 专家包拥有”两种结论。
3. 用基础包和全扩展包做临时生成，并对生成的 `opencode.json` 运行官方 schema smoke。
4. 只有完成 manifest、生成、校验、安装和测试闭环后，才更新本文件的支持子集。

不要把某次下载的完整 schema 快照提交进技能；实时 schema 可能漂移，核心合同必须保持离线、
可复现。
