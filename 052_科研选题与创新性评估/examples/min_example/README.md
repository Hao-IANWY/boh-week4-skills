# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例。两个候选方向（D1「不确定性量化机器学习势函数迁移到硫化物电解质界面」、
  D2「预训练视觉 Transformer 用于聚合物电解质膜裂纹分割」）+ 前瞻筛选协议（IN1/IN2/EX1，其中
  REC07 被 IN1 剔除）+ 6 项资源约束 + 10 条池内记录（内联 abstract，无外部文件）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（不是手写答案）。
- `expected/report.md`：由 `scripts/render_report.py` 从同一份 `result.json` 投影生成的人类可读研究产物，与机器契约同源；正式运行时写到 `/app/output/report.md`。

```bash
cd answer/052_科研选题与创新性评估
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
diff <(python3 -c "import json;d=json.load(open('/tmp/demo/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))") \
     <(python3 -c "import json;d=json.load(open('examples/min_example/expected/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))")
```

预期：`validate.py` PASS，`diff` 无输出（幂等）。

示例输出要点：

| 项 | 值 |
|---|---|
| `status.overall` | `complete`（协议在位、主张全部可判定、正文全部可读） |
| 新颖性 | `novelty_scope=pool_observed`；D1-C1（方法）`covered`（最近邻 REC01，重合引文回链证据表）；D1-C2（数据）、D2-C1（场景迁移）`partially_covered` |
| 最近邻 | 每条主张给最近邻记录 + 五维逐维重合点（status/shared_terms）+ 逐字重合引文（`evidence_id` + `char_span`） |
| 可行性 | 前提三分源：P_COMPUTE/P_TIME/P_SKILL/P_ETHICS=`task_given`；P_DATA、P_BASELINE=`record_evidence`（带 `evidence_id` 回链）；level=`supported`，结论注明记录证据强度低于任务给定 |
| 建议 | `revise_claim_boundary`（covered 主张改述贡献边界），逐条引用 `constraints` 且 `not_claimed` 含「不声称已完成实验」 |
| 筛选 | `criterion_hits` 逐条留痕：10 条 screened / 9 included / 1 excluded（REC07 未命中 IN1） |

复算方式（全部外置在规则文件）：

- `claim_coverage = |主张 token ∩ 记录 token| / |主张 token|`，阈值 `covered≥0.5`、`partial≥0.25`（`rules/scoring.json`）；
- `novelty = 1 - mean(可判定主张的 max_similarity)`；每维四分（novelty/feasibility/value/risk）**并置不合成总分**；
- `feasibility = Σ(premise.strength) / total`，strength 按 `rules/scoring.json#premise_strength`（task_given=1.0、record_evidence=0.7、explicit_assumption=0.0）；
- `risk = Σ(item.weight) / risk_max_total`（假设前提、被覆盖主张、冲突骑乘分别计权）。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。4 案例真实仿真（含冲突骑乘、跨域零重合、
前提链、constraints/协议双缺失）见 `review/052_realtest/`。
