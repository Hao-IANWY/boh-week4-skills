---
name: research-file-library
description: 把组织方授权的真实资料库真正落库：目录级扫描逐文件 sha256/大小/权限、侧车剔除、PDF/DOCX/侧车文本提取、按内容哈希判别名与完全重复（诱饵名降权）、行政性版本说明归组重建版本链、元数据逐字段记出处（人工>伴随表>内容>文件名>未知）、目录模板落库并哈希核验、ops_log 只读建议、归档校验与输入前后哈希对照；跳过全记原因码，未完成绝不写成完成。
---

# 科研资料库与文件管理 Skill（research-file-library）

整理资料的失败不在"没分类"，而在**没有台账**：批量处理时静默跳过无权限目录、同名直接覆盖导致版本链断裂、元数据靠文件名猜、报告写"全部完成"但跳过清单是空的。本 skill 的做法是：**每个文件都要有哈希、每个动作都要有日志、每个跳过都要有原因码、整个操作必须可回滚、输入库全程只读可证**。

## 何时使用

- 组织方授权的真实文件库修复工单：一批真实科研文件（原始数据/分析稿/归档件/扫描件/别名副本/伴随元数据表）要按约定目录与命名**落库**并留档；
- 需要按**内容哈希**判重复（`xxx(1).pdf`、改过名的备份、下载重复、`methods_latest.pdf` 系误命名），而不是按文件名；
- 需要按**行政性版本说明**（`version_notes`/`admin_notes`/`versions`）归版本组并重建"谁替代谁"的版本链；
- 需要元数据清单并且**每个字段标出处**（人工给定/伴随表/内容/文件名/目录名/扩展名/别名继承），抽不到就记 `未知`，来源冲突按出处分级取舍并留痕；
- 需要一份可交接的**回滚计划**、恢复点与**输入未被改动的哈希证明**。

不适用：跨机器同步、断点续传、出版级 PDF 深度解析（本 skill 用 `scripts/pdf_min.py` 做最小文本层提取，复杂 PDF 走诚实缺口）、去重后的物理删除（只建新树、只出建议，源文件永不动）。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、库目录引用、目录/命名模板、补充表、伴随表、版本说明、请求列表 |
| 库目录引用（字段名容错） | `library{root,exclude}`、`root`、或 `materials_root`/`library_path`/`files_root`/`library_dir`/`library_root`/`material_root` 字符串。相对路径先按 cwd 解析，再按 `case.json` 同目录解析；尝试列表写入 `root_tried` |
| `manifest_supplement[]` / `manual_metadata[]` | `{"path": <相对路径或文件名>, ...字段}`：人工给定，出处分级最高 |
| 伴随元数据表 | `metadata_table`/`companion_table`/`manifest_csv`/`manifest_json`/`metadata_csv`/`manifest` 引用（CSV/JSON，kit 表格读取），或库根目录自动识别 `metadata.csv`/`manifest.csv`/`metadata.json`/`manifest.json`（本身也是合法文档，照常入库） |
| `version_notes[]` / `admin_notes[]` / `versions[]` | `{"match": <文件名或路径后缀>, "doc": <文档同一性>, "version": <版本标签>, "note": <行政性说明>}` |
| 模板占位符 | `{project}`/`{year}`/`{kind}`/`{seq}`/`{ext}` |

## 流程（13 阶段）

阶段命名与 `操作证据.operation_log[].step` 一一对应：`load_case` → `workorder` → `inventory` → `extract` → `dedupe` → `versions` → `metadata` → `plan` → `apply` → `archive` → `index` → `requests` → `ops` → `report`

1. **workorder**：解析库目录（多字段名容错）、排除规则（默认 `.git/__pycache__/.DS_Store`…）、目录与命名模板、补充表、伴随表、版本说明、请求列表、单次文件上限。
2. **inventory**：`os.walk` 排序遍历；逐文件算 sha256（分块）、大小、权限位、mtime。**剔除侧车**（`<f>.ocr.txt`/`.ocr.json`、二进制件伴生 `<f>.txt/.json`）——侧车入 `library_mode.sidecars` 台账但不作文档，否则同一正文会双份入库。软链接不跟随、0 字节文件不入库、目录/文件读失败都记 `S_*` 原因码（**不静默**）。
3. **extract**：文本获取链 —— 侧车 → `pdf_min` 提取 PDF 文本层 → DOCX zip+xml 段落 → 纯文本直读。损坏/加密/无文本层/无提取器分别记 `R_FILE_CORRUPT`/`R_FILE_ENCRYPTED`/`R_NO_TEXT_LAYER`/`R_FILE_NO_EXTRACTOR`，诚实跳过不伪造文本；文件仍按字节哈希入库。
4. **dedupe**：按 `sha256` 分组；同哈希异名 = 别名/完全重复。canonical 选择：**非诱饵名（copy/backup/latest 降权）→ 归一名最短 → mtime 最早 → 路径字典序**。
5. **versions**：行政性版本说明优先、文件名模式回退（`version_basis` 如实标注）；别名副本继承同哈希规范件的版本说明（`note_inherited_from`）。canonical = 版本序最大（final>vN>draft）、并列非副本/非诱饵名优先。链按版本序排列，逐条写 `bytes_delta`/`lines_added`/`lines_removed`/`same_content` 差异说明；版本说明指向库外文件记 `S_NOTE_UNMATCHED`，说明与文件名版本号数字冲突记 `W_VERSION_NOTE_MISMATCH`，最高版本序并列记 `W_VERSION_TIE`，说明标注"当前版"与版本序矛盾记 `W_VERSION_CONFLICT`——全部显式进冲突，不猜不压。
6. **metadata**：六路抽取（人工给定 > 伴随表 > 内容首行标题 > 文件名正则 > 上级目录名 > 扩展名）+ 别名继承；每个字段记出处标签（`人工给定[...]`/`伴随表[...]`/`内容提取`/`文件名解析`/`目录名解析`/`扩展名推断`/`别名继承[...]`/`未知`），来源冲突按出处分级取舍并把双方值写入 `field_conflicts`；未知绝不补造，算 `fill_rate`。
7. **plan**：渲染目录与命名模板 → 目标路径建议树；同目录序号自增；**别名副本沿用规范件序号加 `-aliasN`**（避免同一内容伪装成两次实验）；同名不同内容显式登记 `W_NAME_COLLISION`。
8. **apply**：只向 `out/library/` **新建复制**，落库后重算 sha256 逐条核验；目标已存在且内容相同 → `noop_already_present`；内容不同 → `W_TARGET_EXISTS` **不覆盖**。
9. **archive**：归档校验 —— 落库树清单（文件数/总字节/逐文件哈希）与核验结果；源库**处理前后全量哈希对照**（含侧车与空文件），`hashes_unchanged=true` 才证明输入未动。
10. **index**：建可检索索引（id/path/sha256/kind/project/year/title/terms/提取方式）。
11. **requests**：检索 / 重复 / 版本三类请求逐条应答；未登记类型进 `uncertainty`，不猜意图。
12. **ops**：`ops_log` —— 入库建议（逐文件目标路径+依据）、合并重复候选、版本归并、近重复复核、提取缺口修复建议、索引重建、回滚就绪；**全部为建议（scope=suggestion_only、input_readonly=true、executed=false）**，实际改名/合并由人工执行。
13. **report**：三段契约 —— `资料结果` / `操作证据` / `失败与恢复`（含回滚步骤、恢复点、复跑命令）。

## 输出（`result.json`）

| 段（契约字段名） | 内容 |
|---|---|
| `资料结果` | `library_mode`（根目录解析/文档数/侧车台账）、`directory_tree`、`files[]`（源→目标、sha256、bytes、mode、mtime、提取方式与缺口码、元数据与出处、`duplicate_of`、`in_library`）、`counts`、`duplicate_groups[]`、`near_duplicate_hints[]`、`version_chains[]`（canonical/链/差异说明/冲突）、`naming_plan[]`、`index[]`、`requests[]` |
| `操作证据` | `source_root`、`hashes[]`、`journal[]`（逐 op：from/to/sha256/bytes/verified）、`metadata_provenance`、`field_conflicts`、`extraction[]`（逐文件方式/字符数/缺口码/OCR 置信度）、`integrity`（输入前后哈希对照）、`archive_manifest`（落库树清单+核验）、`ops_log`（只读建议）、`canonical_rule`、`script_version`、`operation_log`（阶段序列） |
| `失败与恢复` | `overall`、`skipped[]`、`conflicts[]`（落库+版本）、`issues[]`、`version_note_gaps[]`、`low_fill_rate_files[]`、`recovery{rollback,restore_points,reproduce}` |

同时另附 `main_result` / `资料结果` / `操作证据` 同内容别名，兼容不同字段命名的契约。

## 异常处理

| 情况 | 行为 |
|---|---|
| 根目录不存在/不是目录 | `S_ROOT_MISSING` / `S_ROOT_NOT_DIR` 进跳过清单，`status=empty`，不产出空树冒充成功 |
| 目录不可读（权限不足等） | `onerror` 捕获 → `S_DIR_UNREADABLE`（**不静默**） |
| 0 字节文件 | `S_EMPTY_FILE`（哈希无区分度），不入库但记录 |
| 软链接 | `S_SYMLINK`，不跟随（防逃逸与重复计数） |
| 侧车文件 | 剔除出文档清单，入 `sidecars` 台账；正文通过侧车提取时 `extraction_method=sidecar_ocr_text/sidecar_text` |
| 损坏 PDF/DOCX | `R_FILE_CORRUPT`：文件入库，文本提取诚实跳过，修复建议进 ops_log |
| 加密 PDF | `R_FILE_ENCRYPTED`：检测到 `/Encrypt` 即拒绝提取（不做解密），诚实记码 |
| 无文本层扫描件 | 有侧车用侧车；无侧车记 `R_NO_TEXT_LAYER`，不冒充 OCR |
| 同名目标 | `W_TARGET_EXISTS`，**不覆盖**（覆盖会切断版本链），原文件保留 |
| 落库后哈希不符 | `W_HASH_MISMATCH`，不记为入库成功 |
| 元数据抽不到 | 字段记 `未知`（不按文件名猜），`fill_rate < 0.75` 进 `low_fill_rate_files` |
| 伴随表与文件名冲突 | 按出处分级取舍（人工给定 > 伴随表 > 内容 > 文件名 > 目录名 > 扩展名），双方值记入 `field_conflicts` |
| 版本说明指向库外文件 | `S_NOTE_UNMATCHED` 显式缺口，不凭空建档 |
| 版本说明/文件名版本冲突 | `W_VERSION_NOTE_MISMATCH` / `W_VERSION_TIE` / `W_VERSION_CONFLICT`，显式冲突，需人工裁决 |
| 超出单次上限 | `S_OVER_MAX_FILES`，其余文件明确"本轮未纳入" |
| 未登记请求类型 | 进 `uncertainty`，不猜意图 |

## 自查

```bash
cd answer/043_科研资料库与文件管理
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o43
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o43/result.json
python3 -B scripts/pdf_min.py                 # PDF 写入→读回 round-trip 自检
python3 -B scripts/test_pipeline.py           # 回归：最小示例 + realtest 4 案例
```

- `validate.py` PASS；同一输入 + **干净 out 目录**重复运行，除 `generated_at` 外逐字节一致（`--case` 传相对或绝对路径结果相同）；
- 对账：`counts.source_files = 入库 + 跳过`；`files[]` 条数 = `counts.source_files`；
- 哈希对账：`操作证据.hashes[].sha256` 与磁盘源文件重算值逐条一致（`sha256sum` 可验）；
- 落库核验：`journal[]` 全部 `verified=true`，`archive_manifest.verify.ok=true`，文件数 = `counts.in_library`；
- 只读证明：`integrity.hashes_unchanged=true` 且 `rechecked = 源库全文件数`；`ops_log` 全部 `input_readonly=true, executed=false`；
- 反向验证：`duplicate_groups[].aliases[]` 里的文件与 `canonical` 必须同 `sha256`（不同即判据错）；
- 回滚演练：删掉 `out/library` 后源库 sha256 全量不变。

## 最小调用示例（**示例，非正式测试案例**）

```bash
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo43
```

示例输入：6 个可入库文件（CSV 原始数据 + 其别名副本、分析稿 v1/v2、2023 归档报告、非 UTF-8 扫描件）+ 1 个 0 字节文件；目录/命名模板 `{project}/{year}/{kind}` 与 `{project}_{year}_{kind}_{seq}{ext}`；2 行补充表；3 个请求（检索 / 重复 / 版本）。

示例输出要点（`examples/min_example/expected/result.json`）：

| 项 | 值 |
|---|---|
| `status.overall` | `partial`（0 字节文件与非 UTF-8 各记一条） |
| `counts` | 源文件 6、入库 6、跳过 1、重复组 1、版本链 1、提取缺口 1、冲突 0 |
| `duplicate_groups` | `exp_run01.csv` 为规范件，`exp_run01 (1).csv` 为 alias（同 sha256） |
| `version_chains` | `analysis_v1.md → analysis_v2.md`，v2 为 latest，带 `lines_added/lines_removed` |
| 落库树 | `PROJ-A/2024/raw_data/…_01.csv` + `…_01-alias1.csv`；`PROJ-A/2024/analysis_note/…`；`supplement/UNDATED/binary/…` |
| 元数据 | `analysis_v2.md` 一行补充表覆盖了 `owner/instrument/note`，出处记 `人工给定[...]`；归档件 `project` 与目录名冲突按"人工给定优先"取舍并进 `field_conflicts` |
| `low_fill_rate_files` | `scan_page.bin`（缺 `year`/`title`，提取缺口 `R_FILE_NO_EXTRACTOR`） |
| 归档校验 | `integrity.hashes_unchanged=true`（输入前后全量哈希对照）+ `archive_manifest.verify.ok=true` |
| 回滚 | 全量操作是"新建复制"，删 `out/library` 即完全回滚；源库零改动 |

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。

## 方法来源（借鉴仓库 + 改动点）

| 借鉴 | 取什么 | 改动点 |
|---|---|---|
| `paperless-ngx/paperless-ngx` | 把"目录约定"做成可执行配置（storage path 模板）而非文档说明 | 用 Python `str.format` 实现等价的模板 DSL，模板放 `rules/convention.json`，换项目只改配置 |
| `paperless-ngx` 的 consume 阶段 checksum 去重 | 以内容哈希作"重复 vs 版本"的判据 | 明确区分三种关系：同哈希=重复、同身份异哈希=版本、都不同=不同文件；**绝不按文件名判重复**，诱饵名（copy/backup/latest）降权防 `methods_latest.pdf` 误命名串味 |
| `TagStudioDev/TagStudio` | "文件不动、库内挂元数据"的非破坏性入库思路 | 本 skill 只复制到新树、源库只读；全部整理动作只进 `ops_log`（建议未执行），输入前后全量哈希对照证明未动 |
| 本题 README §1 裸 Agent 失败模式 | 只给建议不落库、按文件名判重、静默跳过、同名覆盖、元数据靠猜 | 分别落成 `plan+apply`（真复制并验哈希）、`dedupe`（sha256）、`skipped[]`（原因码强制）、`W_TARGET_EXISTS`、`provenance/未知` 与 `field_conflicts` |
| 047 标杆管线（answer/047） | 文件库扫描与哈希清单、侧车剔除、别名/版本组 canonical、只读 ops_log、幂等 | 复制其 `pdf_min.py` 为本题 `scripts/pdf_min.py`（PDF 文本层自提 + round-trip 自检）；DOCX 用 zip+xml 自读；版本组按行政性版本说明重建链并写差异说明 |
| 归档与交接实践（manifest + checksum + rollback） | 可交接的三件套：清单、校验和、回滚 | 落成 `files[]`+`hashes[]`+`journal[]`+`archive_manifest`+`integrity`+`recovery.rollback`，并给 `reproduce.command` 与脚本版本 |
