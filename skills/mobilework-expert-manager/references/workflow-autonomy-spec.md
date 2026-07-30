# Workflow 自主度与执行合同

## 1. 五档自主度

| 枚举 | 直观名称 | Agent 可以做什么 | 禁止行为 |
|---|---|---|---|
| `scripted` | 极低：全程照脚本执行，不能自行换方法 | 组装输入、调用声明的确定性执行器、检查输出 | 更换方法、临时写替代代码、口算、目测或纯文字替代执行 |
| `fixed` | 低：按固定步骤执行，只能处理预设分支 | 按 SOP 及预设条件分支或重试；仅在用户明确批准后执行 break-glass 例外 | 发明新方法、更换执行器、跳过步骤，或未记录 break-glass 偏离原因 |
| `bounded` | 中：可在明确边界内选择方法 | 在声明的执行器、方法和标准内选择 | 越出允许清单、自创标准 |
| `guided` | 高：可根据目标灵活安排，但关键决定需确认 | 探索方法、分析异常 | 未确认就执行例外或高影响决定 |
| `adaptive` | 极高：可自主规划、调整和返工，仍受安全与验收标准约束 | 在职责、权限和验收内规划和调整 | 绕过权威脚本、安全规则或质量门 |

已有可靠脚本或 custom tool 推荐 `scripted`；固定 SOP 推荐 `fixed`；多个批准方法推荐
`bounded`；探索但关键决定要确认时推荐 `guided`；开放研究或创意才推荐 `adaptive`。

`fixed` 的未知能力仍为 `ask`，这不是常规自由裁量权。只有用户针对当前例外明确批准后，Agent
才能使用该 ask 路径作为 break-glass；执行时必须记录偏离的 SOP/分支、批准依据和偏离原因。
未批准、无法记录或例外会改变验收标准时停止并报告阻塞。

## 2. 分层继承

优先级固定为：

```text
Agent override > phase.autonomy > workflow.autonomy
```

简单流程只设置 workflow。phase 只有边界不同时覆盖；Agent override 只有同一 phase 中角色边界
不同时使用。降低自主度可直接覆盖；phase 高于 workflow 时填写 `autonomy_reason`，Agent 高于
phase 时填写 `reason`。override 的 execution 一旦声明就完整替换 phase execution，不做字段合并。

各层使用严格 allowlist，未知字段直接拒绝：workflow 为
`name/trigger/autonomy/command/phases`；phase 为
`name/mode/agents/input/expected_output/acceptance/autonomy/autonomy_reason/execution/agent_overrides`；
Agent override 为 `autonomy/execution/reason`。

Phase 的 `effective_autonomy` 表示 Phase 默认值；`max_effective_autonomy` 是 generator 内部派生
的只读风险摘要，取全部必参与角色在 override 后的最高值。Workflow 的同名内部值取全部 Phase
最高值。两者不进入 `expert.json` 或 `opencode.json`，也不得反向提高其他角色权限。

## 3. Workflow、Phase 与动态多实例

顶层 Workflow 是可选的稳定任务合同，不是每个专家都必须具备。统一技能池 manifest 可以省略
`workflows` 或声明空数组；此时 Agent 使用普通 Todo 规划，不能把临时步骤称为声明式 Phase。
一旦声明现代 Workflow，则所有 Workflow 都必须包含 `autonomy`、至少一个 Phase，并让每个 Phase
包含非空 `acceptance[]`；不得混合现代与无自主度 Workflow。

Phase 是执行与验收边界。派发消息、结果返回、返工请求和团员到团长的普通 handoff 都不是独立
Phase。单专家可以有多个 Phase，但不能使用 `parallel` 或克隆主 Agent；新设计使用
`mode: primary`，兼容单角色 `serial`。

专家团：

- `primary` 只表示团长独立编排或产生可单独验收的集成输出，`agents[]` 必须为空；
- `serial/parallel` 必须列出至少一个团员，禁止包含团长；
- `agents[]` 是唯一、必参与的角色集合，不是实例清单，重复 ID 失败；
- `serial` 按角色顺序执行；有角色依赖时拆成前后 Phase；
- `parallel` 允许多个角色，并允许团长为每个角色分别创建 `1..N` 个运行时实例。每个角色至少
  一个实例，各角色实例数可以不同，具体数量与分片范围只能根据本次输入和运行容量决定；
- 每个实例使用新的 `task` 调用、独立 `task_id`、Todo、输出和验收状态。同角色实例继承相同
  override、权限和执行边界，实例 prompt 只能收窄范围，不能提权；
- 先验收每个角色组内全部实例，再完成整个 Phase fan-in。任何必参与角色或实例未通过时，Phase
  不得完成；若无法安全分片，降级为单实例或改走串行 Phase。

## 4. Execution

`execution` 只包含 `executors[]` 和 `standards[]`；`acceptance[]` 表示结果验收。
execution 对象不接受其他字段；每个 executor 对象只接受 `kind` 与 `ref`。

| kind | ref |
|---|---|
| `skill-script` | `<完整-skill-id>:scripts/<path>`，且 `package_resources[]` 中有真实文件 |
| `custom-tool` | `runtime_extensions.custom_tools[].path` |
| `mcp-tool` | `<mcp-name>/<tool-name>`，且参与角色拥有该 MCP |
| `programming-tool` | 精确 Bash pattern，且至少一个 token 必须是 `package_resources[]` 中声明的包资源；standards 限定输入、输出和用途 |
| `agent` | 已声明 Agent id；`scripted` 禁止 |

`scripted`、`fixed`、`bounded` 必须有 executors 和 standards；`guided` 必须有关键确认点 standards，
executors 可选；`adaptive` 可不声明 execution。角色权限明确拒绝执行器时必须失败。

## 5. 最少约束发现

高风险、合规、审计、批量、重复运行、要求结果一致或已有 SOP/脚本/工具时主动推荐低自主度。
先读取用户消息、附件、现有 manifest、声明资源和可信资料，每个执行单元最多确认：自主度、
执行器、执行标准、验收标准。只询问会改变结果的阻塞项，不重复已有事实或泛问其他要求。

对已声明 Workflow，缺少真实执行器、固定标准、批准范围、关键确认点或验收标准时保持设计阶段。
用户说“你来决定”可以授权形成候选，但不能凭空发明外部业务标准或跳过设计确认。

## 6. Workflow command

每个用户可直接触发、会重复使用的稳定 workflow 默认推荐 `workflows[].command`，只声明 `name`
和不带自主度前缀的业务 `description`；源 description 不得以保留前缀 `【自主度：` 或
`【最高生效自主度：` 开头。
generator 生成 `.opencode/commands/<name>.md` 并路由到单专家或团长：frontmatter description
自动以 workflow 最高生效自主度开头，例如 `【最高生效自主度：高】 原始说明`；正文分别显示
Workflow 声明默认自主度与最高生效自主度，每个 Phase 标题使用其最高生效自主度。Phase 内每个
参与角色只出现一次，显示该角色生效自主度、自主度来源和 execution 来源；运行时实例不重复写入
静态 command。
有 Agent override 时在同一 Agent 项下保留原因、执行器和标准。command 不维护第二份手写
workflow，README、Agent 和 Skill 继续使用原有详细投影，不增加这些 command 专用前缀。

额外非 workflow command 继续使用 `runtime_extensions.commands[]`；两种来源不得重名，不向
`opencode.json` 写入自主度或根级 `command`。

## 7. 兼容与错误处理

- unified manifest 的顶层 Workflow 可整体省略；只要声明任一 Workflow，它就必须有 autonomy。
- legacy manifest 可以保留全部无自主度 Workflow；有/无自主度 Workflow 混合时失败。
- 启用自主度的 workflow 至少有一个 phase，每个 phase 都有非空 `acceptance[]`。
- 旧 schema manifest 没有自主度字段时，generator 和 validator 保持旧行为和旧派生结构。
- `runtime_extensions.commands[]` 是普通 command，description 和正文不增加自主度前缀。
- `scripted` 缺输入、执行器、标准或执行失败时停止；不得走替代路线。
- `fixed` 只走声明分支；break-glass 必须先获用户批准并记录偏离原因；`bounded` 穷尽批准方法后升级；`guided` 到确认点先询问；`adaptive`
  可调整方法，但验证失败不得宣称完成。
- 所有等级都禁止静默降低验收标准。
- Agent 静态 permission 由全部 effective autonomy 合并，完整矩阵、冲突降级和提权规则见
  `references/permission-policy-spec.md`。
