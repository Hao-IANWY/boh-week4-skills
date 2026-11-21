# 最小调用示例（合成，非正式测试案例）

`input/case.json` 含 24 行合成数据：3 个站点、每个站点 8 个样地；产量有 1 条缺失。两个工单分别请求组间均值 + SD 和产量×温度相关。

## 运行

```bash
cd answer/060_科研数据可视化
python3 -B scripts/pipeline.py \
  --case examples/min_example/input/case.json --out /tmp/figure060-demo
python3 -B scripts/validate.py \
  --case examples/min_example/input/case.json --result /tmp/figure060-demo/result.json
python3 -B scripts/validate_figures.py --result /tmp/figure060-demo/result.json
```

## 预期证据

- F1：原始 jitter 点 + 均值柱 + SD；各组 n=8/7/8，缺失值不插补。
- F2：散点 + 总体 OLS + 95% 置信带；颜色与形状共同区分站点；有效 n=23。
- 每图均有 PDF、600dpi PNG、可编辑 SVG、完整精度 TSV/CSV、解析后 spec、R 分析 JSON 和源行 lineage。
- Python 独立重算均值/SD/SE 与 OLS/Pearson，和 R 结果一致。
- easyplot 元数据/色板检查、PNG 边距检查通过。
- 第二次从 `source_data.tsv` 执行 `data_prep.sh + plot.R`，TSV、分析 JSON 和 PDF/PNG/SVG 哈希一致。
- `expected/result.json` 是真实运行生成的回归基线，不是手写答案；跨环境字体/依赖版本可能不同，应在同一环境重建基线。

## 独立复跑

```bash
cd /tmp/figure060-demo
bash data_prep.sh
Rscript plot.R
python3 -B scripts/normalize_pdf.py --plan run_plan.json --out .
python3 -B scripts/audit_outputs.py --plan run_plan.json --out .
```

所有数据均为合成示例，不代表任何正式案例或真实科研结论。
