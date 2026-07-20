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
  "common_skills": [{"purpose": "delivery-quality"}],
  "agent": {
    "id": "contract-reviewer",
    "name": "合同审查专家",
    "description": "审查合同条款并提出修改建议。",
    "skills": [{"purpose": "clause-checklist"}]
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
  "common_skills": [{"purpose": "delivery-quality"}],
  "primary_agent": {
    "id": "delivery-director",
    "name": "交付总监",
    "description": "编排、验收并集成跨角色交付。",
    "skills": [{"purpose": "delivery-review"}]
  },
  "subagents": [
    {
      "id": "engineer",
      "name": "工程师",
      "description": "实现并验证代码变更。",
      "skills": [{"purpose": "code-change"}]
    }
  ]
}
```

- `type: team` 必须有一个 `primary_agent` 和至少一个 `subagents[]`，禁止 `agent`。
- agent id 必须唯一；团长使用 `mode: primary`，团员使用 `mode: subagent`。

## 2. 命名与展示字段

`slug`、agent id、skill purpose 和 MCP name 必须匹配：

```text
^[a-z0-9]+(-[a-z0-9]+)*$
```

| 字段 | 规则 |
|---|---|
| `slug` | 稳定包 id，必须与目录名一致。 |
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
| `common_skills` | 非空 purpose 对象列表。 |
| `mcp_servers` | 可选 MCP 声明；支持 local、remote、header auth、OAuth 与 timeout，详见 `runtime-extensions-spec.md`。 |
| `runtime_extensions` | commands、tools、plugins、OpenCode 1.18.3 local/Git references、instructions、LSP。 |
| `package_resources` | supplemental skill 中除生成 `SKILL.md` 外的声明资源。 |

来源资料中的宿主产品、平台发布和智能体容器叙事，在展示草案前改为 MobileWork 口径。
保留运行必需标识：slug、agent id、skill/MCP 名、文件名、命令、API、协议、第三方业务系统名，
以及用户明确要求保留的品牌资产。

## 3. Skill 声明

`common_skills` 和每个角色的 `skills` 都必须是非空 purpose 对象列表：

```json
{
  "common_skills": [
    {"purpose": "delivery-quality"}
  ],
  "agent": {
    "skills": [
      {"purpose": "role-guidelines"},
      {"purpose": "clause-checklist"}
    ]
  }
}
```

生成名称：

- 通用：`<slug>-common-<purpose>`；
- 角色专属：`<slug>-<agent-id>-<purpose>`。

禁止旧字符串数组、完整 skill name、空或重复 purpose、缺失或空列表。
每个 agent 加载全部通用 skill 和自己的全部专用 skill；第一项不具有特殊语义。
`permission.skill` 除 `*` 外只能引用该 agent 实际拥有的计算后 skill。

## 4. Agent 字段与派生语义

每个 `agent`、`primary_agent` 和 `subagents[]` 可以声明：

- `id`、`name`、`display_name`、`profession`、`description`、`avatar_url`、`color`；
- `responsibilities`、`route_triggers`、`workflow`、`quality_gates`、`handoff_contract`；
- `skills`、`mcp`、`permission`；
- OpenCode 正式步数字段 `steps`，以及仅供 `expert.json` 读取旧包的 MobileWork 历史输入
  `max_turns`、`maxTurns`；
- 可选运行参数 `model`、`variant`、`temperature`、`top_p`、`hidden`、`options`。

`title` 是只读旧 MobileWork manifest 时允许的 `name` 回退，不是新的 Agent 字段。新 manifest
统一声明 `name`；当旧角色只有 `title` 时 generator 将其用于显示名，但不会向 Agent Markdown
或 `opencode.json.agent.<id>` 派生 `title`。若同时声明 `name` 与 `title`，`name` 优先。
角色 `mcp[]` 只能引用已声明的 MCP server，且条目不得重复。

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
            "executors": [{"kind": "programming-tool", "ref": "validation-command"}],
            "standards": ["按固定验收清单逐项检查，不得跳项"]
          },
          "acceptance": ["所有必要分支已通过验收"]
        }
      ]
    }
  ]
}
```

- `mode` 只能是 `primary`、`serial` 或 `parallel`。
- `agents[]` 只能引用已声明的 primary 或 subagent id。
- `primary` 用于团长独有协调阶段，`agents` 可为空。
- 有上游依赖时使用 `serial`。
- 只有输入独立、无共享写冲突且输出可分别验收时使用 `parallel`。

### 自主度与继承

- `workflow.autonomy` 使用 `scripted`、`fixed`、`bounded`、`guided`、`adaptive`。
- `phase.autonomy` 可覆盖 workflow；`phase.agent_overrides.<agent>.autonomy` 可覆盖 phase。
- 最终优先级为 `Agent override > phase.autonomy > workflow.autonomy`。
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
| `programming-tool` | 明确工具或受控命令入口 | ref 非空、权限未拒绝，standards 限定输入输出和用途 |
| `agent` | 已声明 Agent id | `scripted` 禁止；其他档位需要明确标准 |

`scripted`、`fixed`、`bounded` 必须有非空 executors 和 standards；`guided` 必须有关键确认点
standards，executors 可选；`adaptive` 可不声明 execution。启用自主度的 workflow 必须至少有
一个 phase，且每个 phase 都必须有非空 `acceptance[]`。

### Workflow command

`workflows[].command` 只声明 `name` 与 `description`。generator 自动路由到单专家或团长并生成
`.opencode/commands/<name>.md`。源 description 只写业务说明，不得以保留前缀 `【自主度：`
开头；生成态 description 自动以 workflow 默认自主度开头。command 中每个 Phase 标题以 Phase
生效自主度开头，每个参与 Agent 只出现一次并显示其生效自主度、自主度来源和 execution 来源；
override 的原因、执行器和标准保留在该 Agent 项下。它不得包含手写 template。README、Agent、
Skill 投影保持原样；额外的非 workflow command 继续使用 `runtime_extensions.commands[]`，两种
来源不得重名，普通 command 不增加自主度前缀。

## 7. 运行时与资源入口

- `runtime_extensions`、MCP、env 和 CLI 安装投影见 `runtime-extensions-spec.md`。
- 头像规则见 `avatar-spec.md`。
- `package_resources[]`、包 allowlist、业务产物和分发合同见 `portable-package-spec.md`。
- agent 与 supplemental skill 编写方法见 `opencode-authoring-best-practices.md`。

## 8. 修改已有包

1. 读取 `expert.json` 和它声明的真实资源文件。
2. 保持 slug、agent id、skill purpose 不变，除非用户明确要求重命名。
3. 在 manifest 用户可见字段中先完成 MobileWork 口径归一化。
4. 修改 manifest 和必要输入资源，不直接修补派生 Markdown 或 runtime config。
5. 经确认后使用 `create_expert.py --force` 重建。
6. 运行 validator、便携性扫描、打包和解压后二次校验。

## 9. 可复制单专家模板

下方标记块是生成新 manifest 时的可复制模板。标记之间的 JSON 内容与迁移前的
单专家模板保持逐字节一致。

<!-- mobilework-template:expert-json:start -->
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
      }
    ],
    "instructions": [
      ".opencode/instructions/contract-review-expert/*.md"
    ]
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
        "*": "allow",
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
