#!/usr/bin/env python3
"""052 回归测试：最小示例 + realtest 4 案例（合成数据，非正式测试案例）。纯标准库、离线。

校验面（对齐本题三大评分点）：
  * 最小示例：契约 PASS、与 expected 基线一致（忽略 generated_at）、协议先于执行落盘、
    novelty_scope=pool_observed、前提三分源、建议带 constraints 与 not_claimed；
  * case1 基准：逐候选方向评估齐全；covered/partially_covered 判定可复算；
    D1 骑在方向冲突证据上 → 冲突双列 + 风险条目标注两侧逐字引文；
    证据 quote 对来源文件（含自研 PDF 提取文本）做 char_span 独立复核；回链闭合；
  * case2 新颖性边界：跨域方向池内零重合 → not_found_in_scope 必须写
    「池内未见（pool_observed），不构成全球首创判定」+ 池外核验建议（executed=false）；
  * case3 前提链：P_TIME=task_given / P_DATA=record_evidence(evidence_id) /
    P_COMPUTE=explicit_assumption 三分源齐备；条件式结论随强度降级（若 P 成立则…）；
  * case4 鲁棒：constraints 缺失 → unconstrained + 方向级建议（无约束可依）；
    部分重合候选 → 逐维度重合依据；0 字节来源诚实降级；协议缺失 → input_gap；
  * 幂等：两次运行 result.json（除 generated_at）与 plan.json 逐字节一致。
"""
from __future__ import annotations

import hashlib
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
BUILDER = REPO_ROOT / "review" / "052_realtest" / "build_cases.py"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.path.insert(0, str(HERE))


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_same_without_generated(a: Path, b: Path) -> bool:
    da, db = load_result(a), load_result(b)
    da.pop("generated_at", None)
    db.pop("generated_at", None)
    return json.dumps(da, ensure_ascii=False, sort_keys=True) == json.dumps(db, sort_keys=True, ensure_ascii=False)


class TopicNoveltyAssessmentTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    root: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="topic052-test-")
        cls.root = Path(cls.tmp.name) / "runtime"
        proc = subprocess.run([sys.executable, "-B", str(BUILDER), str(cls.root)],
                              capture_output=True, text=True, timeout=120, env=ENV)
        if proc.returncode != 0:
            raise AssertionError(f"build_cases 失败：{proc.stderr + proc.stdout}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- 公共工具 ----
    def run_case(self, case_path: Path, work: Path, out: Path) -> dict:
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
        return self.run_case(case_path, work, out)

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
        bl = result["evidence"]["backlink"]
        self.assertEqual(bl["unresolved_ids"], [])
        for eid in bl["referenced_ids"]:
            self.assertIn(eid, ids)
        for row in result["feasibility_assessment"]["premises"]:
            if row["source"] == "record_evidence":
                self.assertTrue(row["evidence_id"], row["premise_id"])
                self.assertIn(row["evidence_id"], ids, row["premise_id"])
        for claim in result["novelty_assessment"]["claims"]:
            for eid in ((claim.get("nearest_prior") or {}).get("quote_evidence_ids") or []):
                self.assertIn(eid, ids, claim["claim_id"])

    # ---- 1. 最小示例 ----
    def test_min_example_matches_baseline(self):
        case_path = SKILL_DIR / "examples/min_example/input/case.json"
        out = Path(self.tmp.name) / "min_example_out"
        result = self.run_case(case_path, Path(self.tmp.name) / "min_example_work", out)
        self.assertEqual(result["status"]["overall"], "complete")
        self.assertEqual(result["screening"]["protocol_status"], "registered_prospective")
        self.assertEqual(result["screening"]["counts"],
                         {"screened": 10, "included": 9, "excluded": 1, "unscored_no_body": 0})
        # novelty_scope=pool_observed：scope 字段与范围声明逐字在位
        self.assertEqual(result["novelty_assessment"]["scope"], "pool_observed")
        self.assertIn("pool_observed", result["novelty_assessment"]["scope_statement"])
        # 判定三档齐备：covered + partially_covered；全部 claim scope=pool_observed
        verdicts = {c["claim_id"]: c["verdict"] for c in result["novelty_assessment"]["claims"]}
        self.assertEqual(verdicts.get("D1-C1"), "covered")
        self.assertIn("partially_covered", set(verdicts.values()))
        self.assertTrue(all(c["scope"] == "pool_observed" for c in result["novelty_assessment"]["claims"]))
        # 前提三分源：P_COMPUTE=task_given；P_DATA=record_evidence 带 evidence_id
        prem = {p["premise_id"]: p for p in result["feasibility_assessment"]["premises"]}
        self.assertEqual(prem["P_COMPUTE"]["source"], "task_given")
        self.assertEqual(prem["P_DATA"]["source"], "record_evidence")
        self.assertTrue(prem["P_DATA"]["evidence_id"])
        # 最近邻重合引文与逐维重合点在位
        c1 = next(c for c in result["novelty_assessment"]["claims"] if c["claim_id"] == "D1-C1")
        self.assertTrue(c1["nearest_prior"]["quote_evidence_ids"])
        self.assertTrue(c1["overlap_points"])
        # 建议引用 constraints 且 not_claimed
        self.assertTrue(result["proposals"]["items"])
        for p in result["proposals"]["items"]:
            self.assertTrue(p["constraint_refs"])
            self.assertIn("不声称已完成实验", " ".join(p["not_claimed"]))
        self.assert_all_quotes_verified(result)
        self.assert_backlink_closed(result)
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        self.assertTrue(expected.exists())
        self.assertTrue(load_same_without_generated(out / "result.json", expected), "与 expected 基线不一致")

    # ---- 2. case1 基准 ----
    def test_case1_direction_assessment_with_conflict_risk(self):
        result = self.realtest_case("case1_baseline", "t1")
        self.assertEqual(result["status"]["overall"], "complete",
                         json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 协议先于结果落盘：plan.json 内容与 result 一致且 mtime 不晚于 result
        case_dir = self.root / "input/case1_baseline"
        plan_path = self.root / "app/case1_baseline/work_t1/plan.json"
        self.assertTrue(plan_path.exists())
        plan = load_result(plan_path)
        self.assertEqual(plan["protocol"]["protocol_id"], "PROTO-C1-HARDCARBON")
        self.assertEqual(plan["protocol"]["protocol_status"], "registered_prospective")
        self.assertEqual(plan["protocol"]["search_order"], ["D1", "D2"])
        self.assertLessEqual(plan_path.stat().st_mtime_ns,
                             (self.root / "app/case1_baseline/out_t1/result.json").stat().st_mtime_ns)
        # 逐条筛选：协议记录与手册记录被 EX1 剔除；PDF 自研提取
        self.assertEqual(result["screening"]["counts"],
                         {"screened": 10, "included": 8, "excluded": 2, "unscored_no_body": 0})
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC01"]["outcome"], "excluded")
        self.assertEqual(by_rid["REC09"]["outcome"], "excluded")
        self.assertTrue(any(h["criterion_id"] == "EX1" and h["matched"] for h in by_rid["REC09"]["exclusion_hits"]))
        self.assertEqual(by_rid["REC09"]["extraction_method"], "pdf_extract")
        # 逐候选方向：两个方向的四维评估与问题陈述齐全
        self.assertEqual({ca["direction_id"] for ca in result["candidate_assessments"]}, {"D1", "D2"})
        for ca in result["candidate_assessments"]:
            self.assertIn("object", ca["problem_statement"])
            self.assertTrue(ca["four_dimension"]["weights"])
            self.assertIsNotNone(ca["novelty"]["scope"])
        # 覆盖判定可复算：D1-C1 covered（最近邻 REC03）、claim_coverage 用 kit.tokens 独立复算
        sys.path.insert(0, str(HERE))
        from kit import tokens
        claims = {c["claim_id"]: c for c in result["novelty_assessment"]["claims"]}
        self.assertEqual(claims["D1-C1"]["verdict"], "covered")
        self.assertEqual(claims["D1-C1"]["nearest_prior"]["record_id"], "REC03")
        self.assertEqual(claims["D1-C2"]["type"], "data")
        self.assertEqual(claims["D2-C1"]["type"], "scenario_transfer")
        pool_ids = {r["record_id"] for r in result["screening"]["records"] if r["outcome"] == "included"}
        pool_texts = {}
        import pdf_min
        from pipeline import _extract_docx
        bases = [case_dir, self.root / "app/case1_baseline/work_t1", Path.cwd()]
        for rec in load_result(case_dir / "case.json")["records"]:
            if rec["record_id"] not in pool_ids:
                continue
            body = str(rec.get("text", "") or "")
            if not body:
                ref = rec.get("source_path", "")
                path = None
                for b in bases:
                    cand = b / ref
                    if cand.is_file():
                        path = cand
                        break
                if path is not None:
                    if path.suffix == ".pdf":
                        body = pdf_min.extract_pdf_file(str(path))["text"]
                    elif path.suffix == ".docx":
                        body, _err = _extract_docx(path)
                    else:
                        body = path.read_text(encoding="utf-8")
            pool_texts[rec["record_id"]] = " ".join([rec.get("title", ""), rec.get("abstract", ""), body])
        ct = set(tokens(claims["D1-C1"]["claim"]))
        best = max(len(ct & set(tokens(t))) / len(ct) for t in pool_texts.values())
        self.assertAlmostEqual(claims["D1-C1"]["max_similarity"], round(best, 4), places=4)
        # D1 骑在冲突证据上：方向冲突双列 + 风险条目标注两侧逐字引文
        dconf = [c for c in result["conflicts"]["items"] if c["type"] == "direction_conflict"]
        self.assertTrue(dconf)
        self.assertTrue(all(c["direction_id"] == "D1" for c in dconf))
        conflict_risk = [r for ca in result["candidate_assessments"] if ca["direction_id"] == "D1"
                         for r in ca["risk"]["items"] if r["code"] == "evidence_conflict"]
        self.assertTrue(conflict_risk)
        ev_ids = {r["evidence_id"] for r in result["evidence_table"]}
        for r in conflict_risk:
            self.assertTrue(r["side_a_quote"] and r["side_b_quote"])
            for eid in r["evidence_ids"]:
                self.assertIn(eid, ev_ids)
            sides = [c for c in result["conflicts"]["items"] if c["conflict_id"]]
            quotes = {s["quote"] for c in result["conflicts"]["items"]
                      for s in (c["side_a"], c["side_b"])}
            self.assertIn(r["side_a_quote"], quotes)
            self.assertIn(r["side_b_quote"], quotes)
        # 全部引文独立复核 + 回链闭合
        self.assert_all_quotes_verified(result)
        self.assert_span_against_sources(result)
        self.assert_backlink_closed(result)
        # 建议逐条引用 constraints；covered 主张触发改述建议
        case_cons = load_result(case_dir / "case.json")["constraints"]
        for p in result["proposals"]["items"]:
            self.assertTrue(p["constraint_refs"])
            self.assertTrue(p["not_claimed"])
        self.assertTrue(any(p["kind"] == "revise_claim_boundary" for p in result["proposals"]["items"]))
        refs_flat = {x["ref"] for p in result["proposals"]["items"] for x in p["constraint_refs"]}
        self.assertTrue(refs_flat & set(case_cons.keys()))
        self.assertTrue(any(p["kind"] == "recheck_conflict" for p in result["proposals"]["items"]))

    # ---- 3. case2 新颖性边界 ----
    def test_case2_pool_observed_wording_and_external_check(self):
        result = self.realtest_case("case2_novelty", "t2")
        self.assertEqual(result["status"]["overall"], "complete")
        self.assertEqual(result["novelty_assessment"]["scope"], "pool_observed")
        claims = {c["claim_id"]: c for c in result["novelty_assessment"]["claims"]}
        d2 = claims["D2-C1"]
        self.assertEqual(d2["verdict"], "not_found_in_scope")
        self.assertEqual(d2["scope"], "pool_observed")
        # 评分措辞：池内未见（pool_observed），不构成全球首创判定
        self.assertIn("池内未见（pool_observed）", d2["basis"])
        self.assertIn("不构成全球首创判定", d2["basis"])
        # 池外核验建议：检索式 + 数据库 + executed=false + 未执行全局检索声明
        pc = d2["pool_external_check"]
        self.assertIsNotNone(pc)
        self.assertFalse(pc["executed"])
        self.assertTrue(pc["search_expressions"])
        self.assertTrue(pc["suggested_databases"])
        self.assertIn("未执行全局检索", pc["declaration"])
        # 对照：跨域方向严格低于同域方向的覆盖度；两者都只声明池内未见
        d1 = claims["D1-C1"]
        self.assertEqual(d1["verdict"], "not_found_in_scope")
        self.assertLess(d2["max_similarity"], d1["max_similarity"])
        self.assertIn("pool_observed", json.dumps(result["novelty_assessment"], ensure_ascii=False))
        # 建议：池外核验建议逐条引用 constraints 且带 not_claimed
        ext = [p for p in result["proposals"]["items"] if p["kind"] == "pool_external_verification"]
        self.assertTrue(ext)
        for p in ext:
            self.assertIn("未执行全局检索", p["statement"])
            self.assertIn("不声称全球首创", " ".join(p["not_claimed"]))
            self.assertTrue(p["constraint_refs"])
        # 不出现全球首创类断言
        self.assertNotIn("全球首创判定：是", json.dumps(result, ensure_ascii=False))
        self.assert_all_quotes_verified(result)
        self.assert_span_against_sources(result)

    # ---- 4. case3 前提链 ----
    def test_case3_premise_sources_and_conditional_degradation(self):
        result = self.realtest_case("case3_premise", "t3")
        self.assertEqual(result["status"]["overall"], "complete",
                         json.dumps(result["status"]["unmet"], ensure_ascii=False))
        prem = {p["premise_id"]: p for p in result["feasibility_assessment"]["premises"]}
        # 三分源：任务给定 / 记录证据（evidence_id）/ 显式假设
        self.assertEqual(prem["P_TIME"]["source"], "task_given")
        self.assertEqual(prem["P_DATA"]["source"], "record_evidence")
        self.assertTrue(prem["P_DATA"]["evidence_id"])
        self.assertEqual(prem["P_COMPUTE"]["source"], "explicit_assumption")
        self.assertEqual(prem["P_SKILL"]["source"], "explicit_assumption")
        # record_evidence 的 evidence_id 必须能解析到证据表且 quote 校验通过
        ids = {r["evidence_id"]: r for r in result["evidence_table"]}
        ev = ids[prem["P_DATA"]["evidence_id"]]
        self.assertTrue(ev["quote_verified"])
        self.assertEqual(ev["use"], "premise_evidence")
        # 条件式结论随强度降级：若 P 成立则可行 + 假设清单 + 记录证据强度注明
        fd = result["feasibility_assessment"]["per_direction"][0]
        self.assertEqual(fd["level"], "conditional")
        self.assertIn("若 P_COMPUTE", fd["conditional_conclusion"])
        self.assertIn("成立则可行", fd["conditional_conclusion"])
        self.assertIn("P_DATA 来自池内证据", fd["conditional_conclusion"])
        self.assertIn(prem["P_DATA"]["evidence_id"], fd["conditional_conclusion"])
        # 分数可复算：Σstrength/total 与 scoring.premise_strength 一致
        scoring = load_result(SKILL_DIR / "rules" / "scoring.json")
        strength = scoring["premise_strength"]
        expect_score = round(sum(strength[p["source"]] for p in prem.values()) / len(prem), 4)
        self.assertEqual(fd["score"], expect_score)
        self.assertAlmostEqual(fd["score"], round((1.0 + 0.7) / 6, 4), places=4)
        self.assertAlmostEqual(fd["supported_ratio"], round(2 / 6, 4), places=4)
        # 假设前提进风险：4 条假设前提各一条风险项（P_ETHICS 用 ethics_approval_missing 权重）
        premise_codes = {"premise_unsupported", "data_access_missing", "ethics_approval_missing"}
        risk_items = [r for ca in result["candidate_assessments"] for r in ca["risk"]["items"]
                      if r["code"] in premise_codes]
        self.assertEqual(len(risk_items), 4)
        self.assertAlmostEqual(sum(r["weight"] for r in risk_items), 3.8, places=4)
        self.assertTrue(any(r["code"] == "ethics_approval_missing" for r in risk_items))
        self.assert_backlink_closed(result)
        self.assert_all_quotes_verified(result)

    # ---- 5. case4 鲁棒 ----
    def test_case4_unconstrained_partial_overlap_and_degradation(self):
        result = self.realtest_case("case4_robust", "t4")
        self.assertEqual(result["status"]["overall"], "partial")
        # 协议缺失 → input_gap + 声明回退口径（不冒充已按协议筛选）
        self.assertEqual(result["screening"]["protocol_status"], "missing_fallback_declared")
        self.assertTrue(result["screening"]["records"])
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        unmet = json.dumps(result["status"]["unmet"], ensure_ascii=False)
        self.assertIn("R_PROTOCOL_MISSING", unmet)
        self.assertIn("记录正文", unmet)
        # 0 字节扫描件诚实降级；PDF/DOCX 自研提取
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC09"]["outcome"], "unscored_no_body")
        self.assertEqual(by_rid["REC09"]["body_chars"], 0)
        self.assertEqual(by_rid["REC07"]["extraction_method"], "pdf_extract")
        self.assertEqual(by_rid["REC08"]["extraction_method"], "docx_xml")
        text = __import__("pdf_min").extract_pdf_file(
            str(self.root / "input/case4_robust/sources/s07_standard_gb.pdf"))["text"]
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), by_rid["REC07"]["body_sha256"])
        # constraints 缺失 → unconstrained，不给「可行」结论
        fd = result["feasibility_assessment"]["per_direction"][0]
        self.assertEqual(fd["level"], "unconstrained")
        self.assertFalse(fd["constraints_present"])
        self.assertIn("无可依约束", fd["conditional_conclusion"])
        # 建议只到方向级并声明无约束可依
        self.assertTrue(result["proposals"]["items"])
        for p in result["proposals"]["items"]:
            self.assertEqual(p["constraint_refs"][0]["ref"], "unspecified")
            self.assertTrue(p["not_claimed"])
        self.assertTrue(any("无约束可依" in p["statement"] for p in result["proposals"]["items"]))
        # 部分重合：逐维度重合依据（explicit 维度来源 + 共享词）
        claim = result["novelty_assessment"]["claims"][0]
        self.assertEqual(claim["verdict"], "partially_covered")
        overlaps = [p for p in claim["overlap_points"] if p["status"] == "overlap"]
        self.assertGreaterEqual(len(overlaps), 2)
        for p in overlaps:
            self.assertTrue(p["shared_terms"])
            self.assertEqual(p["prior_dim_source"], "explicit")
        self.assertTrue(any(p["status"] in ("no_overlap", "prior_not_reported", "direction_not_specified")
                            for p in claim["overlap_points"]))
        # 最邻近是显式维度记录 REC01
        self.assertEqual(claim["nearest_prior"]["record_id"], "REC01")
        self.assert_all_quotes_verified(result)
        self.assert_span_against_sources(result)
        self.assert_backlink_closed(result)

    # ---- 6. 幂等 ----
    def test_idempotent_rerun_case1(self):
        case_path = self.root / "input" / "case1_baseline" / "case.json"
        outs = []
        for i in (1, 2):
            work = self.root / "app" / "case1_baseline" / f"idem_work_{i}"
            out = self.root / "app" / "case1_baseline" / f"idem_out_{i}"
            self.run_case(case_path, work, out)
            outs.append((work / "plan.json", out / "result.json"))
        self.assertTrue(load_same_without_generated(outs[0][1], outs[1][1]), "result.json 除 generated_at 外不一致")
        self.assertEqual(outs[0][0].read_bytes(), outs[1][0].read_bytes(), "plan.json 不幂等")


if __name__ == "__main__":
    unittest.main(verbosity=2)
