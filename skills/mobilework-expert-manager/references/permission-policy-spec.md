# 角色自主度权限策略

设计、生成或校验 Agent permission 时读取本文件。角色自主度是静态权限的唯一基线；Workflow、
Phase、Agent override 的自主度只描述流程决定权、确认点、风险和验收语义，不参与权限计算。

## 五档角色自主度

| 内部值 | 用户标签 | `*` | edit | bash | webfetch | external_directory | doom_loop | 外部 Skill |
|---|---|---|---|---|---|---|---|---|
| `scripted` | 低 | deny | deny | deny | deny | deny | deny | deny |
| `fixed` | 较低 | ask | ask | ask | ask | ask | ask | deny |
| `bounded` | 中 | ask | allow | ask | allow | ask | ask | deny |
| `guided` | 较高 | ask | allow | ask | allow | ask | ask | ask |
| `adaptive` | 高 | ask | allow | ask | allow | ask | allow | allow |

通配规则先写，具体规则后写。所有档位都允许 read/glob/grep/list/LSP；read 必须拒绝 `.env` 和
`.env.*`，并允许 `.env.example`。任何档位都禁止无条件 `bash: {"*": "allow"}` 和
`external_directory: {"*": "allow"}`。

`fixed` 的 ask 只表示当前动作必须确认，不是常规自由裁量权。角色自主度只写在 `expert.json`，
不写入 OpenCode Agent frontmatter；生成器只把它投影为 `permission`。

## 所有权与动作

- 角色 `skills[]`、`mcp[]`、`custom_tools[]` 和团队拓扑决定对象所有权；角色自主度决定相应动作。
- 包内已分配 Skill 五档都精确 `allow`。外部或宿主可发现 Skill 由 `permission.skill.*` 执行上表：
  低、较低、中为 deny，较高为 ask，高为 allow。
- 包内 custom tool 只有角色 `custom_tools[]` 精确引用声明 path 时归该角色所有；拥有后五档默认
  allow。不同 path 映射到相同 OpenCode tool name 时拒绝。未拥有的 custom tool 不生成 allow。
- scripted/fixed 不开放 MCP；bounded/guided/adaptive 只开放角色 `mcp[]` 明确拥有的 server。
  未拥有 MCP 一律精确 deny；高自主度不会自动获得未声明 MCP。
- 团长 `task` 只 allow 声明的团员；团员 `task` 只含 `*`: deny。`mode: all` 只让主 Agent 可被
  通用 Agent 调用，不改变团队内部 task 所有权。
- Todo 由系统托管并固定投影 `todowrite: allow`。角色不得声明 `permission.todowrite`、
  `permission.todoread`、`tools.todowrite` 或 `tools.todoread`；custom tool 也不得映射为这两个名字。

## Workflow execution 是权限子集

`execution` 只选择角色已经拥有、且角色当前动作不是 deny 的能力：

- allow 可直接执行；
- ask 保留运行时确认；
- deny 使 manifest 校验失败。

execution 不生成 Bash pattern、MCP Tool 或 custom tool allowlist，也不改变 Agent permission。
固定角色自主度后，遍历 Workflow/Phase autonomy、override 和 execution，生成 permission 必须字节级
不变。`skill-script` 必须来自分配给该角色的 Skill；custom tool 和 MCP 必须归该角色所有；
programming-tool 还必须引用声明的包资源。Phase `mode: primary` 仍表示流程由主角色独立执行，
与 Agent frontmatter 的 `mode: all` 是两个不同概念。

## 手写 permission 只能收紧

动作顺序为 `deny < ask < allow`。手写 `permission` 和旧 `tools` 兼容输入只能逐项保持或收紧
角色自主度基线；任何提高都报 `PERMISSION_AUTONOMY_ESCALATION_FORBIDDEN`。`permission_reason`
可以保留说明，但不构成提权授权，不能绕过 Bash、外部目录、task、Skill、MCP、custom tool、
包资源或系统 Todo 边界。

README 必须显示每个角色的标签、内部值、来源、敏感项动作和外部 Skill 动作。Workflow/Phase 的
最高生效自主度只作为流程风险摘要，不能出现在权限推导来源中。

## 旧包兼容

旧包角色缺少 `autonomy` 时，只读诊断、校验和安装在临时投影中按 `bounded`（中）处理，产生
`LEGACY_ROLE_AUTONOMY_DEFAULTED` warning，并忽略可能更宽松的旧 permission/tools；源包保持不变。
新建和任何结构性修改都必须先为全部角色显式补齐 autonomy。旧主 Agent 的 `mode: primary` 可继续
只读校验和安装；结构性修改时迁移为 `mode: all`。
