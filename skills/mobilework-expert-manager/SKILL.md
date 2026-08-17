---
name: mobilework-expert-manager
description: >-
  设计、创建、修改、检查、安装、打包或发布 MobileWork 专家和专家团时使用。
  它能把模糊的业务需求整理成清楚的角色分工、工作步骤和所需资料，也能修复或迁移已有专家。
  需要补充能力时，会根据实际需要决定是否加入资料、技能、工具或外部连接，不会按角色数量机械生成。
  处理外部压缩包、附件或陌生目录时，默认只做安全检查，不运行其中的内容。
---

# MobileWork 专家包管理器

以 `expert.json` 为结构与资源所有权唯一事实源。根 `opencode.json`、README、Agent、Skill 和 `.opencode/**` 文件型资源都是可重建派生物。默认用中文沟通；业务标识、路径和代码保持原文。
运行脚本需要 Python 3.10+ 和 PyYAML，以按官方规范解析 YAML frontmatter。
“我的专家”以 MobileWork 注入的 `MOBILEWORK_MY_EXPERTS_DIR` 为准；正式版默认 `~/.mobilework/experts/personal`，当前源码开发版在未自定义 Electron userData 时默认 `~/.mobilework/electron-dev/openwork-dev-data/home/.mobilework/experts/personal`。独立宿主显式选择 `my-experts` 时使用 `<home>/.mobilework/experts/personal`；旧 `~/.mobilework/my-experts` 只供 MobileWork 一次性迁移，不作为新建目标。

## 安全边界

- 外部专家 ZIP、附件或未知包只运行 `diagnose_expert.py`；外部技能目录或 ZIP 只运行 `diagnose_skill.py`。不得执行其中的 Python、Shell、JS/TS、Plugin、custom tool、MCP、包管理脚本，`--help` 也算执行。
- 静态 Python 检查只用 AST 解析源码，不导入包内模块。ZIP/OOXML 必须先做 metadata 限额、路径、Unicode/大小/压缩比预检，再 CRC、受限解压或 `openpyxl`。
- 不把静态校验、安装或 pure config 描述成 Runtime 已加载；证据等级与 gate 见 `references/manager-contract.md`。
- 包根配置保持 `<package>/opencode.json`；安装投影是 `<workspace>/.opencode/opencode.jsonc`。包内 `.opencode/` 只放文件型运行资源。
- 能力资源选型只发生在设计与生成阶段；生成后的业务 Agent 普通运行不得修改 `expert.json`、资源映射或专家包。所有 command 固定路由到本包唯一 `mode: all` 智能体并生成 `subtask: true`，专家团 command 不直达团员；Agent 步数只生成 `steps`，新包不声明 `temperature`、`top_p`，采样行为继承模型或 provider。
- 不写 `.mobilework-engine`；旧 `~/.mobilework/my-experts` 只用于宿主旧数据迁移和禁止路径。

## 新手默认交互

用户只说业务目标时，先在内部完成“需求转译卡”，再用下面三个标题回复：

1. **我理解的需求**
2. **建议方案及原因**
3. **只需你确认的事项**

默认先说“资料库、能力包、共享规则、稳定流程、快捷入口、计算工具、过程控制、外部连接、
单专家、专家团”等业务名称；技术名只在当前整卡确认且用户要求开发细节后，第一次出现时括注一次。每轮最多问三个会改变方案的
问题；按实际需要用户回答的决定计数。交互工具的每个 question 对象只问一个决定；正文降级不列编号或并列问句。路径、Schema 字段、哈希、receipt、sidecar、权限/自主度枚举及其等级标签、
供应商与模型等技术绑定写法放在“开发细节”，当前整卡未确认时不展开；业务层只描述谁能决定、哪些步骤固定、何时确认、允许哪些副作用以及何时停止或返工。

自动匹配只生成候选建议。需求发现期间维护不落盘的 question ledger；按语义选择而非措辞识别同一决定，
已问未答只保留“等待答复”状态，不换 id 或措辞重问；提问仍只能走交互工具或正文之一，并遵守预算。明确由用户选择时才提问，交互工具可用时每轮最多三个互不依赖的 question-ready 决定；工具不可用时正文整轮只写一个精炼的业务组合问题，不列编号或并列问句。
明确委托给管理器时在可信候选证据充分后提出候选，明确由可信资料决定时从资料派生；候选缺少稳定业务标签或可观察差异时只询问缺失的比较证据。
`dependencies` 只表达“前提会改变候选集合或比较”的选择收敛关系；多个决定共享同一执行权限门不自动产生依赖。未决权限继续独立展示并阻止生成与执行，但不得压住其他 question-ready 根。显式最终拒绝不因新候选自动重开；只有用户明确设置未来解决条件的延期，才在条件满足后恢复原决定。阻塞与恢复保留原决定的授权、source、id、提问渠道和预算历史。默认输出业务确认卡，开发字段只按需展开。
新决定准入先于依赖与前沿计算。不能仅凭“付费推理”、用户技术词或假想未来外部能力新建数据外发决定；只有用户明确现在要决定，或可信证据已经确定具体、当前候选或执行路径会外发时，才进入 question-ready。绑定被延期/阻塞且没有具体外部路径时，用只保护联网/外发且不计问题预算、不阻止 design-only 整卡确认的安全守卫随现有 blocker 展示，不新增 `open/asked` 决定；后续出现具体可信路径再带 provenance 记录，不能把守卫当作外发授权。
最终确认前不得写文件、联网、启动进程或 preflight、启用 Plugin、连接 MCP 或扩大权限。校验失败先说明“问题、影响、建议动作”，原始 finding 放在开发细节。完整协议见 `references/requirements-discovery.md`。

首次推荐不能只给模块名。`建议方案及原因` 必须同时说清使用时机、使用角色或作用范围、现有实现状态和副作用。推荐 Reference 时明确由哪些角色使用，并当场说明角色分配只用于路由与审计，不是访问控制；“什么时候查阅”写进资料说明，不为这个触发条件额外发明角色规则。
Git Reference 的使用时机只约束 Agent 何时采用资料，不能承诺宿主只在触发后联网；OpenCode 可能提前异步 materialize。若用户要求触发前绝不联网，改为受控外部连接并说明需要连接器。推荐 MCP 时，当前整卡确认前只用业务语言询问目标系统是否已有真实、可用且经核验的业务入口，不得由 assistant 枚举实现渠道。若没有，说明外部连接能力仍需开发，并阻止具体连接选择和执行；整卡确认后才在开发细节核对实现渠道。没有可信入口证据时不得绑定或声称已连接，外部权限未知时默认只读最小权限。

## 任务路由与按需 References

不要一次加载全部 reference。

| 任务 | 必读 |
|---|---|
| 新建、资料转化、结构性修改、设计确认 | `references/requirements-discovery.md`、`references/expert-json-spec.md` |
| 当前专家原对话受控修改 | `references/controlled-modification.md` |
| Workflow、自主度、权限、executor 所有权 | `references/workflow-autonomy-spec.md`、`references/permission-policy-spec.md` |
| Agent/Skill/Command 编写 | `references/opencode-authoring-best-practices.md`，再按需读 `references/agent-md-spec.md`、`references/skill-md-spec.md` |
| opencode、MCP、tools、plugins、references、instructions、LSP | `references/opencode-json-spec.md`、`references/runtime-extensions-spec.md` |
| 头像、README | `references/avatar-spec.md`、`references/package-docs-spec.md` |
| 包结构、便携、ZIP、技能上传、外部诊断 | `references/portable-package-spec.md`、`references/safe-diagnostics-spec.md` |
| 版本输入、findings、gate、sidecar | `references/manager-contract.md` |
| 旧包迁移、供应链、Bundle | `references/bundle-migration-supply.md` |
| 创建/修改后的 Git 与 SemVer | `references/source-version-control.md` |

## 设计确认门

新建、资料转化和结构性修改先读取上下文，区分用户事实、候选设计和未确认项。只询问会改变行为、权限、成本或运行前提的缺口；完整业务确认卡取得明确确认后才能生成。业务卡为每项候选能力列清稳定业务名称、使用范围、触发或调用方式、输入输出、可见副作用、权限/成本/运行前提、质量门、实现状态和 provenance。
用户把技术载体选择委托给管理器；机器 Skill、custom tool 或 Plugin 标识只按需进入开发细节。

**`full-card-first` 硬门：**任何影响行为、权限、成本或运行前提的决定新增、改变或状态更新——包括技术映射发现前提、
用户改动已确认选择及执行准备发现阻塞——都使旧确认失效。同一条 assistant 回复的第一块内容必须先明确旧确认已失效，
再完整重发当前八区段业务确认卡和 provenance 附录；未变化区段也必须保留。此前不得先给开发确认卡、开发细节、架构或技术绑定、阻塞摘要、
实现选项。卡后动作前先按传递依赖闭包重算状态：`dependencies` 仅表示显式延期或可信证据证明前提会改变候选集合/比较的选择收敛关系，不能因两个决定共用执行权限门而连边。用户明确说明某项运行前提不存在、不可用或尚未建立时，把该前提及所有可达的下游 `open/asked` 转为 `blocked`；根 blocker 的 `blocked_by` 含自身 id，只有新用户/可信证据能清除；`resume_status` 保留原待决状态，多前提须全部恢复且依赖图有效才恢复，真正无可达 blocker 的决定继续待决。阻塞与恢复不得改变原授权、source、id、提问渠道或预算历史。未知依赖或环须给稳定诊断并禁止确认与生成。完成闭包后再求 question-ready 依赖前沿：只有状态为 `open` 且全部传递前提均为 `answered/proposed/confirmed` 的决定可首次处理；前提仍为 `open/asked` 的下游保持待决但不得提问、给绑定选项或计费。
图有效且仍有 `open/asked` 时，完整卡后必须优先处理依赖前沿中的 `open`，一次处理额度内所有互不依赖的 ready 根；不能把当前整卡确认或无关 `asked` 决定当作附加前提。用户明确保留选择权时提问，明确委托时基于充分可信证据提出候选，可信资料可直接派生；`asked` 仅携带等待状态。未决权限、成本或数据外发边界继续作为独立 material decision 阻止生成与执行，但不抑制其他 ready 根。可信候选只用稳定业务标签和可观察差异呈现；证据不足时只问缺失的比较证据。权威来源已回答但其中没有可计算规则时，来源保持 `answered`，原规则值决定保持 `open/asked`，只询问获授权的显式数值、比例、公式或可计算规则，不重问来源、不改目标。显式最终拒绝的路径转为 `blocked/superseded`，新候选证据不得自动重开；条件性延期必须来自用户明确的未来解决条件，条件满足时恢复同一待决决定。若只有尚未就绪的下游待决，也只说明等待前提且不得请求整卡确认。图有效且没有待决项时（包括其余项为 `blocked`），只请求一次当前整卡的 design-only 确认，并说明阻塞卡确认不能授权生成。业务展示边界覆盖整张卡、provenance 和卡后提问：当前整卡确认前，即使用户已经要求开发细节，也只用可观察的业务行为；可处理 question-ready 的业务候选，但不输出 provider ID、URL、配置、凭据、字段映射、内部枚举或其他实现绑定细节。内部枚举、翻译后的等级标签默认同样不输出；逐角色选择自主度时显示低、较低、中、较高、高及其可观察影响，是等级标签的唯一默认例外。用户原话中的技术词只作为事实保留，不继续展开。
不能把失效或重绘推迟到条件齐备后。新建单专家、专家团及“资料转为新专家”在当前业务卡明确确认后、任何 preflight、进程、写入、联网、Plugin/MCP、权限扩大、生成或校验前，必须再单选创建位置；即使用户此前提过路径也要完成这次最终落盘确认。优先调用宿主可用的 `AskUserQuestion`、`question` 或等价提问工具；没有任何等价工具时，直接在对话中询问并等待回复。固定标题“安装位置”，问题“确认后，将新专家创建到哪里？”，选项为“我的专家（MobileWork 个人专家目录）”和“当前工作空间”，并允许用户回复其他绝对父目录。该选择不计发现轮次或决定预算，绑定当前确认版本；设计变化后重新确认并重新选择。多选、自定义父目录无效、最终目标逃逸分别返回 `CREATION_TARGET_ANSWER_AMBIGUOUS`、`CREATION_TARGET_PATH_INVALID`、`TARGET_OUTSIDE_ROOT`。维护性修复、已有专家修改、只读诊断、校验、安装和打包可直接执行。

提示含 `<mobilework-expert-manager-context>` 时严格执行 `controlled-modification.md`，只输出指定
JSON proposal，不自行读写路径或声称生效。

## 标准流程

1. 解析实际 `<skill-root>` 并分类任务。纯咨询、新建和结构设计先完成需求转译与确认，确认前只用
   宿主原生只读/context API，不运行 shell、CLI 或环境脚本；已有包只从 `expert.json` 和声明资源读取事实。
2. 新建执行在当前业务卡确认后先通过可用宿主提问工具或对话 fallback 完成独立创建位置单选；收到唯一有效回答前冻结全部执行与验证。自定义输入表示已存在的绝对父目录，最终目录为 `<parent>/<slug>`；拒绝根、symlink、Windows reparse point、特殊文件和路径逃逸，已有目标仍单独确认 `--force`。
   位置确认通过或进入直接维护通道后才按任务检查环境：生成 `core+git`、校验/导入 `core`、Excel
   Reference `core+excel`、打包 `core+package`、DOCX bundle `core+bundle-docx`、可信配置 `core+config-load`、评测 `core+coverage`。`+` 表示重复 `--feature`；`all` 必须显式提供 caller-reviewed sidecar。
   纯咨询和仅设计确认交付后停止，不选择创建位置，也不运行环境检查。
3. 按已确认业务能力逐项选择无资源、Skill、custom tool 或 OpenCode Plugin；角色、职责、流程和质量门不能直接投影资源。候选必须有稳定业务名称、可观察行为和可信 provenance，不得发明业务规则或副作用。方法/清单/SOP/指导/Python/Shell 脚本包用 Skill，Agent 主动调用的确定性 JS/TS 用 custom tool，事件监听/拦截/运行时修改用 Plugin，外部系统用 MCP；细则见 `requirements-discovery.md` 和 `runtime-extensions-spec.md`。
   同一运行职责只创建一个资源，不同已确认职责才组合；无适合能力时零资源。Skill 使用完整语义 kebab-case，不强制专家/角色前缀；custom tool 和本地 Plugin 使用包 slug 命名空间，Plugin 不声明角色所有权。新专家使用统一 `skills[]`，不手写 `permission.skill`。同一专家包内，两类 Command `name`、完整 Skill 名和所有 Agent `id` 必须两两互斥；冲突直接失败，不自动重命名；展示名称不受限。逐角色确认自主度：单专家一次，专家团分别确认团长和每位
   团员；用户标签为低、较低、中、较高、高，内部保存为
   `scripted/fixed/bounded/guided/adaptive`。顶层 Workflow 可省略；一旦声明，每个 Workflow 必须同时
   声明 autonomy、至少一个 Phase 和逐 Phase acceptance。按需声明 execution、MCP、task 和
   custom tool 所有权。脚本拥有全部已确认步骤时只保留脚本执行，不另造 Agent 编排层；只有另行确认的 Agent SOP 或分支规则才增加独立编排层。
4. 自建 `managed` Skill 先在可信 staging 写完整目录，按 Agent Skills 官方硬规范校验、计算逐文件 SHA-256 并声明全部 `package_resources[]`；generator 只逐字节复制，不从职责、附件名或目录名编写 Skill。随后调用 `create_expert.py` 生成或重建。
   本地资料目录先零执行检查，再把确认过的文本或安全转换
   结果写入包内 Reference；Git Reference 只记录已确认的仓库声明，不由管理器静默 clone。上传技能先静态诊断，再使用 `import_skill.py` 原字节写入
   `.opencode/skills/<name>/` 并分配：单专家自动分配；专家团必须指定一个或多个 Agent id，
   或选择包含团长和全部团员的 `--all-members`。使用 `--creation-target my-experts|workspace|custom` 传入已确认位置；`custom` 同时提供 `--output-dir <parent>`，未给 target 时 `--output-dir` 仍只断言宿主已解析目标；覆盖
   另行确认 `--force`。`--my-experts` 是兼容 alias。创建和导入都必须先通过 Agent Skills 官方 frontmatter 规范；诊断失败
   只报告 finding，不强制转换 YAML 类型或改写上传字节。导入属于结构性修改；旧包必须先为每个
   角色补齐 autonomy。成功导入保持完整目录树和逐文件字节，记录 `origin: uploaded`、`edit_policy: preserved` 与 SHA-256；失败事务零写入。
5. 运行 validator JSON 输出、便携性扫描和相关定向测试；失败时按 finding root cause 修复。
6. 真实创建或修改成功后读取 `source-version-control.md`：展示累计 diff 与 SemVer 建议，并询问用户是否发布。未明确确认不得 commit/tag。
7. 按需安装、pure config、打包或 bundle；逐项读回文件、配置、receipt、hash 和证据门。

## 权限与所有权摘要

角色自主度是 Agent 静态权限的唯一基线；Workflow/Phase 自主度只描述流程决定权、确认点、风险和验收，不参与权限计算。`scripted` 未知能力 `deny`，其他档位未知能力 `ask`；不输出字面量
`guided/adaptive * = allow`。任何档位都不得新生成无条件 `bash: {"*":"allow"}`。

- edit、webfetch、external_directory、doom_loop 只按角色五档矩阵；修改 Workflow、Phase、override
  或 execution 不得改变生成权限。
- Skill、MCP、task 和 custom tool 必须有结构化所有权。已拥有的包内 custom tool 五档默认 allow；
  其他角色、跨包或未知 tool 不得放行。
- Plugin 是 package-wide 运行资源，不进入角色所有权或权限；外部连接使用 MCP，不得让 Plugin 冒充连接器。
- 角色分配的 Reference 和角色规则用于路由、生成与审计，不是底层访问控制。严格隔离资料时使用
  角色专属 Skill 或带权限的 MCP。
- `permission.skill` 从角色 `skills[]` 和角色自主度派生：包内已分配 Skill 精确 allow；外部 Skill
  在低、较低、中为 deny，较高为 ask，高为 allow。上传技能默认
  `origin: uploaded`、`edit_policy: preserved`；没有用户明确授权不得改写任何字节。
- 手写 `permission` 只能逐项收紧；`permission_reason` 不能授权高于角色自主度基线的动作。
- 旧角色缺少 autonomy 时只读校验和安装临时按中处理并产生
  `LEGACY_ROLE_AUTONOMY_DEFAULTED`，不修改源包；结构性修改前必须逐角色显式补齐。
- 新建单专家主 Agent 与专家团团长使用 `mode: all`，团员使用 `subagent`。旧主 Agent 的
  `primary` 可只读校验和安装，结构性修改后迁移为 `all`；Workflow Phase 的 `mode: primary`
  保持流程语义不变。
- 专家团 `parallel` 的 `agents[]` 只列唯一且必参与的角色；团长运行时为每个角色分别创建
  `1..N` 个独立 task 实例。实例数和分片范围不得写死在 manifest。

完整矩阵只维护在 `permission-policy-spec.md`。

## 主要命令

```bash
# 仅用于已确认执行或直接维护通道；纯咨询与设计确认阶段不运行。
python <skill-root>/scripts/check_environment.py --feature core
python <skill-root>/scripts/check_environment.py --feature all \
  --sidecar <caller-reviewed-sidecar>

python <skill-root>/scripts/create_expert.py --manifest <expert.json> --creation-target my-experts|workspace|custom [--output-dir <custom-parent>]
python <skill-root>/scripts/validate_expert.py <package-dir> --format json
python <skill-root>/scripts/diagnose_expert.py <unknown-dir-or-zip> --format json
python <skill-root>/scripts/diagnose_skill.py <skill-dir-or-zip> --format json
python <skill-root>/scripts/import_skill.py \
  --package-dir <package-dir> --skill <skill-dir-or-zip> \
  [--assign-to <agent-id> ... | --all-members]
python <skill-root>/scripts/import_reference.py \
  --package-dir <package-dir> --source <local-file-or-dir> \
  --alias <alias> --description <when-to-use> \
  [--assign-to <agent-id> ... | --all-members] --confirm
python <skill-root>/scripts/scan_portable_artifacts.py <package-or-output>

python <skill-root>/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace> \
  [--format human|json] [--schema-version 1|2]
# 仅在 force-only 输出确认过同一状态绑定 hash 后使用；四项缺一不可。
python <skill-root>/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace> --force \
  --discard-drift --expected-drift-sha256 <preview-sha256> \
  --confirm-discard-drift <slug> \
  [--format human|json] [--schema-version 1|2]
python <skill-root>/scripts/install_expert.py \
  --restore-drift-backup <backup-id> --workspace-dir <workspace> \
  --expected-backup-sha256 <backup-sha256> \
  --confirm-restore-drift <slug> \
  [--format human|json] [--schema-version 1|2]
python <skill-root>/scripts/install_expert.py \
  --uninstall <slug> --workspace-dir <workspace> \
  [--format human|json] [--schema-version 1|2]
python <skill-root>/scripts/verify_trusted_config.py \
  --package-dir <package-dir> --workspace <installed-workspace> \
  --sidecar <explicit-caller-reviewed-sidecar> \
  --target-opencode-version <target-version> \
  [--host-contract <matching-host-contract.json>] \
  [--format human|json] [--schema-version 1|2]

python <skill-root>/scripts/package_expert.py \
  --package-dir <package-dir> --output-dir <dist-dir>
python <skill-root>/scripts/plan_legacy_migration.py <legacy-dir-or-zip> --format json
python <skill-root>/scripts/create_bundle_manifest.py \
  --bundle-dir <bundle> --package-zip <package.zip>
python <skill-root>/scripts/validate_expert_bundle.py <bundle>
```
迁移答复必须原样给出 planner 的 RFC 6902 `candidateJsonPatch` 数组和机械 action 清单，不得只作自然语言概述或自行应用 patch；缺少真实输入时明确请求目录与 ZIP，并逐项承诺 Skills、`maxTurns`→`steps`、references 映射、权限变化、全部派生物重生成、根规则/Bash 业务确认，同时明确检查不 import 或执行包内 modules、commands、Plugins、MCP、lifecycle scripts。

`--force` 单独使用永不丢弃漂移。高危丢弃只在 POSIX 候选后端启用：确认 hash 同时绑定 redacted drift、完整 config/package/receipt/目标内容与 mode 状态；写入前创建 0700/0600 备份并再次
复核，backup 使用 dirfd no-replace publish，提交使用 metadata/content-hash 重绑定的
dirfd/no-follow transaction。rollback/cleanup 无法验证时保留 private staging、脱敏 recovery paths
与 backup 证据；backup publish 已写入 private bytes 但无法 exclusive cleanup 时返回 exit 3
`backup-recovery-required`，不得误报为写入前阻止。human 输出会显示下一步必需的 preview/backup hash。
恢复必须使用结果中读回的 exact backup id/hash/slug，且当前 contract 3 receipt、owned state、完整
目标与 receipt set 仍等于安装后 guard。恢复成功只表示原始漂移字节已恢复，证据等级为 `valid`，
`install`/`configLoad` gate 都保持 blocked，不提升为 `installable` 或 `config-loadable`。Windows 高危
丢弃与恢复均在写入前以 exit 4 policy-blocked；普通 install/uninstall 使用 Win32 handle 与 reparse-free
anchor 的 protocol-v2 lock，但目标文件仍是 legacy transaction 并明确报告 `transactionSecurity: partial`。
crash/stale-lock 自动恢复和 Windows reparse-safe 目标事务仍为 `not-tested`，不得外推为完整平台验证。

通用安装入口按 `--target-opencode-version`、`MOBILEWORK_TARGET_OPENCODE_VERSION`、
`--host-contract` 的优先级解析目标；未提供时为 unknown，不宣称版本能力通过。
`verify_trusted_config.py` 明确忽略 ambient target env，必须提供 target flag 或 host contract；也可
同时提供两者，但版本必须一致。上例可用 `--host-contract <host-contract.json>` 替代 target flag。
sidecar 必须由 caller 显式指定并自行核验来源；管理器安全打开该路径，把同一 descriptor 的字节
hash 并复制到私有 0700 可执行文件，只运行这个 hash 绑定副本来对账目标版本，不把任意 ambient
CLI 自动当作仓库锁定产物。

## 本地 Git/SemVer 门

`create_expert.py` 在可信源成功生成和校验后初始化精确根 Git，并输出 `VERSION_PENDING`，不会自动
commit/tag。每次真实修改后先运行：

```bash
python <skill-root>/scripts/version_expert.py --package-dir <package-dir>
```

向用户展示建议并询问。只有明确确认后才运行：

```bash
python <skill-root>/scripts/version_expert.py \
  --package-dir <package-dir> --version <X.Y.Z> --confirm
```

不得配置或调用 remote。根 `.gitignore` 可分发；`.git/**` 永不进入 ZIP、bundle、安装或 hash。

## 最低验收

- 仓库内运行
  `python apps/desktop/resources/presets/skills/skill-manager/scripts/quick_validate.py apps/desktop/resources/presets/skills/mobilework-expert-manager`
  与完整 `unittest discover`；CI 使用固定提交的官方 `skills-ref` 交叉验证 manager 和生成技能，
  生产路径只依赖内置共享校验器；变更核心模块覆盖率达到目标。
- `expert.json`、根 `opencode.json`、Agent/Skill、权限和 Workflow 投影一致。
- Agent Skills 官方页面是格式权威；强制项失败即阻断，`skills-ref` 只作交叉验证，500 行、渐进披露和浅层引用等建议项只报告 warning。
- 可信包完成 generate → validate → portable scan → package → clean extract → revalidate → install；
  安装后读回 `.opencode/opencode.jsonc`、资源和 receipt。
- 外部恶意 fixture 的 sentinel、子进程和网络均未触发。
- caller 已核验来源并显式提供 sidecar 时，只对私有配置物化执行 `debug config --pure`，记录观测
  hash/version，最高 `config-loadable`；缺失或来源未核验时明确未验证。
- 报告实际路径、测试统计、证据等级、未验证项与 `versionPending`/release 状态。

只有所有已请求 gate 通过才能宣称完成；Runtime 未实际验证时必须明确写 `not-tested`。
