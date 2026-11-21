---
name: research-figure-builder
description: 将科研数据表制成可投稿图件：声明式清洗与行级血缘、按数据和研究目的选图、R/ggplot2 统计绘图、PDF/600dpi PNG/可编辑 SVG 三格式导出，并用独立数值复核、easyplot 元数据/色板审计、真实渲染边距和两次全链重跑验收。
---

# 科研数据可视化（research-figure-builder）

目标不是“画出一张图”，而是让图上每个数据元素都能回到源数据，并让别人用随附快照和脚本重跑出同一交付件。

## 何时使用

- 输入科研数据表与出图任务，需要真实图件、统计口径、图注和复现证据；
- 需要比较、相关、趋势、分布、构成、分类计数或相关热图；
- 需要指定期刊栏宽、画布、DPI、字体或导出格式；
- 需要识别缺失、非有限数、重复、小样本和类别过多，而不是静默删数据。

不用于实验设计、统计方法创新、图像后期美化或交互式仪表盘。显著性标记只接受已提供且带统计方法/校正口径的结果，不自动生成星号。

## 输入

1. 读取实际 `/input/case.json`、`/input/task.md` 和案例材料，不根据数据集名称猜任务。
2. 数据支持内联 `dataset.rows`、CSV、TSV、JSON 和只读 SQLite；相对路径按 case 所在目录解析，case 被迁移到 `/app/work` 等目录时自动回退查找各级祖先目录下的 `input/`、当前目录与 `/input`（Harbor 布局）；全部找不到时报出完整尝试路径，不猜测数据内容。
3. 出图工单 `figure_requests[]` 支持：
   - `figure_id`、`purpose`、`chart_hint`/`chart_type`；
   - `x`、`y`、`group`、`variables`、`unit_id`/`replicate_unit`、`label`；
   - `x_label`、`y_label`、`x_unit`、`unit`/`y_unit`、`title`、`panel`；
   - `error_bar=sd|se|ci95|none`、`fit_group`、`significance`；
   - `venue`、`width_mm`、`height_mm`、`dpi`、`format`/`formats`。
4. `data_dictionary` 的 `label/type/unit/missing_values/sentinels` 会进入标签、类型和清洗规则。数值型 `-999` 等哨兵必须显式声明，不能全局猜测。数据字典的短 `label` 优先作为热图等坐标轴显示名，原始列名保留在血缘与 analysis。
5. 若案例只有自然语言任务而缺 `figure_requests`，先由 Agent 在 `/app/work` 生成一个保留原 `case_id`、数据引用和 `output_contract` 的工作案例，只补充结构化工单；相对数据路径无需改写（按第 2 条回退解析），在证据中记录该转换，不伪造数据。工单映射示例（保持任务原文语义，不添加任务没提的结论）：

   任务说「比较三种基因型表达量差异，误差线用标准误」→
   ```json
   {"figure_id":"F1","purpose":"comparison","x":"基因型","y":"表达量","error_bar":"se",
    "x_label":"基因型","y_label":"表达量","title":"三种基因型表达量比较"}
   ```
   选图理由写进工单转化记录：purpose=comparison 且显式误差线 → `bar_with_error_points`（均值柱+全部原始点）；拒绝饼图（无序分类不做角度编码）。

   任务说「花瓣长宽的关联按物种分色，给拟合与置信带」→
   ```json
   {"figure_id":"F2","purpose":"correlation","x":"petal_length_cm","y":"petal_width_cm",
    "group":"species","fit_group":true,"x_label":"花瓣长度","y_label":"花瓣宽度"}
   ```
   拒绝折线（无序观测不连线）；`fit_group=true` 才分组拟合。

   任务说「探索性看全部变量相关结构」→ `{"figure_id":"F3","purpose":"correlation","chart_hint":"heatmap"}`（变量未列则由数据字典连续列自动识别，并记录该决定）。

## 执行

生产环境直接运行：

```bash
python3 -B /skills/weekly-skill/scripts/pipeline.py \
  --case /input/case.json --task /input/task.md --out /app/output
python3 -B /skills/weekly-skill/scripts/validate.py \
  --case /input/case.json --result /app/output/result.json
python3 -B /skills/weekly-skill/scripts/validate_figures.py \
  --result /app/output/result.json
```

依赖：Rscript + ggplot2 + ggprism + svglite + jsonlite + systemfonts；Python 审计使用 Pillow 与 pypdf。缺依赖时保存快照、运行计划和脚本，状态标为部分完成并列出缺失项。若容器缺 R 包且有网络，可先冷启动安装再复跑：

```bash
Rscript -e 'install.packages(c("ggplot2","ggprism","svglite","jsonlite","systemfonts"), repos="https://cloud.r-project.org")'
python3 -m pip install --user Pillow pypdf
```

安装失败不阻塞：依赖探测结果写入 `dependencies.json`，交付材料按部分完成口径如实降级。

## 流程

### 1. 冻结输入与契约

保存 `source_data.tsv`、原始来源/哈希、`run_plan.json`、清洗规则和依赖版本。动态绑定案例 `output_contract`，未产出的字段明确进入 `status.unmet`。

### 2. Shell/Python 只整备数据

`data_prep.sh` 调用 `scripts/prepare_data.py`，生成干净 TSV，不画图：

- 默认缺失标记：空值、NA、N/A、NaN、null、None、`.`；
- 非有限数和不可解析数值排除并记录原行号与原因；
- 完全重复行默认保留并报告，只有显式 `drop_duplicates=true` 才删除；
- 类别不取前 N 个；类别较多时保留全部并调整布局；
- 输出 `cleaned_data.tsv`、`figure_N.tsv`、`figure_N_lineage.json`、`data_quality.json`。

### 3. R/ggplot2 统计与绘图

`plot.R` 只读 TSV 与运行计划，调用 `theme.R`：

| 路由 | 实际表达 |
|---|---|
| `bar_with_error_points` | 原始 jitter 点 + 均值柱 + 显式 SD/SE/95% CI |
| `comparison_auto` | n<3 只画点；3–9 画点与声明的摘要；每组 n≥10 用箱线 + jitter |
| `boxplot_points` / `violin_points` | 显式箱线/小提琴 + jitter；每组 n<3 时降级为点图并记录 |
| `scatter_with_fit` | 散点 + 总体 OLS 与 95% 置信带；`fit_group=true` 才分组拟合；颜色 + 形状双编码 |
| `line_series` | 按真实时间/有序数值排序，颜色 + 线型；重复点给均值趋势，保留原始点 |
| `histogram` | Sturges 分组，可按组分面 |
| `stacked_bar` | 明确整体/组分字段，柱顶按累计总量设置刻度；无组分时用排序横条 |
| `category_count` | 分类计数，排序横条 |
| `correlation_heatmap` | pairwise-complete Pearson；常量列/有效对不足为 NA；发散色阶固定 [-1,1] 并带 colorbar |

`n=1` 的 SD/SE/CI、常量变量相关和零 X 方差拟合均为 `null`，不写成 0。柱/条从 0 起，最高刻度覆盖柱顶和误差线。

可读性自动防御（不截断数据）：全图基础字号由 min(宽, 1.55×高) 驱动，宽扁画布不随宽度放大字号；散点/折线点径按「可用绘图面积/点数」密度收缩（下限 0.9mm）；类别 >8 转横向布局并按行数自动增高画布（封顶 `max_height_mm`），行距不足时按行距收缩类别标签字号（下限 6pt）；垂直布局预测标签重叠时改 45° 旋转；热图按变量数与宽度增高画布，轴标签优先用数据字典短名并剥离括号单位，水平投影装不下时升到 90°，字号按格宽收缩，格内数字按格宽定径（上限 ~9pt，格 <5.5mm 或变量 >12 时省略）；时间轴刻度按画布宽度封顶抽稀并用「MM-DD / YYYY」两行式标签；标题按画布宽度换行。每次调整都写入 `layout_adjustment`、analysis 的 notes 与 warnings，可追溯。

### 4. 三格式导出

每图默认生成：

- `figure_N.pdf`：Cairo PDF，检查实际页面尺寸和字体嵌入证据；
- `figure_N.png`：默认 600 dpi，任务可指定更高/更低但不得低于 300 dpi；
- `figure_N.svg`：svglite，保留可编辑 `<text>`；
- `figure_N.tsv` + `figure_N.csv`、`figure_N_spec.json`、`figure_N_analysis.json`、`figure_N_lineage.json`。

即使用户只请求 PDF，也保留 PNG 预览和 SVG 可编辑件。导出格式分级：请求且属于支持范围（PDF/PNG/SVG）的格式缺失时该图不得标为完整；请求不支持的格式（如 emf）记录为 `format_gaps` 范围外缺口并交付默认三格式，不静默忽略也不伪造。

### 5. 四级 QA

1. **科学表达**：图型、分组、单位、误差线、显著性和图注口径一致。
2. **数值复核**：Python 独立重算均值/SD/SE、OLS/Pearson 和相关矩阵，与 R 结果比对。
3. **文件与视觉检查**：easyplot 检查尺寸/DPI/PDF 字体/SVG 文本；检查 PNG 非空白、实际边距和色板上下文。边距语义：空白图或内容贴边/裁切（距画布 <0.02 in）判失败；留白低于 0.1 in 仅记 `tight` 标志，不把合格图判成 partial。
4. **端到端复现**：从随附原始快照再次执行 `data_prep.sh + plot.R + normalize_pdf.py`，比较绘图 TSV/CSV、lineage、统计 JSON、PDF、PNG、SVG 的 SHA-256。PDF 规范化只把 Cairo/pypdf 的 CreationDate/ModDate 固定为 Unix epoch，不改页面、字体或图形内容。

自动检查完成后，Agent 必须查看实际 PNG（必要时再渲染 PDF），检查标签重叠、乱码、遮挡和科学语义。像素边距检查不等于人工/多模态视觉审查。

## 输出

`result.json` 保留 `case_id` 与原输出契约，并包含：

- `figures`：逐图路由、实际图型、准备记录、R 分析、独立图注、文件元数据/哈希、QA、全链复现结果、`layout_adjustment`（布局自适应记录）与 `format_gaps`（范围外格式缺口）；
- `data_to_visual_map`：源列 → 视觉通道、绘图 TSV 与行级 lineage；
- `baseline_comparison`：与基础 Agent/通用绘图方案的七维度确定性防御对照（图表选择/坐标轴/编码可读性/标注图注/导出规格/复现证据/异常降级），每项注明典型失败、本 Skill 处理与证据位置；平台侧公平对照由组织方自行运行，本表不虚构基线数字；
- `evidence_map`：源快照、运行计划、规则哈希、依赖、脚本和复现命令；
- `状态说明`：与 `status.overall` 一致的缺口、说明和不确定性。

所有交付文件位于 `/app/output`。可独立重跑：

```bash
cd /app/output
bash data_prep.sh
Rscript plot.R
python3 -B scripts/normalize_pdf.py --plan run_plan.json --out .
python3 -B scripts/audit_outputs.py --plan run_plan.json --out .
```

## 异常处理

| 情况 | 行为 |
|---|---|
| 案例不可解析、重复列名、缺字段 | 写诚实的错误/部分结果和缺口，不伪造图件 |
| 缺失/NaN/Inf/声明哨兵 | 排除该图不可用观测，保留源行号和原因 |
| 完全重复 | 默认保留并报告；仅在明确规则下去重 |
| 某组 n=1 | 画原始点，SD/SE/CI 为 null |
| 常量相关/零 X 方差 | 只画可用点，相关/拟合为 null |
| 类别或系列过多 | 不截断；自动增高画布/横向布局/收缩标签字号/分面并记录 `layout_adjustment`；超出最大高度时明示标签可能仍密集 |
| 热图变量多/标签长 | 画布按变量数与宽度增高；字典短名+剥离括号单位；角度 45°→90° 自适应；字号按格宽收缩（≥6pt）；>12 变量省略格内数值 |
| 日期刻度密集 | 刻度按画布宽度封顶抽稀，两行式 MM-DD/YYYY 标签 |
| 请求不支持的导出格式（如 emf） | 记录 `format_gaps` 范围外缺口并交付默认三格式，不判 partial、不静默 |
| 任务提供显著性结果 | 透传渲染：配对→括线+标注，单类别→组末端字母；图注注明方法与「未计算任何检验」，绝不自造星号 |
| R 包、字体、Pillow、pypdf 缺失 | 保存可复跑材料并明确未完成项；有网络时可按冷启动命令安装后复跑；matplotlib 可用时降级渲染真实 PNG（300dpi+，仅 PNG，PDF/SVG 缺口如实记录不伪造） |
| 数据文件路径失效 | 按多基目录回退解析；仍失败时报告全部尝试路径 |
| 单图渲染失败 | 其它图继续；该图写 `status=error` 和真实原因 |
| 任一出图或 QA 失败 | 整体状态为 partial，不写 complete |

## 自查

```bash
# 从仓库根目录运行
python3 -B answer/060_科研数据可视化/scripts/test_pipeline.py
python3 answer/_shared/verify.py --only 060
```

最小示例必须满足：通用契约 PASS、图件专项 PASS、每图三格式存在、QA 全过、两次全链运行哈希一致。重复运行时仅 `generated_at` 可变化。

## 方法来源与许可

- 本机 `sci-plot`：R/ggplot2、ggprism 主题、Arial/Cairo、svglite、Sturges、刻度覆盖与视觉 QA 规则。
- [Rimagination/easyplot](https://github.com/Rimagination/easyplot) 提交 `d150e01`：复用 `easyplot_metadata.py` 与 `easyplot_palette_audit.py`；MIT 原文在 `scripts/easyplot-LICENSE.txt`。
- 中文标签是本题输入保真要求：默认 Arial；只有实际标签需要 CJK 时选择真实中文字体并记录，不把缺字回退当成成功。

## 最小调用示例

```bash
cd answer/060_科研数据可视化
python3 -B scripts/pipeline.py \
  --case examples/min_example/input/case.json --out /tmp/figure060-demo
python3 -B scripts/validate.py \
  --case examples/min_example/input/case.json --result /tmp/figure060-demo/result.json
python3 -B scripts/validate_figures.py --result /tmp/figure060-demo/result.json
```

示例为 24 行合成数据，包含一个组间比较和一个相关图；示例输出只用于回归，不是正式案例结果。
