# ChillWithYou2Thunderstore

将多个 Mod 源仓库的 GitHub Release 自动转换为 Thunderstore 可校验包。

本项目聚焦两段式流程：
1. 同步阶段：下载 Release 资产并写入 `assets/<mod_key>` 分支。
2. 构建阶段：从 assets 分支组包、同步 README、调用 Thunderstore validate API。

当前默认不执行自动 publish，仅构建和校验，确保流程稳定。

## 1. 快速开始

### 1.1 前置条件

- GitHub Actions 可用。
- 仓库已存在 `config/mods.json`。
- 若启用 validate 接口鉴权：
	- Thunderstore token 按 namespace 绑定，并按以下规则配置仓库 Secret：
		- `{namespace}_THUNDER_TOKEN`
		- 其中 namespace 需要先转换为全大写，且 `-` 替换为 `_`
		- 示例：namespace=`small-tail` 时，Secret 名应为 `SMALL_TAIL_THUNDER_TOKEN`
	- Variable(可选): `THUNDERSTORE_AUTH_SCHEME`，默认 `Bearer`

### 1.2 最小使用步骤

1. 在 `config/mods.json` 增加或修改目标 mod 条目。
2. 触发 `Sync Release Assets` 工作流，将资产同步到 `assets/<mod_key>`。
3. 触发 `Build And Validate Thunderstore Package` 工作流，生成 zip 并执行 validate。
4. 在 artifact 中下载包与校验日志。

## 2. 架构与数据流

### 2.1 总览

- 配置源：`config/mods.json`
- 资产沉淀：`assets/<mod_key>` 分支，目录形如 `assets/<mod_key>/<version>/...`
- 构建输出：`build/packages/<mod_key>/<version>/<namespace>-<name>-<version>.zip`
- 校验日志：`build/validation/*.json`

### 2.2 同步阶段做了什么

1. 读取 `mods.json` 中目标 mod 的 `source` 与 `assets` 规则。
2. 请求 GitHub Release（`latest` 或 `--tag` 指定版本）。
3. 按 `assets[].matcher` 匹配资产文件名。
4. 对 `kind=file` 直接复制到目标路径。
5. 对 `kind=zip` 解压并按 `extract` 规则提取。
6. 可选保留 zip 中其余文件（`preserve_unmatched=true`），并应用默认/自定义排除规则。
7. 写入 `_sync_metadata.json`。
8. 提交并推送到 `assets/<mod_key>` 分支。

### 2.3 构建阶段做了什么

1. 从 `assets/<mod_key>` 分支读取指定版本目录。
2. 生成 `manifest.json`（来自 `thunderstore` 字段）。
3. 处理 README：
	 - 优先按源仓库与 tag 同步 README（可配置开关）。
	 - 自动将 README 中相对链接改写为 GitHub 绝对链接。
	 - 同步失败则回退模板 README。
4. 复制 icon、同步内容并打 zip。
5. 调用 validate API 校验 manifest/readme/icon。

## 3. 工作流 API

### 3.1 Sync Release Assets

文件：`.github/workflows/sync-release-assets.yml`

触发器：
- `workflow_dispatch`
- `repository_dispatch`（`type=sync-release-assets`）
- `schedule`（每小时）

输入参数（workflow_dispatch）：
- `mod_key`：可选，目标 mod key。
- `tag`：可选，指定 release tag。
- `all`：可选，是否同步全部 enabled mod。
- `dry_run`：可选，是否仅演练不推分支。

`repository_dispatch` payload 示例：

```json
{
	"event_type": "sync-release-assets",
	"client_payload": {
		"mod_key": "aichat",
		"tag": "1.8.3",
		"all": false,
		"dry_run": true
	}
}
```

行为：
- `schedule` 自动等价 `all=true`。
- `dry_run=true` 时不推送分支。

输出：
- 成功时创建或更新 `assets/<mod_key>` 分支。

### 3.2 Build And Validate Thunderstore Package

文件：`.github/workflows/build-and-validate-thunderstore.yml`

触发器：
- `workflow_dispatch`
- `repository_dispatch`（`type=build-validate-thunderstore`）

输入参数（workflow_dispatch）：
- `mod_key`：必填。
- `version`：可选，不填则自动取 assets 分支最新版本。

`repository_dispatch` payload 示例：

```json
{
	"event_type": "build-validate-thunderstore",
	"client_payload": {
		"mod_key": "aichat",
		"version": "1.8.3"
	}
}
```

产物：
- `package-<mod_key>-<version>`：打包 zip。
- `validation-<mod_key>-<version>`：校验响应日志。

## 4. 脚本 API

### 4.1 scripts/sync_release_assets.sh

用法：

```bash
bash scripts/sync_release_assets.sh [options]
```

参数：
- `--config <path>`：配置文件路径，默认 `config/mods.json`。
- `--mod-key <key>`：单 mod 同步。
- `--tag <tag>`：覆盖 release tag。
- `--all`：同步所有 enabled mod。
- `--dry-run`：只演练。

返回语义：
- 0：执行成功。
- 非 0：配置无效、无可用资产、下载失败、提取失败等。

### 4.2 scripts/build_package.sh

用法：

```bash
bash scripts/build_package.sh --mod-key <key> [--version <x.y.z>] [--config <path>]
```

行为重点：
- 自动抓取 `assets/<mod_key>` 分支内容。
- 自动同步并重写 README（可配置关闭）。
- 输出 `GITHUB_OUTPUT` 供工作流后续步骤消费。

### 4.3 scripts/validate_thunderstore.sh

用法：

```bash
bash scripts/validate_thunderstore.sh \
	--manifest <path> \
	--readme <path> \
	--icon <path> \
	--namespace <team>
```

环境变量：
- `THUNDERSTORE_API_BASE`：默认 `https://thunderstore.io`
- `THUNDERSTORE_AUTH_TOKEN`：由工作流按 namespace 动态注入，不需要手动传参
- `THUNDERSTORE_AUTH_SCHEME`：可选，默认 `Bearer`

调用接口：
- `POST /api/experimental/submission/validate/manifest-v1/`
- `POST /api/experimental/submission/validate/readme/`
- `POST /api/experimental/submission/validate/icon/`

成功条件：
- 三个响应的 `success` 都是 `true`。

### 4.4 scripts/rewrite_readme_links.py

职责：
- 将 README 内相对链接转换为绝对链接。
- 普通链接指向 `github.com/<owner>/<repo>/blob/<ref>/...`
- 图片链接指向 `raw.githubusercontent.com/<owner>/<repo>/<ref>/...`

## 5. 配置文件规范（mods.json）

根结构：

```json
{
	"mods": [
		{ "...": "..." }
	]
}
```

### 5.1 mod 条目字段

- `key`：mod 唯一标识；用于分支名与工作流输入。
- `enabled`：是否参与同步。
- `source.owner`：源仓库 owner。
- `source.repo`：源仓库名。
- `assets[]`：资产提取规则数组。
- `thunderstore`：manifest 构建元数据。
- `package_files`：README/icon 与 README 同步策略。

### 5.2 assets 规则字段

通用字段：
- `matcher`：匹配 release 资产名的 glob，如 `*.dll`、`*.zip`。
- `kind`：`file` 或 `zip`。

`kind=file` 字段：
- `target`：目标路径，末尾 `/` 表示目录。

`kind=zip` 字段：
- `extract[]`：提取规则数组。
- `extract[].from`：zip 内源路径 glob。
- `extract[].to`：输出目标目录。
- `preserve_unmatched`：可选，是否保留未被 `extract` 命中的其余文件。
- `exclude[]`：可选，自定义排除 glob（在默认排除基础上附加）。

### 5.3 thunderstore 字段

- `community`：社区标识（当前主要用于配置层表达）。
- `namespace`：包命名空间（团队名）。
- `name`：包名。
- `description`：描述，构建时会截断到 256 字符。
- `dependencies[]`：依赖数组，写入 manifest。

### 5.4 package_files 字段

- `readme`：本地 fallback README 路径。
- `icon`：本地 icon 路径。
- `readme_source`：可选，源仓库 README 路径，默认 `README.md`。
- `sync_with_source_readme`：可选，默认 `true`。

### 5.5 完整示例

```json
{
	"key": "aichat",
	"enabled": true,
	"source": {
		"owner": "qzrs777",
		"repo": "AIChat"
	},
	"assets": [
		{
			"matcher": "*.zip",
			"kind": "zip",
			"preserve_unmatched": true,
			"exclude": [
				"manifest.json",
				"README.md",
				"icon.png",
				"CHANGELOG.md",
				"winhttp.dll"
			],
			"extract": [
				{ "from": "BepInEx/plugins/*.dll", "to": "BepInEx/plugins/" },
				{ "from": "plugins/*.dll", "to": "BepInEx/plugins/" },
				{ "from": "*.dll", "to": "BepInEx/plugins/" }
			]
		}
	],
	"thunderstore": {
		"community": "chillwithyou",
		"namespace": "qzrs777",
		"name": "AIChat",
		"description": "AI 聊天插件（zip 资产测试项）。",
		"dependencies": ["BepInEx-BepInExPack-5.4.2304"]
	},
	"package_files": {
		"readme": "templates/aichat/README.md",
		"readme_source": "README.md",
		"sync_with_source_readme": true,
		"icon": "templates/aichat/icon.png"
	}
}
```

## 6. 输出约定

### 6.1 assets 分支结构

```text
assets/<mod_key>/<version>/
├── BepInEx/plugins/*.dll
├── ...(按规则保留的其他文件)
└── _sync_metadata.json
```

### 6.2 包结构

```text
<namespace>-<name>-<version>.zip
├── manifest.json
├── README.md
├── icon.png
└── ...(mod 内容)
```

## 7. 常见问题与排障

### 7.1 同步显示 No asset matched rule

原因：`matcher` 与 release 资产名不一致。
处理：检查 release 真实文件名，调整 `assets[].matcher`。

### 7.2 同步成功但没有推送分支

原因：启用了 dry-run。
处理：移除 `dry_run` 或 `--dry-run`。

### 7.3 Build 时报 Remote branch not found

原因：还没执行真实同步，或分支名与 `mod_key` 不一致。
处理：先跑同步工作流，确认存在 `assets/<mod_key>`。

### 7.4 Validate 返回 401

原因：缺少 Thunderstore 鉴权，或 namespace 对应 Secret 未按规则命名。
处理：配置与 namespace 对应的 Secret，命名格式为 `{NAMESPACE_UPPER_WITH_UNDERSCORE}_THUNDER_TOKEN`，必要时配置 `THUNDERSTORE_AUTH_SCHEME`。

### 7.5 README 同步失败

行为：自动回退到 `package_files.readme` 模板，不会阻断构建。
处理：检查 `readme_source` 路径与 tag 对应文件是否存在。

## 8. 安全建议

- 不要把 token 写进仓库文件。
- 仅通过 GitHub Secrets 传递 token，且按 namespace 命名：`{NAMESPACE_UPPER_WITH_UNDERSCORE}_THUNDER_TOKEN`。
- 若后续接入自动发布，建议按 namespace 或 community 细分 token。

## 9. 当前限制与后续方向

- 当前不自动 publish 到 Thunderstore。
- 当前以 BepInEx 常见目录为主，复杂安装规则建议通过 `assets` 规则扩展。
- 可后续增加：
	- 自动发布开关
	- 更细粒度文件重命名/映射
	- 针对不同社区的多 token 策略