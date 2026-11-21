#!/usr/bin/env python3
"""011 多案例合成回归（纯标准库、离线、无正式案例数据）。

覆盖：
  A. 单元级：号码规范化变体/合并 WO（case2 的不变量）、pdf_min 写入→读回 round-trip、
     影印件权项解析（连续 N. 锚点）。
  B. 端到端：examples/min_example 真跑 + validate.py + 两次运行幂等 + claims span 精确子串。
  C. realtest：调 review/011_realtest/build_cases.py 构建临时 runtime，对 5 个案例各跑
     pipeline + validate，并断言关键不变量：
       case1 基准（含材料正文命中、族、影印件索引）
       case2 号码混乱（变体同 canonical、合并 WO ambiguous）
       case3 仅传真件权项（source_ref/page/精确 char_span/ocr_boundary/无来源缺口）
       case4 鲁棒（missing+failed 如实记缺、二手状态不越权）
       case5 空记录（empty 口径）

用法：python3 -B answer/011_专利与技术文献检索/scripts/test_pipeline.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
REPO = SKILL_DIR.parents[1]
PIPELINE = HERE / "pipeline.py"
VALIDATE = HERE / "validate.py"
PDF_MIN = HERE / "pdf_min.py"
BUILD_CASES = REPO / "review" / "011_realtest" / "build_cases.py"

sys.path.insert(0, str(HERE))
import pipeline  # noqa: E402
import pdf_min  # noqa: E402

FIELDS = json.loads((SKILL_DIR / "rules" / "field_dictionary.json").read_text(encoding="utf-8"))


def _load_build_cases():
    spec = importlib.util.spec_from_file_location("build_cases_011", BUILD_CASES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_pipeline(case_path: Path, out_dir: Path, cwd: Path | None = None) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out_dir)],
        capture_output=True, text=True, timeout=300, cwd=str(cwd) if cwd else None,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        raise AssertionError(f"pipeline 失败：{proc.stderr or proc.stdout}")
    return json.loads((out_dir / "result.json").read_text(encoding="utf-8"))


def run_validate(case_path: Path, result_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-B", str(VALIDATE), "--case", str(case_path), "--result", str(result_path)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, f"validate FAIL: {proc.stdout or proc.stderr}"


def strip_generated(result: dict) -> str:
    d = dict(result)
    d.pop("generated_at", None)
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


class NumberNormalizationTests(unittest.TestCase):
    """case2 的不变量在单元层先钉死：变体同 canonical、合并 WO 给拆解候选。"""

    def test_variant_writes_normalize_to_same_canonical(self):
        variants = ["US 10,123,456 B2", "us10123456b2", "US 10 123 456 B2", "ＵＳ１０，１２３，４５６Ｂ２", "US 10,123,456 B2."]
        canonical = {pipeline.normalize_number(v, FIELDS)["canonical"] for v in variants}
        self.assertEqual(canonical, {"US10123456B2"})
        for v in variants:
            n = pipeline.normalize_number(v, FIELDS)
            self.assertEqual(n["number_kind"], "grant", v)
            self.assertFalse(n["ambiguous"], v)

    def test_wo_slash_variants(self):
        clean = pipeline.normalize_number("WO 2018/123456 A1", FIELDS)
        self.assertEqual(clean["canonical"], "WO2018123456A1")
        self.assertEqual(clean["wo_split"], "2018/123456")
        self.assertFalse(clean["ambiguous"])
        self.assertFalse(clean["wo_merged"])

    def test_wo_ocr_merged_is_ambiguous_with_split_candidates(self):
        merged = pipeline.normalize_number("WO2018123456A1", FIELDS)
        self.assertTrue(merged["wo_merged"])
        self.assertTrue(merged["ambiguous"])
        self.assertEqual(merged["wo_split"], "2018/123456")
        self.assertTrue(merged["alternatives"])
        self.assertIn("登记确认", merged["alternatives"][0]["reason"])
        # 全角/小写的合并形态同样命中
        for v in ("wo 2018123456 a1", "ＷＯ２０１８１２３４５６Ａ１"):
            n = pipeline.normalize_number(v, FIELDS)
            self.assertTrue(n["wo_merged"], v)
            self.assertEqual(n["canonical"], "WO2018123456A1", v)
        # 干净斜杠形式与合并形式归一到同一 canonical（跨写法命中同一记录）
        self.assertEqual(pipeline.normalize_number("WO 2018/123456 A1", FIELDS)["canonical"], merged["canonical"])

    def test_missing_kind_code_stays_unknown_with_alternatives(self):
        n = pipeline.normalize_number("US 10,234,567", FIELDS)
        self.assertEqual(n["number_kind"], "unknown")
        self.assertTrue(n["ambiguous"])
        self.assertGreaterEqual(len(n["alternatives"]), 2)

    def test_truncated_merged_wo_cannot_fake_a_split(self):
        n = pipeline.normalize_number("WO2018999999999", FIELDS)  # 拆不出合理年份+序号
        self.assertTrue(n["wo_merged"])
        self.assertTrue(n["ambiguous"])
        self.assertTrue(any(a["number_kind"] == "unknown" for a in n["alternatives"]))


class PdfMinRoundTripTests(unittest.TestCase):
    def test_build_then_extract_round_trip(self):
        pages = [
            "USPTO FACSIMILE (SYNTHETIC)\nPatent No.: US 99,888,777 B2\n\nClaims:\n1. A first synthetic claim.\n2. A second synthetic claim.",
            "Page 2: parens (x) and backslash \\ survive; non-latin1 becomes ?",
        ]
        with tempfile.TemporaryDirectory(prefix="pdfmin011-") as tmp:
            path = Path(tmp) / "doc.pdf"
            pdf_min.write_pdf_file(path, pages)
            got = pdf_min.extract_pdf_text(path)
            self.assertTrue(got["ok"])
            self.assertEqual(len(got["pages"]), 2)
            self.assertEqual(got["pages"][0].split("\n"), pages[0].split("\n"))
            self.assertIn("(x)", got["pages"][1])
            self.assertIn("?", got["pages"][1])

    def test_scan_pdf_reports_failure_without_fabrication(self):
        with tempfile.TemporaryDirectory(prefix="pdfmin011-") as tmp:
            path = Path(tmp) / "broken.pdf"
            path.write_bytes(b"%PDF-1.4 not-a-real-text-stream\n" * 4)
            got = pdf_min.extract_pdf_text(path)
            self.assertFalse(got["ok"])
            self.assertEqual(got["pages"], [])


class FacsimileClaimParsingTests(unittest.TestCase):
    def test_consecutive_claim_run_is_extracted_with_exact_spans(self):
        page = (
            "USPTO FACSIMILE (SYNTHETIC)\nPatent No.: US 12,345,678 B2\n\nClaims:\n"
            "1. A lithium dendrite suppression coating on a garnet-type solid electrolyte.\n"
            "2. The coating of claim 1, deposited by pulsed laser deposition.\n"
            "FIG. 1 is a schematic drawing. 3. not a claim anchor because run broke.\n"
        )
        claims = pipeline.extract_claims_from_text([page], "B2", 200)
        self.assertEqual([c["claim_no"] for c in claims], ["1", "2"])
        for c in claims:
            s, e = c["char_span"]
            self.assertEqual(page[s:e], c["quote"])
            self.assertTrue(c["quote"].startswith(tuple(c["text"])[:1]))
            self.assertEqual(c["page"], 1)
            self.assertIn("(facsimile)", c["version"])

    def test_no_claims_anchor_returns_empty(self):
        self.assertEqual(pipeline.extract_claims_from_text(["No claims section here. Just 2. stray numbers."], "B2", 200), [])


class MinExampleEndToEndTests(unittest.TestCase):
    def test_min_example_validate_idempotent_and_exact_spans(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="o11-min-") as tmp:
            out1, out2 = Path(tmp) / "o1", Path(tmp) / "o2"
            r1 = run_pipeline(case, out1, cwd=str(SKILL_DIR))
            r2 = run_pipeline(case, out2, cwd=str(SKILL_DIR))
            self.assertEqual(strip_generated(r1), strip_generated(r2), "同一输入两次运行必须逐字节一致（除 generated_at）")
            run_validate(case, out1 / "result.json")
            self.assertEqual(r1["status"]["overall"], "partial")  # 法律状态未核验 → 不得 complete
            # inline span 校验：从 case.json 重建内联文本，逐条验证 quote 是精确子串
            records = {x["record_id"]: x for x in json.loads(case.read_text(encoding="utf-8"))["records"]}
            for t in r1["claims_table"]:
                rec = records[t["record_id"]]
                full = rec.get("claims")
                full = full if isinstance(full, str) else "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in (full or []))
                for e in t["claims"]:
                    if not e["located"] or e["source_ref"] != "inline_field":
                        continue
                    s, en = e["char_span"]
                    self.assertEqual(full[s:en], e["quote"], (t["record_id"], e["claim_no"]))
            # 影印件索引与佐证
            fac = r1["evidence_map"]["facsimiles"]
            self.assertEqual(len(fac), 3)
            methods = {m["extraction_method"] for m in fac}
            self.assertIn("pdf_stdlib_text", methods)
            self.assertIn("ocr_sidecar", methods)
            self.assertTrue(all(m["sha256"] and m["char_count"] > 0 for m in fac))
            p1 = next(t for t in r1["claims_table"] if t["record_id"] == "P1")
            self.assertTrue(p1["claims"][0]["facsimile_corroborated"])
            self.assertTrue(p1["claims"][0]["ocr_boundary"])
            # 材料正文命中与 records 打分分开呈现
            hits = r1["evidence_map"]["literature_hits"]
            self.assertTrue(hits and hits[0]["query_id"] == "Q3")
            for m in hits[0]["materials"]:
                for h in m["hits"]:
                    s, en = h["char_span"]
                    self.assertGreater(en, s)
            # 全部状态 unverified
            self.assertTrue(all(not x["verified"] for x in r1["legal_status"]))


class RealtestEndToEndTests(unittest.TestCase):
    """直接调 review/011_realtest/build_cases.py 在临时目录重建 5 案例，逐案例断言不变量。"""

    @classmethod
    def setUpClass(cls):
        if not BUILD_CASES.exists():
            raise unittest.SkipTest(f"realtest builder 不存在：{BUILD_CASES}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="o11-realtest-")
        cls.root = Path(cls.tmp.name)
        cls.builder = _load_build_cases()
        cls.builder.build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, name: str) -> tuple:
        case_dir = self.root / "input" / name
        work_case = self.root / "app" / name / "work" / "case.json"
        out = self.root / "app" / name / "output" / "res"
        # 以 Harbor 姿态运行：case 在 app/work 副本里，材料相对路径靠 cwd=input/<case> 回退解析
        result = run_pipeline(work_case, out, cwd=str(case_dir))
        run_validate(work_case, out / "result.json")
        return result, case_dir

    def test_case1_base(self):
        result, _ = self.run_case("case1_base")
        self.assertEqual(result["status"]["overall"], "partial")
        cands = {c["record_id"] for c in result["candidates"]}
        self.assertEqual(cands, {"P1", "P2", "P3", "P4", "P5", "P6"})
        # P6（技术文献）经挂接正文的关键词命中进入候选
        p6 = next(c for c in result["candidates"] if c["record_id"] == "P6")
        self.assertIn("solid electrolyte", p6["keyword_hits"])
        self.assertEqual(p6["score_breakdown"]["keyword_match"], 0.6667)
        # 族：优先权归一为第一判据，scope 不穷尽
        fams = {f["basis"]: [m["record_id"] for m in f["members"]] for f in result["patent_families"]}
        self.assertIn("priority:US62345678", fams)
        self.assertEqual(sorted(fams["priority:US62345678"]), ["P1", "P2"])
        self.assertTrue(all(f["scope"] == "observed_not_exhaustive" for f in result["patent_families"]))
        # 3 份影印件（PDF/侧车）+ 2 篇文献全部带哈希与提取方法；WO 侧车标 ocr_boundary
        fac = {m["file"].split("/")[-1]: m for m in result["evidence_map"]["facsimiles"]}
        self.assertEqual(len(fac), 5)
        self.assertEqual(fac["US10123456B2.pdf"]["extraction_method"], "pdf_stdlib_text")
        self.assertEqual(fac["WO2022123456A1.pdf"]["extraction_method"], "ocr_sidecar")
        self.assertTrue(fac["WO2022123456A1.pdf"]["ocr_boundary"])
        # 主题查询的材料正文命中（quote 为精确切片）
        hits = result["evidence_map"]["literature_hits"]
        qids = {h["query_id"] for h in hits}
        self.assertEqual(qids, {"Q3", "Q5"})
        for h in hits:
            for m in h["materials"]:
                for term_hit in m["hits"]:
                    s, e = term_hit["char_span"]
                    self.assertEqual(e - s, len(term_hit["quote"]))
        # 二手状态观测不越权
        self.assertTrue(all(not x["verified"] for x in result["legal_status"]))
        self.assertEqual(result["legal_status"][0]["value"], "unverified")

    def test_case2_number_variants(self):
        result, _ = self.run_case("case2_number_variants")
        ft = {f["record_id"]: f for f in result["field_table"]}
        # 小写/千分位变体 → 同 canonical，且判定 grant
        self.assertEqual(ft["P1"]["publication_number_canonical"], "US10123456B2")
        self.assertEqual(ft["P1"]["publication_number_kind"], "grant")
        self.assertFalse(ft["P1"]["number_ambiguous"])
        # 全角 EP
        self.assertEqual(ft["P2"]["publication_number_canonical"], "EP3987654A1")
        # OCR 合并 WO：wo_merged + ambiguous + 拆解候选
        self.assertEqual(ft["P3"]["publication_number_canonical"], "WO2018123456A1")
        self.assertTrue(ft["P3"]["wo_ocr_merged"])
        self.assertTrue(ft["P3"]["number_ambiguous"])
        alts = next(r for r in result["evidence_map"]["number_normalization"] if r["record_id"] == "P3")["publication"]["alternatives"]
        self.assertTrue(any(a.get("wo_split") == "2018/123456" for a in alts))
        # 缺种类码
        self.assertEqual(ft["P4"]["publication_number_kind"], "unknown")
        self.assertTrue(ft["P4"]["number_ambiguous"])
        # 干净写法的号码查询命中噪声写法的记录（canonical 相等）
        qs = {c["record_id"]: c["query_scores"] for c in result["candidates"]}
        self.assertGreaterEqual(qs["P1"]["Q1"], 0.5)
        self.assertGreaterEqual(qs["P3"]["Q2"], 0.5)
        status_texts = json.dumps(result["status"], ensure_ascii=False)
        self.assertIn("OCR 合并", status_texts)

    def test_case3_facsimile_only_claims(self):
        import pdf_min as pm
        result, case_dir = self.run_case("case3_facsimile_claims")
        tables = {t["record_id"]: t for t in result["claims_table"]}
        # P1：权项只在影印件 PDF 里
        t1 = tables["P1"]["claims"]
        self.assertTrue(t1)
        page_text = pm.extract_pdf_text(case_dir / "facsimiles" / "US12 345 678B2.pdf")["pages"][t1[0]["page"] - 1]
        for e in t1:
            self.assertTrue(e["source_ref"].startswith("facsimile:"))
            s, en = e["char_span"]
            self.assertEqual(page_text[s:en], e["quote"])
            self.assertFalse(e["ocr_boundary"])
        # P2：权项只在 OCR 侧车里
        t2 = tables["P2"]["claims"]
        self.assertTrue(t2)
        side_text = (case_dir / "facsimiles" / "US13-987-654A1.ocr.txt").read_text(encoding="utf-8")
        for e in t2:
            self.assertTrue(e["source_ref"].startswith("ocr_sidecar:"))
            self.assertTrue(e["ocr_boundary"])
            s, en = e["char_span"]
            self.assertEqual(side_text[s:en], e["quote"])
        # P3：内联与影印件文本不一致 → 佐证必须为 false
        p3 = tables["P3"]["claims"][0]
        self.assertEqual(p3["source_ref"], "inline_field")
        self.assertFalse(p3["facsimile_corroborated"])
        # P4：完全无权项来源 → 如实挂缺口，不摘引、不编造
        self.assertTrue(tables["P4"]["gap"].startswith("R_CLAIM_SOURCE_UNAVAILABLE"))
        self.assertFalse(tables["P4"]["claims"])
        self.assertIn("权利要求定位", [u["item"] for u in result["status"]["unmet"]])

    def test_case4_robust(self):
        result, _ = self.run_case("case4_robust")
        self.assertEqual(result["status"]["overall"], "partial")
        # 提取失败如实记缺，不编造正文
        fac = {m["file"].split("/")[-1]: m for m in result["evidence_map"]["facsimiles"]}
        self.assertEqual(fac["gone_missing.pdf"]["extraction_method"], "missing")
        self.assertEqual(fac["broken.pdf"]["extraction_method"], "failed")
        self.assertFalse(fac["broken.pdf"]["text_available"])
        self.assertIn("材料文本提取", [u["item"] for u in result["status"]["unmet"]])
        # 二手/未知来源状态一律 unverified，越权观测只登记为 observed
        self.assertTrue(all(not x["verified"] and x["value"] == "unverified" for x in result["legal_status"]))
        self.assertTrue(all(x["recheck_path"] for x in result["legal_status"]))
        self.assertTrue(any("R_OVERSCOPED_CLAIM" in n for n in result["status"]["notes"]))
        # 影印件缺失的记录权项缺口显式挂出
        tables = {t["record_id"]: t for t in result["claims_table"]}
        self.assertTrue(tables["P3"]["gap"].startswith("R_CLAIM_SOURCE_UNAVAILABLE"))

    def test_case5_empty(self):
        result, _ = self.run_case("case5_empty")
        self.assertEqual(result["status"]["overall"], "empty")
        self.assertEqual(result["candidates"], [])
        self.assertIn("检索结果", [u["item"] for u in result["status"]["unmet"]])
        self.assertTrue(all(not x["verified"] or True for x in result["legal_status"]))  # 空表不产出状态结论
        self.assertEqual(result["legal_status"], [])

    def test_realtest_idempotent(self):
        name = "case1_base"
        case_dir = self.root / "input" / name
        work_case = self.root / "app" / name / "work" / "case.json"
        out_a = self.root / "app" / name / "output" / "idem_a"
        out_b = self.root / "app" / name / "output" / "idem_b"
        ra = run_pipeline(work_case, out_a, cwd=str(case_dir))
        rb = run_pipeline(work_case, out_b, cwd=str(case_dir))
        self.assertEqual(strip_generated(ra), strip_generated(rb))


if __name__ == "__main__":
    unittest.main(verbosity=2)
