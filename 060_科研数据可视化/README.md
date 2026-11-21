# 060｜科研数据可视化

> 分类：科研数据分析 ｜ 赛题：challenge-9562cc8d ｜ ARMI:2611.000056v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-9562cc8d)

本目录交付可运行的 `research-figure-builder` Skill。当前版本把原来的手写 SVG 双图型实现升级为：

```text
case + 数据字典 + 出图工单
→ 原始数据快照与运行计划
→ data_prep.sh / prepare_data.py
→ 干净 TSV + 行级 lineage
→ plot.R / ggplot2 / theme.R
→ PDF + 600dpi PNG + 可编辑 SVG
→ 独立数值复核 + easyplot 文件/色板审计 + 视觉边距
→ 第二次全链运行与 SHA-256 对比
→ result.json
```

## 已覆盖的评分缺口

| 原问题 | 当前修复 |
|---|---|
| 趋势、分布、构成只有路由没有 builder | R 执行层覆盖比较、散点拟合、趋势、直方图、堆叠/横条、分类计数、相关热图 |
| PDF 请求被忽略 | 每图默认 PDF/PNG/SVG 三件齐交，并逐格式检查 |
| NaN/Inf 导致无结果 | 非有限数按行排除并记录；全不可用时仍输出部分结果 |
| 只证明 spec→SVG 可重跑 | 从原始快照重跑数据整备、R 统计和三格式导出，并比较全部哈希 |
| 裸均值柱 | 默认按每组 n 选点图或箱线 + jitter；显式均值柱也叠加原始点 |
| n=1 误差线写 0 | SD/SE/CI 在 n<2 时为 null |
| 常量相关写 0 | 零方差/有效点不足时相关与拟合为 null，并说明原因 |
| 类别只画前 6 个 | 不截断类别；按行数自动增高画布、横向布局、收缩标签字号（≥6pt）或分面，调整写入 `layout_adjustment` |
| 热图标签糊成一团 | 画布按变量数与宽度增高；字典短名+剥离括号单位；45°/90° 自适应；字号按格宽收缩；>12 变量省略格内数值 |
| 日期刻度重叠 | 刻度按画布宽度封顶抽稀（`pretty` 结果强制 ≤n+1），MM-DD/YYYY 两行式标签 |
| 宽扁画布文字吃掉图面 | 基础字号由 min(宽, 1.55×高) 驱动；190×62mm 条图不再按宽度放大到 20pt |
| 大样本散点糊成色块 | 点径按「可用绘图面积/点数」收缩（1030 点从 3.3mm 降到 ~1.2mm） |
| 热图格内数字撑爆格子 | geom_text 按格宽定径（上限 ~9pt、下限 ~4.5pt），格 <5.5mm 或变量 >12 时省略 |
| 显著性凭空画星号 | 只透传任务提供的统计结果：配对→括线+标注、单类别→字母，图注注明方法与「未计算任何检验」 |
| 无 R 环境时整链无图 | matplotlib 降级渲染真实 PNG（300dpi+，含 CJK 字体与类别抖动点），仅 PNG；PDF/SVG 缺口如实记录不伪造 |
| 缺对照证据 | result.json 内置 `baseline_comparison` 七维度对照（典型失败 vs 本 Skill 处理 vs 证据位置），不虚构基线数字 |
| 完整主题擦除逐图微调 | `finish_plot` 支持 `extra_theme`，旋转/字号在 `theme_prism` 之后应用 |
| 边距检查误伤合格图 | 空白或贴边/裁切（<0.02in）才判失败；<0.1in 仅记 `tight` 标志 |
| /app/work 转工单后数据路径失效 | 相对路径多基目录回退解析（case 目录→各级 input/→CWD→/input），失败报全部尝试路径 |
| venue 键名写错静默回退 | `default_double_column`/`double_column`/`双栏` 等别名归一化并记录 |
| 请求不支持格式拖垮整图 | `format_gaps` 记录范围外缺口并交付默认三格式；支持范围内格式缺失才判 partial |
| 图例只靠颜色 | 散点用颜色 + 形状，趋势用颜色 + 线型 |
| 热图没有连续色阶核验 | Pearson 色阶固定 [-1,1]、0 为中心、带 colorbar；常量列保留 NA |
| 动态输出契约不足 | 支持原字段、常见别名与显式未产出缺口 |
| 状态不一致 | 任一出图、请求格式、QA 或复现失败均使整体状态保持 partial |

## 目录

- `SKILL.md`：Agent 调用契约与生产命令。
- `scripts/pipeline.py`：输入适配、计划冻结、运行、契约与状态汇总。
- `scripts/prepare_data.py`：缺失/重复/非有限数处理与行级血缘；不绘图。
- `scripts/data_prep.sh`：TSV 整备入口。
- `scripts/plot.R`：唯一绘图入口；逐图容错，R 统计与三格式导出。
- `scripts/theme.R`：便携 sci-plot 主题、字体和保存函数。
- `scripts/audit_outputs.py`：独立数值、文件、边距和色板 QA。
- `scripts/easyplot_metadata.py` / `easyplot_palette_audit.py`：easyplot 审计模块。
- `rules/`：图型、色板、版式和评分映射。
- `examples/min_example/`：合成最小示例与真实运行回归基线。
- `scripts/test_pipeline.py`：合成压力测试，不含正式案例答案。

## 依赖

- R：Rscript、ggplot2、ggprism、svglite、jsonlite、systemfonts。
- Python：3.10+；Pillow 与 pypdf 用于文件/视觉审计。
- 默认 Arial；输入明确包含中文时使用真实 CJK 字体并记录。缺字体或依赖时保留复现材料并标缺口。

本地安装示例：

```bash
Rscript -e 'install.packages(c("ggplot2","ggprism","svglite","jsonlite","systemfonts"), repos="https://cloud.r-project.org")'
python3 -m pip install --user Pillow pypdf
```

## 最小运行

```bash
cd answer/060_科研数据可视化
python3 -B scripts/pipeline.py \
  --case examples/min_example/input/case.json --out /tmp/figure060-demo
python3 -B scripts/validate.py \
  --case examples/min_example/input/case.json --result /tmp/figure060-demo/result.json
python3 -B scripts/validate_figures.py --result /tmp/figure060-demo/result.json
```

独立复跑交付目录：

```bash
cd /tmp/figure060-demo
bash data_prep.sh
Rscript plot.R
python3 -B scripts/normalize_pdf.py --plan run_plan.json --out .
python3 -B scripts/audit_outputs.py --plan run_plan.json --out .
```

## 验收

从仓库根目录运行：

```bash
python3 -B answer/060_科研数据可视化/scripts/test_pipeline.py
python3 answer/_shared/verify.py --only 060
```

验收要求：最小示例与合成压力用例通过；三格式真实存在；独立数值复核一致；PDF 字体、PNG DPI、SVG 文本、边距（空白/贴边判失败，偏紧记标志）与色板检查通过；两次全链哈希一致；文本资源不超过 50 个、单文件不超过 500KB、总量不超过 2MB。

真实数据集回归（非提交内容，用于开发验证）：UCI Concrete(1030×9 SQLite+中文标签热图/多龄期柱)、Iris bezdek(自然语言任务→Agent 转工单→/app/work 迁移运行)、Wine(178×14+emf 格式探针)与中文列名/日期/缺失/重复长表案例均已跑通 `complete`。

GitHub 附加分：公开仓库 [Hao-IANWY/research-figure-builder](https://github.com/Hao-IANWY/research-figure-builder)（单作品仓库，`GITHUB_URL.txt` 第一行为链接、第二行指定 `SKILL.md`）；已用未登录窗口验证可访问。

## 来源与边界

- 主题、导出和科研图型原则参考本机 `sci-plot`。
- 文件与色板审计复用 [Rimagination/easyplot](https://github.com/Rimagination/easyplot) 提交 `d150e01`，MIT 许可保存在 `scripts/easyplot-LICENSE.txt`。
- 完整 easyplot 色板注册表约 3.3MB，超过本题 2MB 资源预算，因此只保留实际使用的 Okabe–Ito 色板，不复制完整注册表。
- 中文标签保真是本题对 sci-plot 默认英文标签规则的场景适配；不擅自翻译科学字段。
- 自动 QA 证明文件、数值和部分视觉属性，不代替研究问题、实验设计或因果解释审查。
