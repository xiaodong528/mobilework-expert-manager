# MobileWork 运行时扩展规范

当专家包需要 commands、custom tools、plugins、references、workspace 全局 instructions、LSP、
MCP 或环境变量时读取本文件。所有配置都从 `expert.json` 派生到 `opencode.json` 与 `.opencode/`；
不要直接编辑派生配置来掩盖上游 manifest 问题。

`opencode.json` 的官方 schema、包级支持字段和 workspace/user 配置边界见
`opencode-json-spec.md`；本文件只描述已支持扩展如何从 manifest 投影到运行时。

## 目录

1. [包内落位](#1-包内落位)
2. [完整 Manifest 示例](#2-完整-manifest-示例)
3. [Commands](#3-commands)
4. [Custom tools](#4-custom-tools)
5. [Plugins 与依赖](#5-plugins-与依赖)
6. [References](#6-references)
7. [Instructions](#7-instructions)
8. [LSP](#8-lsp)
9. [MCP](#9-mcp)
10. [环境变量](#10-环境变量)
11. [CLI 安装投影](#11-cli-安装投影)
12. [已知边界](#12-已知边界)

## 1. 包内落位

```text
<slug>/
├── expert.json
├── opencode.json
├── .env.example                 # 仅存在 env 引用时生成
└── .opencode/
    ├── commands/
    ├── tools/
    ├── plugins/
    ├── references/<slug>/<alias>/
    ├── instructions/<slug>/
    └── package.json
```

根级 `references/` 与 `instructions/` 非法。所有包内路径使用 POSIX 相对路径，禁止绝对路径、
`..`、symlink 和本机 checkout 信息。

`opencode.json` 必须写入：

```json
{
  "$schema": "https://opencode.ai/config.json"
}
```

## 2. 完整 Manifest 示例

```json
{
  "runtime_extensions": {
    "commands": [
      {
        "name": "review-scope",
        "description": "审查范围和验收标准",
        "template": "执行“范围与验收审查”workflow。\n用户要求：$ARGUMENTS\n结合本次调用中可访问的图片、PDF 或其他附件；附件不可访问时明确要求用户重新附加。",
        "agent": "contract-reviewer"
      }
    ],
    "custom_tools": [
      {
        "path": "score.ts",
        "content": "export default {}"
      }
    ],
    "plugins": {
      "npm": ["opencode-example-plugin"],
      "local": [
        {
          "path": "notify.ts",
          "content": "export const NotifyPlugin = async () => ({})"
        }
      ],
      "package_json": {
        "dependencies": {
          "shescape": "^2.1.0"
        }
      }
    },
    "reference_files": [
      {
        "path": ".opencode/references/contract-review-expert/playbook/overview.md",
        "content": "# Playbook\n"
      }
    ],
    "references": {
      "playbook": {
        "path": ".opencode/references/contract-review-expert/playbook",
        "description": "处理合同审查时使用",
        "hidden": false
      },
      "upstream": {
        "repository": "https://example.com/reference.git",
        "branch": "stable",
        "description": "上游参考资料",
        "hidden": true
      }
    },
    "instruction_files": [
      {
        "path": ".opencode/instructions/contract-review-expert/evidence.md",
        "content": "# Evidence rules\n"
      }
    ],
    "instructions": [
      ".opencode/instructions/contract-review-expert/*.md"
    ],
    "lsp": {
      "custom-lsp": {
        "command": ["custom-lsp-server", "--stdio"],
        "extensions": [".custom"]
      }
    }
  },
  "mcp_servers": [
    {
      "name": "secure-docs",
      "type": "remote",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer {env:API_TOKEN}"
      },
      "oauth": false,
      "timeout": 3000,
      "enabled": false
    },
    {
      "name": "oauth-docs",
      "type": "remote",
      "url": "https://example.com/oauth/mcp",
      "oauth": {
        "clientId": "{env:OAUTH_CLIENT_ID}",
        "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
        "scope": "documents.read documents.write",
        "callbackPort": 19876,
        "redirectUri": "http://127.0.0.1:19876/mcp/oauth/callback"
      },
      "enabled": false
    }
  ]
}
```

## 3. Commands

Workflow command 优先声明在 `workflows[].command`，只包含 `name` 与 `description`。generator
从 workflow 合同生成正文，自动路由到单专家或团长，并完整写入 `$ARGUMENTS`、同次调用附件、
五档自主度、全部 phase、全部 Agent override、执行器、标准、验收和停止条件。不得手写第二份
workflow template。

`runtime_extensions.commands[]` 只用于额外的非 workflow command，继续支持自定义 template、
agent、subtask 和 model。两种来源最终都生成到 `.opencode/commands/`，名称不得冲突，也都不写入
`opencode.json` 根级 `command`。

`runtime_extensions.commands[]` 生成 `.opencode/commands/<name>.md`。
OpenCode 官方同时支持根级 `command` 与 Markdown 文件；MobileWork 专家包固定采用文件投影，
因此 `opencode.json` 不生成根级 `command`。上游语法见
[OpenCode Commands](https://opencode.ai/docs/commands/)。

| 字段 | 规则 |
|---|---|
| `name` | 必填 kebab-case，决定文件名。 |
| `template` | 必填命令正文。 |
| `description` | 可选说明。 |
| `agent` | 可选默认 agent；必须引用本包已声明的 Agent id。 |
| `subtask` | 可选 OpenCode subtask 配置。 |
| `model` | 可选模型选择；必须至少采用非空 `provider/model` 形式。 |

command 是面向用户的 workflow 快捷入口，不是 workflow 本体的第二份真相源：

- 为每个可由用户直接触发、会重复使用的稳定 workflow 默认推荐一个 `workflows[].command`；多个 workflow
  使用多个独立的 kebab-case 名称。内部 handoff、单个 phase 和一次性流程不单独创建。
- 单专家默认把 `agent` 指向该专家；专家团默认指向团长，由团长按已确认 workflow 编排。只有用户
  明确要求直达某个团员时，才指向 subagent 并按需要显式设置 `subtask`。
- 模板使用 `$ARGUMENTS` 接收 `/command 用户提示词` 的动态文字；需要稳定位置参数时可用 `$1`、
  `$2`，固定项目文件可用 OpenCode 官方 `@path` 引用。
- 图片、PDF、音频或其他多模态输入由用户在同一次调用中附加，属于宿主消息层，不属于
  `$ARGUMENTS`。模板只要求处理当前可访问附件；附件不可访问时明确要求重新附加，不新增附件
  占位符、二进制编码或推测出的本机路径。
- `description` 写明对应 workflow 和使用时机。按项目锁定的 OpenCode command registry，
  `/init` 与 `/review` 是 OpenCode 内置命令，两类 command 都必须拒绝同名；`/help` 不属于该
  核心冲突集合。诊断格式统一为
  `<field>.name: conflicts with OpenCode built-in command <name>`，不提供 override 字段。
- 命令正文不得包含真实 secret 或开发机路径。

调用示例：

```text
/review-scope 请重点检查交付范围和验收风险 + 用户在同一次调用中附加的合同 PDF 与现场截图
```

## 4. Custom tools

`runtime_extensions.custom_tools[]` 生成 `.opencode/tools/<path>`。

当用户需要智能体直接调用的 JavaScript/TypeScript 执行能力时推荐 custom tool；如果需求是
监听事件、拦截既有工具或修改运行时行为，则改用 plugin。

- `path` 只接受 `.js` 或 `.ts` 包内相对路径。
- `content` 必须内嵌非空文本；生成器按声明重建真实文件。
- Todo 由系统托管，禁止声明 `todowrite.ts`、`todoread.ts` 或任何 stem 为 `todowrite` /
  `todoread` 的 custom tool。
- 工具产生业务文件时，接收 workspace root 或等价参数，不把业务产物写进 `.opencode/`。
- 权限通过角色 `permission` 声明，不把 custom tool 塞入 legacy `tools` 布尔映射。
- `.opencode/tools/` 由 OpenCode 自动发现，`opencode.json` 不生成根级 `tools`。

## 5. Plugins 与依赖

当用户需要类似 hook 的事件监听、工具执行前后拦截、外部服务集成或运行时行为修改时推荐
local plugin；不要用 plugin 代替一个只需被智能体直接调用的普通 custom tool。

- `plugins.npm[]` 合并到 `opencode.json.plugin`；条目不得重复。
- `plugins.local[]` 生成 `.opencode/plugins/<path>`，只接受内嵌 `content` 的 `.js` 或 `.ts`。
- `plugins.package_json` 只接受 `dependencies` 与 `devDependencies`，生成 `.opencode/package.json`。
- 包内不得携带 `node_modules`、lock 缓存或安装产物。
- `.opencode/plugins/` 由 OpenCode 自动发现，本地 plugin 路径不写入 `opencode.json.plugin`。

CLI 安装按 package name 合并依赖。同一依赖名版本不一致时，必须在写入 workspace 前失败。

### 5.1 Workspace 共存与命名空间

安装会把多个专家包投影到同一个 `.opencode`。下列资源不是包内私有空间，必须在设计时
做跨包冲突审计：Agent id、MCP name、LSP server key、command 文件名、local plugin 路径和
custom tool 路径。local plugin 与 custom tool 的 `path` 应使用 `<slug>-<name>.ts`、
`<slug>-<name>.js` 或 `<slug>/<name>.*`，并同步更新 workflow executor ref 与 `permission` 中的
工具名。MCP、LSP、Agent 和 command 不能复用另一个包已拥有的 key；看似相同的配置也不能绕过
receipt 所有权边界。

验收至少包含三次安装：每个包分别安装，以及全部包在同一干净 workspace 顺序安装。共存安装后
读回所有 receipts、owned file hashes 和 `opencode.jsonc`；若仅共存失败且投影与 receipt
没有丢字段或串改，归因为专家包编写错误并修正命名，而不是修改 MobileWork 以静默覆盖资源。

## 6. References

目标 OpenCode capability contract 支持根级 `references` 时，`runtime_extensions.references` 是唯一上游声明，
生成器把短 alias 改写为 `<slug>-<alias>` 后投影到 `opencode.json.references`。local entry 由
`reference_files[]` 提供包内真实文件；Git entry 由 OpenCode 按 repository 异步 materialize。

用户提供需要随包分发的领域资料、规范、案例、知识库或操作手册时推荐 reference。当前合同
只接受非空 UTF-8 文本；PDF、DOCX、图片等先转换为 Markdown 或文本，不随包保留二进制原件。

- `reference_files[].path` 必须位于 `.opencode/references/<slug>/<alias>/`。
- 每个文件嵌入非空 UTF-8 `content`；validator 比较声明与实际内容。
- MobileWork ownership 合同只接受对象，虽然上游支持字符串简写，专家包仍拒绝简写以保留 source 类型与可选字段的精确校验。
- local `path` 必须精确等于 `.opencode/references/<slug>/<alias>`。
- local reference 目录至少匹配一个 `reference_files[]` 文件。
- local 对象只允许必需 `path` 与可选 `description`、`hidden`；`hidden` 必须是布尔值。
- Git 对象只允许必需非空 `repository` 与可选非空 `branch`、字符串 `description`、布尔 `hidden`。
- `repository` 是交给 OpenCode materialize 的不透明、无首尾空白非空字符串；manager 不限制协议、
  主机或可达性，`http://127.0.0.1:<port>/repo.git` 等可控测试源也合法。空值和仅空白值仍拒绝。
- 每个对象必须在 `path` 与 `repository` 中恰好声明一个；`branch` 仅对 Git entry 有效。
- 每个 `reference_files[]` 都必须被一个 local entry 拥有；Git entry 不得伪造本地 backing file。
- manifest 使用短 alias；生成配置和 CLI receipt 使用 `<slug>-<alias>`，避免多个专家互相覆盖。

例如 local `playbook` 与 Git `upstream` 生成：

```json
{
  "references": {
    "contract-review-expert-playbook": {
      "path": ".opencode/references/contract-review-expert/playbook",
      "description": "处理合同审查时使用",
      "hidden": false
    },
    "contract-review-expert-upstream": {
      "repository": "https://example.com/reference.git",
      "branch": "stable"
    }
  }
}
```

## 7. Instructions

`instruction_files[]` 提供包内文件，`instructions[]` 只表示 workspace 全局指令。

- `instruction_files[].path` 必须位于 `.opencode/instructions/<slug>/`，并嵌入非空 UTF-8 `content`。
- 本地 instruction 可为文件路径或 Glob，必须至少匹配一个声明文件。
- `instructions[]` 不得包含重复条目。
- 远程 instruction 只允许 HTTPS；validator 会提示不可复现。HTTP 必须失败。
- 角色专属规则放入 agent Markdown 或对应 skill，不要扩大为 workspace 全局指令。
- 专家包不开发或生成根级 `AGENTS.md`；需要对整个 workspace 生效的自定义指令统一生成到
  `.opencode/instructions/<slug>/`，并通过 `opencode.json.instructions` 明确索引。

## 8. LSP

`runtime_extensions.lsp` 使用管理器通用合同支持的三种 shape：
`true`、`false` 或非空 server mapping，并原样投影到 `opencode.json.lsp`。未声明时必须省略根键；
省略与显式 `false` 语义不同。显式空 mapping `{}` 没有可执行含义，必须拒绝并提示省略字段。

每个 server 使用稳定 kebab-case 名称，并且只能采用以下两种对象之一：

- disabled-only：精确为 `{"disabled": true}`，不得同时携带其他字段；
- custom server：必需非空 `command: string[]` 与非空 `extensions: string[]`，可选布尔
  `disabled`、`env: string -> string`、对象 `initialization`。

`command` 和 `extensions` 的每个成员都必须是非空字符串；扩展名由 OpenCode 解释，manager 不额外
要求 `.` 前缀。`initialization` 必须是 JSON object，server 未知字段一律拒绝，以免 OpenCode
静默丢弃后破坏 source/projection exact parity。命令数组不写本机绝对可执行文件路径。

- CLI 安装按 server name 合并，不整体覆盖 workspace 已有 `lsp`。
- 不同 slug 声明同名但不兼容的 LSP 配置时，在写入前报告冲突。

## 9. MCP

`mcp_servers[]` 是 `opencode.json.mcp` 的上游声明。

### Local MCP

```json
{
  "name": "local-review",
  "type": "local",
  "command": ["review-mcp", "--stdio"],
  "environment": {
    "API_TOKEN": "{env:API_TOKEN}"
  },
  "timeout": 3000,
  "enabled": false
}
```

### Remote MCP

```json
{
  "name": "remote-review",
  "type": "remote",
  "url": "https://example.com/{env:TENANT_ID}/mcp",
  "headers": {
    "Authorization": "Bearer {env:API_TOKEN}"
  },
  "oauth": false,
  "timeout": 3000,
  "enabled": false
}
```

带固定 header 的 remote MCP 应显式设置 `"oauth": false`，否则 OpenCode 会继续尝试 OAuth
自动发现。需要 OAuth 时可以使用动态客户端注册：

```json
{
  "name": "oauth-review",
  "type": "remote",
  "url": "https://example.com/mcp",
  "oauth": {},
  "enabled": false
}
```

也可以声明 capability contract 支持的完整客户端配置：

```json
{
  "name": "oauth-review",
  "type": "remote",
  "url": "https://example.com/mcp",
  "oauth": {
    "clientId": "{env:OAUTH_CLIENT_ID}",
    "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
    "scope": "reviews.read reviews.write",
    "callbackPort": 19876,
    "redirectUri": "http://127.0.0.1:19876/mcp/oauth/callback"
  },
  "enabled": false
}
```

- `name` 使用 kebab-case。
- local 需要非空 `command` 数组，可声明字符串映射 `environment`；remote 需要 HTTP(S) URL，可声明
  字符串映射 `headers`。
- `enabled` 默认 `false`，manifest 显式布尔值按原样保留。
- local/remote 均可声明正整数 `timeout`。
- remote `oauth` 只接受 `false` 或对象；`true` 非法。空对象表示动态客户端注册。
- OAuth 对象只允许 `clientId`、`clientSecret`、`scope`、`callbackPort`、`redirectUri`；
  `callbackPort` 范围为 `1–65535`，`redirectUri` 使用 HTTP(S)。
- `redirectUri` 与 `callbackPort` 同时存在时 OpenCode 优先使用 `redirectUri`；需要分别验证两种回调
  形态时使用独立 fixture，不把同一对象的两个字段误报为同时控制回调地址。
- OAuth 凭据与授权结果由 OpenCode 写入隔离的用户状态目录，不进入专家包、安装 receipt 或证据日志。
- 没有 `mcp_servers` 时，运行时配置不生成 MCP 占位。
- 真实 token、key 和私有 endpoint 不得明文写入包。
- 角色 `mcp[]` 只能引用已声明 MCP，生成权限与运行配置必须一致。
- 同名 MCP、未知字段、local/remote 交叉字段、缺失 command/URL 必须在生成前失败，不能静默覆盖或补占位。
- 目标版本与能力必须由 CLI、环境、host contract 或可信 sidecar 显式证明；schema 中未声明的 local
  `cwd` 不属于当前合同。

## 10. 环境变量

`{env:VARIABLE}` 可以出现在 MCP URL、headers、environment 或其他生成态 OpenCode 字符串中。

- 生成器从最终 `opencode.json` 递归提取变量名。
- 有引用时生成根级 `.env.example`，排序并去重。
- 每行严格使用 `VARIABLE=<required>`，不写真实值。
- `.env.example` 只用于配置发现，不自动加载、安装或注入 workspace。
- 没有环境变量引用时不生成该文件。
- 新包变量集合不一致时 validator 失败；兼容旧包缺文件时只给迁移 warning。

## 11. CLI 安装投影

完整安装目标为 `<workspace>/.opencode/`，配置文件为
`.opencode/opencode.jsonc`，receipt 为
`.opencode/.expert-installs/<slug>.json`。

CLI 安装把 reference 和 instruction 文件路径统一改写为 `.opencode/` 下的实际路径：

| 包内配置 | workspace 文件 | 安装后配置 |
|---|---|---|
| `.opencode/references/<slug>/<alias>` | `.opencode/references/<slug>/<alias>/*` | `references/<slug>/<alias>`（位于 namespaced `references` entry） |
| `.opencode/instructions/<slug>/*.md` | `.opencode/instructions/<slug>/*.md` | `.opencode/instructions/<slug>/*.md` |

安装前完成结构、内容、hash、路径和 ownership 冲突预检。
`agent`、`mcp`、`references`、`plugin`、`instructions` 与 `lsp` 按键或条目合并；local reference
只改写 `path`，Git `repository` entry 原样保留。receipt 记录每个 namespaced alias 的精确值。
`--force` 只能替换同 slug receipt 拥有的资源，不能覆盖其他专家资源。
安装使用 staging、备份和失败回滚；失败后 workspace 恢复到写入前状态。

## 12. 已知边界

- MobileWork 桌面安装通过完整事务投影复制 `.opencode` 资源、共享 Skills、配置与依赖，并用
  revision ownership 台账读回；CLI 安装保留为独立兼容入口。
- Git repository 的可访问性与 materialize 时机由 OpenCode 管理；manager 只校验声明、ownership、
  精确投影和安装回读，不把网络 clone 成功误当作离线 package validator 的职责。
