#!/usr/bin/env python3
"""048 回归测试：最小示例 + realtest 4 案例（合成数据，非正式测试案例）。纯标准库、离线。

校验面：
  * 最小示例：契约 PASS、与 expected 基线一致（忽略 generated_at）、缺协议 → input_gap；
  * case1 基准：协议先于结果落盘（mtime + plan.json 内容）、逐条 criterion_hits、
    证据 quote 对来源文件做精确子串独立复核、回链覆盖率、边界化建议；
  * case2 冲突：方向词典成对冲突双列（+1 × -1 与 0 × ±1）、双方逐字引文、不写「尚存争议」；
  * case3 空白：零证据子问题 → 研究空白严格来自检索缺口、建议不越界；
  * case4 鲁棒：PDF 自研提取 / DOCX zip+xml / 缺正文诚实降级 / 协议缺失声明回退；
  * 幂等：两次运行 result.json（除 generated_at）与 plan.json 逐字节一致。
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
BUILDER = REPO_ROOT / "review" / "048_realtest" / "build_cases.py"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.path.insert(0, str(HERE))


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_same_without_generated(a: Path, b: Path) -> bool:
    da, db = load_result(a), load_result(b)
    da.pop("generated_at", None)
    db.pop("generated_at", None)
    return json.dumps(da, ensure_ascii=False, sort_keys=True) == json.dumps(db, ensure_ascii=False, sort_keys=True)


class DeepResearchTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    root: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="deepresearch048-test-")
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

    def realtest_case(self, dirname: str, tag: str) -> tuple:
        case_path = self.root / "input" / dirname / "case.json"
        work = self.root / "app" / dirname / f"work_{tag}"
        out = self.root / "app" / dirname / f"out_{tag}"
        return self.run_case(dirname, case_path, work, out), case_path, work, out

    def assert_all_quotes_verified(self, result: dict):
        for row in result["evidence_table"]:
            self.assertTrue(row["quote_verified"], f"{row['evidence_id']} 未通过管线内引文校验")

    def assert_span_against_sources(self, result: dict):
        """独立复核：quote 必须是来源文本（或自研提取文本）char_span 处的精确子串。"""
        import pdf_min
        from pipeline import _extract_docx

        for row in result["evidence_table"]:
            src, method = row["source_file"], row["extraction_method"]
            if src == "inline" and method in ("inline_text", "inline_abstract"):
                continue
            if method == "pdf_extract":
                text = pdf_min.extract_pdf_file(src)["text"]
            elif method == "docx_xml":
                text, err = _extract_docx(Path(src))
                self.assertEqual(err, "")
            else:
                text = Path(src).read_text(encoding="utf-8")
            start, end = row["char_span"]["start"], row["char_span"]["end"]
            self.assertGreaterEqual(start, 0, row["evidence_id"])
            self.assertEqual(text[start:end], row["quote"], row["evidence_id"])

    def assert_backlink_closed(self, result: dict):
        ids = {r["evidence_id"] for r in result["evidence_table"]}
        for concl in result["conclusions"]:
            for eid in concl["evidence_ids"]:
                self.assertIn(eid, ids, concl["conclusion_id"])
        claimed = [c for c in result["conclusions"] if c["support_level"] != "no_evidence"]
        self.assertTrue(claimed)
        self.assertEqual(result["evidence"]["backlink"]["coverage"], 1.0)

    # ---- 1. 最小示例 ----
    def test_min_example_matches_baseline_and_declares_protocol_gap(self):
        case_path = SKILL_DIR / "examples/min_example/input/case.json"
        out = Path(self.tmp.name) / "min_example_out"
        result = self.run_case("min_example", case_path, Path(self.tmp.name) / "min_example_work", out)
        self.assertEqual(result["status"]["overall"], "partial")
        self.assertEqual(result["research_plan"]["protocol"]["protocol_status"], "missing_fallback_declared")
        self.assertIn("R_PROTOCOL_MISSING", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        self.assertEqual(result["screening"]["counts"]["screened"], 6)
        self.assertEqual({c["type"] for c in result["conflicts"]["items"]}, {"numeric_conflict"})
        self.assertIn("52.3", json.dumps(result["conflicts"]["items"], ensure_ascii=False))
        self.assert_all_quotes_verified(result)
        self.assert_backlink_closed(result)
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        self.assertTrue(expected.exists())
        self.assertTrue(load_same_without_generated(out / "result.json", expected), "与 expected 基线不一致")

    # ---- 2. case1 基准 ----
    def test_case1_full(self):
        (res_bundle, case_path, work, out) = self.realtest_case("case1_baseline", "t1")
        result = res_bundle
        self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 协议先于结果落盘：plan.json 在 work，且内容与 result 的协议一致，mtime 不晚于 result
        plan_path = work / "plan.json"
        self.assertTrue(plan_path.exists())
        plan = load_result(plan_path)
        self.assertEqual(plan["protocol"]["protocol_id"], "PROTO-C1-FEC/DTD")
        self.assertEqual(plan["protocol"]["protocol_status"], "registered_prospective")
        self.assertEqual(plan["protocol"]["search_order"], ["SQ1", "SQ2", "SQ3", "SQ4"])
        self.assertEqual(plan["protocol"]["protocol_id"], result["research_plan"]["protocol"]["protocol_id"])
        self.assertLessEqual(plan_path.stat().st_mtime_ns, (out / "result.json").stat().st_mtime_ns)
        # 逐条筛选：10 条记录都有 criterion 记录；协议记录与手册记录被 EX1 剔除
        self.assertEqual(result["screening"]["counts"], {"screened": 10, "included": 8, "excluded": 2, "unscored_no_body": 0})
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC01"]["outcome"], "excluded")
        self.assertTrue(any(h["criterion_id"] == "EX1" and h["matched"] for h in by_rid["REC01"]["exclusion_hits"]))
        self.assertEqual(by_rid["REC09"]["extraction_method"], "pdf_extract")
        self.assertTrue(all(r["inclusion_hits"] or r["exclusion_hits"] for r in result["screening"]["records"]))
        # 检索语料只含纳入记录；证据引文独立复核 + 回链闭合
        corpus = result["evidence"]["execution"]["corpus"]
        self.assertEqual(sum(1 for c in corpus if c["included"]), 8)
        self.assert_all_quotes_verified(result)
        self.assert_span_against_sources(result)
        self.assert_backlink_closed(result)
        self.assertEqual(len(result["evidence_table"]), 12)
        # 边界化建议
        self.assertTrue(result["proposals"]["items"])
        for p in result["proposals"]["items"]:
            self.assertTrue(p["constraint_refs"])
            self.assertTrue(p["not_claimed"])
            self.assertIn("不声称已完成实验", " ".join(p["not_claimed"]))

    # ---- 3. case2 冲突 ----
    def test_case2_direction_conflicts_dual_column(self):
        result, *_ = self.realtest_case("case2_conflict", "t2")
        types = {c["type"] for c in result["conflicts"]["items"]}
        self.assertIn("direction_conflict", types)
        self.assertIn("zero_vs_direction", types)
        dconf = [c for c in result["conflicts"]["items"] if c["type"] == "direction_conflict"]
        self.assertTrue(dconf)
        directions = set()
        for c in result["conflicts"]["items"]:
            sides = (c["side_a"], c["side_b"])
            directions |= {s["direction"] for s in sides}
            for s in sides:
                self.assertTrue(s["quote"], c["conflict_id"])
            self.assertIn("不择一", c["resolution"])
            self.assertIn("双列", c["resolution"])
        self.assertEqual(directions, {"+1", "-1", "0"})  # 三种方向都被双列留痕
        # 零效应记录（REC04）必须出现在冲突双列里
        involved = {s["record_id"] for c in result["conflicts"]["items"] for s in (c["side_a"], c["side_b"])}
        self.assertIn("REC04", involved)
        # 冲突子问题的结论不合并双方，support_level=conflicting，dual_column 逐字
        sq1 = next(c for c in result["conclusions"] if c["subquestion_id"] == "SQ1")
        self.assertEqual(sq1["support_level"], "conflicting")
        self.assertTrue(sq1["dual_column"])
        quotes = {d["side_a"]["quote"] for d in sq1["dual_column"]} | {d["side_b"]["quote"] for d in sq1["dual_column"]}
        self.assertTrue(quotes)
        self.assert_span_against_sources(result)
        self.assert_all_quotes_verified(result)
        self.assertEqual(result["status"]["overall"], "complete")  # 冲突如实双列是成功交付，不是缺口

    # ---- 4. case3 空白 ----
    def test_case3_gap_only_from_retrieval_hole_and_bounded_proposal(self):
        result, *_ = self.realtest_case("case3_gap", "t3")
        gaps = result["research_gaps"]["items"]
        self.assertEqual([g["subquestion_id"] for g in gaps], ["SQ4"])
        self.assertEqual(gaps[0]["code"], "R_NO_EVIDENCE")
        self.assertEqual(gaps[0]["basis"], "zero_evidence")
        no_ev = {c["subquestion_id"] for c in result["conclusions"] if c["support_level"] == "no_evidence"}
        gap_subs = {g["subquestion_id"] for g in gaps}
        self.assertEqual(no_ev, gap_subs)  # 空白严格来自零证据子问题
        self.assertNotIn("领域尚属空白", json.dumps(gaps, ensure_ascii=False))
        self.assertTrue(result["proposals"]["items"])
        pr = "\n".join(p["statement"] for p in result["proposals"]["items"])
        self.assertIn("SQ4", pr)  # 建议明确引用缺口
        for p in result["proposals"]["items"]:
            self.assertTrue(p["constraint_refs"])
            self.assertTrue(p["not_claimed"])
        self.assertNotIn("已完成实验", pr)
        self.assert_backlink_closed(result)

    # ---- 5. case4 鲁棒 ----
    def test_case4_pdf_docx_extraction_and_honest_degradation(self):
        result, *_ = self.realtest_case("case4_robust", "t4")
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC07"]["extraction_method"], "pdf_extract")
        self.assertGreater(by_rid["REC07"]["body_chars"], 100)
        self.assertEqual(by_rid["REC08"]["extraction_method"], "docx_xml")
        self.assertEqual(by_rid["REC09"]["outcome"], "unscored_no_body")
        self.assertEqual(by_rid["REC09"]["body_chars"], 0)
        self.assertEqual(result["research_plan"]["protocol"]["protocol_status"], "missing_fallback_declared")
        self.assertTrue(result["research_plan"]["protocol"]["input_gap"])
        unmet = json.dumps(result["status"]["unmet"], ensure_ascii=False)
        self.assertIn("R_PROTOCOL_MISSING", unmet)
        self.assertIn("记录正文", unmet)
        self.assertEqual(result["status"]["overall"], "partial")
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        # PDF 提取文本哈希可独立复算；引文在提取文本上是精确子串
        import pdf_min
        text = pdf_min.extract_pdf_file(str(self.root / "input/case4_robust/sources/s07_standard_gb.pdf"))["text"]
        import hashlib
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), by_rid["REC07"]["body_sha256"])
        self.assert_span_against_sources(result)
        self.assert_all_quotes_verified(result)

    # ---- 6. 幂等 ----
    def test_idempotent_rerun_case1(self):
        case_path = self.root / "input" / "case1_baseline" / "case.json"
        outs = []
        for i in (1, 2):
            work = self.root / "app" / "case1_baseline" / f"idem_work_{i}"
            out = self.root / "app" / "case1_baseline" / f"idem_out_{i}"
            self.run_case("case1_baseline", case_path, work, out)
            outs.append((work / "plan.json", out / "result.json"))
        self.assertTrue(load_same_without_generated(outs[0][1], outs[1][1]), "result.json 除 generated_at 外不一致")
        self.assertEqual(outs[0][0].read_bytes(), outs[1][0].read_bytes(), "plan.json 不幂等")


if __name__ == "__main__":
    unittest.main(verbosity=2)
