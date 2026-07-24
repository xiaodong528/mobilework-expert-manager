# 五档自主度权限策略

设计、生成或校验 Agent permission 时读取本文件。自主度与权限是双轴：自主度只定义静态权限
上限，实际开放能力还必须来自 workflow execution、角色拥有的 Skill/MCP、团队拓扑或显式声明。
生成器不得从自由文本职责推断权限。

## 默认矩阵

| 自主度 | `*` | edit | bash | webfetch | external_directory | doom_loop |
|---|---|---|---|---|---|---|
| scripted | deny | deny | deny | deny | deny | deny |
| fixed | ask | ask | ask | ask | ask | ask |
| bounded | ask | allow | ask | allow | ask | ask |
| guided | ask | allow | ask | allow | ask | ask |
| adaptive | ask | allow | ask | allow | ask | allow |

通配规则先写，具体规则后写。所有档位都允许 read/glob/grep/list/LSP 检查，但 read 必须拒绝
`.env` 和 `.env.*`，并允许 `.env.example`。任何档位都禁止无条件
`bash: {"*": "allow"}` 和 `external_directory: {"*": "allow"}`。

## 角色合并

对每个角色收集 `workflow_autonomy.normalize_workflows()` 产生的全部 effective autonomy：

1. primary Phase 的空 `agents[]` 归团长；
2. `*` 使用参与档位中的最高自主度；
3. edit、bash、webfetch、external_directory、doom_loop 分别计算；
4. 同一敏感项动作不一致时降为 ask；
5. 精确 executor、命令、MCP Tool 和角色拥有 custom tool 白名单取并集；
6. 再施加 Skill、MCP、task 和包资源所有权；
7. 最后合并通过审计的显式 permission。

纯 adaptive 的 `doom_loop=allow`；adaptive 与任一较低档位混合时该项为 ask。没有参与自主度
workflow 的角色使用 bounded 回退并产生 `UNUSED_ROLE_BOUNDED_FALLBACK` warning。

## Executor 与所有权

- programming-tool 只允许声明的精确 Bash pattern；Bash 通配仍为 deny 或 ask；
- custom tool 只有在角色 `custom_tools[]` 精确引用声明 path，或参与 workflow 的 executor 精确引用时
  建立所有权；建立所有权后五档一律 `allow`；
- 不同 path 映射为相同 OpenCode tool name 时拒绝；其他角色、其他包、未声明和未来未知 tool
  保持 `deny/ask`，不得由 `*` 放行；
- scripted/fixed 的 MCP 通配不开放，只允许 execution 引用的精确 `<mcp>_<tool>`；
- bounded/guided/adaptive 可开放角色明确拥有的 MCP，未拥有的 MCP 一律 deny；
- Skill 先 deny `*`，再只 allow 角色计算后的 Skill；
- 团长 task 只 allow 声明的团员，团员 task 只含 `*`: deny；
- scripted 不允许 agent executor，但同一角色参与其他 workflow 所需的静态 task 拓扑仍保留。

远程发布、删除、消息、支付等真实外部写入：scripted/fixed 为 deny，其他档位为 ask。静态权限
不能可靠识别工具内部是否外写时，不自动放行为 allow。

## 显式提权

动作顺序为 `deny < ask < allow`。显式规则收紧权限无需理由；任何高于计算结果的动作必须在同一
角色声明非空 `permission_reason`。逐 pattern 与其计算动作比较，不以整个 section 粗略比较。

显式 permission 即使有理由也不能：

- 改写 task 拓扑；
- 授予未声明 Skill 或 MCP；
- 放行 Bash 或 external_directory 通配；
- 绕过 package resource ownership。

README 必须显示每个角色的来源、参与档位、最高生效自主度、敏感项动作和提权理由。

## 旧 manifest

完全没有 workflow autonomy 的 manifest 保留既有 permission 与旧默认行为，并产生
`LEGACY_PERMISSION_BASELINE` warning；校验、安装和维护性修复不强制迁移。新建、资料转化和
结构性修改必须声明自主度并使用新策略。
