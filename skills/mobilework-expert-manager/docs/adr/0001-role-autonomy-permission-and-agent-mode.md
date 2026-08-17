# ADR 0001：以角色自主度作为权限唯一基线，并分离 Agent 与 Phase 模式

- 状态：Accepted
- 日期：2026-08-11
- 决策范围：MobileWork 专家包设计、能力资源选型、生成、校验、Skill 导入和安装投影

## 背景

旧设计会汇总 Workflow、Phase 和角色流程覆盖的自主度来生成 Agent 权限，并可由 execution 生成精确
放行项。这使流程编排调整意外改变角色静态能力，也让资源所有权、执行意图和权限上限难以分别审计。
同时，主 Agent 使用 `primary` 会把专家限制为主入口，不利于未来由通用 Agent 调用专家或专家团。

## 决策

1. 每个角色在 `expert.json` 中持有独立且必填的 `autonomy`。五档用户标签依次为低、较低、中、较高、
   高，对应 `scripted`、`fixed`、`bounded`、`guided`、`adaptive`。
2. Agent 静态权限只由角色自主度、角色资源所有权和系统托管规则生成。Workflow、Phase 及 execution
   不参与权限计算。
3. Workflow/Phase 自主度继续描述流程决策范围、确认、风险和验收。execution 只引用角色当前拥有且
   动作允许的能力：`allow` 直接执行，`ask` 保留确认，`deny` 校验失败。
4. 手写 `permission` 只能逐项收紧，`permission_reason` 不构成提权授权。
5. 包内已分配 Skill 始终精确 `allow`。宿主可发现的外部 Skill 在低、较低、中为 `deny`，较高为
   `ask`，高为 `allow`。未声明 MCP 和 custom tool 始终不因高自主度获得所有权。
6. 新建单专家主 Agent 和专家团团长使用 OpenCode Agent `mode: all`，团员使用 `subagent`；Workflow
   Phase 的 `mode: primary` 保持流程语义，不与 Agent mode 合并。
7. 旧角色缺少自主度时，只读诊断、校验和安装临时按中投影并报告
   `LEGACY_ROLE_AUTONOMY_DEFAULTED`，不回写源包。任何结构性修改前必须逐角色显式补齐。
8. 旧主 Agent 的 `primary` 可只读校验和安装并报告兼容 warning；结构性修改后迁移为 `all`。
9. 用户上传 Skill 必须先通过 [Agent Skills Specification](https://agentskills.io/specification) 与
   MobileWork 静态安全门禁。通过后按原目录树和原字节复制，记录上传来源、保留编辑策略和逐文件
   hash，并绑定目标角色；失败事务不得写入目标包。[官方规范页面](https://agentskills.io/specification)
   是格式真源；官方 `skills-ref` 只作为交叉校验 oracle，固定到
   [commit `69ef37e9424c0a7ea9dd2293b559e43ec8176379`](https://github.com/agentskills/agentskills/tree/69ef37e9424c0a7ea9dd2293b559e43ec8176379)。
   页面规定的硬约束失败时阻断；500 行、渐进披露和浅层引用等建议项只报告 warning。
10. 能力资源选型发生在管理器的设计与生成阶段。角色数量、职责、流程或质量门不能直接投影成
    资源；管理器只能从用户目标或可信来源提出具有稳定业务名称、可观察运行行为和 provenance 的
    候选，不得发明业务规则、阈值、外部读写或副作用。
11. 完整业务卡确认后，管理器按最小权限把不同运行职责映射为无资源、Skill、Custom Tool 或
    OpenCode Plugin。可复用方法、清单、SOP、指导材料和 Python/Shell 脚本包使用 Skill；角色主动
    调用的确定性 JavaScript/TypeScript 能力使用 Custom Tool；事件监听、工具拦截与运行时行为修改
    使用 Plugin。外部系统访问使用 MCP，不由 Plugin 代替连接器。
12. 同一运行职责只生成一个资源，不同且已确认的职责才允许组合资源。同一能力只创建一份并由
    多角色共享。业务 Skill 使用语义 kebab-case，不强制专家或角色前缀；Custom Tool 和本地 Plugin
    使用包命名空间。Plugin 是 package-wide 运行行为，不声明为角色私有能力。
13. 整卡确认只授权当前专家包资源生成，不授权安装、启用、联网下载、外部连接、权限扩大、执行
    生成代码或发布。技术映射若发现新的自动触发、外写、联网、权限、依赖、成本或 Runtime 前提，
    旧确认立即失效，重新完整确认前零写入。生成后的业务 Agent 普通运行不得修改专家包。

## 后果

### 正面

- 流程调整不再暗改权限，角色能力可以独立审计和比较。
- 所有权与动作分离；高自主度不会把未声明的 MCP/custom tool 变成角色能力。
- 外部 Skill 的较高/高自主度行为有清晰且可测试的例外规则。
- `all` 让主专家既能直接使用，也能作为未来通用 Agent 的可调用专家。
- 旧包仍可安全诊断和安装，结构性修改则强制完成显式迁移。
- 能力资源形态由管理器按已确认业务边界自主选择，用户无需先理解 Skill、Tool 或 Plugin 的差异。
- 角色设计不再机械放大为一角色一个 Skill；无适合固化能力时，零资源是完整合法结果。
- Skill、Custom Tool、Plugin 和 MCP 的运行语义、所有权与副作用边界可以分别审计。

### 代价

- 新建专家团需要为团长和每位团员分别选择自主度。
- 旧包第一次结构性修改前需要补齐角色自主度，并迁移主 Agent mode。
- execution 中曾依赖流程生成精确 allowlist 的声明可能变成 `ask` 或校验失败，需要重新确认角色
  自主度或资源所有权。
- 设计确认卡必须覆盖能力名称、作用范围、触发或调用、输入输出、可见副作用、权限/成本/运行前提、
  质量门和实现状态；技术映射出现 material impact 时需要重新完整确认。

## 被否决的方案

- 继续取 Workflow/Phase/角色覆盖中的最高自主度：流程编辑仍会产生隐式提权。
- 取多流程权限并集或冲突时降为 `ask`：结果仍依赖角色参与了哪些流程，不能成为稳定角色合同。
- 允许 `permission_reason` 解锁高于矩阵的动作：理由文本无法提供可机械验证的安全边界。
- 高自主度自动开放全部宿主 MCP/custom tool：混淆发现能力与资源所有权，扩大跨包能力面。
- 把角色自主度写进 Agent frontmatter：该字段不是 OpenCode Agent 官方字段，且会产生第二真源。
- 将主 Agent 保持为 `primary`：不符合未来由通用 Agent 调用专家（团）的复用目标。
- 按角色或职责条目自动创建 Skill：把组织结构误当成可复用能力，制造重复资源和无依据的业务规则。
- 要求用户逐项选择 Skill、Custom Tool 或 Plugin：把运行时实现细节转嫁给业务用户，且无法保证最小权限。
- 允许生成后的业务 Agent 动态改包：绕过完整业务卡、确定性生成和包校验，破坏发布证据链。

## 验证约束

验证必须覆盖五档矩阵、流程不变量、execution 越权、手写权限只收紧、资源所有权、旧包临时投影、
Agent/Phase mode 分离、目录与 ZIP Skill 的原字节导入及失败零写入。静态校验、临时安装和配置读回只
能证明包合同与安装投影，不能宣称 MobileWork Electron 或真实 OpenCode 会话已运行。还必须覆盖
零资源、共享 managed Skill、角色拥有的 namespaced Custom Tool、package-wide Plugin、三种资源按
不同职责组合、material impact 触发整卡失效，以及普通运行不得修改专家包。
