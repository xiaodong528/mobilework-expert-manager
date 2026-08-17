# 管理器版本、诊断与证据合同

## 目录

1. [版本输入](#版本输入)
2. [不可信输入](#不可信输入)
3. [安装 receipt 与漂移门](#安装-receipt-与漂移门)
4. [配置边界](#配置边界)
5. [结构化 findings](#结构化-findings)
6. [Gate 与证据等级](#gate-与证据等级)
7. [可信 sidecar](#可信-sidecar)
8. [需求发现合同](#需求发现合同)
9. [退出码](#退出码)

## 版本输入

目标 OpenCode 版本按 `--target-opencode-version`、
`MOBILEWORK_TARGET_OPENCODE_VERSION`、`--host-contract`、`unknown` 的顺序解析。
版本字符串只证明来源和冲突，不自动证明能力。host contract 的 `capabilities` 仅在其版本与
生效目标版本一致时可作为显式能力证据。机器可读政策源是
`scripts/manager-contract.json`；Python CLI 常量从该文件加载，测试对读回值和实现支持边界做一致性
校验，不在 reference 中复制当前合同版本。唯一例外是 `cli_contract` 的最小 emergency renderer
schema：仅在机器合同损坏时输出一个 exit 3 文档，action 在该 fallback 下绝不执行。旧专家包仍可
诊断、校验和安装；缺少角色 Reference
绑定时给兼容 warning。结构性修改前先生成迁移预览，让用户确认每项 Reference 的使用角色，以及
既有 workspace Instruction 是否仍应全局生效。不得静默把全局规则改成角色规则。

Reference 能力只看已核实的 host contract：

- `references=true` 时使用原生 local/Git 投影；
- 能力不支持或未知时，local Reference 在安装阶段降级为角色专属派生 Skill；
- 能力不支持或未知时，Git Reference 在写 workspace 前返回 `capability-missing`。

不能根据 OpenCode 版本号猜测 Reference 能力。Git 异步 materialize 成功属于独立 Runtime 证据。

## 不可信输入

文件系统输入在读取内容或计算 provenance 之前，必须按
`scripts/manager-contract.json.inputLimits` 完成元数据预检。内存快照限制为总计 64 MiB、单文件
16 MiB；归档解压限额是另一套流式风险门，不能反向扩大内存快照限额。`safe_input.inspect()` 拒绝
symlink、Windows reparse point、FIFO、socket、字符/块设备和未知对象，并限制原始目录条目数、
总大小、单文件大小、路径长度及路径深度。原始条目门在排序、完整物化和 exclusion 判断前生效，
被排除的同级条目也计数；探测到第 `maxEntries + 1` 个条目即停止，不再消费后续 iterator。

包快照 exclusion 的机器可读真源是 `packageSnapshotExclusions`。通用 `inspect()` 保留所有条目；
显式 `inspect_package()` 只跳过包根 `.git` 子树，并记录已经 `lstat`/类型检查的根 `.git` 路径和
identity metadata。根 `.git` 本身若为 symlink 或 Windows reparse point 仍必须拒绝，但其内部
对象不枚举、不物化。根 `.git` 的内容变化不得改变 package tree hash；嵌套 `.git` 不视作包根
源码元数据，仍进入快照并接受类型和资源门。

`.cache`、`__pycache__`、`.DS_Store`、`.pyc`、`.pyo` 不属于 package exclusion，必须保留给
validator/diagnose 产出不可分发 finding。`package_snapshot`、validator、diagnose、packager 和
installer 必须消费同一个 `inspect_package()` 结果，不能为了过滤 finding 再扫描原始 package。

通过预检的文件使用 `O_BINARY | O_NOFOLLOW | O_NONBLOCK`（平台支持时）打开，并在打开前、
打开后、读取后及最终复核 identity、大小和时间戳。任何竞态统一返回
`INPUT_CHANGED_DURING_SCAN`。受保护读取的内容、逐文件 hash 和 tree hash 固化到同一个
`InputSnapshot`；消费者不得重新无保护遍历原路径。当前实现把快照内容保留在内存中，因此除
Python 对象开销外，持久 payload 受 64 MiB 总量门约束，单文件 `chunks` 合并产生的瞬时副本受
16 MiB 单文件门约束。

当前 Candidate 已在 validator、`diagnose_expert.py`、`diagnose_skill.py`、package 与 install
入口对预检失败立即阻断。既有 validator reader 只重开快照 materialize 的可信临时目录，不重开
原输入；importer 等剩余入口仍待迁移，因此不得宣称所有入口完成单快照消费。真实 Windows
reparse point 也保持 `not-tested`。

## 安装 receipt 与漂移门

新安装及成功升级写 contract 3。contract 3 在 contract 2 的精确文件、mapping、scalar、list 和
dependency ownership 之上，增加 `packageTreeSha256`、`manifestSha256`、
`managerContractSha256`、`targetOpenCodeVersion`、`targetCapabilitiesSha256` 和
`projectionSha256`。这些字段分别从同一 `InputSnapshot`、机器合同、安装时 resolved target
（允许 `unknown`）和包拥有的投影复算；`projectionSha256` 不覆盖无关 workspace 配置。只有
receipt 中 exact target 与 trusted-config 的显式 target 复算一致时，证据链才可继续。

contract 1/2 继续支持保守卸载和无漂移升级；contract 1 的 list/dependency ownership 不可信，
所以相关值只保留、不删除。旧 receipt 不能获得 `config-loadable`，但干净升级成功后会重写为
contract 3。

`managerContractSha256` 绑定整个 `manager-contract.json` 的原始字节。新增需求发现政策也会保守地
轮换该 hash：用旧政策写出的 contract 3 receipt 在安装包更新后不能继续获得
`config-loadable`，必须完成无漂移升级并读回新 receipt。不得排除新字段或改变 hash domain 来
静默延长旧证据。

同 slug `--force` 升级和卸载都在创建 staging 或修改 workspace 前执行
`verify_owned_state(runtime, receipt, all_receipts)`。检查范围包括所有 owned files、mapping、scalar
LSP，以及 contract 2/3 的 list 和 dependency；共享 owned file 也必须保持 receipt hash。合并读取
的完整 config、package.json、receipt tree 与待写目标状态从受保护 capture 解析；提交前同时重做
ownership 检查并比较 capture fingerprint，避免用旧 staging 覆盖并发用户新增项或 receipt。无关
用户新增项不算 owned drift，但若发生在合并之后，会以 `INSTALL_INPUT_STATE_CHANGED`、exit 1
停止并原样保留。config、package、receipt 与去重 target 共用同一 entry/byte budget；target 的 mode
和平台 file attributes 进入 state-bound content hash，ctime 进入同次 capture 的 identity fingerprint。
发现初始 owned drift 时返回
`INSTALL_OWNED_STATE_DRIFT`、不含原值的确定性 preview 与 `previewSha256`，exit 1，并保证
workspace、配置、依赖和 receipt 字节不变。当前 `previewSha256` 使用确认 domain v2，同时绑定
slug、规范化 receipt、完整 config/package.json/receipt set、所有受影响目标的 content-state hash
与 redacted drift。preview 项没有变化但无关用户配置或 receipt 字节变化时，确认 hash 也必须变化。

`--force` 本身仍永不丢弃漂移。高危升级必须同时提供：

```text
--force --discard-drift
--expected-drift-sha256 <force-only 输出的 previewSha256>
--confirm-discard-drift <exact-slug>
```

缺少任一参数或用于 fresh/clean install 时停止。初检后可以 staging，但在目标写入前必须在同一
workspace mutation lock 内重新 capture、复算 state-bound preview、比较 identity fingerprint，
再把 `staged ∪ stale` 的写前状态持久化到
`.opencode/.expert-drift-backups/<slug>/<UTC backup-id>/`。目录固定 0700，manifest 与编号 payload
固定 0600；manifest 保存 slug/id/time、确认与 post-state/receipt-set guards、路径、presence、mode、
size 与 hash，但 entry 不含配置原值。原本不存在的目标也以 absent entry 记录。manifest 最后写入并
fsync；临时目录通过 Darwin `renameatx_np(RENAME_EXCL)` 或
Linux `renameat2(RENAME_NOREPLACE)` 由已打开的 parent dirfd 独占 publish，不支持时 fail closed。
宿主缺少该原语时在写入前以 exit 4 阻止。backup filesystem 在 publish 时拒绝固定 no-replace flag
且 private cleanup 已验证时，以 exit 4 报告 `attempted: true`、`committed: false` 和
`rollbackVerified: true`；若 exclusive cleanup 也无法完成，则以 exit 3
`backup-recovery-required` 保留脱敏 private recovery path。quarantine rename 成功后 recovery path
切换到实际 quarantine；若目录已删除但 parent fsync 失败，则报告 parent 与
`durabilityUnverified: true`。目标文件 transaction 遇到相同拒绝时，
只在 rollback 已验证后以 attempted exit 4 返回，不得误报为 preflight 零执行。
publish 后复核 inode，失败读回只 quarantine/清理同一 identity，随后用 `safe_input` 完整读回，
才可进入 `commit_transaction`。POSIX 写入通过 `secure_transaction` 从已打开的 runtime/staging dirfd
执行 no-follow parent traversal、metadata/content hash 重绑定与 shared atomic no-replace rename；
guard 后出现的 symlink、特殊文件或同 inode 等长改写都会停止，不得按绝对路径跟随到 runtime 外。
所有 POSIX install、
uninstall 与 restore 在提交前还会复核 lock owner/workspace identity；成功后 contract 3 receipt、
完整目标状态、receipt set 与 backup post-state guard 必须读回一致。

独立恢复 operation 为：

```text
--restore-drift-backup <backup-id>
--expected-backup-sha256 <exact-backup-sha256>
--confirm-restore-drift <exact-slug>
```

backup id 不是任意路径，只能按机器合同从固定根推导。恢复前严格验证 backup tree/hash/slug，要求
当前同 slug contract 3 receipt 仍 clean，并要求所有受影响目标和完整 receipt set 等于安装后的
post-state guard；任一变化都以 `INSTALL_RESTORE_TARGET_CHANGED`、exit 1、零写入停止。恢复从受保护
payload 复制到私有 staging，再通过同一 transaction 和写前 guard 提交；读回必须等于原文件
bytes/mode pre-image
及原 state-bound preview。成功状态是 `drift-restored`、`evidenceLevel: valid`、`configLoad: blocked`：
`install` gate 同样为 `blocked`。它表示用户原始漂移已恢复，不表示该状态仍 installable，也不恢复
空父目录、时间戳、owner、ACL 或 xattr。

Python 写入口使用 lock protocol v2 的 `ownerToken/pid/createdAt/heartbeatAt/protocolVersion` 文档。
POSIX cooperative backend 使用 O_EXCL 获取，并在释放时把活动名原子移到 owner-specific quarantine，
复核 identity/token 后删除。Windows backend 逐级持有 reparse-free directory handle，以
`CreateFileW(CREATE_NEW)` 排他创建常规文件，使用 volume serial + 128-bit file id 绑定 owner；释放时
由同一 source handle no-replace rename 到 owner-specific quarantine，再标记 delete-on-close。不匹配
对象不会删除，两端都在 install/uninstall 最终提交前复核 lock owner/workspace identity。

heartbeat 和 stale reclaim 尚未验证，本轮不回收 crash 留下的 stale lock。高危备份与恢复只在
POSIX candidate backend 开放；Windows 高危丢弃与恢复在写入前以 exit 4 阻止。Windows 普通
install/uninstall 已持有 protocol-v2 lock，但目标文件仍沿用 legacy transaction，并在成功结果中报告
`transactionBackend: windows-legacy`、`workspaceLockProtocol: 2` 和 `transactionSecurity: partial`。
Windows reparse-safe 目标事务、stale-lock quarantine 与五阶段 crash injection 仍为 `not-tested`，
不能因锁或 focused 测试通过就宣称完整平台验证。

force-only human 输出必须包含 `slug` 与 `previewSha256`；高危成功 human 输出必须包含
`backupId` 与 `backupSha256`，从而无需改用 JSON 就能完成显式确认和恢复。提交后 readback 失败或
成功 action 的 lock release 未验证时返回 exit 3、`execution.attempted: true` 和
`committed-unverified`/`lock-release-unverified`，并在已发布 backup 的场景保留其结构化 id/hash；
rollback/cleanup 未验证时同样保留 `committed`、`rollbackVerified`、脱敏 recovery paths 和已发布
backup id/hash；被报告为恢复路径的 private staging 不会被自动清理。不得把已提交或可能已提交的
状态误报为 preflight 零执行。

## 配置边界

专家包配置始终位于 `<package>/opencode.json`；包内 `.opencode/**` 只保存文件型运行资源。
安装器读取根配置并按所有权合并到 `<workspace>/.opencode/opencode.jsonc`，receipt 位于
`<workspace>/.opencode/.expert-installs/<slug>.json`。旧 `.mobilework-engine` 只允许出现在
迁移检测、禁止路径或历史说明中。

## 结构化 findings

schema v2 的 finding 原生提供 `code`、`severity`、`phase`、`path`、`location`、
`rootCause`、`remediation` 和脱敏 `evidence`。尚未迁移的旧校验点才允许 catalog 分类；
已知 fixture 不得退化为无意义的通用错误码。`--schema-version 1` 只适配同一结果，不重新运行。

## Gate 与证据等级

Gate 固定为 `archive`、`contract`、`portability`、`install`、`configLoad`，取值只有
`passed`、`failed`、`blocked`、`not-run`。`evidenceLevel` 只有 `invalid`、`valid`、
`installable`、`config-loadable`。Runtime 状态独立为 `not-tested`、`blocked`、`verified`。
静态校验、安装或 pure config 不能描述成真实 Runtime 会话已加载。

历史 `ValidationResult` provenance 记录 skill 路径与过滤后 hash、合同和 finding catalog 版本、
Python、目标 OpenCode 来源、输入 hash、安全阈值及 UTC 时间。已迁移的 install/trusted-config
envelope 改为记录该操作实际验证的 target、package snapshot、contract-3 receipt、projection、
owned state，以及 pure config 的显式 sidecar 路径、观测 hash/version；尚未迁移字段不得伪造为
已验证。任何入口都不得记录 secret 或完整凭据。

## 可信 sidecar

只对与当前 manager contract 3、supplied package、resolved target 和 captured runtime 一致的受控
安装使用
`verify_trusted_config.py --package-dir <package> --workspace <workspace> --sidecar <explicit-path>`，
并显式提供 `--target-opencode-version` 或 `--host-contract`。脚本先验证安全包快照、contract 3
receipt、owned state、六项证据和 workspace 投影。config、package.json、receipt 与 owned files
先由同一受保护 capture 绑定 state hash，再以权限受限文件物化到私有临时 runtime，并在那里
重新验证 receipt、ownership 与 projection；任一门失败都不得启动 sidecar。通过后才隔离
`HOME`、XDG 与 ambient `OPENCODE_*`，执行 `--version` 和读取该私有 runtime 的
`debug config --pure`。显式 sidecar 通过 `O_NOFOLLOW` 打开；同一 descriptor 的字节在计算 hash
时复制到 0700 私有可执行文件，实际 `--version` 与 `debug config --pure` 只执行该副本，因此记录
的 `sidecarSha256` 绑定实际执行字节。resolved config 必须包含全部 projected values，最后再复核
原 workspace state hash 与 owned state。脚本不启动服务、不运行会话。不得自动搜索 sidecar，也
不得把 ambient 系统 CLI 冒充 caller 明确核验的锁定产物。目标版本冲突时停止。手写配置和
contract 1/2 receipt 不能
提升为 `config-loadable`。`--sidecar` 的可信来源由 caller 明确保证；Candidate 记录实际内容 hash，
但隔离测试本身不证明发布者真实性。

## 需求发现合同

`scripts/manager-contract.json.requirementsDiscovery` schema 14 是 question ledger 字段、状态、提问渠道、
业务确认卡区段、轮次/决定预算、唯一例外轮、确认前禁用副作用、任务到 feature 映射及
`full-card-first` 响应顺序的机器可读真源。一般业务模板与领域内容保留在
`requirements-discovery.md`。创建位置门是有意的窄例外：其宿主 API payload、固定用户文案、label
映射、fallback 和零副作用集合都属于安全边界，必须完整保存在 JSON 合同；Python validator 和回归
测试是该 JSON 的严格 enforcement mirror，不是第二个可独立编辑的来源。`roleAutonomySelection`
规定新建和结构性修改逐角色选择低、较低、中、较高、高，保存内部值并让角色自主度成为权限唯一
基线；只读诊断、校验、安装和打包排除在强制选择之外。旧角色在这些只读/安装路径临时按中投影并
产生 `LEGACY_ROLE_AUTONOMY_DEFAULTED`，结构性修改前必须补齐。任何字段变化必须在同一
提交中同步 JSON、validator、SDK contract test、Reference 与 Eval，直至宿主提供可直接消费 JSON
合同的统一 adapter 后再删除镜像校验。
Follow-up owner 是 `apps/desktop/electron/mobilework` 的 Skill Runtime/host-adapter 维护者；对应任务是让
宿主 adapter 直接消费 `creationTargetSelection` JSON 并删除 Python/测试中的逐字段 enforcement
mirror。该任务以统一 adapter 成为 MobileWork 与 OpenCode 固定 sidecar 的唯一提问入口为完成条件。

顶层 `agentSkillsSpecification` 是 Skill 规范来源合同：官方页面
`https://agentskills.io/specification` 权威，官方仓库快照固定为
`69ef37e9424c0a7ea9dd2293b559e43ec8176379`，`skills-ref` 与 quick validator 只作
cross-check oracle。官方强制项失败必须阻断；500 行、渐进披露和浅层引用等建议项只报告 warning。
CI 和 `test_official_skills_ref.py` 从此对象读取快照 SHA，不能在各处维护第二份常量；validator 与
页面冲突时以页面为准并记录差异回归。MobileWork 可以增加更严格的安全、路径、语法和便携性门，
但不得接受官方页面判定无效的 Skill。

顶层 `expertRuntimeProjection` 是专家 command 与 Agent 运行字段的机器真源。所有 command 的
`agent` 固定指向唯一 `mode: all` 智能体，`subtask` 固定为 `true`。Agent 步数正式字段只有
`steps`；`max_turns`、`maxTurns` 仅作旧 manifest 输入兼容，`maxSteps` 已弃用并拒绝。新生成包
拒绝 `temperature`、`top_p`（包括藏入 `options` 的同名键）；旧包只在未结构性修改时兼容读取并
报告 warning。generator、validator、Reference 与 Eval 必须消费或严格镜像该对象，不能各自扩展。

`requirementsDiscovery.capabilityImplementationMapping` 是能力资源动态选型真源，
`defaultMode` 固定为 `manager-selects-minimal-fit`，`rolePresenceCreatesResource` 固定为 `false`。
管理器可从用户目标、角色职责、流程、质量要求或可信资料提出候选，但职责、流程和质量门只能是
候选证据，不能直接投影资源。每项候选必须有稳定业务名称、可观察运行行为和可信 provenance；
不得发明业务规则、阈值、外部读写或副作用。结果为 `none`、`skill`、`custom-tool` 或
`opencode-plugin`：Skill 承载可复用方法、清单、SOP、指导材料和 Python/Shell 脚本包；custom tool
是 Agent 主动调用的确定性 JS/TS；Plugin 用于事件监听、工具执行前后拦截或运行时行为修改，并且
是 package-wide 资源；外部系统访问继续使用 MCP。

该映射优先选择运行权限最小的形态；同一运行职责只生成一个资源，不同且均已确认的运行职责才
能组合。同一能力只创建一份，多角色通过完整名称或所有权引用。Skill 使用语义 kebab-case，不
强制专家或角色前缀；custom tool 和本地 Plugin 使用包 slug 命名空间。npm Plugin 必须来自可信、
真实存在且精确锁定版本的包，不能虚构名称或版本。没有适合固化的能力时，顶层和角色 `skills[]`
为空或省略、保留空 `.opencode/skills/`、业务 `SKILL.md` 为零，且不生成 tool、Plugin 或
`opencode.json.plugin`。旧 purpose schema 的前缀与内部 `<slug>-reference-<alias>` fallback 继续
保留，不能把业务 Skill 的语义命名策略反向套到兼容资源。

业务确认卡确认能力名称、使用范围、触发或调用方式、输入输出、可见副作用、权限/成本/运行前提、
质量门和当前实现状态。用户委托管理器选择技术载体；映射完全落在已确认边界内时不增加技术类型
确认。若映射发现新的自动触发、外写、联网、权限、依赖、成本或 Runtime 前提，
`materialMappingChangeAction` 使旧确认失效并执行 `full-card-first`，此前零写入。整卡确认只授权
生成当前专家包资源，不授权安装、启用、联网下载、外部连接、发布或执行生成代码。生成后的业务
Agent 在普通运行中不得修改专家包。

`decisionIntroduction` 在依赖收敛和 question frontier 之前限定何时可以新增 material decision。单纯“付费推理”、用户技术词或假想未来
外部能力不足以派生新的外发问题；需要用户明确现在决定该边界，或可信证据证明具体当前候选/执行
路径会外发数据。没有具体路径且相关绑定已延期或阻塞时，合同只投影禁止联网与外发的执行守卫到
现有 blocker。该守卫只保护 network/data-egress，不计提问预算、不阻止 design-only 整卡确认、
不新增 `open/asked` 决定，也不把守卫当作用户授权；具体路径被分类并获明确授权或证明无外发后才解除。

`decisionIdentity` 按会话内同一语义选择归一决定；同一依据来源的“是否存在、具体是哪份、请提供”
不能拆成新 id。决定第一次提问后保持 `asked`，`asked_via` 写定，后续只作为“等待答复”携带，
不能退回 `open`、换措辞重问或改用另一渠道。新证据更新原决定。

`technicalMappingReturn` 适用于任何 material-impact 决定的新增、改变或状态更新，不限于技术映射
第一次发现。其 `requiredSequence` 先记录或更新决定、使旧确认失效，再对当前决定状态执行依赖
收敛，然后把收敛结果（含未改变的 confirmed 与已问未答的 asked 决定）合并进完整业务卡后渲染。`responseGate.name` 为
`full-card-first`：失效说明和完整八区段卡必须在同一 assistant 回复中作为第一块内容；在它们之前
禁止开发确认卡、开发细节、架构或技术绑定、阻塞摘要及实现选项，当前整卡确认前延后开发细节。
`stateReconciliation` 定义 dependencies 为“当前决定列出直接前提 id”，沿 prerequisite → dependent
做传递可达闭包。`blocked_by` 排序去重聚合根 blocker；显式不可用根的集合包含自身 id，且只有新
用户事实或可信资料能清除该自引用 blocker。`resume_status` 在第一次从 open/asked 进入任意阻塞时
保存；每次尾随动作前从完整 ledger 全图重算。只有没有可达 blocker 的派生决定保持或恢复 pending；
多前提决定须等全部 blocker 解除且图有效后才恢复。未知引用与环产生稳定 finding，阻塞受影响待决子图，并禁止整卡
确认和生成。该动作保存 decision id、source、用户授权、asked channel 与预算历史，恢复时继续原决定而不新建、重复计费、重问或改变解决责任。
`dependencySemantics` 进一步限定这张图只表达选择收敛：只有用户显式延期，或可信证据证明前提会
改变候选集合或比较，才创建边。多个决定共用同一个执行权限、安全或成本门不产生依赖；这类未决项
保持独立 material decision，并由 `materialDecisionExecutionGate` 统一阻止生成与执行，却不抑制其他
question-ready 根。该执行门的阻塞状态必须与 pending states 加 `blocked` 一致，且必须要求当前整卡确认。
`stateTransitionSemantics` 要求负向前提回答只解决该前提、保留尚未解决的原下游；显式最终拒绝转为
blocked/superseded，不能被新候选自动重开。只有用户明确给出未来解决条件的延期，才保留并在条件
满足后恢复同一待决决定；权限和目标都不能由候选证据隐式改变。
`questionSelection` 在全图校验和 blocker closure 之后另算 question-ready 依赖前沿：只有状态为 open
且全部传递前提为 answered/proposed/confirmed 的决定可首次提问；前提仍为 open/asked 的下游保持
pending，但不得提问或消耗预算。`frontierPrecedence` 要求 blocker 恢复后重算，并在完整卡后的同一
assistant turn 处理 question-ready open；整卡确认和开发细节边界都不能继续延后它。`businessCandidateAction`
把可信候选的业务选择放在完整卡后、整卡确认前；`implementationBindingAction` 只延后 provider ID、
URL、配置、凭据、字段映射等实现绑定。`candidateEvidence` 要求候选具有稳定业务标签、会影响决定的
可观察差异和可信 provenance；证据不足时保持选择 pending，只询问缺失的比较证据，且没有 Runtime
证据时禁止声称候选已在 Runtime 验证。`resolutionRouting` 不新增 ledger 字段，而是结合现有 source
和用户明确授权处理 open 决定：用户保留选择权时提问，明确委托时用充分可信证据提出候选，可信资料
明确决定时带 provenance 派生；阻塞与恢复保留原 id、source、授权和预算。`readyBatchAction` 要求在
轮次额度内处理所有互不依赖的 ready open，`unrelatedAskedAction` 要求无关 asked 只携带等待而不能
压住其他根；
存在 pending 而当前没有 question-ready open 时仍不请求整卡确认。图有效且收敛后若仍有 open/asked，
只处理前沿中的 open；没有待决项时（包括剩余决定为 blocked）仅请求一次 design-only 当前整卡确认。
解除阻塞并重新确认前继续禁止生成。

`businessStandards.decisionPairing` 把 `authority-source` 与 `executable-rule-value` 定义为两个
`decision_id`。两者都 question-ready 且独立时同轮分别提问；明确由来源定义规则值时按依赖顺序分轮。
跨决定解决必须有显式证据：只提供制度名称或版本不会自动解决可执行规则值，也不得据此推断默认
阈值。`authorityAnswerWithoutComputableRule` 要求来源回答后若材料仍无可计算规则，来源保持 answered，
原规则值保持 open/asked；重算前沿后只询问获授权的显式数值、比例、公式或可计算规则，不重问来源、
不请求整卡确认，也不改变目标。`presentationBoundary` 把业务卡、provenance 和当前整卡确认前的卡后提问统一限制为业务展示面：
question-ready 的可信业务候选可使用稳定业务标签和可观察差异，但内部权限/自主度枚举、机器标识
以及 provider ID、URL、配置、凭据、字段映射等实现绑定细节都延后。逐角色选择权限自主度时显示
低、较低、中、较高、高及其可观察影响，是翻译后等级标签的唯一默认例外。业务卡仍须
写清决策权、固定与可选步骤、确认点、副作用以及停止/返工/升级条件。用户已经给出的技术词只可作为
provenance 事实保留，不得借此提前展开实现细节。`capabilityDisclosure.businessCard` 必须与
`capabilityImplementationMapping.authorization.cardMustConfirm` 完全一致，写清能力名称、使用范围、
触发/调用、输入输出、可见副作用、权限/成本/运行前提、质量门和实现状态。Skill、Custom Tool、
Plugin 的机器标识与技术载体类型只进入当前整卡确认后的开发细节；边界不变时由管理器选型，无需追加
一次技术类型确认。
`presentationBoundary.externalEntryDiscovery` 将“是否存在真实、可用且经核验的业务入口”和入口的
实现渠道分开：当前整卡确认前，Agent 编写的问题只询问业务入口是否存在，不枚举 Connector、MCP、
URL 或启动命令；用户已经提供的技术词仍只能作为 provenance 事实保留。具体渠道选择和参数收集必须
同时等待可信的真实入口证据与当前整卡确认。入口不存在或证据不足时，明确标记外部入口集成仍需
交付，并阻止绑定和执行；没有 Runtime 证据时同样不得声称入口已在 Runtime 验证。
`executionLayerMapping` 要求先判定谁拥有固定步骤：脚本拥有全部已确认步骤时，业务层只说明“脚本
执行、无额外 Agent 编排”，按需开发细节才写 `scripted-only`；只有另行确认的 Agent SOP 或分支规则
才允许独立 `fixed` 编排层。`businessCardSections` 仍恰好是八个业务区段，`information-sources`
由 `businessCardAppendices` 单独声明为 provenance 附录。上述政策都属于
`managerContractSha256` 的 hash domain。

`questionChannelEvidence` 明确区分 Agent 记账与宿主证据：`asked_via` 只说明会话内计划使用的渠道；
“每个决定只走一个提问渠道”必须由覆盖完整的 host question-channel ledger 证明。宿主未提供完整事件
时结果只能是 `not-verified`，Skill 文本或 assistant 自述都不能替代 host evidence。

`creationTargetSelection` 是当前整卡确认后的独立创建位置门，只适用于新建单专家、专家团和资料转
新专家。它不进入 question ledger、不消耗发现轮次或决定预算，并绑定当前整卡确认版本；设计变化
同时使旧确认和旧位置选择失效。优先使用宿主的 `AskUserQuestion`、`question` 或其他等价单选+自定义
输入能力；OpenCode/MobileWork 请求形状与固定 `QuestionInfo` 一致，`question.replied` 从
`properties.answers[0][0]` 取唯一 label 后映射 target。所有等价提问能力均不可用时，必须在正文提出同一个问题、
两个固定选项和其他绝对父目录入口并等待回复，不能默认选择。唯一有效回答前环境检查、进程、文件
写入、联网/数据外发、Plugin/MCP、权限扩大、generator 和 validation 均被阻止；多选、自定义父目录
无效与最终目标逃逸分别返回 `CREATION_TARGET_ANSWER_AMBIGUOUS`、
`CREATION_TARGET_PATH_INVALID` 与 `TARGET_OUTSIDE_ROOT`。自定义值是已存在的绝对父
目录，最终目标为 `<parent>/<slug>`，根、symlink、Windows reparse point、特殊文件和路径逃逸均被
拒绝，覆盖已有 slug 仍需独立 `--force` 确认。`ExecutionContext` version 2 以 `targetMode` 记录显式
选择，同时保留 `workspaceRoot`、`outputRoot` 和 `pathSource`；没有显式 target 时保持旧宿主解析语义。
`my-experts` 是兼容接口名，不是旧目录名：MobileWork 使用宿主注入的
`MOBILEWORK_MY_EXPERTS_DIR`，独立宿主默认使用 `<home>/.mobilework/experts/personal`；旧
`<home>/.mobilework/my-experts` 仅用于 MobileWork 迁移，不接受新建写入。

`reservedCommands` 是命令保留名真源。生成器和 validator 从经验证的 host capability 解释命令注册表，
缺少该证据时至少拒绝合同列出的已知 server built-in；不得在 Python 模块中维护另一份名称副本或把
目标 OpenCode 版本硬编码进 manager contract。

`findingCatalog` 是 legacy validation message 到稳定 finding code、phase、root cause 和 remediation 的
唯一映射真源；`finding_catalog.py` 只编译并消费该对象。fallback code 同样来自合同，Python 不再
维护第二份 code/规则副本。

`trustedConversionAdapter` 当前只定义 Desktop host adapter 边界，`implementationStatus` 保持
`defined`。输入必须绑定源文件 SHA-256 与类型；输出必须绑定转换产物 SHA-256、页/表/幻灯片锚点，
以及 provider id/version/hash。Manager 不直接执行 `parse-document`、`uv`、包内脚本或任意
LibreOffice 路径；没有通过 provenance 和 Runtime 证据验证的 Desktop adapter 时，稳定返回
`conversion-required`，不得声称已转换或已验证。

Ledger 只存在于当前需求发现会话，不进入 manifest、包、receipt、投影或 ZIP。同一语义决定在
会话内只能有一个 `decision_id`，也只能经交互工具或正文之一提问。技术映射中的业务选择必须
追溯到 `answered`、`proposed` 或 `confirmed` 决定；确定性的 hash、路径、receipt、schema、sidecar
和投影字段追溯机器合同或已确认技术规则，不虚构用户决定。映射发现新决定时返回澄清；最终整卡
确认把卡内当前业务决定提升为 `confirmed`，且不计入预算。纯咨询和设计确认映射为空 feature，
因此不得启动 `check_environment.py`；确认后的执行或直接维护通道才按任务选择 feature。

这是 Agent 行为合同，不是可信用户确认凭证。`create_expert.py` 不验证聊天历史；模型自行写出的
确认字段不能证明用户操作。Desktop 若未来提供强门，必须由 host attestation 绑定 user event、
proposal/session id 和 manifest hash，不能复用 session-only ledger 冒充。

`check_environment.py --feature all` 包含 `config-load`，没有显式 `--sidecar` 时必须在 module、
command、execution-context 检查前返回 schema v2 `ENVIRONMENT_SIDECAR_REQUIRED` 和 exit 2。
显式 sidecar 的 preflight 只检查路径身份和可执行属性，不执行 sidecar。

## 退出码

以下退出码是 schema v2 全入口迁移完成后的目标合同。当前 Candidate 只在已经集中迁移的入口
执行该映射；未迁移入口不得据此宣称统一 CLI 合同已完成。

集中 CLI emitter 在写入真实 `sys.stdout` 或 `sys.stderr` 前按机器合同将流重新配置为 UTF-8，
使 human/JSON 输出及脱敏诊断不依赖 Windows ambient code page。测试或嵌入调用方显式传入的
`TextIO` 不会被重配置，其编码与解码仍由该调用方负责。CI 的 `PYTHONUTF8=1` 固定文件读取和
子进程默认编码，是宿主环境门，不替代 emitter 的标准流合同。

- `0`：请求的 gate 通过；
- `1`：合同或安全 finding；
- `2`：参数、环境或版本合同错误；
- `3`：管理器内部异常；
- `4`：请求了策略禁止的运行操作。

warning 不使进程失败。
