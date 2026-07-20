# MobileWork 可分发专家包规范

本文件定义包 allowlist、`package_resources[]`、业务运行产物位置、便携性扫描和 zip 分发合同。

## 1. 根目录与运行资源

```text
<slug>/
├── expert.json
├── opencode.json
├── README.md
├── .env.example                 # 可选，仅占位值
├── avatars/
└── .opencode/
    ├── agents/
    ├── skills/
    ├── commands/                # 可选
    ├── tools/                   # 可选
    ├── plugins/                 # 可选
    ├── references/<slug>/<alias>/ # 可选
    ├── instructions/<slug>/     # 可选
    └── package.json             # 可选，不携带 node_modules
```

根级 `AGENTS.md`、`references/`、`instructions/`、真实 `.env`、额外配置和其他隐藏目录非法。
需要影响整个 workspace 的专家包指令必须放入 `.opencode/instructions/<slug>/`，并由
`opencode.json.instructions` 索引。
`.opencode/` 中的 agents、skills、commands、tools、plugins、references 和 instructions 是随包
分发的运行资源，不是专家执行任务时产生的业务文件。

## 2. `package_resources[]`

`package_resources[]` 声明 supplemental skill 内除生成 `SKILL.md` 外的真实资源：

```json
{
  "package_resources": [
    {
      "path": ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/references/rules.md",
      "kind": "text"
    },
    {
      "path": ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/templates/input.xlsx",
      "kind": "binary",
      "sha256": "<optional-lowercase-sha256>"
    }
  ]
}
```

- `path` 必须位于 manifest 已声明的 supplemental skill 子树。
- 生成器管理的 `SKILL.md` 不重复声明。
- `kind` 只能是 `text` 或 `binary`；text 必须是 UTF-8。
- 输入可省略 `sha256`；提供时必须为匹配真实字节的小写 SHA-256。
- 输出 `expert.json` 始终写入重新计算的 SHA-256。
- skill 子树内除 `SKILL.md` 外的所有文件都必须声明；孤儿文件校验失败。
- 生成的 skill 资源导航逐项列出自己拥有的资源和使用时机。

`--force` 在 sibling staging 中重建，只保留当前 manifest 声明的资源。staging 完整校验通过后
才原子替换；失败必须保持旧包逐字节不变。

## 3. 包内便携性

包文件内容和路径禁止：

- 开发机绝对路径，例如 `/Users/<name>`、`/home/<name>` 或本机盘符路径；
- `~/.agents`、`.agents/skills`、本地 checkout 或浏览器 profile；
- 路径逃逸 `..`、绝对路径或 symlink；
- 真实 token、API key、密码、私有 endpoint 或非占位 `.env`；
- `.git`、`.serena`、`node_modules`、缓存、日志、Python bytecode、OS 元数据；
- manifest 未声明的 commands、tools、plugins、references、instructions 或 skill 资源。

允许包相对路径、命令名、HTTPS URL、`{env:VARIABLE}` 和业务安全占位符。

## 4. 业务运行产物

会生成报告、JSON、Markdown、Excel、图片或日志的专家，必须定义 workspace root。

默认目录：

```text
<workspace>/<业务交付目录>/<run-id>/
```

禁止写入：

- `<workspace>/.opencode/`；
- `<workspace>/.mobilework-engine/`；
- workspace 外部；
- 专家安装目录或全局 skill 目录。

脚本应支持 `--workspace-root <workspace>` 或等价机制。产物正文中的输入、输出、模板和报告路径
使用 workspace 相对形式；外部输入只展示文件名、占位符或安全业务描述。

扫描业务产物：

```bash
python scripts/scan_portable_artifacts.py \
  --workspace-root <workspace> \
  <workspace>/<业务交付目录>/<run-id>
```

扫描器检查 JSON、Markdown、文本、日志和 Excel 字符串单元格，同时验证产物目录位于 workspace
内部且不落入引擎目录。

扫描专家包时不要传 `--workspace-root`，以免把包内合法 `.opencode/skills/...` 误判为业务产物。

## 5. Validator 合同

`scripts/validate_expert.py` 至少检查：

- `expert.json` 存在且与 type、角色、skill、workflow 和 runtime config 一致；
- 根目录和 `.opencode/` 只包含 allowlist 文件；
- symlink、路径逃逸、未声明文件和内容漂移全部失败；
- `package_resources[]` hash 与真实字节一致；
- agent/skill frontmatter、permission、steps、description 与运行配置一致；
- avatar、MCP、env、references、instructions、plugins 和 LSP 的声明与文件一致；
- 所有文本和支持的二进制产物不含非便携路径或 secret-like 内容。

不要为了让 validator 通过而删除或重写用户未授权的业务输入；先报告具体失败项。

## 6. Zip 分发

打包前：

```bash
python scripts/validate_expert.py <package-dir>
python scripts/scan_portable_artifacts.py <package-dir>
```

打包：

```bash
python scripts/package_expert.py \
  --package-dir <package-dir> \
  --output-dir <dist-dir>
```

zip 根目录只能包含一个 `<slug>/`。packager 必须独立执行 allowlist 和 symlink 检查，不能只相信
validator 的自报结果。目标 `<slug>.zip` 已存在时必须显式传 `--force`；未传时旧 zip 的字节
不得改变。

完成后：

```bash
unzip -t <dist-dir>/<slug>.zip
```

packager 在目标目录内创建 sibling temporary zip，依次运行 Python CRC/顶层目录检查、可选的
外部 `unzip -t`、干净解压后的 validator 和便携性扫描，全部通过后才用 `os.replace` 发布。
`--skip-unzip-test` 只跳过外部命令，不能跳过 Python 检查和解压复验。任一步失败都删除临时
文件并保留旧 zip。分发包不得包含缓存、真实 `.env`、`node_modules`、`.git`、日志、字节码或
未声明资源。

## 7. 安装边界

完整 CLI 安装把运行资源复制到 `<workspace>/.mobilework-engine/`，并写 receipt 追踪每个 slug 的
文件、配置键与依赖 ownership。安装结构、路径重写、冲突和回滚规则见
`runtime-extensions-spec.md`。

桌面端“立即使用”不保证安装所有 runtime extensions；依赖 commands、tools、plugins、references、
instructions、LSP、MCP 或 `.opencode/package.json` 时，使用 `scripts/install_expert.py` 验证。

## 8. 最终检查

- 包根与 `.opencode/` 文件集合符合 allowlist。
- manifest 声明资源与真实文件一一对应，hash 可复算。
- 没有非便携路径、secret、symlink、缓存或未声明资源。
- 业务产物位于 workspace 业务目录，内容路径可迁移。
- zip 只有一个顶层 slug 目录，`unzip -t` 通过。
- 解压包再次通过 validator 与 portable scan。
