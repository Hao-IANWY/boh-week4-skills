#!/usr/bin/env python3
"""056 回归测试：最小示例 + review/056_realtest 真实仿真 6 案例（纯标准库、离线）。

校验面：
  - 最小示例：通用契约 PASS、与 expected 基线一致（忽略 generated_at）、两次运行逐字节幂等；
  - case1 英文全文基准：种子问题命中且定位精确（T5/T4/T6/T9/T7/T11/T10/T14/T13），
    三类不应误报材料（显式假设/转述他人/有意阴性对照）全部进 suppressed 且不进 findings；
  - case2 中文全文：中文章节切分、句级索引、char_span 反查、白名单抑制；
  - case3 facets 定向：只执行任务点名维度，其余标 unchecked，findings 不越界；
  - case4a：PDF 无侧车走自研 stdlib 提取（round-trip）；case4b：截断诚实降级；
    case4c：扫描件无文本层诚实 gap；
  - 侧车文本优先级与 DOCX/PDF 提取单元测试。
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
REPO_ROOT = HERE.parents[2]
REALTEST = REPO_ROOT / "review" / "056_realtest"
BUILD_SCRIPT = REALTEST / "build_cases.py"
sys.path.insert(0, str(HERE))
HAS_REALTEST = BUILD_SCRIPT.exists()

CASES = ("case1_fulltext_en", "case2_fulltext_zh", "case3_facets",
         "case4a_pdf_only", "case4b_truncated", "case4c_scanned")


def _run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


class RuntimeMixin(unittest.TestCase):
    runtime: Path = None
    _tmp: tempfile.TemporaryDirectory = None

    @classmethod
    def setUpClass(cls):
        if not HAS_REALTEST:
            raise unittest.SkipTest("review/056_realtest/build_cases.py 不存在，跳过 realtest 回归")
        cls._tmp = tempfile.TemporaryDirectory(prefix="boh056-realtest-")
        cls.runtime = Path(cls._tmp.name)
        sys.path.insert(0, str(BUILD_SCRIPT.parent))
        import build_cases
        build_cases.build(cls.runtime)

    @classmethod
    def tearDownClass(cls):
        if cls._tmp is not None:
            cls._tmp.cleanup()

    def run_pipeline(self, case_key: str) -> dict:
        case_path = self.runtime / "input" / case_key / "case.json"
        work = self.runtime / "app" / case_key / "work"
        out = self.runtime / "app" / case_key / "out"
        proc = _run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path),
                     "--work", str(work), "--out", str(out)])
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result_path = out / "result.json"
        self.assertTrue(result_path.exists(), proc.stderr + proc.stdout)
        val = _run([sys.executable, "-B", str(HERE / "validate.py"),
                    "--case", str(case_path), "--result", str(result_path)])
        self.assertEqual(val.returncode, 0, val.stdout + val.stderr)
        return json.loads(result_path.read_text(encoding="utf-8"))


class TestMinExample(unittest.TestCase):
    SKILL = SKILL_DIR

    def _pipeline(self, out: Path):
        case = self.SKILL / "examples/min_example/input/case.json"
        proc = _run([sys.executable, "-B", str(PIPELINE), "--case", str(case), "--out", str(out)])
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        val = _run([sys.executable, "-B", str(HERE / "validate.py"), "--case", str(case),
                    "--result", str(out / "result.json")])
        self.assertEqual(val.returncode, 0, val.stdout + val.stderr)
        return json.loads((out / "result.json").read_text(encoding="utf-8"))

    def test_min_example_matches_baseline_and_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="boh056-min-") as tmp:
            root = Path(tmp)
            r1 = self._pipeline(root / "a")
            r2 = self._pipeline(root / "b")
            a = json.loads((root / "a/result.json").read_text(encoding="utf-8"))
            b = json.loads((root / "b/result.json").read_text(encoding="utf-8"))
            a.pop("generated_at"), b.pop("generated_at")
            self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False),
                             json.dumps(b, sort_keys=True, ensure_ascii=False), "两次运行应逐字节幂等")
            expected = json.loads((self.SKILL / "examples/min_example/expected/result.json")
                                  .read_text(encoding="utf-8"))
            expected.pop("generated_at", None)
            r1.pop("generated_at", None)
            self.assertEqual(json.dumps(r1, sort_keys=True, ensure_ascii=False),
                             json.dumps(expected, sort_keys=True, ensure_ascii=False),
                             "与 expected 基线不一致：规则或流程变更后未重生成基线")
            self.assertEqual(r1["status"]["overall"], "complete")
            self.assertTrue(all(f["quote_verified"] for f in r1["findings"]))
            self.assertTrue(any(f["detector"] == "T3_CIRCULAR" for f in r1["findings"]))
            self.assertTrue(any(s["guard_id"] == "G_LIMITATION_SECTION" for s in r1["suppressed"]))


class TestRealCases(RuntimeMixin, unittest.TestCase):

    # ---------------------------------------------------------------- case1
    def test_case1_en_fulltext_seed_hits_and_suppression(self):
        d = self.run_pipeline("case1_fulltext_en")
        self.assertEqual(d["status"]["overall"], "complete")
        cov = d["coverage"]
        self.assertEqual(cov["extraction_method"], "inline_text")
        self.assertFalse(cov["truncated"])
        findings, suppressed = d["findings"], d["suppressed"]

        def has(det, sub):
            return any(f["detector"] == det and sub.lower() in f["quote"].lower() for f in findings)

        # 种子问题：命中 + 定位到句
        self.assertTrue(has("T5_STRENGTH_MISMATCH", "prove"), "句级过强表述未命中")
        self.assertTrue(has("T4_PROMISE_UNDELIVERED", "presenteeism"), "引言承诺未交付未命中")
        self.assertTrue(has("T6_CAUSAL_IN_CORRELATIONAL", "leads to"), "相关当因果未命中")
        self.assertTrue(any(f["detector"] == "T9_METHOD_ANSWER_GAP" for f in findings), "方法无法回答问题未命中")
        t7 = [f for f in findings if f["detector"] == "T7_NUM_CROSS_SECTION"]
        self.assertTrue(t7 and {0.82, 0.741} <= set(t7[0]["evidence"]["values"]),
                        f"应答率数值冲突未命中: {t7}")
        t11 = [f for f in findings if f["detector"] == "T11_FIGTAB_MISSING"
               and f["evidence"].get("kind") == "cited_but_missing"]
        self.assertTrue(any(f["evidence"]["label"] == "figure 2" for f in t11), "Figure 2 引用无本体未命中")
        self.assertTrue(any(f["detector"] == "T10_STATS_REPORTING"
                            and f["evidence"].get("rule") == "p_without_effect_size" for f in findings))
        self.assertTrue(any(f["detector"] == "T14_METHODS_RESULTS_TERMS"
                            and f["evidence"]["metric"] == "cronbach" for f in findings))
        t13 = [f for f in findings if f["detector"] == "T13_STRENGTH_DIRECTION_MISMATCH"]
        self.assertTrue(t13 and t13[0]["evidence"]["direction"] == "abstract_stronger")

        # 严重度可解释依据
        for f in findings:
            self.assertIn("affects_main_conclusion", f["severity_basis"], f["detector"])
            self.assertTrue(f["severity"] in ("major", "moderate", "minor", "suggestion", "editorial"))
        f5 = next(f for f in findings if f["detector"] == "T5_STRENGTH_MISMATCH" and "prove" in f["quote"].lower())
        self.assertEqual(f5["severity"], "major")
        self.assertIn("revision", f5) and self.assertTrue(f5["revision"]["target_sent_id"])

        # 三类不应误报：全部进 suppressed，且不进 findings
        sup_by_guard = {s["guard_id"]: s for s in suppressed}
        for guard, needle in (("G_EXPLICIT_ASSUMPTION", "hypothesize"),
                              ("G_REPORTED_VIEW", "reported that"),
                              ("G_INTENDED_CONTROL", "negative control")):
            self.assertIn(guard, sup_by_guard, f"{guard} 未出现在 suppressed")
            self.assertIn(needle, sup_by_guard[guard]["quote"].lower())
            self.assertTrue(sup_by_guard[guard]["quote_verified"])
        banned = ("we hypothesize", "reported that", "negative control")
        for f in findings:
            for needle in banned:
                self.assertNotIn(needle, f["quote"].lower(),
                                 f"被抑制句泄漏进 findings: {f['finding_id']} {f['quote'][:60]}")

        # quote 精确子串 + char_span 反查
        text_len_probe = self.run_pipeline_text_probe()
        for f in findings:
            core = f["quote"].strip("…")
            self.assertTrue(core and core in text_len_probe, f"quote 非原文子串: {f['finding_id']}")
            s0, s1 = f["location"]["char_span"]
            self.assertEqual(text_len_probe[s0:s0 + len(core)], core, f"char_span 与 quote 不对齐: {f['finding_id']}")

    def run_pipeline_text_probe(self) -> str:
        case_path = self.runtime / "input" / "case1_fulltext_en" / "case.json"
        sys.path.insert(0, str(HERE))
        import pipeline as P
        from kit import load_case
        cfg = P.load_rules()["arg"]
        bank = P.CueBank(cfg)
        doc = P.collect_manuscript_doc(load_case(str(case_path)), Path("."), cfg, bank)
        return doc["text"]

    def test_case1_idempotent(self):
        d1 = self.run_pipeline("case1_fulltext_en")
        d2 = self.run_pipeline("case1_fulltext_en")
        d1.pop("generated_at"), d2.pop("generated_at")
        self.assertEqual(json.dumps(d1, sort_keys=True, ensure_ascii=False),
                         json.dumps(d2, sort_keys=True, ensure_ascii=False))

    # ---------------------------------------------------------------- case2
    def test_case2_zh_fulltext_split_index_and_suppression(self):
        d = self.run_pipeline("case2_fulltext_zh")
        self.assertEqual(d["status"]["overall"], "complete")
        sections = {s["heading"]: s["role"] for s in []}
        idx = d["sentence_index"]
        self.assertGreaterEqual(len(idx), 30)
        method_headings = ("方法", "研究对象", "测量", "统计分析", "材料与方法")
        self.assertTrue(any(s["section_role"] == "evidence" and any(k in s["heading"] for k in method_headings)
                            for s in idx), "中文方法章节未切出")
        self.assertTrue(any(s["section_role"] == "evidence" and "结果" in s["heading"] for s in idx))
        findings, suppressed = d["findings"], d["suppressed"]

        def has(det, sub):
            return any(f["detector"] == det and sub in f["quote"] for f in findings)

        self.assertTrue(has("T5_STRENGTH_MISMATCH", "证明"), "中文摘要强度越界未命中")
        t4 = [f for f in findings if f["detector"] == "T4_PROMISE_UNDELIVERED"]
        self.assertTrue(any("成本效益" in f["evidence"].get("aim", "") for f in t4), "中文承诺未交付未命中")
        self.assertTrue(any(f["detector"] == "T13_STRENGTH_DIRECTION_MISMATCH"
                            and f["evidence"]["direction"] == "abstract_stronger" for f in findings))
        sup_quotes = " ".join(s["quote"] for s in suppressed)
        self.assertIn("报道", sup_quotes)
        self.assertIn("推测", sup_quotes)
        self.assertIn("阴性对照", sup_quotes)
        self.assertTrue(any(s["guard_id"] == "G_REPORTED_VIEW" for s in suppressed))
        self.assertTrue(any(s["guard_id"] == "G_EXPLICIT_ASSUMPTION" for s in suppressed))
        self.assertTrue(any(s["guard_id"] == "G_INTENDED_CONTROL" for s in suppressed))
        for f in findings:
            self.assertNotIn("王某某", f["quote"])
            self.assertNotIn("推测", f["quote"])
            self.assertNotIn("阴性对照", f["quote"])

    # ---------------------------------------------------------------- case3
    def test_case3_facets_trimmed_and_rest_unchecked(self):
        d = self.run_pipeline("case3_facets")
        self.assertEqual(d["status"]["overall"], "complete")
        detectors = {f["detector"] for f in d["findings"]}
        allowed = {"T7_NUM_CROSS_SECTION", "T11_FIGTAB_MISSING", "T12_SIGN_CONFLICT",
                   "T10_STATS_REPORTING"}  # fact_check + stats_reporting 的 detector 全集
        self.assertTrue(detectors <= allowed, f"越界报告了未点名维度: {detectors - allowed}")
        self.assertIn("T7_NUM_CROSS_SECTION", detectors)
        self.assertIn("T10_STATS_REPORTING", detectors)
        status = {f["facet"]: f["status"] for f in d["facets_status"]}
        self.assertEqual(status["fact_check"], "executed")
        self.assertEqual(status["stats_reporting"], "executed")
        for unchecked in ("argument_chain", "section_coherence", "method_design",
                          "citation_support", "academic_norm"):
            self.assertEqual(status[unchecked], "unchecked", f"{unchecked} 应标 unchecked")
            entry = next(f for f in d["facets_status"] if f["facet"] == unchecked)
            self.assertTrue(entry.get("reason"), "unchecked 需给出原因")
        # 契约字段按维度裁剪后绑定
        self.assertTrue(d["事实核验"])
        self.assertTrue(d["统计报告"])

    # ---------------------------------------------------------------- case4
    def test_case4a_pdf_only_stdlib_extraction(self):
        d = self.run_pipeline("case4a_pdf_only")
        self.assertEqual(d["status"]["overall"], "complete")
        cov = d["coverage"]
        self.assertEqual(cov["extraction_method"], "pdf_stdlib_flate")
        self.assertTrue(cov["total_chars"] > 2000)
        self.assertFalse(cov["truncated"])
        self.assertTrue(cov["sections_present"]["abstract"])
        self.assertTrue(cov["sections_present"]["results"])
        self.assertTrue(any(f["detector"] == "T5_STRENGTH_MISMATCH" and "prove" in f["quote"].lower()
                            for f in d["findings"]))
        self.assertTrue(all(f["quote_verified"] for f in d["findings"]))

    def test_case4b_truncated_honest_partial(self):
        d = self.run_pipeline("case4b_truncated")
        self.assertNotEqual(d["status"]["overall"], "complete", "截断文本不得报 complete")
        self.assertEqual(d["status"]["overall"], "partial")
        cov = d["coverage"]
        self.assertTrue(cov["truncated"])
        self.assertIn("results", cov["core_sections_missing"])
        self.assertIsNotNone(cov["char_coverage"])
        self.assertLess(cov["char_coverage"], 1.0)
        self.assertTrue(any(u["item"] == "全文覆盖" for u in d["status"]["unmet"]))
        self.assertTrue(cov["truncation_evidence"])

    def test_case4c_scanned_no_text_layer_honest_gap(self):
        d = self.run_pipeline("case4c_scanned")
        self.assertEqual(d["status"]["overall"], "empty")
        self.assertEqual(d["研究产物"]["status"], "not_produced")
        self.assertTrue(any(u["item"] == "论文正文" for u in d["status"]["unmet"]))
        extraction = d["evidence"]["extraction"]
        self.assertEqual(extraction["text_layers"], False)
        self.assertTrue(extraction["unparsed"])


class TestExtractionUnits(unittest.TestCase):
    """提取层单元测试：侧车优先级、DOCX、PDF round-trip（纯标准库）。"""

    def test_pdf_roundtrip_and_docx_and_sidecar(self):
        sys.path.insert(0, str(HERE))
        import pdf_min
        import pipeline as P
        from kit import load_case

        with tempfile.TemporaryDirectory(prefix="boh056-extract-") as tmp:
            root = Path(tmp)
            if HAS_REALTEST:
                sys.path.insert(0, str(BUILD_SCRIPT.parent))
                import build_cases
                pdf_bytes = build_cases.write_pdf(
                    build_cases.paginate("Unit Test Paper\n\nAbstract\n\nThis sentence proves the point. Results were associated with noise."),
                    root / "u.pdf")
                info = pdf_min.extract_pdf_text(pdf_bytes.read_bytes())
                self.assertTrue(info["text_layers"])
                self.assertIn("proves the point", info["text"])

            # DOCX：zipfile 构造最小 word/document.xml
            import zipfile
            docx = root / "u.docx"
            xml = ("<w:document xmlns:w='w'><w:body>"
                   "<w:p><w:r><w:t>第一段：横断面设计。</w:t></w:r></w:p>"
                   "<w:p><w:r><w:t>Second paragraph with &amp; entity.</w:t></w:r></w:p>"
                   "</w:body></w:document>")
            with zipfile.ZipFile(docx, "w") as zf:
                zf.writestr("word/document.xml", xml)
            info = pdf_min.extract_docx_text(docx.read_bytes())
            self.assertTrue(info["text_layers"])
            self.assertIn("横断面设计", info["text"])
            self.assertIn("& entity", info["text"])

            # 侧车优先：同一 PDF 放了 .txt 侧车 → extraction_method=sidecar_txt
            pdf = build_cases.write_pdf(build_cases.paginate("Sidecar Test\n\nAbstract\n\nBody sentence here."),
                                        root / "input.pdf") if HAS_REALTEST else None
            if pdf is not None:
                (root / "input.txt").write_text("SIDECAR TEXT MARKER\n\nAbstract\n\nBody sentence here.", encoding="utf-8")
                case = {"case_id": "EXAMPLE-056-SIDECAR", "materials": [{"path": "input.pdf"}]}
                got = P.collect_manuscript(case, root, case_dir=root)
                self.assertEqual(got["extraction_method"], "sidecar_txt")
                self.assertIn("SIDECAR TEXT MARKER", got["text"])
                # 删掉侧车 → 回退自研 PDF 提取
                (root / "input.txt").unlink()
                got2 = P.collect_manuscript(case, root, case_dir=root)
                self.assertEqual(got2["extraction_method"], "pdf_stdlib_flate")
                self.assertTrue(got2["text_layers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
