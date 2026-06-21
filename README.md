# ChillWithYou2Thunderstore

将 GitHub Release 自动转换为 [Thunderstore](https://thunderstore.io) 包的 CI 流水线 —— **你只需写好配置文件，剩下的全自动完成。**

## 这是什么？

你的 Mod 发布在 GitHub 上，每次发 Release 后要手动打包、写 manifest、上传 Thunderstore —— 繁琐且容易出错。

这个项目让你只需要在 [`config/mods.json`](config/mods.json) 里描述 **从哪个仓库下载什么文件、包名叫什么**，剩下的同步、打包、校验、发布全部由 CI 自动完成。

## 快速开始

### 1. 准备工作

- 一个托管在 GitHub 上的 Mod 仓库（源仓库）
- 一个 Thunderstore 团队/命名空间，以及对应的 API Token
- 将此仓库 Fork 或直接使用

### 2. 配置 Secret

Thunderstore API Token 按命名空间配置为 GitHub Secret，命名规则：

```text
{NAMESPACE_UPPER}_THUNDER_TOKEN
```

> 将命名空间中的 `-` 替换为 `_`，全部大写。
> 例如：命名空间 `Small_tailqwq` → Secret 名 `SMALL_TAILQWQ_THUNDER_TOKEN`

### 3. 添加你的 Mod

编辑 [`config/mods.json`](config/mods.json)，在 `mods` 数组中添加一个条目：

```json
{
    "key": "my-mod",
    "enabled": true,
    "source": {
        "owner": "MyGitHubUser",
        "repo": "MyModRepo"
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
        "namespace": "MyNamespace",
        "name": "MyModName",
        "description": "一句话描述你的 Mod 功能。",
        "dependencies": ["BepInEx-BepInExPack-5.4.2304"]
    },
    "package_files": {
        "icon": "templates/my-mod/icon.png"
    }
}
```

### 4. 准备模板文件

在 `templates/my-mod/` 下放置：

- `icon.png` — Thunderstore 列表图标

> README 无需手动维护——流水线会自动从源仓库同步并重写链接。

### 5. 运行流水线

触发方式：

| 方式 | 操作 |
| ---- | ---- |
| **全自动** | 流水线每天 UTC 0 自动运行，同步全部 `enabled: true` 的 Mod |
| **手动运行** | 在 Actions 页面手动触发 `Full Pipeline Orchestrator`，可选择单个 Mod 或全部 |
| **单步调试** | 分别触发 `Sync Release Assets` → `Build And Validate` → `Publish` 逐步排查 |

## 三阶段流水线

```text
你的 GitHub Release              Thunderstore
     │                                ▲
     ▼                                │
① Sync ────► ② Build & Validate ──► ③ Publish
   下载资产      打包 + API 校验        上传发布
```

| 阶段 | 做什么 | 产物 |
| ---- | ------ | ---- |
| **① Sync** | 从源仓库 Release 下载资产，按规则提取文件，同步 README 和可选的 CHANGELOG | `assets/<key>` 分支（`<version>/` 直接位于根，含 `readme_rewrite.md`、二进制文件） |
| **② Build & Validate** | 生成 manifest.json、从分支读取 README 和 CHANGELOG、打 zip 包、调用 Thunderstore 校验 API | 包 zip + 校验日志 |
| **③ Publish** | 分块上传 zip 到 Thunderstore 并提交发布 | 线上包 |

> **注意：** 阶段 ③ 默认是 dry-run 模式（仅演练）。需要真正发布时，手动触发 `Publish` 工作流并将 `dry_run` 设为 `false`。全自动流水线（orchestrator）会自动关闭 dry-run。

## 本地使用

支持在本地运行完整流水线，方便调试。

### 环境要求

- Python 3.12+（通过 [uv](https://docs.astral.sh/uv/) 管理依赖）
- `git`、`zip`、`unzip`
- [`gh` CLI](https://cli.github.com/)（需登录）

### 安装

```bash
uv sync
```

### 命令

```bash
# 验证配置文件
uv run python -m thunderstore_pipeline config-check

# 同步单个 Mod（下载最新 Release 资产）
uv run python -m thunderstore_pipeline sync --mod-key my-mod

# 同步指定版本 + 演练（不推送分支）
uv run python -m thunderstore_pipeline sync --mod-key my-mod --tag v1.2.3 --dry-run

# 同步全部启用的 Mod
uv run python -m thunderstore_pipeline sync --all

# 从 assets 分支构建包
uv run python -m thunderstore_pipeline build --mod-key my-mod --version 1.2.3

# 校验构建产物
THUNDERSTORE_AUTH_TOKEN=<your-token> uv run python -m thunderstore_pipeline validate \
  --manifest build/packages/my-mod/1.2.3/manifest.json \
  --readme build/packages/my-mod/1.2.3/README.md \
  --icon build/packages/my-mod/1.2.3/icon.png \
  --namespace MyNamespace

# 发布（默认 dry-run）
THUNDERSTORE_AUTH_TOKEN=<your-token> uv run python -m thunderstore_pipeline publish \
  --mod-key my-mod --version 1.2.3 \
  --package-zip build/packages/my-mod/1.2.3/MyNamespace-MyMod-1.2.3.zip
```

## 配置参考

详见 **[docs/configuration-guide.md](docs/configuration-guide.md)** —— 包含 `mods.json` 每个字段的完整说明、资产匹配规则详解、zip 提取策略、常见场景示例。

## 目录结构

```text
config/mods.json              # 唯一配置源 —— 所有 Mod 定义在这里
thunderstore_pipeline/        # Python CLI 包（sync / build / validate / publish）
templates/<mod_key>/          # 各 Mod 的 icon.png
.github/workflows/            # CI 工作流
docs/                         # 详细文档
scripts/                      # 旧版 Shell 脚本（保留作为参考）
```

## 常见问题

### 同步显示 "No asset matched rule"

Release 中的文件名与 `assets[].matcher` glob 不匹配。检查源仓库 Release 页面的实际文件名，调整 matcher。

### 构建时找不到 assets 分支

需要先成功运行一次 Sync（非 dry-run），才会创建 `assets/<mod_key>` 分支。

### Validate 返回 401

检查对应命名空间的 GitHub Secret 是否已配置，名称是否符合 `{NAMESPACE_UPPER}_THUNDER_TOKEN` 格式。

### README 同步失败

README 同步失败（`sync_readme: true` 时）会**阻断同步**（Thunderstore 包必须含 README）。检查源仓库 `readme_source` 文件是否存在及对应 tag。CHANGELOG 同步失败仅警告（`sync_changelog` 默认为 `true`），因为许多仓库没有此文件。

### 标签必须严格遵循 SemVer

只接受 `X.Y.Z` 格式（如 `v1.2.3`）。`v1.0.0-beta` 这类预发布标签会被拒绝。
