# Workflow 自主度与执行合同

## 1. 五档自主度

| 枚举 | 直观名称 | Agent 可以做什么 | 禁止行为 |
|---|---|---|---|
| `scripted` | 极低：全程照脚本执行，不能自行换方法 | 组装输入、调用声明的确定性执行器、检查输出 | 更换方法、临时写替代代码、口算、目测或纯文字替代执行 |
| `fixed` | 低：按固定步骤执行，只能处理预设分支 | 按 SOP 及预设条件分支或重试 | 发明新方法、更换执行器、跳过步骤 |
| `bounded` | 中：可在明确边界内选择方法 | 在声明的执行器、方法和标准内选择 | 越出允许清单、自创标准 |
| `guided` | 高：可根据目标灵活安排，但关键决定需确认 | 探索方法、分析异常 | 未确认就执行例外或高影响决定 |
| `adaptive` | 极高：可自主规划、调整和返工，仍受安全与验收标准约束 | 在职责、权限和验收内规划和调整 | 绕过权威脚本、安全规则或质量门 |

已有可靠脚本或 custom tool 推荐 `scripted`；固定 SOP 推荐 `fixed`；多个批准方法推荐
`bounded`；探索但关键决定要确认时推荐 `guided`；开放研究或创意才推荐 `adaptive`。

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

## 3. Execution

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

## 4. 最少约束发现

高风险、合规、审计、批量、重复运行、要求结果一致或已有 SOP/脚本/工具时主动推荐低自主度。
先读取用户消息、附件、现有 manifest、声明资源和可信资料，每个执行单元最多确认：自主度、
执行器、执行标准、验收标准。只询问会改变结果的阻塞项，不重复已有事实或泛问其他要求。

缺少真实执行器、固定标准、批准范围、关键确认点或验收标准时保持设计阶段。用户说“你来决定”
可以授权形成候选，但不能凭空发明外部业务标准或跳过设计确认。

## 5. Workflow command

每个用户可直接触发、会重复使用的稳定 workflow 默认推荐 `workflows[].command`，只声明 `name`
和不带自主度前缀的业务 `description`；源 description 不得以保留前缀 `【自主度：` 开头。
generator 生成 `.opencode/commands/<name>.md` 并路由到单专家或团长：frontmatter description
自动以 workflow 默认自主度开头，例如 `【自主度：中】 原始说明`；每个 Phase 标题以其生效
自主度开头；Phase 内每个参与 Agent 只出现一次，显示生效自主度、自主度来源和 execution 来源。
有 Agent override 时在同一 Agent 项下保留原因、执行器和标准。command 不维护第二份手写
workflow，README、Agent 和 Skill 继续使用原有详细投影，不增加这些 command 专用前缀。

额外非 workflow command 继续使用 `runtime_extensions.commands[]`；两种来源不得重名，不向
`opencode.json` 写入自主度或根级 `command`。

## 6. 兼容与错误处理

- workflow 未声明 `autonomy` 时，phase 不得单独声明新字段。
- 启用自主度的 workflow 至少有一个 phase，每个 phase 都有非空 `acceptance[]`。
- 旧 manifest 没有自主度字段时，generator 和 validator 保持旧行为和旧派生结构。
- `runtime_extensions.commands[]` 是普通 command，description 和正文不增加自主度前缀。
- `scripted` 缺输入、执行器、标准或执行失败时停止；不得走替代路线。
- `fixed` 只走声明分支；`bounded` 穷尽批准方法后升级；`guided` 到确认点先询问；`adaptive`
  可调整方法，但验证失败不得宣称完成。
- 所有等级都禁止静默降低验收标准。
- Agent 静态 permission 由全部 effective autonomy 合并，完整矩阵、冲突降级和提权规则见
  `references/permission-policy-spec.md`。
