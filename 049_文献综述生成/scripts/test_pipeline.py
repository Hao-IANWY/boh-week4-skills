#!/usr/bin/env python3
"""049 回归测试：最小示例 + realtest 4 案例 + 内联边界案例（合成数据，非正式测试案例）。

校验面（对齐 049 评分死穴「按通用框架套主题、抹平冲突」）：
  * 最小示例：契约 PASS、与 expected 基线一致（忽略 generated_at）、缺协议 → input_gap、
    DOI 去重、数值冲突（52.3% vs 49.0%，同数据集同方法口径）双列、引文精确子串；
  * case1 基准：协议先于结果落盘（mtime 时序 + plan.json 与结果协议一致）、逐条 criterion_hits、
    主题全部由证据聚类生成（topic_source=evidence_clustered，无通用框架节标题）、
    证据 quote 对来源文件做 char_span 独立复核（含自研 PDF 提取）、覆盖声明、docx 产物；
  * case2 冲突：方向词典成对冲突双列（direction_conflict +1×-1 与 zero_vs_direction 0×±1）、
    双方逐字引文、不写「尚存争议」、综述正文含双列段落；
  * case3 薄证据：案例声明主题锚点（case_declared）、TH4 只有 1 条证据 → thin=True +
    R_TOPIC_THIN_EVIDENCE，综述写「证据薄弱声明」而非硬凑章节；
  * case4 鲁棒：PDF 无侧车自研提取 / DOCX zip+xml / 缺正文记录 unscored_no_body 诚实降级 /
    协议缺失 input_gap + 声明回退口径（fallback=true）；
  * 内联边界：未聚类记录单列（R_TOPIC_UNASSIGNED）+ DOI 去重（R_DUP_DOI）；
  * 幂等：两次运行 result.json（除 generated_at）逐字节一致，plan.json 字节一致。
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
BUILDER = REPO_ROOT / "review" / "049_realtest" / "build_cases.py"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.path.insert(0, str(HERE))


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_same_without_generated(a: Path, b: Path) -> bool:
    da, db = load_result(a), load_result(b)
    da.pop("generated_at", None)
    db.pop("generated_at", None)
    return json.dumps(da, ensure_ascii=False, sort_keys=True) == json.dumps(db, ensure_ascii=False, sort_keys=True)


class LiteratureReviewTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    root: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="litreview049-test-")
        cls.root = Path(cls.tmp.name) / "runtime"
        proc = subprocess.run([sys.executable, "-B", str(BUILDER), str(cls.root)],
                              capture_output=True, text=True, timeout=120, env=ENV)
        if proc.returncode != 0:
            raise AssertionError(f"build_cases 失败：{proc.stderr + proc.stdout}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- 公共工具 ----
    def run_case(self, case_path: Path, work: Path, out: Path, case_json: Path | None = None) -> dict:
        out.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path),
                               "--work", str(work), "--out", str(out)],
                              capture_output=True, text=True, timeout=300, env=ENV)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result_path = out / "result.json"
        self.assertTrue(result_path.exists(), proc.stderr + proc.stdout)
        val = subprocess.run([sys.executable, "-B", str(HERE / "validate.py"), "--case",
                              str(case_json or case_path), "--result", str(result_path)],
                             capture_output=True, text=True, timeout=60, env=ENV)
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

    def assert_spans_against_sources(self, result: dict, case_json: Path):
        """独立复核：quote 必须是来源文本（或自研提取文本）char_span 处的精确子串。"""
        import pdf_min
        from pipeline import _extract_docx

        case = json.loads(case_json.read_text(encoding="utf-8"))
        by_id = {r.get("record_id"): r for r in case.get("records", [])}
        for row in result["evidence_table"]:
            src, method, field = row["source_file"], row["extraction_method"], row["quote_field"]
            if method == "inline_abstract":
                # 管线 resolve_body：无内联 text 时以 abstract 作为正文（rec["text"]=rec["abstract"]）
                text = str((by_id[row["record_id"]] or {}).get("abstract") or "")
            elif method == "inline_text":
                raw = by_id[row["record_id"]] or {}
                body = raw.get("text") or raw.get("content") or raw.get("body") or ""
                text = str(body if isinstance(body, str) else "\n".join(str(x) for x in body))
            elif method == "pdf_extract":
                text = pdf_min.extract_pdf_file(src)["text"]
            elif method == "docx_xml":
                text, err = _extract_docx(Path(src))
                self.assertEqual(err, "", row["evidence_id"])
            else:
                text = Path(src).read_text(encoding="utf-8")
            start, end = row["char_span"]["start"], row["char_span"]["end"]
            self.assertGreaterEqual(start, 0, row["evidence_id"])
            self.assertEqual(text[start:end], row["quote"], row["evidence_id"])

    def assert_draft_sentences_backlinked(self, result: dict):
        ids = {r["evidence_id"] for r in result["evidence_table"]}
        cited = []
        for sec in result["draft"]["sections"]:
            for p in sec["paragraphs"]:
                for s in p.get("sentences", []):
                    self.assertTrue(s["evidence_ids"],
                                    f"{sec['section_id']} 存在无证据实质句：{s['text'][:40]}")
                    cited.extend(s["evidence_ids"])
        self.assertTrue(cited, "综述正文没有任何挂证据的实质句")
        for eid in cited:
            self.assertIn(eid, ids, f"草稿引用的 {eid} 不在证据表")
        self.assertEqual(result["draft"]["audit"]["sentences_without_evidence"], [])
        self.assertEqual(result["evidence"]["backlink"]["unresolved_ids"], [])
        self.assertGreater(len(result["evidence"]["backlink"]["referenced_ids"]), 0)

    def assert_no_generic_framework_sections(self, result: dict):
        titles = {s["title"] for s in result["draft"]["sections"]}
        for generic in ("背景", "方法", "挑战", "展望", "研究背景与意义", "结论与展望"):
            self.assertNotIn(generic, titles, "出现通用框架节标题（主题必须从证据聚类生成）")
        for sec in result["draft"]["sections"]:
            if sec["kind"] in ("topic", "thin_topic"):
                self.assertTrue(sec.get("topic_id"), "主题节缺 topic_id")

    # ---- 1. 最小示例 ----
    def test_min_example_matches_baseline_and_declares_protocol_gap(self):
        case_path = SKILL_DIR / "examples/min_example/input/case.json"
        out = Path(self.tmp.name) / "min_example_out"
        result = self.run_case(case_path, Path(self.tmp.name) / "min_example_work", out)
        self.assertEqual(result["status"]["overall"], "partial")
        self.assertEqual(result["screening"]["protocol_status"], "missing_fallback_declared")
        self.assertTrue(result["screening"]["input_gap"])
        self.assertIn("R_PROTOCOL_MISSING", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        counts = result["screening"]["counts"]
        self.assertEqual(counts["screened"], 9)
        self.assertEqual(counts["excluded_duplicate"], 1)  # P2 与 P1 同 DOI
        dup = [r for r in result["screening"]["records"] if r["record_id"] == "P2"]
        self.assertEqual(dup[0]["outcome"], "excluded")
        self.assertEqual(dup[0]["reason_code"], "R_DUP_DOI")
        self.assertEqual(dup[0]["dedupe_of"], "P1")
        self.assertTrue(result["topics"])
        self.assertEqual({t["topic_source"] for t in result["topics"]}, {"evidence_clustered"})
        items = json.dumps(result["conflicts"]["items"], ensure_ascii=False)
        self.assertIn("numeric_conflict", items)
        self.assertIn("52.3", items)
        self.assertIn("49.0", items)
        for c in result["conflicts"]["items"]:
            self.assertIn("双列", c["resolution"])
            self.assertTrue(c["side_a"]["quote"] and c["side_b"]["quote"])
        self.assert_all_quotes_verified(result)
        self.assert_spans_against_sources(result, case_path)
        self.assert_draft_sentences_backlinked(result)
        self.assert_no_generic_framework_sections(result)
        self.assertIn("语料边界", result["coverage"]["statement_text"])
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        self.assertTrue(expected.exists())
        self.assertTrue(load_same_without_generated(out / "result.json", expected), "与 expected 基线不一致")

    # ---- 2. case1 基准 ----
    def test_case1_baseline_full(self):
        dirname = "case1_baseline"
        case_path = self.root / "input" / dirname / "case.json"
        work = self.root / "app" / dirname / "work_t1"
        out = self.root / "app" / dirname / "out_t1"
        result = self.run_case(case_path, work, out)
        self.assertEqual(result["status"]["overall"], "complete",
                         json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 前瞻时序：plan.json 先于 result.json 落盘，协议内容与结果一致
        plan_path = work / "plan.json"
        self.assertTrue(plan_path.exists())
        plan = load_result(plan_path)
        self.assertEqual(plan["protocol"]["protocol_id"], "PROTO-C1-PVSK")
        self.assertEqual(plan["protocol"]["protocol_status"], "registered_prospective")
        self.assertEqual(plan["protocol"], result["evidence"]["protocol"])
        self.assertLessEqual(plan_path.stat().st_mtime_ns, (out / "result.json").stat().st_mtime_ns)
        self.assertEqual(plan["themes"], [])
        self.assertIn("证据聚类", plan["topic_rule"])
        # 逐条筛选：协议与手册被 EX1 剔除；手册 PDF 自研提取
        counts = result["screening"]["counts"]
        self.assertEqual(counts["screened"], 10)
        self.assertEqual(counts["unscored_no_body"], 0)
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC01"]["outcome"], "excluded")
        self.assertTrue(any(h["criterion_id"] == "EX1" and h["matched"] for h in by_rid["REC01"]["exclusion_hits"]))
        self.assertEqual(by_rid["REC09"]["outcome"], "excluded")
        self.assertEqual(by_rid["REC09"]["extraction_method"], "pdf_extract")
        self.assertGreater(by_rid["REC09"]["body_chars"], 100)
        self.assertTrue(all(r["inclusion_hits"] or r["exclusion_hits"] for r in result["screening"]["records"]))
        # 主题由证据聚类生成
        self.assertEqual(result["evidence"]["clustering"]["themes_source"], "evidence_clustering")
        self.assertEqual({t["topic_source"] for t in result["topics"]}, {"evidence_clustered"})
        self.assertTrue(len(result["topics"]) >= 2)
        self.assertTrue(all(t["support_count"] >= 2 for t in result["topics"]))
        self.assert_no_generic_framework_sections(result)
        # 证据：引文独立复核 + 回链闭合 + docx
        self.assert_all_quotes_verified(result)
        self.assert_spans_against_sources(result, case_path)
        self.assert_draft_sentences_backlinked(result)
        docx = [a for a in result["evidence"]["artifacts"] if isinstance(a, dict) and a.get("file") == "review_draft.docx"]
        self.assertTrue(docx and docx[0]["paragraphs"] > 0)
        self.assertTrue((out / "review_draft.docx").is_file())
        # 覆盖声明
        cov = result["coverage"]
        self.assertEqual(cov["corpus_boundary"]["records_total"], 10)
        self.assertEqual(cov["corpus_boundary"]["sources_total"], 9)
        self.assertIn("池内快照", cov["statement_text"])
        self.assertIn("覆盖声明", result["draft"]["body"])

    # ---- 3. case2 冲突 ----
    def test_case2_conflicts_dual_column(self):
        result = self.realtest_case("case2_conflict", "t2")
        types = {c["type"] for c in result["conflicts"]["items"]}
        self.assertIn("direction_conflict", types)
        self.assertIn("zero_vs_direction", types)
        directions = set()
        for c in result["conflicts"]["items"]:
            sides = (c["side_a"], c["side_b"])
            directions |= {s["direction"] for s in sides}
            for s in sides:
                self.assertTrue(s["quote"], c["conflict_id"])
                self.assertTrue(s["quote_verified"], c["conflict_id"])
            self.assertIn("不择一", c["resolution"])
            self.assertIn("双列", c["resolution"])
        self.assertEqual(directions, {"+1", "-1", "0"})  # 三种方向都被双列留痕
        # 零效应记录（REC04）必须出现在冲突双列里
        involved = {s["record_id"] for c in result["conflicts"]["items"] for s in (c["side_a"], c["side_b"])}
        self.assertIn("REC04", involved)
        # 冲突主题 contested；综述正文双列段落（一方面/另一方面 + 双 [E###]）
        contested = {t["topic_id"] for t in result["topics"] if t["support_level"] == "contested"}
        self.assertTrue(contested)
        body = result["draft"]["body"]
        self.assertIn("一方面，", body)
        self.assertIn("另一方面，", body)
        for c in result["conflicts"]["items"]:
            self.assertIn(c["side_a"]["evidence_id"], body)
            self.assertIn(c["side_b"]["evidence_id"], body)
        self.assertNotIn("尚存争议", body.replace("不以「尚存争议」", ""))  # 只允许出现在规则引用里
        self.assert_spans_against_sources(result, self.root / "input" / "case2_conflict" / "case.json")
        self.assert_all_quotes_verified(result)
        self.assert_draft_sentences_backlinked(result)
        self.assertEqual(result["status"]["overall"], "complete")  # 冲突如实双列是成功交付，不是缺口

    # ---- 4. case3 薄证据 ----
    def test_case3_thin_topic_honestly_declared(self):
        result = self.realtest_case("case3_thin", "t3")
        self.assertEqual({t["topic_source"] for t in result["topics"]}, {"case_declared"})
        thin = [t for t in result["topics"] if t["thin"]]
        self.assertEqual([t["topic_id"] for t in thin], ["T04"])  # TH4 停用后长期复发
        self.assertEqual(thin[0]["support_level"], "suggestive")
        gaps = result["research_gaps"]["items"]
        codes = {g["code"] for g in gaps}
        self.assertIn("R_TOPIC_THIN_EVIDENCE", codes)
        thin_gap = next(g for g in gaps if g["code"] == "R_TOPIC_THIN_EVIDENCE")
        self.assertEqual(thin_gap["topic_id"], "T04")
        self.assertEqual(thin_gap["basis"], "weak_evidence_only")
        self.assertIn("主题缺口:T04", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 综述里的薄证据小节：标题带（证据薄弱），正文有薄弱声明 + 仍引用现有证据
        thin_secs = [s for s in result["draft"]["sections"] if s["kind"] == "thin_topic"]
        self.assertEqual(len(thin_secs), 1)
        self.assertIn("证据薄弱", thin_secs[0]["title"])
        text = thin_secs[0]["section_text"]
        self.assertIn("证据薄弱声明", text)
        self.assertIn("[E", text)  # 现有证据仍逐字引用
        self.assertNotIn("领域尚属空白", text)
        # 其余主题正常成节且每句回链
        self.assert_draft_sentences_backlinked(result)
        self.assert_spans_against_sources(result, self.root / "input" / "case3_thin" / "case.json")
        self.assertFalse(result["coverage"]["not_covered_themes"])  # 4 个声明主题都有支撑
        self.assertEqual(result["coverage"]["themes_declared"], True)
        self.assertEqual(result["status"]["overall"], "partial")

    # ---- 5. case4 鲁棒 ----
    def test_case4_pdf_docx_extraction_and_honest_degradation(self):
        result = self.realtest_case("case4_robust", "t4")
        by_rid = {r["record_id"]: r for r in result["screening"]["records"]}
        self.assertEqual(by_rid["REC07"]["extraction_method"], "pdf_extract")  # PDF 无侧车，自研提取
        self.assertGreater(by_rid["REC07"]["body_chars"], 100)
        self.assertEqual(by_rid["REC08"]["extraction_method"], "docx_xml")     # DOCX zip+xml
        self.assertEqual(by_rid["REC09"]["outcome"], "unscored_no_body")       # 空文件诚实降级
        self.assertEqual(by_rid["REC09"]["body_chars"], 0)
        self.assertEqual(result["screening"]["protocol_status"], "missing_fallback_declared")
        self.assertTrue(result["screening"]["input_gap"])
        self.assertEqual(result["screening"]["input_gap"]["code"], "R_PROTOCOL_MISSING")
        self.assertTrue(all(r["fallback"] for r in result["screening"]["records"]))
        unmet = json.dumps(result["status"]["unmet"], ensure_ascii=False)
        self.assertIn("R_PROTOCOL_MISSING", unmet)
        self.assertIn("记录正文", unmet)
        self.assertEqual(result["status"]["overall"], "partial")
        # PDF 提取文本哈希可独立复算；引文在提取文本上是精确子串
        import hashlib
        import pdf_min
        text = pdf_min.extract_pdf_file(str(self.root / "input/case4_robust/sources/s07_standard_gb.pdf"))["text"]
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), by_rid["REC07"]["body_sha256"])
        self.assert_spans_against_sources(result, self.root / "input" / "case4_robust" / "case.json")
        self.assert_all_quotes_verified(result)
        self.assert_draft_sentences_backlinked(result)
        self.assertEqual(result["evidence"]["clustering"]["themes_source"], "evidence_clustering")

    # ---- 6. 内联边界：未聚类单列 + DOI 去重 ----
    def test_inline_unassigned_and_dedupe(self):
        case = {
            "case_id": "EXAMPLE-049-INLINE-EDGE",
            "synthetic_notice": "合成回归样例，非正式测试案例。",
            "research_question": "纳米涂层对基体耐磨性的影响如何？",
            "records": [
                {"record_id": "A1", "title": "涂层A提升基体耐磨性研究", "type": "journal-article",
                 "year": 2023, "doi": "10.0/x1",
                 "text": "添加纳米涂层后基体耐磨性提高 30%，磨损失重降低。"},
                {"record_id": "A2", "title": "涂层A对基体耐磨性的另一研究", "type": "journal-article",
                 "year": 2024, "doi": "10.0/x2",
                 "text": "纳米涂层使基体磨损失重降低 22%，耐磨性提高。"},
                {"record_id": "A3", "title": "涂层A提升基体耐磨性研究", "type": "journal-article",
                 "year": 2023, "doi": "10.0/x1",
                 "text": "重复题录：与 A1 相同 DOI 的重复记录，不应进入主题。"},
                {"record_id": "B9", "title": "完全无关的天文观测记录", "type": "journal-article",
                 "year": 2025, "doi": "10.0/x9",
                 "text": "望远镜观测到系外行星凌星事件，测光精度达到 0.2 mmag。"},
            ],
        }
        root = Path(self.tmp.name) / "inline_edge"
        root.mkdir(parents=True, exist_ok=True)
        case_path = root / "case.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        result = self.run_case(case_path, root / "work", root / "out")
        counts = result["screening"]["counts"]
        self.assertEqual(counts["excluded_duplicate"], 1)
        dup = [r for r in result["screening"]["records"] if r["record_id"] == "A3"]
        self.assertEqual(dup[0]["reason_code"], "R_DUP_DOI")
        unassigned = [u["record_id"] for u in result["coverage"]["unassigned_records"]]
        self.assertIn("B9", unassigned)  # 与主题词表无共享术语 → 单列不硬塞
        codes = {g["code"] for g in result["research_gaps"]["items"]}
        self.assertIn("R_TOPIC_UNASSIGNED", codes)
        self.assert_draft_sentences_backlinked(result)
        self.assert_spans_against_sources(result, case_path)

    # ---- 7. 幂等 ----
    def test_idempotent_rerun_case1(self):
        case_path = self.root / "input" / "case1_baseline" / "case.json"
        outs = []
        for i in (1, 2):
            work = self.root / "app" / "case1_baseline" / f"idem_work_{i}"
            out = self.root / "app" / "case1_baseline" / f"idem_out_{i}"
            self.run_case(case_path, work, out)
            outs.append((work / "plan.json", out / "result.json"))
        self.assertTrue(load_same_without_generated(outs[0][1], outs[1][1]),
                        "result.json 除 generated_at 外不一致")
        self.assertEqual(outs[0][0].read_bytes(), outs[1][0].read_bytes(), "plan.json 不幂等")


if __name__ == "__main__":
    unittest.main(verbosity=2)
