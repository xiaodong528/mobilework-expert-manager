# 迁移、供应链与 Bundle 操作

## 目录

1. [只读旧包迁移](#只读旧包迁移)
2. [供应链审计](#供应链审计)
3. [Bundle 创建](#bundle-创建)
4. [Bundle 校验](#bundle-校验)

## 只读旧包迁移

`plan_legacy_migration.py <directory-or-zip>` 永久只读，输出机械迁移项、候选 JSON Patch、资源移动
表、权限变化、业务确认项和需要重生成的派生物。它不提供 `--apply`，不修改源包，不从旧派生物
覆盖 `expert.json`，不执行 package code。ZIP 先走 metadata preflight、CRC 和受限解压。
面向用户的迁移规划必须包含 planner 返回的实际 RFC 6902 `candidateJsonPatch` 数组和机械 action
清单，不得用“将会归一化”等自然语言摘要替代机器可读候选，也不得自行应用候选 patch。
没有真实输入时必须拒绝编造并请求目录与 ZIP；同时明确说明真实规划将映射 Skills、
`maxTurns`→`steps` 和 references，列出实际权限变化与全部需重生成派生物，根规则作用域与 Bash
收窄要求继续作为业务确认项；静态检查不 import 或执行包内 modules、commands、Plugins、MCP 或 lifecycle scripts。

旧包声明 Reference 但没有角色使用关系时，迁移预览逐项询问使用角色；已有 workspace
Instruction 则逐项确认继续全局生效，还是改成角色规则。预览不得静默选择作用范围。

## 供应链审计

validator 静态运行 warning-first 审计：package lifecycle、secret、危险路径和未声明可执行资源为
error；`latest`/semver range、Git URL dependency、`npx -y`、未锁 npm Plugin、enabled MCP 和运行时
动态下载为 warning。本轮不强制 lockfile，也不宣称依赖完全可复现。

## Bundle 创建

`create_bundle_manifest.py` 以 package ZIP 列表创建 `bundle-manifest.json` 和受控
`bundle-summary.md`。manifest 是唯一事实源，记录 schema/contract、每包 slug/version/hash、可选
source repository/commit、generator hash、测试 collected/passed/failed/skipped 和文档路径。
`.git/**` 不得出现在 package ZIP 或 hash 输入中。

## Bundle 校验

`validate_expert_bundle.py <bundle-dir>` 校验 package 数量、hash、静态 validator、测试统计及受控
Markdown/DOCX 字段。DOCX 仅通过标准 ZIP/XML 读取 `word/document.xml`，不执行宏、不要求
`python-docx`。validator 只比较 `MOBILEWORK_BUNDLE_FIELD key=value` 受控字段，不尝试理解任意
自然语言陈述；DOCX 不是第二事实源。
