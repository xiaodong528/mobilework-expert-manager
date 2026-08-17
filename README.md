# MobileWork Expert Manager

把业务目标转换成可确认、可生成、可验证的 MobileWork 专家或专家团。管理器先用白话确认角色、
资料、流程、能力、权限和运行前提；完整业务方案确认后，再按最小运行权限选择实现方式。新建专家
还会单独确认创建位置，未经确认不写入专家包。

本仓库同时发布 Claude Code 与 Codex 插件，并通过同名的 `mobilework-tools` Git marketplace
提供安装。两套宿主共用 `skills/mobilework-expert-manager/`，但各自读取独立的 Manifest 和市场清单。

## 插件接口

| 项目 | 值 |
|---|---|
| 插件名 | `mobilework-expert-manager` |
| Marketplace | `mobilework-tools` |
| 当前版本 | `0.7.0` |
| Skill | `mobilework-expert-manager` |
| Claude Code 调用 | `/mobilework-expert-manager:mobilework-expert-manager` |
| Codex 调用 | `$mobilework-expert-manager:mobilework-expert-manager` |

## 0.7.0 更新重点

- **角色权限独立**：单专家、团长和每位团员分别选择低、较低、中、较高或高；角色自主度是静态
  权限的唯一基线，流程调整不会隐式提权。
- **主专家可复用**：单专家和专家团团长使用 `mode: all`，既可直接使用，也可由其他 Agent 调用；
  团员继续使用 `mode: subagent`。
- **能力按需落地**：确认业务能力后，管理器才在无资源、Skill、custom tool、Plugin 或 MCP 中
  选择最小适配方案，不再按角色、职责或流程数量机械生成资源。
- **创建位置强确认**：完整业务方案确认后，仍需单独选择“我的专家”“当前工作空间”或安全的
  自定义父目录；设计变化后必须重新选择。
- **命令入口收敛**：Workflow command 和自定义 command 都固定路由到唯一 `mode: all` Agent，
  并以 `subtask: true` 运行，专家团 command 不绕过团长直达团员。
- **规范交叉验证更新**：Agent Skills 官方校验固定到仓库声明的 `skills-ref` 提交，格式硬约束阻断，
  渐进披露等建议项只报告 warning。

## 适用场景

| 需求 | 管理器提供的结果 |
|---|---|
| 从业务目标新建专家 | 需求转译、完整业务确认、创建位置选择、生成与校验 |
| 设计多角色专家团 | 团长与团员职责、逐角色自主度、委派、验收、返工和集成边界 |
| 修改或迁移旧专家 | 兼容诊断、结构迁移预览、派生物重建和回归校验 |
| 导入资料或 Skill | 外部内容静态检查、原字节保留、角色分配和完整性 hash |
| 校验、安装或打包 | 结构化 findings、可移植性扫描、安装 receipt、漂移保护和干净复验 |
| 排查陌生 ZIP 或目录 | 只做受限静态诊断，不执行包内脚本、Plugin、MCP 或生命周期逻辑 |

## 通过插件市场安装

“添加市场”和“安装插件”是两步：第一步让宿主认识 `mobilework-tools`，第二步才把
`mobilework-expert-manager` 安装到本机。本仓库提供的是由 `xiaodong528` 维护的 Git marketplace，
不是 OpenAI 或 Anthropic 官方市场。安装前请先检查仓库内容和权限边界。

### Claude Code

```bash
claude plugin marketplace add xiaodong528/mobilework-expert-manager
claude plugin install mobilework-expert-manager@mobilework-tools
```

安装后启动新会话，或在已有会话中执行 `/reload-plugins`，再调用：

```text
/mobilework-expert-manager:mobilework-expert-manager
```

更新已安装版本：

```bash
claude plugin marketplace update mobilework-tools
claude plugin update mobilework-expert-manager@mobilework-tools
```

更新完成后执行 `/reload-plugins`，或重新启动 Claude Code 会话。

### Codex

```bash
codex plugin marketplace add xiaodong528/mobilework-expert-manager
codex plugin add mobilework-expert-manager@mobilework-tools
```

安装后启动新任务，再使用：

```text
$mobilework-expert-manager:mobilework-expert-manager
```

本仓库 CI 固定使用的 Codex `0.145.0` 没有单独的 `plugin update` 命令。先刷新 marketplace，
再重新安装：

```bash
codex plugin marketplace upgrade mobilework-tools
codex plugin remove mobilework-expert-manager@mobilework-tools
codex plugin add mobilework-expert-manager@mobilework-tools
```

然后启动新任务，让新的插件缓存和 Skill 生效。

## 两套市场文件分别负责什么

| 宿主 | 市场清单 | 插件 Manifest | 安装后的调用方式 |
|---|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` | `.claude-plugin/plugin.json` | `/mobilework-expert-manager:mobilework-expert-manager` |
| Codex | `.agents/plugins/marketplace.json` | `.codex-plugin/plugin.json` | `$mobilework-expert-manager:mobilework-expert-manager` |

两个 marketplace 都叫 `mobilework-tools`，都指向本仓库根目录的插件。版本号必须同时更新两份
`plugin.json`；实际技能内容只维护一份，放在 `skills/mobilework-expert-manager/`。

## 在其他 Marketplace 中引用

Claude Code 团队市场可以在 `.claude-plugin/marketplace.json` 的 `plugins` 数组中加入：

```json
{
  "name": "mobilework-expert-manager",
  "source": {
    "source": "github",
    "repo": "xiaodong528/mobilework-expert-manager"
  }
}
```

用户添加本组 marketplace 后，使用本组市场名安装：

```text
/plugin install mobilework-expert-manager@<marketplace-name>
/reload-plugins
```

开发者也可以直接在仓库根目录加载插件：

```bash
claude --plugin-dir .
```

Codex 团队市场可以在 `.agents/plugins/marketplace.json` 的 `plugins` 数组中加入：

```json
{
  "name": "mobilework-expert-manager",
  "source": {
    "source": "url",
    "url": "https://github.com/xiaodong528/mobilework-expert-manager.git"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Developer Tools"
}
```

这只是把插件加入团队自己的市场目录，不代表它进入了 OpenAI 的公共插件目录。

## 核心能力

- 以 `expert.json` 作为专家包结构与资源所有权的唯一事实源。
- 先把模糊业务需求整理成可观察、可确认的角色、资料、流程、能力和权限边界；整卡确认后才映射为
  Reference、Skill、Instruction、Workflow、custom tool、Plugin、MCP 或 Agent/Team。
- 支持单专家和专家团，以及角色路由、交接、验收、返工与最终集成。
- Reference 支持包内本地资料和 Git 仓库；Git 地址、分支、说明和 `hidden` 状态进入统一合同，
  不在专家包中保存凭据。
- 支持把 Reference 和角色级 Instruction 分配给指定角色，并把绑定关系写入安装 receipt 供审计；
  这些绑定是路由信息，不冒充系统级访问控制。
- 支持串行、并行和团长协调 Workflow，并为团长和每位团员分别确认五档角色自主度；角色自主度
  是静态权限的唯一基线，Workflow/Phase 自主度只描述流程决定权与验收边界。
- 根据已确认的运行职责选择无资源、Skill、custom tool 或 Plugin，不按角色或职责数量机械生成。
- 新专家使用统一顶层 `skills[]`，角色通过完整技能名引用拥有的技能。
- 支持把技能目录或 ZIP 原字节导入 `.opencode/skills/<name>/`，并明确分配给一个、多个或全部成员。
- 支持 Skills、MCP、custom tools、commands、plugins、references、instructions 与 LSP。
- 支持外部 ZIP、附件和未知目录的无执行静态诊断。
- 支持结构化 findings、root cause、证据 gate、可信 sidecar 和 OpenCode pure config 验证。
- 对未知目录和 ZIP 使用单次安全快照，限制条目数、大小、深度与路径长度，并拒绝链接、设备和
  扫描期间发生变化的输入。
- 所有 CLI 共享 schema v2、稳定退出码和双层输出脱敏；Plugin 依赖共用 npm spec 解析合同。
- 支持旧包迁移规划、供应链审计、Bundle manifest 和 Bundle 校验。
- 支持生成、校验、可移植性扫描、打包、安装、漂移预览、受确认的漂移丢弃、备份恢复和
  contract 3 receipt 读回。
- Python 与 Desktop Runtime 共用带 owner token、心跳和 quarantine 竞争规则的锁协议。
- 支持专家包本地 Git 初始化、SemVer 建议及用户确认后的本地版本发布。

## 可以创建什么

| 类型 | `expert.json` 结构 | 生成结果 | 协作方式 |
|---|---|---|---|
| 单专家 | `type: expert`，一个 `agent` | 一个 `mode: all` Agent | 专家独立执行职责内任务，不包含团队委派规则 |
| 专家团 | `type: team`，一个 `primary_agent` 和至少一个 `subagents[]` 成员 | 一个 `mode: all` 团长及多个 `mode: subagent` 团员 | 团长路由任务、验收、要求返工并集成结果；团员只完成被委派的专业任务 |

专家团通过结构化 `task` 路由把任务交给指定团员。首次委派需要包含上游输入、预期产物、验收标准
和证据要求；验收失败时，团长使用同一个 `task_id` 继续返工。团长不模拟团员的专业产出，团员
也不绕过团长直接交付最终结果。

## 生成后的专家包结构

```text
<slug>/
├── expert.json                         # 唯一结构与资源所有权事实源
├── opencode.json                       # 生成的运行时配置
├── README.md                           # 生成的专家包使用说明
├── .env.example                       # 可选；只有环境变量引用，不含真实值
├── .gitignore
├── avatars/
└── .opencode/
    ├── agents/                         # 生成的 Agent Markdown
    ├── skills/                         # 统一技能池及导入的完整 Skill
    ├── commands/                       # 可选；Workflow 或自定义命令
    ├── tools/                          # 可选；custom tools
    ├── plugins/                        # 可选；本地 plugins
    ├── references/<slug>/<alias>/      # 可选；包内引用资料
    ├── instructions/<slug>/            # 可选；workspace 全局指令
    └── package.json                    # 可选；只声明依赖
```

能力、角色、展示字段、Workflow、权限或资源发生变化时，应先修改 `expert.json` 和其中声明的真实
资源，再用生成器重建；不要直接修补 `opencode.json`、README、Agent 或 Skill 等派生文件。
顶层 `skills[]` 记录完整技能名、来源和编辑策略，角色 `skills[]` 只引用完整技能名。
`package_resources[]` 声明技能目录内包括 `SKILL.md` 在内的全部文件及 SHA-256，生成器会校验
真实文件、归属和完整性。

专家包不生成根目录 `AGENTS.md`、`references/` 或 `instructions/`，也不包含真实 `.env`、
`.mobilework-engine`、`node_modules`、缓存或日志。可信源目录可由管理器初始化根 `.git/`，
但 `.git/**` 永远不会进入 ZIP、Bundle、安装投影或 package hash。专家执行时产生的报告、表格、
图片等业务产物应写入 workspace 的业务目录，不得写入 `.opencode/` 或专家安装目录。

## 功能与配置

| 能力 | 在 `expert.json` 中声明 | 生成或执行效果 |
|---|---|---|
| 展示信息 | `name`、`summary`、`description`、`tags`、`quick_prompts`、头像等 | 生成专家包 README、公开信息和可解析的本地头像引用 |
| 角色与路由 | `agent`，或 `primary_agent` + `subagents[]`；每个角色可声明职责、触发条件、质量门和交接合同 | 生成角色 Markdown、运行时 Agent 配置和专家团委派边界 |
| Skills | 顶层 `skills[]` 与角色 `skills[]` 完整名称引用 | 只复制声明技能并从角色引用派生 `permission.skill`；不生成通用/专属前缀 |
| Workflow | `primary`、`serial`、`parallel` phases，及可选 Workflow command | 生成可执行流程说明；可将稳定 Workflow 暴露为 `.opencode/commands/<name>.md` |
| 自主度 | 每个角色使用 `scripted`、`fixed`、`bounded`、`guided`、`adaptive`；Workflow/Phase 可另行声明流程自主度 | 角色自主度生成静态权限基线；Workflow/Phase 只描述流程决定权、确认点与验收边界 |
| 执行器 | `skill-script`、`custom-tool`、`mcp-tool`、`programming-tool`、`agent` | 校验执行器引用、真实资源、参与角色和权限所有权，不从职责文本猜测能力 |
| 运行参数 | `steps`，以及可选 `model`、`variant`、`hidden`、`options` | 精确投影到 Agent Markdown 和 `opencode.json`；采样行为继承模型或 provider，不声明 `temperature`、`top_p` |
| 权限 | 角色自主度、execution、Skill/MCP/task/custom tool 所有权及显式 `permission` | 生成最小权限；禁止无条件 `bash: {"*": "allow"}`，手写权限只能收紧基线 |
| 运行时扩展 | `mcp_servers` 与 `runtime_extensions` 中的 commands、tools、plugins、Local/Git references、workspace instructions、role instructions、LSP | 生成 `.opencode/**` 资源、`opencode.json` 配置和可选 `.env.example`；角色绑定写入 Agent Markdown 与安装 receipt |
| 安装与回滚 | 包根 `opencode.json` 和所有声明资源 | 投影到 `<workspace>/.opencode/`，按资源归属预检冲突、原子安装、失败回滚并写入 receipt |
| 本地版本 | 包内容的累计 diff 与用户确认的 SemVer | 初始化精确包根 Git；只在用户确认后本地 commit/tag，永不自动配置 remote |

`steps` 是当前 OpenCode 正式步数字段。历史输入 `max_turns`、`maxTurns` 只用于读取旧 manifest，
不会写入 Agent Markdown、`opencode.json` 或 README；已弃用的 `maxSteps` 不属于当前合同。
`hidden` 只允许用于专家团成员。新专家不声明 `temperature` 或 `top_p`，采样行为继承模型或
provider；`options` 也不能绕过该边界。

## 创建与交付流程

1. **需求澄清**：读取用户目标、资料和已有包，区分已知事实、候选设计和未确认项。
2. **设计确认**：新建、资料转化或结构性修改时，先确认角色、各角色自主度、Workflow、能力资源、
   权限及运行前提。
3. **创建位置确认**：新建专家在当前业务卡确认后，单独选择“我的专家”“当前工作空间”或一个
   已存在的绝对父目录；设计变化后必须重新确认位置。
4. **生成或重建**：以 `expert.json` 和声明资源为输入运行 `create_expert.py`；覆盖已有包时在 sibling
   staging 中完整重建，校验通过后才原子替换。
5. **技能导入与分配**：先用 `diagnose_skill.py` 静态检查目录或 ZIP，再用 `import_skill.py` 原字节
   导入。单专家自动分配；专家团必须指定 `--assign-to`，或用 `--all-members` 分配给团长和全部团员。
6. **静态验证**：运行 `validate_expert.py`，检查 manifest、派生文件、角色、权限、Workflow、
   runtime config 与资源归属的一致性。
7. **可移植性扫描**：运行 `scan_portable_artifacts.py`，排查绝对路径、secret、symlink、缓存和
   未声明资源。
8. **打包与干净复验**：运行 `package_expert.py`，完成 ZIP 结构与 CRC 检查、干净解压、再次校验
   和可移植性扫描后才发布 ZIP。
9. **安装与读回**：运行 `install_expert.py`，投影到 `<workspace>/.opencode/`，再读回
   `.opencode/opencode.jsonc`、安装资源和 `.opencode/.expert-installs/<slug>.json` receipt。

### 创建位置如何解析

| 选择 | 实际位置 |
|---|---|
| 我的专家（MobileWork 正式版） | 宿主注入的 `MOBILEWORK_MY_EXPERTS_DIR`；默认 `~/.mobilework/experts/personal` |
| 我的专家（源码开发版） | 未自定义 Electron userData 时，默认 `~/.mobilework/electron-dev/openwork-dev-data/home/.mobilework/experts/personal` |
| 我的专家（独立宿主） | `<home>/.mobilework/experts/personal` |
| 当前工作空间 | 当前 workspace 下的 `<slug>/` |
| 其他路径 | 用户选择的已存在绝对父目录下的 `<slug>/` |

旧 `~/.mobilework/my-experts` 只用于 MobileWork 一次性迁移，不是新建目标。自定义父目录必须已存在，
且不能是文件系统根、symlink、Windows reparse point 或特殊文件；已有同名目标仍需另行确认覆盖。

上传技能默认保持 `edit_policy: preserved`。同名同内容复用；同名异内容默认阻止，只有同时提供
`--replace --confirm-managed` 才允许替换，并把编辑策略改为 `managed`。未修改旧专家继续兼容；
发生结构性修改时迁移到统一技能合同。

真实创建或修改完成后，管理器会根据累计 diff 给出 SemVer 建议；只有用户明确确认，才执行包根
本地 commit 和 `vX.Y.Z` tag。静态校验通过只证明 package-valid，安装读回只证明 installed；
pure config 最多证明 config-loadable。没有完成真实 Runtime 调用时，结果必须标记为
`not-tested`，不能宣称专家已经在业务链路中运行成功。

### 验证结果怎样理解

| 结果 | 能证明什么 | 不能证明什么 |
|---|---|---|
| `valid` | 输入、结构或静态合同通过相应检查 | 已安装或已在真实会话运行 |
| `installable` | 包完成生成、校验、打包、干净复验及安装门要求 | OpenCode 已真实加载并执行 |
| `config-loadable` | 可信 sidecar 已读取并核对安装配置 | 业务任务输出正确 |
| Runtime `verified` | 已完成明确记录的真实 Runtime 调用 | 未覆盖的平台、模型或业务场景同样通过 |
| Runtime `not-tested` | 尚无真实 Runtime 证据 | 不能外推为运行成功 |

## 创建请求示例

单专家：

```text
/mobilework-expert-manager:mobilework-expert-manager 请创建一个合同审查专家：
它需要逐条引用合同证据，区分法律风险与商业风险，并输出可执行的修订建议。
使用中档自主度；任何对外写入都需要确认。
```

专家团：

```text
/mobilework-expert-manager:mobilework-expert-manager 请创建一个软件交付专家团：
团长负责编排、验收和最终集成；产品经理、架构师、工程师和测试工程师作为团员。
需求与架构可并行分析，实现与测试串行衔接；每个阶段都要声明输入、产物、证据和验收标准。
```

插件会先展示候选结构，并只询问会改变职责、角色、流程、能力资源、权限或运行前提的关键缺口。
确认完整设计并单独选择创建位置后，才会生成或结构性修改专家包。

## 插件仓库结构

```text
.
├── .agents/plugins/
│   └── marketplace.json
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── .codex-plugin/
│   └── plugin.json
├── skills/
│   └── mobilework-expert-manager/
│       ├── CONTEXT.md
│       ├── SKILL.md
│       ├── docs/
│       ├── scripts/
│       ├── references/
│       ├── evals/
│       └── tests/
└── .github/workflows/
    └── validate-plugin.yml
```

## 安全边界

- 外部或未知输入默认只允许静态诊断，不执行其中的 Python、Shell、JavaScript/TypeScript、
  Plugin、custom tool、MCP 或包管理脚本。
- 上传技能未经明确授权不得改写，任何已声明文件的 SHA-256 漂移都会使验证失败。
- 不把静态校验、安装成功或 pure config 加载成功描述为 Runtime 已验证。
- 未经明确确认，不自动 commit、tag、配置 remote 或发布专家包版本。
- `.git`、真实 `.env`、`node_modules`、lockfile、缓存、日志、密钥和个人配置不得进入分发包。

## 本地验证

```bash
python3 /path/to/skill-manager/scripts/quick_validate.py \
  skills/mobilework-expert-manager
claude plugin validate . --strict
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
python3 -m unittest discover \
  -s tests \
  -p 'test_*.py'
python3 -m unittest discover \
  -s skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

CI 使用 Node.js 22、`@anthropic-ai/claude-code@2.1.218`、`@openai/codex@0.145.0`、
Python 3.11、`PyYAML==6.0.3` 和固定提交的官方 `skills-ref` 执行相同校验，并在隔离配置目录中
真实添加本地 marketplace、安装插件和读回 Skill。发布新能力或修复时必须同步升级 Claude Code
与 Codex 两个 `plugin.json` 的 SemVer。

## 详细规范

- [管理器工作流、安全边界与验收要求](skills/mobilework-expert-manager/SKILL.md)
- [`expert.json` 结构、角色和 Workflow](skills/mobilework-expert-manager/references/expert-json-spec.md)
- [Workflow 自主度与执行合同](skills/mobilework-expert-manager/references/workflow-autonomy-spec.md)
- [权限与资源所有权矩阵](skills/mobilework-expert-manager/references/permission-policy-spec.md)
- [运行时扩展与安装投影](skills/mobilework-expert-manager/references/runtime-extensions-spec.md)
- [需求转译与模块推荐](skills/mobilework-expert-manager/references/requirements-discovery.md)
- [可分发专家包、ZIP 与便携性](skills/mobilework-expert-manager/references/portable-package-spec.md)
- [本地 Git 与 SemVer](skills/mobilework-expert-manager/references/source-version-control.md)

## 资料

- [Claude Code 插件开发](https://code.claude.com/docs/en/plugins)
- [Claude Code 插件技术参考](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code 插件市场](https://code.claude.com/docs/en/plugin-marketplaces)
- [OpenAI 插件打包与市场边界](https://developers.openai.com/plugins/build/plugins)
- [OpenCode 官方文档](https://opencode.ai/docs/)

## License

Apache License 2.0，详见 [LICENSE.txt](LICENSE.txt)。
