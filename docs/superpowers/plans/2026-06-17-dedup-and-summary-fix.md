# 版本去重 & Summary 覆盖修复计划（v2）

**日期:** 2026-06-17
**分支:** feat/sh2py

---

## Context

两个实际运行中发现的问题需要修复。

---

## 问题 1：缺少已有包的校验 → 重复构建/发布

### 根因

`sync_mod` 无论 assets 分支是否已有版本都会返回 `(mod_key, version)`。即使 push 被跳过，该 mod 仍然进入下游 build/publish matrix。

### 修正后的逻辑

```
sync_mod() 获取 latest release → version X.Y.Z
  │
  ├─ 检查 Thunderstore 是否已有 <namespace>/<name>/<version>
  │    └─ 有 → return None (不进入 build/publish matrix，彻底跳过)
  │
  ├─ 检查 assets/<mod_key> 分支是否已有 version X.Y.Z
  │    └─ 有 → 跳过分支同步（不下载、不 push），但仍然 return (mod_key, version)
  │            （分支没新内容，但 Thunderstore 上没这个包，仍需构建发布）
  │
  └─ 两个都没有 → 正常 sync → return (mod_key, version)
```

**决策表：**

| 分支有 | Store有 | 行为 |
|--------|---------|------|
| ❌ | ❌ | 执行 sync push → 进入 matrix |
| ✅ | ❌ | 跳过 push（已有内容）→ 仍进入 matrix |
| ❌ | ✅ | 不该出现（Store 有但分支没有），保险起见 return None |
| ✅ | ✅ | return None，全跳过 |

### API 端点

从 `docs/thunderstore_openapi.json` 确认：

```
GET /api/experimental/package/{namespace}/{name}/
  → 200: 返回 PackageExperimental（包存在）
  → 404: 不存在
  → 无需 auth token
```

### 涉及改动

**`thunderstore_api.py`** — 新增方法：

```python
def check_package_exists(self, namespace: str, name: str) -> bool:
    """Check if a package exists on Thunderstore (by namespace/name).
    
    GET /api/experimental/package/{namespace}/{name}/
    Returns True if exists (200), False if not (404 or network error).
    """
```

注意：API 按 `{namespace}/{name}` 查询包，不区分 version。只要包存在，后续版本更新也走同一发布流程，所以单纯按包存在性即可判断是否需要进入 matrix。

如果包已存在于 Thunderstore，说明该 mod 之前已发布过。要判断**特定版本**是否已发布，需要检查返回的 `latest.version_number` 是否与当前同步的版本一致。

**精化逻辑：** 查询包 → 如果 200 → 检查 `latest.version_number == version` → 相同则 return None；如果 version 更新则仍需继续（升级场景）。

```python
def check_package_exists(self, namespace: str, name: str) -> dict | None:
    """GET /api/experimental/package/{namespace}/{name}/
    Returns parsed PackageExperimental dict if exists, None if 404/error.
    """
```

**`sync.py`** — `sync_mod` 修改：

```python
def sync_mod(cfg, mod_key, tag_override, dry_run, ci) -> tuple[str, str] | None:
    mod = get_mod(cfg, mod_key, require_enabled=True)
    owner, repo = mod.source.owner, mod.source.repo
    release = get_release(owner, repo, tag_override)
    version = _semver_from_tag(release.tag_name)

    # --- Pre-flight checks ---

    # Check 1: Thunderstore — if same version already published, skip entirely
    ns = mod.thunderstore.namespace
    nm = mod.thunderstore.name
    api = ThunderstoreAPI()  # no auth needed for GET
    ts_pkg = api.check_package_exists(ns, nm)
    if ts_pkg is not None:
        ts_version = ts_pkg.get("latest", {}).get("version_number")
        if ts_version == version:
            print(f"Version {version} already on Thunderstore ({ns}/{nm}), skipping")
            _write_skipped(mod_key, version, f"already on Thunderstore {ns}/{nm}@{ts_version}")
            return None

    # Check 2: Assets branch — if version exists, skip download+push only
    branch = f"assets/{mod_key}"
    if remote_branch_exists(branch):
        existing = list_versions_on_branch(branch, mod_key)
        if version in existing:
            print(f"Version {version} already on {branch}, skipping download+push")
            # Still return (mod_key, version) — build/publish still needed
            # But skip the heavy download+extract+push
            _write_sync_summary(mod_key, version)
            return mod_key, version

    # --- Existing full sync logic ---
    # download, process, push...
    # (unchanged from current code)
```

**`sync.py`** — `sync_all` 过滤 `None`：

```python
def sync_all(cfg, tag, dry_run, ci) -> list[tuple[str, str]]:
    results = []
    for mod in [m for m in cfg.mods if m.enabled]:
        try:
            result = sync_mod(cfg, mod.key, tag, dry_run, ci)
            if result is not None:  # None = Thunderstore already has this version
                results.append(result)
        except Exception as e:
            print(f"Failed to sync {mod.key}: {e}")
    return results
```

---

## 问题 2：Workflow Summary 覆盖问题

### 根因

- `GITHUB_STEP_SUMMARY` 是 **per-job** 的文件
- orchestrator 的 sync/build/publish 是三个**不同 job**，各自有独立的 `GITHUB_STEP_SUMMARY`
- 用户查看 workflow run 时只能看到某个 job 的 tab，没有一个汇总视图

### 修正方案

1. **standalone 触发**：保持不变，每个 workflow 自己的 summary 正常显示
2. **orchestrator 触发**：在最后新增一个 `summary` job（`if: always()`），汇总所有阶段的输出

### 涉及改动

**`orchestrator.yml`** — 新增 `summary` job：

```yaml
jobs:
  sync:
    # ... (unchanged)

  build:
    # ... (unchanged)

  publish:
    # ... (unchanged)

  summary:                          # NEW
    needs: [sync, build, publish]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Write pipeline summary
        run: |
          {
            echo "## Pipeline Summary"
            echo ""
            echo "**Trigger:** ${{ github.event_name }}"
            echo "**Run:** [\`${{ github.run_id }}\`](https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }})"
            echo ""
            echo "### Synced Mods"
            echo '```json'
            echo '${{ needs.sync.outputs.synced_mods }}'
            echo '```'
            echo ""
            echo "### Skipped Mods"
            echo '```json'
            echo '${{ needs.sync.outputs.skipped_mods }}'
            echo '```'
            echo ""
            echo "| Phase | Status |"
            echo "|-------|--------|"
            echo "| sync | ${{ needs.sync.result }} |"
            echo "| build | ${{ needs.build.result }} |"
            echo "| publish | ${{ needs.publish.result }} |"
          } >> "$GITHUB_STEP_SUMMARY"
```

**`sync.yml`** — 新增 `skipped_mods` output + `SYNC_SKIPPED_FILE` env：

```yaml
    outputs:
      synced_mods:  ${{ jobs.sync.outputs.synced_mods }}
      skipped_mods: ${{ jobs.sync.outputs.skipped_mods }}  # NEW

    # In the "Run sync" step:
        env:
          SYNC_SUMMARY_FILE: /tmp/sync_summary.jsonl
          SYNC_SKIPPED_FILE: /tmp/sync_skipped.jsonl     # NEW

    # In the "Read sync summary" step:
        if [[ -f /tmp/sync_skipped.jsonl ]]; then
          SKIPPED=$(jq -s -c '.' /tmp/sync_skipped.jsonl)
          echo "skipped_mods=${SKIPPED}" >> "$GITHUB_OUTPUT"
        else
          echo "skipped_mods=[]" >> "$GITHUB_OUTPUT"
        fi
```

**`sync.py`** — 新增跳过记录函数：

```python
SKIPPED_SUMMARY_FILE = os.environ.get("SYNC_SKIPPED_FILE")

# Called when sync_mod decides to skip:
def _write_skipped(mod_key: str, version: str, reason: str) -> None:
    skipped_file = os.environ.get("SYNC_SKIPPED_FILE")
    if skipped_file:
        sp = Path(skipped_file)
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(sp, "a") as f:
            json.dump({"mod_key": mod_key, "version": version, "reason": reason}, f)
            f.write("\n")
```

---

## 受影响文件汇总

| 文件 | 改动 |
|------|------|
| `thunderstore_pipeline/thunderstore_api.py` | 新增 `check_package_exists(namespace, name) -> dict \| None` |
| `thunderstore_pipeline/sync.py` | `sync_mod` 增加 TS 预检 + 分支预检 + 分支跳过逻辑；新增 `_write_skipped`；`sync_all` 过滤 `None` |
| `.github/workflows/sync.yml` | 新增 `SYNC_SKIPPED_FILE` env + `skipped_mods` output |
| `.github/workflows/orchestrator.yml` | 新增 `summary` job（`if: always()`） |
| 其他文件 | 不变 |

---

## 验证

1. **api 端点确认：** `curl https://thunderstore.io/api/experimental/package/Small_tailqwq/RealTimeWeather/` 确认返回格式
2. **sync dry-run：** `python -m thunderstore_pipeline sync --mod-key realtime-weather --dry-run` — 检查日志输出
3. **orchestrator：** 触发后查看 workflow run 的 Summary 是否显示汇总表格
