# MobileWork Expert Manager

用于创建、转换、修改、诊断、校验、安装、打包和版本发布 MobileWork 专家与专家团，并为角色
导入和分配技能的独立 Claude Code 插件。

本仓库只发布 `mobilework-expert-manager` 插件，不提供面向实习分组的 marketplace。各组组长应维护
自己的 marketplace GitHub 仓库，并从本仓库引用公共专家管理插件。

## 插件接口

| 项目 | 值 |
|---|---|
| 插件名 | `mobilework-expert-manager` |
| 当前版本 | `0.3.0` |
| Skill | `mobilework-expert-manager` |
| Skill 调用 | `/mobilework-expert-manager:mobilework-expert-manager` |

## 在 Marketplace 中引用

组长在本组 `.claude-plugin/marketplace.json` 的 `plugins` 数组中加入：

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

本仓库不是 marketplace，因此不要执行
`/plugin marketplace add xiaodong528/mobilework-expert-manager`。

开发者也可以直接在仓库根目录加载插件：

```bash
claude --plugin-dir .
```

## 核心能力

- 以 `expert.json` 作为专家包结构与资源所有权的唯一事实源。
- 支持单专家和专家团，以及角色路由、交接、验收、返工与最终集成。
- 支持串行、并行和团长协调 Workflow，以及 `scripted`、`fixed`、`bounded`、`guided`、
  `adaptive` 五档自主度。
- 新专家使用统一顶层 `skills[]`，角色通过完整技能名引用拥有的技能。
- 支持把技能目录或 ZIP 原字节导入 `.opencode/skills/<name>/`，并明确分配给一个、多个或全部成员。
- 支持 Skills、MCP、custom tools、commands、plugins、references、instructions 与 LSP。
- 支持外部 ZIP、附件和未知目录的无执行静态诊断。
- 支持结构化 findings、root cause、证据 gate、可信 sidecar 和 OpenCode pure config 验证。
- 支持旧包迁移规划、供应链审计、Bundle manifest 和 Bundle 校验。
- 支持生成、校验、可移植性扫描、打包、安装、回滚和 receipt 读回。
- 支持专家包本地 Git 初始化、SemVer 建议及用户确认后的本地版本发布。

## 可以创建什么

| 类型 | `expert.json` 结构 | 生成结果 | 协作方式 |
|---|---|---|---|
| 单专家 | `type: expert`，一个 `agent` | 一个 `mode: primary` Agent | 专家独立执行职责内任务，不包含团队委派规则 |
| 专家团 | `type: team`，一个 `primary_agent` 和至少一个 `subagents[]` 成员 | 一个 `mode: primary` 团长及多个 `mode: subagent` 团员 | 团长路由任务、验收、要求返工并集成结果；团员只完成被委派的专业任务 |

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
| 自主度 | `scripted`、`fixed`、`bounded`、`guided`、`adaptive` | 按 Workflow、Phase、Agent override 的优先级计算执行边界；任何档位都不能降低安全和验收标准 |
| 执行器 | `skill-script`、`custom-tool`、`mcp-tool`、`programming-tool`、`agent` | 校验执行器引用、真实资源、参与角色和权限所有权，不从职责文本猜测能力 |
| 运行参数 | `steps`，以及可选 `model`、`variant`、`temperature`、`top_p`、`hidden`、`options` | 精确投影到 Agent Markdown 和 `opencode.json`；未声明项继承 OpenCode、模型或 provider 默认值 |
| 权限 | Workflow 自主度、execution、Skill/MCP/task/custom tool 所有权及显式 `permission` | 生成最小权限；禁止无条件 `bash: {"*": "allow"}`，显式提权需要 `permission_reason` |
| 运行时扩展 | `mcp_servers` 与 `runtime_extensions` 中的 commands、tools、plugins、references、instructions、LSP | 生成 `.opencode/**` 资源、`opencode.json` 配置和可选 `.env.example` |
| 安装与回滚 | 包根 `opencode.json` 和所有声明资源 | 投影到 `<workspace>/.opencode/`，按资源归属预检冲突、原子安装、失败回滚并写入 receipt |
| 本地版本 | 包内容的累计 diff 与用户确认的 SemVer | 初始化精确包根 Git；只在用户确认后本地 commit/tag，永不自动配置 remote |

`steps` 是当前 OpenCode 正式步数字段。历史输入 `max_turns`、`maxTurns` 只用于读取旧 manifest，
不会写入 Agent Markdown、`opencode.json` 或 README；已弃用的 `maxSteps` 不属于当前合同。
`hidden` 只允许用于专家团成员。`temperature` 和 `top_p` 可以同时声明，但通常只调整其中一个，
以便解释行为变化。

## 创建与交付流程

1. **需求澄清**：读取用户目标、资料和已有包，区分已知事实、候选设计和未确认项。
2. **设计确认**：新建、资料转化或结构性修改时，先确认角色、Workflow、Skills、权限及运行能力。
3. **生成或重建**：以 `expert.json` 和声明资源为输入运行 `create_expert.py`；覆盖已有包时在 sibling
   staging 中完整重建，校验通过后才原子替换。
4. **技能导入与分配**：先用 `diagnose_skill.py` 静态检查目录或 ZIP，再用 `import_skill.py` 原字节
   导入。单专家自动分配；专家团必须指定 `--assign-to`，或用 `--all-members` 分配给团长和全部团员。
5. **静态验证**：运行 `validate_expert.py`，检查 manifest、派生文件、角色、权限、Workflow、
   runtime config 与资源归属的一致性。
6. **可移植性扫描**：运行 `scan_portable_artifacts.py`，排查绝对路径、secret、symlink、缓存和
   未声明资源。
7. **打包与干净复验**：运行 `package_expert.py`，完成 ZIP 结构与 CRC 检查、干净解压、再次校验
   和可移植性扫描后才发布 ZIP。
8. **安装与读回**：运行 `install_expert.py`，投影到 `<workspace>/.opencode/`，再读回
   `.opencode/opencode.jsonc`、安装资源和 `.opencode/.expert-installs/<slug>.json` receipt。

上传技能默认保持 `edit_policy: preserved`。同名同内容复用；同名异内容默认阻止，只有同时提供
`--replace --confirm-managed` 才允许替换，并把编辑策略改为 `managed`。未修改旧专家继续兼容；
发生结构性修改时迁移到统一技能合同。

真实创建或修改完成后，管理器会根据累计 diff 给出 SemVer 建议；只有用户明确确认，才执行包根
本地 commit 和 `vX.Y.Z` tag。静态校验通过只证明 package-valid，安装读回只证明 installed；
pure config 最多证明 config-loadable。没有完成真实 Runtime 调用时，结果必须标记为
`not-tested`，不能宣称专家已经在业务链路中运行成功。

## 创建请求示例

单专家：

```text
/mobilework-expert-manager:mobilework-expert-manager 请创建一个合同审查专家：
它需要逐条引用合同证据，区分法律风险与商业风险，并输出可执行的修订建议。
使用 bounded 自主度；任何对外写入都需要确认。
```

专家团：

```text
/mobilework-expert-manager:mobilework-expert-manager 请创建一个软件交付专家团：
团长负责编排、验收和最终集成；产品经理、架构师、工程师和测试工程师作为团员。
需求与架构可并行分析，实现与测试串行衔接；每个阶段都要声明输入、产物、证据和验收标准。
```

插件会先展示候选结构并询问会改变职责、角色、Workflow、Skill、权限或运行能力的关键缺口。
确认完整设计后才会生成或结构性修改专家包。

## 插件仓库结构

```text
.
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── mobilework-expert-manager/
│       ├── SKILL.md
│       ├── scripts/
│       ├── references/
│       ├── evals/
│       └── tests/
└── .github/workflows/
    └── validate-plugin.yml
```

`skills/mobilework-expert-manager/agents/openai.yaml` 是该 Skill 的既有资源，不是 Claude Code
插件根目录下的 subagent。

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
python3 -m unittest discover \
  -s skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

CI 使用 Node.js 22、`@anthropic-ai/claude-code@2.1.218`、Python 3.11、
`PyYAML==6.0.3` 和固定提交的官方 `skills-ref` 执行相同校验。发布新能力或修复时必须同步升级
`.claude-plugin/plugin.json` 的 SemVer。

## 详细规范

- [管理器工作流、安全边界与验收要求](skills/mobilework-expert-manager/SKILL.md)
- [`expert.json` 结构、角色和 Workflow](skills/mobilework-expert-manager/references/expert-json-spec.md)
- [Workflow 自主度与执行合同](skills/mobilework-expert-manager/references/workflow-autonomy-spec.md)
- [权限与资源所有权矩阵](skills/mobilework-expert-manager/references/permission-policy-spec.md)
- [运行时扩展与安装投影](skills/mobilework-expert-manager/references/runtime-extensions-spec.md)
- [可分发专家包、ZIP 与便携性](skills/mobilework-expert-manager/references/portable-package-spec.md)
- [本地 Git 与 SemVer](skills/mobilework-expert-manager/references/source-version-control.md)

## 资料

- [Claude Code 插件开发](https://code.claude.com/docs/en/plugins)
- [Claude Code 插件技术参考](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code 插件市场](https://code.claude.com/docs/en/plugin-marketplaces)
- [OpenCode 官方文档](https://opencode.ai/docs/)

## License

Apache License 2.0，详见 [LICENSE.txt](LICENSE.txt)。
