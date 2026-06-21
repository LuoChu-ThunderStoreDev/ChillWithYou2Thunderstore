# mods.json 配置指南

`config/mods.json` 是本流水线的**唯一配置源**。所有 Mod 的源仓库、资产匹配规则、Thunderstore 元数据都在这里定义。

## 目录

- [顶层结构](#顶层结构)
- [Mod 条目字段](#mod-条目字段)
- [source —— 源仓库](#source--源仓库)
- [assets —— 资产匹配规则](#assets--资产匹配规则)
  - [通用字段](#通用字段)
  - [kind: file](#kind-file)
  - [kind: zip](#kind-zip)
  - [zip 提取策略详解](#zip-提取策略详解)
- [thunderstore —— 包元数据](#thunderstore--包元数据)
- [package_files —— README 与图标](#package_files--readme-与图标)
- [完整示例](#完整示例)
- [常见场景](#常见场景)

## 顶层结构

```json
{
    "mods": [
        { "..." : "..." }
    ]
}
```

`mods` 数组中的每个对象定义一个 Mod。`key` 必须唯一，重复会导致验证失败。

## Mod 条目字段

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `key` | string | ✅ | Mod 唯一标识，用作分支名（`assets/<key>`）和工作流输入参数 |
| `enabled` | boolean | ✅ | `true` 时参与全量同步（`--all` / 定时任务），`false` 时跳过 |
| `source` | object | ✅ | 源仓库信息，见 [source](#source--源仓库) |
| `assets` | array | ✅ | 资产匹配规则数组，至少 1 条，见 [assets](#assets--资产匹配规则) |
| `thunderstore` | object | ✅ | Thunderstore 包元数据，见 [thunderstore](#thunderstore--包元数据) |
| `package_files` | object | ✅ | README/icon 路径与同步策略，见 [package_files](#package_files--readme-与图标) |

## source —— 源仓库

指定 Mod 源代码所在的 GitHub 仓库和 Release 版本来源。

```json
"source": {
    "owner": "Small-tailqwq",
    "repo": "RealTimeWeatherMod"
}
```

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `owner` | string | GitHub 用户名或组织名 |
| `repo` | string | 仓库名（不含 `.git`） |

流水线通过 `gh release download` 从 `https://github.com/<owner>/<repo>` 获取资产。支持两种版本选择方式：

- **默认（不指定 tag）：** 使用该仓库的最新 Release
- **指定 tag：** 通过工作流的 `--tag v1.2.3` 参数指定版本

> **注意：** tag 必须严格遵循 SemVer `X.Y.Z` 格式（如 `v1.2.3`）。`v1.0.0-beta` 等预发布标签会被拒绝。

## assets —— 资产匹配规则

`assets` 数组的每条规则描述如何匹配和处理 Release 中的文件。多条规则按顺序执行。

### 通用字段

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `matcher` | string | ✅ | Shell 风格 glob 模式，匹配 Release 资产文件名（如 `*.dll`、`*.zip`） |
| `kind` | string | ✅ | 资产类型：`"file"` 或 `"zip"` |

### kind: file

适用于单个文件资产（如 `.dll`）。直接复制到目标路径。

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `target` | string | ✅ | 目标路径。以 `/` 结尾表示目录（文件放入该目录），否则表示完整目标文件名 |

**示例：**

```json
{
    "matcher": "*.dll",
    "kind": "file",
    "target": "BepInEx/plugins/"
}
```

> `target` 为 `BepInEx/plugins/` → 匹配到的 `.dll` 文件放入 `BepInEx/plugins/<文件名>.dll`

### kind: zip

适用于 zip 压缩包资产。解压后按规则提取内容。

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `extract` | array | ✅ | 提取规则数组，至少 1 条 |
| `preserve_unmatched` | boolean | ❌ | 是否保留未被 `extract` 命中的文件，默认 `false` |
| `exclude` | array | ❌ | 排除规则数组，glob 格式，在默认排除基础上附加 |

**extract 条目字段：**

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `from` | string | ✅ | zip 内源路径的 glob 模式 |
| `to` | string | ✅ | 提取后的目标目录（始终以 `/` 结尾视为目录） |

**示例：**

```json
{
    "matcher": "*.zip",
    "kind": "zip",
    "extract": [
        { "from": "BepInEx/plugins/*.dll", "to": "BepInEx/plugins/" },
        { "from": "plugins/*.dll", "to": "BepInEx/plugins/" }
    ]
}
```

### zip 提取策略详解

#### 默认排除列表

无论是否配置 `exclude`，以下文件**始终被排除**（不会出现在最终包中）：

- `manifest.json`
- `README.md`
- `icon.png`
- `CHANGELOG.md`

这些文件由流水线在同步/构建阶段自动生成或从源仓库同步。

#### preserve_unmatched

- **`false`（默认）：** 只保留 `extract` 规则命中的文件，其余全部丢弃
- **`true`：** 保留 zip 中未被 `extract` 命中的所有文件（排除列表中的除外）

**适用场景：** 当 zip 包中除了 `.dll` 还有配置文件、资源文件等需要保留的内容时，设为 `true`。

#### exclude 自定义排除

在默认排除列表基础上附加更多排除项，避免某些文件被打包。

```json
{
    "matcher": "*.zip",
    "kind": "zip",
    "preserve_unmatched": true,
    "exclude": [
        "BepInEx/*",
        "winhttp.dll",
        "doorstop_config.ini"
    ],
    "extract": [
        { "from": "BepInEx/plugins/*.dll", "to": "BepInEx/plugins/" }
    ]
}
```

> 这里 `"BepInEx/*"` 排除 zip 内 `BepInEx/` 下的所有文件，避免重复（因为 `extract` 已将需要的 `.dll` 提取到了目标位置）。

### 多条规则组合

```json
"assets": [
    {
        "matcher": "*.dll",
        "kind": "file",
        "target": "BepInEx/plugins/"
    },
    {
        "matcher": "*.zip",
        "kind": "zip",
        "extract": [
            { "from": "BepInEx/plugins/*.dll", "to": "BepInEx/plugins/" }
        ]
    }
]
```

> 同时支持 `.dll` 直接下载和 `.zip` 解压提取。流水线按顺序匹配，两个规则互不干扰。这是应对 Release 同时包含裸 DLL 和 zip 包两种格式的兼容策略。

## thunderstore —— 包元数据

定义最终 Thunderstore 包的 manifest 信息。

```json
"thunderstore": {
    "community": "chill-with-you",
    "namespace": "Small_tailqwq",
    "name": "Chill_Env_Sync",
    "description": "实时天气同步插件。",
    "dependencies": ["BepInEx-BepInExPack-5.4.2304"]
}
```

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `community` | string | ✅ | Thunderstore 社区标识（如 `"chill-with-you"`） |
| `namespace` | string | ✅ | Thunderstore 命名空间（团队名）。**注意：** 此字段也用于生成 Token Secret 名——`{namespace.upper().replace('-', '_')}_THUNDER_TOKEN` |
| `name` | string | ✅ | 包名（会作为最终包名的一部分：`<namespace>-<name>-<version>.zip`） |
| `description` | string | ✅ | 包描述，**自动截断至 256 个 Unicode 字符**。使用 `[:256]` 按字符截断，不会在多字节字符中间切断 |
| `dependencies` | array | ✅ | 依赖包列表，格式 `"作者-包名-版本号"` |
| `has_nsfw_content` | boolean | ❌ | 是否有 NSFW 内容，默认 `false` |
| `categories` | array | ❌ | 分类标签，默认 `["mods"]` |

**关于 Token Secret 命名：**

Token 对应的 GitHub Secret 名由 `namespace` 通过规则生成：
1. 转为全大写
2. 将 `-` 替换为 `_`
3. 末尾追加 `_THUNDER_TOKEN`

| namespace | Secret 名 |
| --------- | --------- |
| `Small_tailqwq` | `SMALL_TAILQWQ_THUNDER_TOKEN` |
| `small-tail` | `SMALL_TAIL_THUNDER_TOKEN` |
| `My-Team` | `MY_TEAM_THUNDER_TOKEN` |

## package_files —— README 与图标

定义 Mod 包的 README 和图标来源。README 由同步阶段从源仓库自动拉取并重写链接，图标使用本地模板。

```json
"package_files": {
    "icon": "templates/my-mod/icon.png",
    "readme_source": "README.md",
    "sync_readme": true,
    "sync_changelog": false,
    "changelog_source": "CHANGELOG.md"
}
```

| 字段 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| `icon` | string | ✅ | 本地图标路径（PNG 格式，建议 256x256） |
| `sync_readme` | boolean | ❌ | 是否从源仓库同步 README，默认 `true`。设为 `false` 会导致构建失败（Thunderstore 包必须包含 README） |
| `readme_source` | string | ❌ | 源仓库中文档的相对路径，默认 `"README.md"` |
| `sync_changelog` | boolean | ❌ | 是否同步源仓库 CHANGELOG。仅当 `sync_readme: true` 时生效，默认 `false` |
| `changelog_source` | string | ❌ | 源仓库中 CHANGELOG 的相对路径，默认 `"CHANGELOG.md"`。拉取失败仅输出警告，不阻断同步 |

### README 同步机制

1. 同步阶段从源仓库下载 `readme_source` 对应文件，原文保存为 `readme_origin.md`
2. 自动将其中所有**相对链接**改写为 GitHub 绝对路径，保存为 `readme_rewrite.md`：
   - 普通链接 → `https://github.com/<owner>/<repo>/blob/<tag>/...`
   - 图片链接 → `https://raw.githubusercontent.com/<owner>/<repo>/<tag>/...`
3. README 拉取**失败即报错**，阻断同步（Thunderstore 包必须包含 README）
4. 若 `sync_changelog: true`，还会拉取 `changelog_source`，失败仅警告不阻断
5. 构建阶段将 `readme_rewrite.md` 重命名为 `README.md` 并打包；`CHANGELOG.md` 存在时一并打包

> **提示：** 如果你的源仓库 README 没有任何相对链接或图片，同步后的内容与原文相同。如果你想用不同的文档作为 Thunderstore 页面，可以设置 `readme_source` 为其他路径。

## 完整示例

### 示例 1：简单 DLL Mod

源 Release 只有一个 `.dll`，无需复杂提取。

```json
{
    "key": "simple-mod",
    "enabled": true,
    "source": {
        "owner": "MyUser",
        "repo": "SimpleMod"
    },
    "assets": [
        {
            "matcher": "*.dll",
            "kind": "file",
            "target": "BepInEx/plugins/"
        }
    ],
    "thunderstore": {
        "community": "chill-with-you",
        "namespace": "MyTeam",
        "name": "SimpleMod",
        "description": "一个简单的 BepInEx 插件。",
        "dependencies": ["BepInEx-BepInExPack-5.4.2304"]
    },
    "package_files": {
        "icon": "templates/simple-mod/icon.png"
    }
}
```

### 示例 2：包含资源的 Zip Mod

源 Release 是一个 zip，除了 `.dll` 还有配置文件和资源文件需要保留。

```json
{
    "key": "resource-mod",
    "enabled": true,
    "source": {
        "owner": "MyUser",
        "repo": "ResourceMod"
    },
    "assets": [
        {
            "matcher": "*.zip",
            "kind": "zip",
            "preserve_unmatched": true,
            "extract": [
                { "from": "BepInEx/plugins/*.dll", "to": "BepInEx/plugins/" }
            ]
        }
    ],
    "thunderstore": {
        "community": "chill-with-you",
        "namespace": "MyTeam",
        "name": "ResourceMod",
        "description": "含资源的 Mod，zip 包内文件全部保留。",
        "dependencies": [
            "BepInEx-BepInExPack-5.4.2304",
            "MyTeam-CoreLib-1.0.0"
        ]
    },
    "package_files": {
        "icon": "templates/resource-mod/icon.png",
        "readme_source": "docs/THUNDERSTORE_README.md",
        "sync_readme": true
    }
}
```

> 这里 `readme_source` 指向 `docs/THUNDERSTORE_README.md`，允许源仓库维护专用 Thunderstore 文档而不影响面向 GitHub 用户的 `README.md`。

### 示例 3：同步源 README（自动链接重写）

利用流水线自动从源仓库拉取 README 并重写相对链接。

```json
{
    "key": "readme-sync-mod",
    "enabled": true,
    "source": {
        "owner": "MyUser",
        "repo": "ReadmeSyncMod"
    },
    "assets": [
        {
            "matcher": "*.dll",
            "kind": "file",
            "target": "BepInEx/plugins/"
        }
    ],
    "thunderstore": {
        "community": "chill-with-you",
        "namespace": "MyTeam",
        "name": "ReadmeSyncMod",
        "description": "自动同步源仓库 README 并重写相对链接。",
        "dependencies": ["BepInEx-BepInExPack-5.4.2304"]
    },
    "package_files": {
        "icon": "templates/readme-sync-mod/icon.png",
        "sync_readme": true
    }
}
```

> 未指定 `readme_source`，默认使用源仓库根目录的 `README.md`。

## 常见场景

### 关闭 README 同步（不推荐）

```json
"package_files": {
    "icon": "templates/my-mod/icon.png",
    "sync_readme": false
}
```

> **警告：** 设为 `false` 后构建会失败，因为 Thunderstore 包必须包含 README。仅当你有其他方式提供 README 时才使用此选项。

### 仅同步 README，不同步 CHANGELOG（默认行为）

```json
"package_files": {
    "icon": "templates/my-mod/icon.png",
    "sync_readme": true,
    "sync_changelog": false
}
```

> 默认 `sync_changelog` 为 `false`，只同步 README。即使设为 `true`，CHANGELOG 拉取失败也仅输出警告。

### 禁用某个 Mod（不参与全量同步）

```json
{
    "key": "legacy-mod",
    "enabled": false,
    ...
}
```

设置 `"enabled": false` 后，该 Mod 不会被 `--all` 和定时任务选中，但**仍可通过手动指定 `--mod-key` 单独同步**。

### 配置验证

提交前使用 CLI 验证配置：

```bash
uv run python -m thunderstore_pipeline config-check
```

Pydantic 会检查所有必填字段、类型匹配、`key` 唯一性、`kind=file` 有 `target`、`kind=zip` 有 `extract` 等。通过验证的配置不会在运行时报 schema 错误。
