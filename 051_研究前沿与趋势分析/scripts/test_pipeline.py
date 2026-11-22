#!/usr/bin/env python3
"""051 回归测试：最小示例 + realtest 4 案例（合成数据，非正式测试案例）。纯标准库、离线。

校验面：
  * 最小示例：契约 PASS、与 expected 基线一致（忽略 generated_at）、缺协议 → input_gap、
    每条 bucketed 趋势主张都带分桶计数与逐字代表记录；
  * case1 基准：9 来源 + 10 记录、协议记录/手册记录被 EX1 剔除、PDF 自研提取、
    证据 quote 对来源文件做 char_span 独立复核、方向冲突双列（+1 × -1）、快照范围声明；
  * case2 趋势判定：rising（cell_integration）+ 前沿候选（近桶新起 + 基数对比）、
    established（dendrite_mechanism，不下衰退结论）、thin（computation 只报计数不写趋势句）；
  * case3 词频陷阱：『机器学习势』raw_hits 高但 distinct_records=1 → 热度坍缩、
    不得出现在任何趋势主张里；对照主题按记录级分桶如实判 established/flat；
  * case4 鲁棒：缺年份主题 no_year_data（只报计数并声明）、协议缺失 input_gap + 回退口径、
    无文本来源 unscored_no_body、超短记录被回退纳入标准剔除、SQ4 零证据 → 检索缺口、
    rising 但非新起 → 如实判非前沿；
  * 幂等：两次运行（不同 PYTHONHASHSEED）result.json 除 generated_at 逐字节一致。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
PIPELINE = HERE / "pipeline.py"
REPO_ROOT = SKILL_DIR.parent.parent
BUILDER = REPO_ROOT / "review" / "051_realtest" / "build_cases.py"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.path.insert(0, str(HERE))

BUCKET_LABELS = ["2019-2020", "2021-2022", "2023-2024", "2025-2025"]


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_same_without_generated(a: Path, b: Path) -> bool:
    da, db = load_result(a), load_result(b)
    da.pop("generated_at", None)
    db.pop("generated_at", None)
    return json.dumps(da, ensure_ascii=False, sort_keys=True) == json.dumps(db, ensure_ascii=False, sort_keys=True)


class FrontierTrendTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    root: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="frontier051-test-")
        cls.root = Path(cls.tmp.name) / "runtime"
        proc = subprocess.run([sys.executable, "-B", str(BUILDER), str(cls.root)],
                              capture_output=True, text=True, timeout=120, env=ENV)
        if proc.returncode != 0:
            raise AssertionError(f"build_cases 失败：{proc.stderr + proc.stdout}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- 公共工具 ----
    def run_case(self, dirname: str, case_path: Path, work: Path, out: Path) -> dict:
        out.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path),
                               "--work", str(work), "--out", str(out)],
                              capture_output=True, text=True, timeout=300, env=ENV)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result_path = out / "result.json"
        self.assertTrue(result_path.exists(), proc.stderr + proc.stdout)
        val = subprocess.run([sys.executable, "-B", str(HERE / "validate.py"), "--case", str(case_path),
                              "--result", str(result_path)], capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(val.returncode, 0, val.stdout + val.stderr)
        return load_result(result_path)

    def realtest_case(self, dirname: str, tag: str) -> dict:
        case_path = self.root / "input" / dirname / "case.json"
        work = self.root / "app" / dirname / f"work_{tag}"
        out = self.root / "app" / dirname / f"out_{tag}"
        return self.run_case(dirname, case_path, work, out)

    def claim_of(self, result: dict, topic: str) -> dict:
        return next(c for c in result["frontier_trends"]["claims"] if c["topic"] == topic)

    def assert_all_quotes_verified(self, result: dict):
        for row in result["evidence_table"]:
            self.assertTrue(row["quote_verified"], f"{row['evidence_id']} 未通过管线内引文校验")

    def assert_spans_against_sources(self, result: dict):
        """独立复核：文件类来源的 quote 必须是来源文本（或自研提取文本）char_span 处的精确子串；
        内联记录行由管线内精确子串校验覆盖，这里跳过。"""
        import pdf_min
        for row in result["evidence_table"]:
            src, method = Path(row["source_file"]), row["extraction_method"]
            if str(row["source_file"]) in ("", "inline"):
                continue
            if method == "pdf_extract":
                text = pdf_min.extract_pdf_file(str(src))["text"]
            else:
                text = src.read_text(encoding="utf-8")
            start, end = row["char_span"]["start"], row["char_span"]["end"]
            self.assertGreaterEqual(start, 0, row["evidence_id"])
            self.assertEqual(text[start:end], row["quote"], f"{row['evidence_id']} span 不匹配")

    def assert_claims_wellformed(self, result: dict):
        """趋势句必带分桶计数与代表记录；主张不得引用词频 raw_hits。"""
        claims = result["frontier_trends"]["claims"]
        self.assertTrue(claims)
        for c in claims:
            if c["verdict"] in ("rising", "established", "flat"):
                self.assertTrue(c["bucket_table"], f"{c['topic']} 缺证据桶表")
                self.assertTrue(c["representative_records"], f"{c['topic']} 缺代表记录")
                self.assertTrue(c["basis"], f"{c['topic']} 缺判定依据")
                self.assertEqual(c["claim_scope"], "pool_snapshot")
                for b in c["bucket_table"]:
                    self.assertEqual(b["count"], len(b["record_ids"]), f"{c['topic']} 桶计数与 record_ids 不一致")
                for r in c["representative_records"]:
                    self.assertTrue(r["quote"], f"{c['topic']} 代表记录缺逐字引文")
                    self.assertTrue(r["quote_verified"], f"{c['topic']} 代表引文未通过校验")
            elif c["verdict"] == "thin":
                self.assertIn("不作趋势主张", c["claim"], f"{c['topic']} thin 却写了趋势句")
            elif c["verdict"] == "no_year_data":
                self.assertIn("不做时间趋势", c["claim"])
        blob = json.dumps(result["frontier_trends"], ensure_ascii=False)
        self.assertNotIn("raw_hits", blob, "趋势/前沿主张不得引用词频 raw_hits")
        # 快照范围声明
        scope = result["snapshot_scope"]
        self.assertEqual(scope["claim_scope"], "pool_snapshot")
        self.assertIn("池内快照", scope["note"])

    # ---- 1. 最小示例 ----
    def test_min_example_matches_baseline_and_declares_protocol_gap(self):
        case_path = SKILL_DIR / "examples/min_example/input/case.json"
        out = Path(self.tmp.name) / "min_example_out"
        result = self.run_case("min_example", case_path, Path(self.tmp.name) / "min_example_work", out)
        self.assertEqual(result["status"]["overall"], "partial")
        self.assertEqual(result["screening"]["protocol_status"], "missing_fallback_declared")
        self.assertIn("R_PROTOCOL_MISSING", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        labels = result["snapshot_scope"]["buckets"]
        self.assertEqual(labels, BUCKET_LABELS)
        self.assert_claims_wellformed(result)
        self.assert_all_quotes_verified(result)
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        self.assertTrue(expected.exists())
        self.assertTrue(load_same_without_generated(out / "result.json", expected), "与 expected 基线不一致")

    # ---- 2. case1 基准 ----
    def test_case1_baseline_full(self):
        result = self.realtest_case("case1_baseline", "t1")
        self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 协议注册 + 逐条筛选：10 条记录全有 criterion 留痕；协议记录与手册记录被 EX1 剔除
        self.assertEqual(result["screening"]["protocol_status"], "registered_prospective")
        self.assertEqual(result["screening"]["counts"],
                         {"screened": 10, "included": 8, "excluded": 2, "unscored_no_body": 0})
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        for rid in ("REC01", "REC09"):
            self.assertEqual(by_rid[rid]["outcome"], "excluded", rid)
            self.assertTrue(any(h["criterion_id"] == "EX1" and h["matched"] for h in by_rid[rid]["exclusion_hits"]), rid)
        self.assertTrue(all(r["inclusion_hits"] or r["exclusion_hits"] for r in result["screening"]["records"]))
        # PDF 来源自研提取
        self.assertEqual(by_rid["REC09"]["extraction_method"], "pdf_extract")
        self.assertGreater(by_rid["REC09"]["body_chars"], 50)
        # 来源清单 9 份且全部存在
        manifest = result["evidence"]["sources_manifest"]
        self.assertEqual(manifest["count"], 9)
        self.assertTrue(all(s["exists"] for s in manifest["sources"]))
        # 逐字引文独立复核（含 PDF 提取文本）+ 方向冲突双列
        self.assert_all_quotes_verified(result)
        self.assert_spans_against_sources(result)
        types = {c["type"] for c in result["evidence"]["conflicts"]["items"]}
        self.assertIn("direction_conflict", types)
        for c in result["evidence"]["conflicts"]["items"]:
            self.assertTrue(c["side_a"]["quote"] and c["side_b"]["quote"], c["conflict_id"])
            self.assertIn("不择一", c["resolution"])
        directions = {s["direction"] for c in result["evidence"]["conflicts"]["items"]
                      for s in (c["side_a"], c["side_b"])}
        self.assertIn("+1", directions)
        self.assertIn("-1", directions)
        self.assert_claims_wellformed(result)
        # 代表作（阶段级三口径）全覆盖
        self.assertEqual(result["representative_works"]["period_coverage"], 1.0)
        self.assertTrue(result["representative_works"]["citations_available"])

    # ---- 3. case2 趋势判定 ----
    def test_case2_verdicts_rising_established_thin(self):
        result = self.realtest_case("case2_verdict", "t2")
        self.assertEqual(result["status"]["overall"], "complete")
        # rising + 前沿候选（近桶新起 + 基数对比）
        cell = self.claim_of(result, "cell_integration")
        self.assertEqual(cell["verdict"], "rising")
        self.assertTrue(cell["frontier"]["is_frontier_candidate"])
        self.assertTrue(cell["frontier"]["new_start"])
        self.assertEqual(cell["frontier"]["prior_bucket_counts"]["2019-2020"], 0)
        self.assertEqual(cell["frontier"]["near_bucket"]["count"], 2)
        base = cell["frontier"]["base_comparison"]
        self.assertLess(base["base_value"], base["claim_topic_near_count"])
        self.assertTrue(base["base_topics"])
        self.assertIn("前沿候选", cell["claim"])
        self.assertIn("相对增量明确", cell["claim"])
        self.assertEqual([b["count"] for b in cell["bucket_table"]], [0, 1, 1, 2])
        # established：记录集中在早期桶，不下衰退结论
        dend = self.claim_of(result, "dendrite_mechanism")
        self.assertEqual(dend["verdict"], "established")
        self.assertEqual([b["count"] for b in dend["bucket_table"]][:2], [2, 1])
        self.assertIn("不下全局成熟/衰退结论", dend["basis"])
        # thin：只有 1 条记录的主题不作趋势主张
        comp = self.claim_of(result, "computation")
        self.assertEqual(comp["verdict"], "thin")
        self.assertEqual(len(comp["record_ids"]), 1)
        self.assertIn("证据太薄", comp["claim"])
        self.assertIn("R_EVIDENCE_THIN", json.dumps(result["topic_evolution"]["evidence_thin"], ensure_ascii=False))
        self.assert_claims_wellformed(result)

    # ---- 4. case3 词频陷阱 ----
    def test_case3_heat_collapse_not_trend(self):
        result = self.realtest_case("case3_heat", "t3")
        self.assertEqual(result["status"]["overall"], "complete")
        rows = result["keyword_heat"]["rows"]
        trap = next(r for r in rows if r["keyword"] == "机器学习势")
        self.assertGreaterEqual(trap["raw_hits"], 8)
        self.assertEqual(trap["distinct_records"], 1)
        self.assertTrue(trap["heat_collapse"])
        self.assertIn("热度坍缩", trap["collapse_reason"])
        self.assertIn("R_HEAT_COLLAPSED", json.dumps(result["uncertainty"], ensure_ascii=False))
        # 该关键词所属主题只有 1 条记录 → thin，不得冒充趋势/前沿
        # （代表记录的题名/逐字引文属于来源原文，不受限制；趋势主张文本与判定依据不得以词频为据）
        comp = self.claim_of(result, "computation")
        self.assertEqual(comp["verdict"], "thin")
        self.assertLessEqual(len(comp["record_ids"]), 1)
        for c in result["frontier_trends"]["claims"]:
            self.assertNotIn("机器学习势", c["claim"] + c["basis"],
                             f"{c['topic']} 的趋势主张文本不得引用词频陷阱关键词")
        # 对照主题按记录级分桶如实判定
        self.assertEqual(self.claim_of(result, "interface_engineering")["verdict"], "established")
        self.assertEqual(self.claim_of(result, "ion_transport")["verdict"], "flat")
        self.assert_claims_wellformed(result)

    # ---- 5. case4 鲁棒 ----
    def test_case4_robust_degradations(self):
        result = self.realtest_case("case4_robust", "t4")
        self.assertEqual(result["status"]["overall"], "partial")
        unmet = json.dumps(result["status"]["unmet"], ensure_ascii=False)
        # 协议缺失 → input_gap + 声明回退口径
        self.assertEqual(result["screening"]["protocol_status"], "missing_fallback_declared")
        self.assertTrue(result["evidence"]["protocol"]["input_gap"])
        self.assertIn("R_PROTOCOL_MISSING", unmet)
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        # 无文本来源 unscored_no_body；超短记录被回退纳入标准剔除
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC01"]["outcome"], "unscored_no_body")
        self.assertEqual(by_rid["REC01"]["body_chars"], 0)
        self.assertEqual(by_rid["REC09"]["outcome"], "excluded")
        self.assertTrue(any(h["criterion_id"] == "FB-IN1" and not h["matched"]
                            for h in by_rid["REC09"]["inclusion_hits"]), "超短记录未被 FB-IN1 剔除")
        self.assertIn("记录正文", unmet)
        # 缺年份主题：不做时间趋势、只报池内计数并声明
        sust = self.claim_of(result, "sustainability")
        self.assertEqual(sust["verdict"], "no_year_data")
        self.assertEqual(len(sust["record_ids"]), 2)
        self.assertEqual(sust["bucket_stats"]["records_without_year"], sust["record_ids"])
        self.assertIn("时间趋势:sustainability", unmet)
        self.assertIn("年份字段全缺", sust["claim"])
        # rising 但非新起 → 如实判非前沿
        dend = self.claim_of(result, "dendrite_mechanism")
        self.assertEqual(dend["verdict"], "rising")
        self.assertFalse(dend["frontier"]["is_frontier_candidate"])
        self.assertFalse(dend["frontier"]["new_start"])
        self.assertIn("非新起", dend["frontier"]["reason_if_not"])
        self.assertIn("非前沿候选", dend["claim"])
        # 空白只来自检索缺口：SQ4 零证据
        gaps = result["research_gaps"]["gaps"]
        sq4 = next(g for g in gaps if g.get("subquestion_id") == "SQ4")
        self.assertEqual(sq4["type"], "retrieval_gap")
        self.assertEqual(sq4["basis"], "zero_evidence")
        self.assertEqual(sq4["evidence_record_ids"], [])
        self.assertIn("检索缺口", json.dumps(result["research_gaps"], ensure_ascii=False))
        self.assertNotIn("领域尚属空白", json.dumps(gaps, ensure_ascii=False))
        self.assertTrue(result["research_gaps"]["forbidden_phrase_check"]["clean"])
        self.assert_claims_wellformed(result)
        self.assert_all_quotes_verified(result)

    # ---- 6. 幂等 ----
    def test_idempotent_rerun_case1(self):
        case_path = self.root / "input" / "case1_baseline" / "case.json"
        outs = []
        for i, seed in ((1, "1"), (2, "42")):
            work = self.root / "app" / "case1_baseline" / f"idem_work_{i}"
            out = self.root / "app" / "case1_baseline" / f"idem_out_{i}"
            env = {**ENV, "PYTHONHASHSEED": seed}
            out.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path),
                                   "--work", str(work), "--out", str(out)],
                                  capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            outs.append(out / "result.json")
        self.assertTrue(load_same_without_generated(outs[0], outs[1]), "result.json 除 generated_at 外不一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)
