# `expert.json` 规范

`expert.json` 是 MobileWork 专家包的结构与资源所有权 manifest。生成器以它和声明资源为输入，
在 sibling staging 中重建派生文件，完整校验通过后再原子替换目标目录。

本文件描述当前合同。不要增加 `package_layout_version`，不要建立版本分支，也不要从
`README.md`、`opencode.json` 或 `.opencode/` 反推缺失的 manifest。

设计 agent、skill、触发描述和输出合同时，同时读取
`references/opencode-authoring-best-practices.md`。

## 目录

1. [类型与最小结构](#1-类型与最小结构)
2. [命名与展示字段](#2-命名与展示字段)
3. [Skill 声明](#3-skill-声明)
4. [Agent 字段与派生语义](#4-agent-字段与派生语义)
5. [专家团委派合同](#5-专家团委派合同)
6. [Workflow 结构](#6-workflow-结构)
7. [运行时与资源入口](#7-运行时与资源入口)
8. [修改已有包](#8-修改已有包)
9. [可复制单专家模板](#9-可复制单专家模板)

## 1. 类型与最小结构

### 单专家

```json
{
  "slug": "contract-review-expert",
  "type": "expert",
  "name": "合同审查专家",
  "description": "审查合同并提供有证据的修改建议。",
  "skills": [],
  "agent": {
    "id": "contract-reviewer",
    "name": "合同审查专家",
    "description": "审查合同条款并提出修改建议。",
    "skills": []
  }
}
```

- `type: expert` 必须有 `agent`，禁止 `primary_agent` 与 `subagents`。
- 单专家只生成一个 `mode: primary` agent，不包含团队委派规则。

### 专家团

```json
{
  "slug": "software-dev-team",
  "type": "team",
  "name": "软件开发专家团",
  "description": "由多个专业角色协作完成软件交付。",
  "skills": [],
  "primary_agent": {
    "id": "delivery-director",
    "name": "交付总监",
    "description": "编排、验收并集成跨角色交付。",
    "skills": []
  },
  "subagents": [
    {
      "id": "engineer",
      "name": "工程师",
      "description": "实现并验证代码变更。",
      "skills": []
    }
  ]
}
```

- `type: team` 必须有一个 `primary_agent` 和至少一个 `subagents[]`，禁止 `agent`。
- agent id 必须唯一；团长使用 `mode: primary`，团员使用 `mode: subagent`。

## 2. 命名与展示字段

`slug`、agent id、完整 skill name 和 MCP name 必须匹配：

```text
^[a-z0-9]+(-[a-z0-9]+)*$
```

| 字段 | 规则 |
|---|---|
| `slug` | 稳定包 id，必须与目录名一致。 |
| `version` | 可选 `X.Y.Z`。只在用户确认本地 release 时更新；未 release 的可信源可省略。 |
| `name` | 专家或专家团公开名称。单专家不得用 slug 或内部 id 代替。 |
| `summary` | 单行定位摘要。 |
| `description` | 说明能力、适用场景和交付结果。 |
| `language` | 默认 `zh`。 |
| `profession` | 可选职业定位。 |
| `category_id` | 可选分类 id。 |
| `display_description` | 可选公开短描述。 |
| `avatar_url` | HTTPS URL 或 `avatars/<file>` 包内路径。 |
| `tags` | 字符串数组，建议 3 项。 |
| `quick_prompts` | 快捷提示词数组，建议 3 项。 |
| `default_prompt` | 如存在，必须等于 `quick_prompts[0]`。 |
| `skills` | 统一技能池；可省略或为空，条目声明完整 name、origin 与 edit_policy。 |
| `mcp_servers` | 可选 MCP 声明；支持 local、remote、header auth、OAuth 与 timeout，详见 `runtime-extensions-spec.md`。 |
| `runtime_extensions` | commands、tools、plugins、目标 capability contract 支持的 local/Git references、角色规则、workspace instructions、LSP。 |
| `package_resources` | 统一技能池中包括 `SKILL.md` 在内的全部声明资源及 SHA-256。 |

来源资料中的宿主产品、平台发布和智能体容器叙事，在展示草案前改为 MobileWork 口径。
保留运行必需标识：slug、agent id、skill/MCP 名、文件名、命令、API、协议、第三方业务系统名，
以及用户明确要求保留的品牌资产。

## 3. Skill 声明

新 manifest 使用统一顶层技能池；不区分通用技能和角色专用技能，也不根据专家 slug 或角色 id
拼接名称：

```json
{
  "skills": [
    {
      "name": "contract-clause-review",
      "origin": "uploaded",
      "edit_policy": "preserved"
    }
  ],
  "agent": {
    "skills": [
      "contract-clause-review"
    ]
  }
}
```

- 顶层 `skills[]` 可省略或为空；名称不得重复。
- `name` 是合法 kebab-case 完整技能名，必须与目录名及 `SKILL.md` frontmatter `name` 一致。
- `origin` 只能是 `uploaded`、`managed` 或 `legacy-migrated`。
- `edit_policy` 只能是 `preserved` 或 `managed`。用户上传时固定为 `preserved`；只有用户明确授权
  修改包内副本时才可转为 `managed`，并且 `origin` 保持 `uploaded`。
- 每个角色的 `skills[]` 是完整技能名字符串列表，可省略或为空，只能引用顶层已声明名称。
- 同一技能可分配给一个、多个或全部角色；“全部成员”展开为团长和每个团员，不保存通配。
- `permission.skill` 完全由角色 `skills[]` 派生，禁止在新 manifest 中手写。
- `package_resources[]` 必须声明每个技能目录内包括 `SKILL.md` 在内的全部文件和匹配 SHA-256。

新 `skills[]` 与旧 `common_skills`/purpose schema 不得混用。未修改的旧包仍可读取、校验、安装和
打包；任何结构性修改先按本页“修改已有包”迁移，不在新建流程继续使用旧命名规则。

## 4. Agent 字段与派生语义

每个 `agent`、`primary_agent` 和 `subagents[]` 可以声明：

- `id`、`name`、`display_name`、`profession`、`description`、`avatar_url`、`color`；
- `responsibilities`、`route_triggers`、`workflow`、`quality_gates`、`handoff_contract`；
- `skills`、`references`、`instructions`、`mcp`、`custom_tools`、`permission`、可选 `permission_reason`；
- OpenCode 正式步数字段 `steps`，以及仅供 `expert.json` 读取旧包的 MobileWork 历史输入
  `max_turns`、`maxTurns`；
- 可选运行参数 `model`、`variant`、`temperature`、`top_p`、`hidden`、`options`。

`title` 是只读旧 MobileWork manifest 时允许的 `name` 回退，不是新的 Agent 字段。新 manifest
统一声明 `name`；当旧角色只有 `title` 时 generator 将其用于显示名，但不会向 Agent Markdown
或 `opencode.json.agent.<id>` 派生 `title`。若同时声明 `name` 与 `title`，`name` 优先。
角色 `mcp[]` 只能引用已声明的 MCP server，且条目不得重复。
角色 `custom_tools[]` 只接受非空、不重复的相对 path，并必须精确匹配
`runtime_extensions.custom_tools[].path`。单专家不自动拥有全部包级 custom tool；workflow
executor 可以只为实际参与角色建立该 workflow 所需的 tool 所有权。旧 `tools` mapping 仅作为
布尔 permission 兼容输入，不是 custom tool 所有权声明。

角色资源绑定合同：

- `references[]` 使用 kebab-case 短 alias，只能引用 `runtime_extensions.references`；新包一旦声明
  Reference，所有角色都显式写出数组，空数组有效，每个 Reference 至少有一个使用角色。
- `instructions[]` 使用 kebab-case 短 alias，只能引用
  `runtime_extensions.role_instructions`；存在角色规则时所有角色显式写出数组，每条规则至少有一个
  使用角色。
- 两个数组都拒绝未知、重复 alias 和 `*`。Reference 缺少 `description` 仍合法，但已分配角色时
  validator 给 warning。
- `references[]` 和 `instructions[]` 负责路由、生成与审计，不是访问控制。Reference 仍投影为根级
  OpenCode 配置；角色规则则只写入被分配角色的 Agent Markdown。严格隔离资料时使用角色专属
  Skill 或带权限的 MCP。

`steps` 是 OpenCode 官方支持且新设计唯一使用的步数字段。`max_turns`、`maxTurns` 不是 OpenCode Agent
选项，只为避免批量迁移既有 MobileWork manifest 而在 `expert.json` 输入层兼容；三种输入都
必须是正整数，同时出现时必须完全相等，否则拒绝。历史输入永不写入 Agent Markdown 或
`opencode.json`，两份派生配置只写 `steps`。默认值：

- 单专家：`80`；
- 专家团团长：`150`；
- 专家团团员：`50`。

运行参数合同：

| 字段 | 规则 |
|---|---|
| `model` | 非空 `provider/model`；未声明时继承 OpenCode 或 workspace 默认模型。 |
| `variant` | 非空字符串，并且同一角色必须显式声明 `model`。 |
| `temperature` | 有限数字，范围 `0.0–1.0`。 |
| `top_p` | 有限数字，范围 `0.0–1.0`。 |
| `hidden` | 布尔值，只允许出现在 `subagents[]`。 |
| `options` | 非空 JSON 对象；用于 `reasoningEffort`、`textVerbosity` 等 provider-specific 参数，递归禁止非有限数字。 |

`temperature` 与 `top_p` 可以同时声明，但通常只调一个以便解释行为。未声明的可选参数不生成，
由 OpenCode、模型或 provider 继承默认值；生成器不得自行推断采样参数。`options` 仍受 secret 与
便携性扫描约束，不得写真实凭证、私有 endpoint 或开发机路径。

角色字段使用显式 allowlist。OpenCode 遗留字段 `maxSteps` 已弃用，本合同也拒绝它；同时拒绝
`prompt`、`disable` 及其他未知顶层字段；
provider-specific 参数必须放入 `options`。旧 `tools` 只作为 manifest 到 `permission` 的布尔兼容
输入，永不写入 Agent Markdown 或 `opencode.json`。

统一技能池 manifest 未声明顶层 Workflow 时，permission 使用 `no-workflow-bounded-default`；
启用 workflow autonomy 时，permission 按 `references/permission-policy-spec.md` 从角色的全部
effective autonomy、execution 和 ownership 合并。显式规则提高计算动作时必须声明非空
`permission_reason`；它不能改写 task、Skill、MCP、Bash 通配或外部目录硬边界。

生成的 agent frontmatter 至少包含：

```yaml
name: contract-reviewer
description: 审查合同条款并提出修改建议；当用户要求合同风险审查时使用。
displayName:
  en: 合同审查专家
  zh: 合同审查专家
profession:
  en: 合同风险审查专家
  zh: 合同风险审查专家
steps: 80
mode: primary
color: '#2563eb'
permission: {}
```

`name` 等于文件名 stem 和 agent id。`expert.json` 接受的三种步数输入一律只派生为官方
`steps`；角色声明的 `model`、
`variant`、`temperature`、`top_p`、`hidden`、`options` 原样投影。frontmatter 与
`opencode.json.agent.<id>` 的 `description`、`steps`、`mode`、`permission` 及所有已声明运行参数
必须一致；未声明的可选参数在两处都必须省略。

生成态 `description` 组合角色能力与具体触发条件。触发示例、职责边界、异常处理、输出格式和
质量门控写入正文，不增加其他宿主的 frontmatter 字段。

## 5. 专家团委派合同

团长只负责编排、验收、返工和最终集成；团员只负责被委派的专业任务。

1. 团长调用 `task`，用 `subagent_type` 指定已声明团员 id。
2. 首次委派包含任务、上游输入、预期产物、验收标准和证据要求。
3. 团长保存返回的 `task_id`；验收失败时携带同一 `task_id` 继续返工。
4. 专业结论必须来自对应 task 结果；团长不得自行模拟团员产出。
5. 团员在当前 task 的最终消息中返回完整结果，不继续调度其他团员。
6. 团员不得绕过团长直接交付最终用户答案。
7. `parallel` Phase 的 `agents[]` 只列唯一且必参与的角色。团长可以为每个角色分别发起多个新的
   `task` 调用，每个实例保留独立 `task_id`、Todo、输出和验收状态。
8. 每个角色组内全部实例先通过验收，再由团长完成整个 Phase fan-in；任一必参与角色或实例
   未通过时不得完成 Phase。

默认 `permission.task`：

```json
{
  "primary": {
    "*": "deny",
    "product-strategist": "allow",
    "architect": "allow"
  },
  "subagent": {
    "*": "deny"
  }
}
```

生成内容不得包含 `TeamCreate` 或 `SendMessage`。单专家不得包含 `subagent_type`、`task_id`
或团队委派说明。

## 6. Workflow 结构

顶层 `workflows` 可省略或为空。适合开放式、一次性或无法预先固定执行与验收边界的专家时，不要
为了形式完整而创建 Workflow；Agent 仍可使用普通会话 Todo，但不能声称存在 manifest Phase。
统一技能池 manifest 一旦声明 Workflow，其中每个 Workflow 都必须声明 autonomy、至少一个 Phase，
并为每个 Phase 声明非空 acceptance。现代与无自主度 Workflow 不得混合。

```json
{
  "workflows": [
    {
      "name": "标准交付",
      "trigger": "用户需要完整交付时触发。",
      "autonomy": "bounded",
      "command": {
        "name": "standard-delivery",
        "description": "按照标准交付 workflow 完成发现、审查和最终验收"
      },
      "phases": [
        {
          "name": "并行发现",
          "mode": "parallel",
          "agents": ["product-strategist", "architect"],
          "autonomy": "guided",
          "autonomy_reason": "需要结合项目上下文探索多个候选方案。",
          "input": "用户目标、约束和上下文",
          "expected_output": "产品边界和架构建议",
          "execution": {
            "executors": [
              {"kind": "agent", "ref": "product-strategist"},
              {"kind": "agent", "ref": "architect"}
            ],
            "standards": ["关键范围和架构决定必须先请求用户确认"]
          },
          "agent_overrides": {
            "architect": {
              "autonomy": "bounded",
              "execution": {
                "executors": [{"kind": "agent", "ref": "architect"}],
                "standards": ["只在已批准技术栈和架构边界内选择"]
              }
            }
          },
          "acceptance": ["每个分支有独立可验收输出"]
        },
        {
          "name": "最终验收",
          "mode": "primary",
          "agents": [],
          "autonomy": "fixed",
          "input": "已验收的专业结果",
          "expected_output": "最终集成交付",
          "execution": {
            "executors": [{"kind": "programming-tool", "ref": "python3 .opencode/skills/software-delivery-team-quality-control/scripts/validate.py *"}],
            "standards": ["按固定验收清单逐项检查，不得跳项"]
          },
          "acceptance": ["所有必要分支已通过验收"]
        }
      ]
    }
  ]
}
```

上例的 `programming-tool` 同时要求 manifest 在 `package_resources[]` 中声明
`.opencode/skills/software-delivery-team-quality-control/scripts/validate.py`，并随可信源提供该真实文件；
缺少声明或文件时生成前失败。

- `mode` 只能是 `primary`、`serial` 或 `parallel`。
- 单专家可以有多个 Phase；新设计使用 `primary`，兼容只引用自身的 `serial`，禁止 `parallel`。
- 团队 `primary` 用于团长独有协调或独立集成输出，`agents` 必须为空。
- 团队 `serial/parallel` 的 `agents[]` 必须非空且只能引用 subagent，禁止包含团长。
- `agents[]` 不得重复；它表示唯一、必参与的角色集合，而不是运行时实例集合。
- 有上游依赖时使用 `serial`。
- 只有输入独立、无共享写冲突且输出可分别验收时使用 `parallel`。每个列出角色运行时至少一个
  实例，并可分别动态扩展为 `1..N`；实例数和分片不得写死在 manifest。

### 自主度与继承

- `workflow.autonomy` 使用 `scripted`、`fixed`、`bounded`、`guided`、`adaptive`。
- `phase.autonomy` 可覆盖 workflow；`phase.agent_overrides.<agent>.autonomy` 可覆盖 phase。
- 最终优先级为 `Agent override > phase.autonomy > workflow.autonomy`。
- generator 内部 `phase.max_effective_autonomy` 取全部参与角色最高值，
  `workflow.max_effective_autonomy` 取全部 Phase 最高值；两者只用于风险摘要，不是 manifest
  字段，也不改变其他角色 permission。
- phase 高于 workflow 时必须填写 `autonomy_reason`；Agent 高于 phase 时必须填写 `reason`。
- override 未声明 `execution` 时完整继承 phase；一旦声明则完整替换，不做字段级合并。
- workflow 未声明 `autonomy` 时，phase 不得声明自主度、execution 或 Agent override。

所有层级采用严格 allowlist，未知字段拒绝：workflow 只接受 `name/trigger/autonomy/command/phases`；
phase 只接受 `name/mode/agents/input/expected_output/acceptance/autonomy/autonomy_reason/execution/agent_overrides`；
execution 只接受 `executors/standards`；每个 Agent override 只接受 `autonomy/execution/reason`；
每个 executor 只接受 `kind/ref`。

### Execution

`execution` 只包含 `executors[]` 和 `standards[]`。`acceptance[]` 继续表示结果验收，不能用
过程标准替代。

| kind | ref | 必须满足 |
|---|---|---|
| `skill-script` | `<完整-skill-id>:scripts/<path>` | 对应 `package_resources[]` 真实文件存在，角色未拒绝该 skill |
| `custom-tool` | `runtime_extensions.custom_tools[].path` | backing source 存在，角色权限未明确拒绝 |
| `mcp-tool` | `<mcp-name>/<tool-name>` | MCP 已声明且属于参与角色 |
| `programming-tool` | 精确 Bash pattern | 不含 shell 控制符，至少一个 token 精确引用 `package_resources[]`，权限未拒绝，standards 限定输入输出和用途 |
| `agent` | 已声明 Agent id | `scripted` 禁止；其他档位需要明确标准 |

`scripted`、`fixed`、`bounded` 必须有非空 executors 和 standards；`guided` 必须有关键确认点
standards，executors 可选；`adaptive` 可不声明 execution。启用自主度的 workflow 必须至少有
一个 phase，且每个 phase 都必须有非空 `acceptance[]`。

### Workflow command

`workflows[].command` 只声明 `name` 与 `description`。generator 自动路由到单专家或团长并生成
`.opencode/commands/<name>.md`。源 description 只写业务说明，不得以保留前缀 `【自主度：` 或
`【最高生效自主度：` 开头；生成态 description 自动以 workflow 最高生效自主度开头。command
正文同时显示声明默认自主度和最高生效自主度，每个 Phase 标题使用 Phase 最高生效自主度；每个
参与角色只出现一次并显示其生效自主度、自主度来源和 execution 来源，运行时实例不重复写入；
override 的原因、执行器和标准保留在该 Agent 项下。它不得包含手写 template。README、Agent、
Skill 投影保持原样；额外的非 workflow command 继续使用 `runtime_extensions.commands[]`，两种
来源不得重名，普通 command 不增加自主度前缀。

## 7. 运行时与资源入口

- `runtime_extensions`、MCP、env 和 CLI 安装投影见 `runtime-extensions-spec.md`。
- 本地与 Git Reference 分别使用 `path` 与 `repository`，两者恰好出现一个；角色通过
  `references[]` 声明使用关系。角色规则使用 `role_instructions` 声明本地 Markdown，再通过角色
  `instructions[]` 分配，不进入 workspace 全局 `runtime_extensions.instructions`。
- 头像规则见 `avatar-spec.md`。
- `package_resources[]`、包 allowlist、业务产物和分发合同见 `portable-package-spec.md`。
- agent 与 skill 编写方法见 `opencode-authoring-best-practices.md`。

## 8. 修改已有包

1. 读取 `expert.json` 和它声明的真实资源文件。
2. 若包使用 `common_skills` 和 purpose 对象，结构性修改前先迁移到统一技能池：保留现有完整
   技能名和全部文件字节，旧通用技能分配给全部角色，旧角色技能只保留原角色分配，并将条目标记
   为 `origin: legacy-migrated`、`edit_policy: managed`。
3. 保持 slug、agent id、完整 skill name 不变，除非用户明确要求重命名。
4. `origin: uploaded`、`edit_policy: preserved` 的技能默认不得改写；明确授权后保留 origin，
   仅将 edit_policy 转为 `managed`。
5. 在 manifest 用户可见字段中先完成 MobileWork 口径归一化。
6. 修改 manifest 和必要输入资源，不直接修补派生 Markdown 或 runtime config。
7. 经确认后使用 `create_expert.py --force` 重建。
8. 运行 validator、便携性扫描、打包和解压后二次校验。

## 9. 可复制单专家模板

下方第一个标记块是生成新 manifest 时的可复制模板。它使用显式空技能池；需要技能时先准备
完整技能目录，再将每个文件登记到 `package_resources[]` 并用完整名称分配给角色。第二个标记块
只供兼容测试读取旧 schema，不用于新建。

<!-- mobilework-template:expert-json:start -->
````json
{
  "slug": "contract-review-expert",
  "type": "expert",
  "name": "合同审查专家",
  "summary": "Single expert manifest with public display fields and derived permissions.",
  "description": "Reviews contracts, identifies legal and business risks, and returns evidence-backed revision guidance.",
  "language": "zh",
  "avatar_url": "avatars/contract-review-expert.png",
  "tags": [
    "contract-review",
    "risk-analysis",
    "revision-advice"
  ],
  "quick_prompts": [
    "帮我审查这份合同的关键风险并给出修改建议。",
    "请提取合同中的付款、交付、违约和终止条款。",
    "请把这份合同改写成更保护我方权益的版本。"
  ],
  "skills": [],
  "package_resources": [],
  "runtime_extensions": {
    "reference_files": [
      {
        "path": ".opencode/references/contract-review-expert/playbook/overview.md",
        "content": "# Contract review playbook\n\nUse this reference for clause-level review guidance.\n"
      }
    ],
    "references": {
      "playbook": {
        "path": ".opencode/references/contract-review-expert/playbook",
        "description": "Use for clause-level contract review guidance"
      }
    },
    "instruction_files": [
      {
        "path": ".opencode/instructions/contract-review-expert/evidence.md",
        "content": "# Evidence rule\n\nCite the relevant clause for every finding.\n"
      },
      {
        "path": ".opencode/instructions/contract-review-expert/roles/source-policy.md",
        "content": "# Source policy\n\nMark the source of every quoted clause.\n"
      }
    ],
    "instructions": [
      ".opencode/instructions/contract-review-expert/*.md"
    ],
    "role_instructions": {
      "source-policy": {
        "path": ".opencode/instructions/contract-review-expert/roles/source-policy.md",
        "description": "审查角色引用条款时标明来源"
      }
    }
  },
  "agent": {
    "id": "contract-reviewer",
    "name": "合同审查专家",
    "display_name": "合同审查专家",
    "description": "Reviews contract terms, identifies risk, and proposes precise amendments.",
    "mode": "primary",
    "steps": 80,
    "color": "#2563eb",
    "avatar_url": "avatars/contract-reviewer.png",
    "skills": [],
    "references": [
      "playbook"
    ],
    "instructions": [
      "source-policy"
    ],
    "responsibilities": [
      "Identify obligations, liabilities, rights, remedies, and ambiguous terms.",
      "Separate legal risk, commercial risk, and missing information.",
      "Produce a concise review memo with clause-level evidence."
    ],
    "workflow": [
      "Clarify the contract type, parties, jurisdiction assumptions, and review goal.",
      "Read the source contract and extract high-risk clauses.",
      "Classify each issue by severity and explain the evidence.",
      "Draft recommended revisions or negotiation points.",
      "Verify the final memo against the requested review goal."
    ],
    "quality_gates": [
      "Every finding cites the relevant clause or source text location.",
      "Recommendations are actionable and preserve uncertainty where legal facts are missing.",
      "Final output distinguishes legal information from legal advice when appropriate."
    ],
    "permission": {
      "read": "allow",
      "edit": "allow",
      "bash": {
        "*": "ask",
        "git status*": "allow",
        "git diff*": "allow"
      },
      "webfetch": "allow"
    },
    "permission_reason": "允许只读 Git 状态检查，以便为合同修改保留可核验的变更证据。",
    "profession": "合同风险审查专家",
    "route_triggers": [
      "用户要求审查合同风险、提取关键条款或生成修改建议。"
    ],
    "handoff_contract": [
      "列出任务理解、关键风险、条款证据、修改建议、验证状态和未决风险。"
    ]
  },
  "profession": "合同审查专家",
  "category_id": "11-SecurityCompliance",
  "display_description": "面向合同审查、风险识别和修改建议的单专家。",
  "default_prompt": "帮我审查这份合同的关键风险并给出修改建议。"
}
````
<!-- mobilework-template:expert-json:end -->

### 旧 schema 兼容测试模板

<!-- mobilework-template:legacy-expert-json:start -->
````json
{
  "slug": "contract-review-expert",
  "type": "expert",
  "name": "合同审查专家",
  "summary": "Single expert manifest with public display fields, local skills, and permissions.",
  "description": "Reviews contracts, identifies legal and business risks, and returns evidence-backed revision guidance.",
  "language": "zh",
  "avatar_url": "avatars/contract-review-expert.png",
  "tags": [
    "contract-review",
    "risk-analysis",
    "revision-advice"
  ],
  "quick_prompts": [
    "帮我审查这份合同的关键风险并给出修改建议。",
    "请提取合同中的付款、交付、违约和终止条款。",
    "请把这份合同改写成更保护我方权益的版本。"
  ],
  "common_skills": [
    {"purpose": "delivery-quality"}
  ],
  "package_resources": [],
  "runtime_extensions": {
    "reference_files": [
      {
        "path": ".opencode/references/contract-review-expert/playbook/overview.md",
        "content": "# Contract review playbook\n\nUse this reference for clause-level review guidance.\n"
      }
    ],
    "references": {
      "playbook": {
        "path": ".opencode/references/contract-review-expert/playbook",
        "description": "Use for clause-level contract review guidance"
      }
    },
    "instruction_files": [
      {
        "path": ".opencode/instructions/contract-review-expert/evidence.md",
        "content": "# Evidence rule\n\nCite the relevant clause for every finding.\n"
      },
      {
        "path": ".opencode/instructions/contract-review-expert/roles/source-policy.md",
        "content": "# Source policy\n\nMark the source of every quoted clause.\n"
      }
    ],
    "instructions": [
      ".opencode/instructions/contract-review-expert/*.md"
    ],
    "role_instructions": {
      "source-policy": {
        "path": ".opencode/instructions/contract-review-expert/roles/source-policy.md",
        "description": "审查角色引用条款时标明来源"
      }
    }
  },
  "agent": {
    "id": "contract-reviewer",
    "name": "合同审查专家",
    "display_name": "合同审查专家",
    "description": "Reviews contract terms, identifies risk, and proposes precise amendments.",
    "mode": "primary",
    "steps": 80,
    "color": "#2563eb",
    "avatar_url": "avatars/contract-reviewer.png",
    "skills": [
      {"purpose": "role-guidelines"},
      {"purpose": "clause-checklist"}
    ],
    "references": [
      "playbook"
    ],
    "instructions": [
      "source-policy"
    ],
    "responsibilities": [
      "Identify obligations, liabilities, rights, remedies, and ambiguous terms.",
      "Separate legal risk, commercial risk, and missing information.",
      "Produce a concise review memo with clause-level evidence."
    ],
    "workflow": [
      "Clarify the contract type, parties, jurisdiction assumptions, and review goal.",
      "Read the source contract and extract high-risk clauses.",
      "Classify each issue by severity and explain the evidence.",
      "Draft recommended revisions or negotiation points.",
      "Verify the final memo against the requested review goal."
    ],
    "quality_gates": [
      "Every finding cites the relevant clause or source text location.",
      "Recommendations are actionable and preserve uncertainty where legal facts are missing.",
      "Final output distinguishes legal information from legal advice when appropriate."
    ],
    "permission": {
      "read": "allow",
      "edit": "allow",
      "bash": {
        "*": "ask",
        "git status*": "allow",
        "git diff*": "allow"
      },
      "webfetch": "allow",
      "skill": {
        "*": "deny",
        "contract-review-expert-common-delivery-quality": "allow",
        "contract-review-expert-contract-reviewer-role-guidelines": "allow",
        "contract-review-expert-contract-reviewer-clause-checklist": "allow"
      }
    },
    "permission_reason": "允许只读 Git 状态检查，以便为合同修改保留可核验的变更证据。",
    "profession": "合同风险审查专家",
    "route_triggers": [
      "用户要求审查合同风险、提取关键条款或生成修改建议。"
    ],
    "handoff_contract": [
      "列出任务理解、关键风险、条款证据、修改建议、验证状态和未决风险。"
    ]
  },
  "profession": "合同审查专家",
  "category_id": "11-SecurityCompliance",
  "display_description": "面向合同审查、风险识别和修改建议的单专家。",
  "default_prompt": "帮我审查这份合同的关键风险并给出修改建议。"
}
````
<!-- mobilework-template:legacy-expert-json:end -->
