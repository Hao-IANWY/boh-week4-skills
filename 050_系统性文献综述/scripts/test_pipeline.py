#!/usr/bin/env python3
"""050 回归测试：最小示例 + realtest 4 案例（合成数据，非正式测试案例）。纯标准库、离线。

校验面：
  * 最小示例：契约 PASS、与 expected 基线一致（忽略 generated_at）、前瞻协议注册、
    flow_counts 闭合恒等式全真、数值冲突双列（52.3% × 49.0%）、not_reported 不判低质量；
  * case1 基准：协议先于结果落盘（mtime + protocol.json 内容）、四阶段计数链闭合（测试内独立复算）、
    证据逐字 quote 对来源文件按 char_span 独立复核（含 PDF 自研提取）、冲突双列共享口径、回链覆盖率 1.0；
  * case2 计数压力：每条排除记录恰落一个原因桶（主因唯一）、M10 双因命中按规则取主因、
    各桶计数之和 = 阶段排除总数、和差恒等式独立复算；
  * case3 质量评价边界：缺 sample_size/methodology 字段判 not_reported（weight=0、不计入分母）、
    等级不降级（缺失字段不产生 low）、综合确定性如实降级（strong 被封顶 moderate）；
    另含 not_assessable 单元级检查（全部条目未报告 → 无法分级而非低质量）；
  * case4 鲁棒：协议缺失 → input_gap + 声明回退口径（fallback=true）、空来源文件与缺摘要记录
    诚实排除（不编造正文）、预印本 DOI 去重、综述主结果为空但筛选/质量审计完整；
  * 幂等：两次运行 result.json（除 generated_at）与 protocol.json 逐字节一致。
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
BUILDER = REPO_ROOT / "review" / "050_realtest" / "build_cases.py"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
sys.path.insert(0, str(HERE))


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def same_without_generated(a: Path, b: Path) -> bool:
    da, db = load_result(a), load_result(b)
    da.pop("generated_at", None)
    db.pop("generated_at", None)
    return json.dumps(da, ensure_ascii=False, sort_keys=True) == json.dumps(db, ensure_ascii=False, sort_keys=True)


def independent_closure(flow: dict) -> list:
    """测试内独立复算 PRISMA 计数链恒等式，不依赖管线自报。"""
    fc = flow["flow_counts"]
    ta_buckets = flow["screening"]["exclusion_reasons"]
    ft_buckets = flow["eligibility"]["exclusion_reasons"]
    identities = [
        ("I1", fc["identified"] == fc["duplicates_removed"] + fc["after_dedup"]),
        ("I2", fc["after_dedup"] == fc["excluded_title_abstract"] + fc["assessed_full_text"]),
        ("I3", fc["assessed_full_text"] == fc["excluded_full_text"] + fc["included"]),
        ("I4", sum(b["count"] for b in ta_buckets) == fc["excluded_title_abstract"]),
        ("I5", sum(b["count"] for b in ft_buckets) == fc["excluded_full_text"]),
        ("I6", len({r for b in ta_buckets + ft_buckets for r in b["record_ids"]})
         == sum(len(b["record_ids"]) for b in ta_buckets + ft_buckets)),
        ("I7", sum(flow["identification"]["per_source"].values()) == fc["identified"]),
    ]
    return identities


class SystematicReviewTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory
    root: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="sysreview050-test-")
        cls.root = Path(cls.tmp.name) / "runtime"
        proc = subprocess.run([sys.executable, "-B", str(BUILDER), str(cls.root)],
                              capture_output=True, text=True, timeout=120, env=ENV)
        if proc.returncode != 0:
            raise AssertionError(f"build_cases 失败：{proc.stderr + proc.stdout}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- 公共工具 ----
    def run_case(self, dirname: str, tag: str) -> tuple:
        case_path = self.root / "input" / dirname / "case.json"
        work = self.root / "app" / dirname / f"work_{tag}"
        out = self.root / "app" / dirname / f"out_{tag}"
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
        return load_result(result_path), work, out, case_path

    def assert_closure(self, result: dict):
        checks = result["prisma_flow"]["consistency"]["checks"]
        self.assertTrue(checks)
        self.assertTrue(all(c["ok"] for c in checks), json.dumps(checks, ensure_ascii=False))
        for cid, ok in independent_closure(result["prisma_flow"]):
            self.assertTrue(ok, f"{cid} 独立复算失败")

    def assert_span_against_sources(self, result: dict, case_path: Path):
        """独立复核：quote 必须是来源文本（或自研提取文本）char_span 处的精确子串。"""
        import pdf_min

        case_dir = case_path.parent
        for row in result["evidence_matrix"]:
            if row["extraction_method"] in ("inline_text", "inline_abstract"):
                continue
            src = case_dir / row["source_file"] if not Path(row["source_file"]).is_absolute() else Path(row["source_file"])
            if row["extraction_method"] == "pdf_extract":
                text = pdf_min.extract_pdf_file(src)["text"]
            else:
                text = src.read_text(encoding="utf-8").strip()
            start, end = row["char_span"]["start"], row["char_span"]["end"]
            self.assertGreaterEqual(start, 0, row["evidence_id"])
            self.assertEqual(text[start:end], row["quote"], row["evidence_id"])

    # ---- 1. 最小示例 ----
    def test_min_example_matches_baseline(self):
        case_path = SKILL_DIR / "examples/min_example/input/case.json"
        out = Path(self.tmp.name) / "min_out"
        out.mkdir(parents=True)
        proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out)],
                              capture_output=True, text=True, timeout=300, env=ENV)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result = load_result(out / "result.json")
        self.assertEqual(result["status"]["overall"], "complete")
        self.assertEqual(result["prisma_flow"]["identification"]["records_identified"], 8)
        self.assertEqual(result["prisma_flow"]["included"]["records_included"], 4)
        self.assertEqual(result["screening_log"]["protocol_status"], "registered_prospective")
        self.assertEqual(result["screening_log"]["protocol_id"], "PROTO-EXAMPLE-050-GNN-RETRO")
        self.assert_closure(result)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["type"], "numeric_conflict")
        self.assertEqual({conflict["side_a"]["value"], conflict["side_b"]["value"]}, {"52.3", "49.0"})
        self.assertTrue(conflict["side_a"]["quote_verified"] and conflict["side_b"]["quote_verified"])
        self.assertTrue(conflict["shared_context"])  # 双列共享口径（uspto-50k + gnn）
        quality = result["quality_appraisal"]
        self.assertEqual(quality["overall"]["low"], 0)  # not_reported 不产生低质量
        self.assertGreater(quality["not_reported_total"], 0)
        for concl in result["synthesis"]:
            if concl["support_level"] != "no_evidence":
                self.assertTrue(concl["sentence_map"])
                self.assertTrue(concl["evidence_ids"])
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        self.assertTrue(same_without_generated(out / "result.json", expected), "与 expected 基线不一致")

    # ---- 2. case1 基准：完整 PRISMA 流 ----
    def test_case1_prospective_flow_and_span_verification(self):
        result, work, out, case_path = self.run_case("case1_baseline", "t1")
        self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        # 前瞻协议先落盘：protocol.json 在 work，内容与结果一致，mtime 不晚于 result.json
        protocol_path = work / "protocol.json"
        self.assertTrue(protocol_path.exists())
        self.assertLessEqual(protocol_path.stat().st_mtime, (out / "result.json").stat().st_mtime)
        protocol_doc = load_result(protocol_path)
        self.assertEqual(protocol_doc["protocol"]["protocol_id"], "PROTO-050-C1-IBS-PROBIOTIC")
        self.assertTrue(protocol_doc["protocol"]["prospective"])
        self.assertEqual([c["id"] for c in protocol_doc["protocol"]["criteria"]],
                         [c["id"] for c in result["screening_log"]["criteria"]])
        self.assertEqual(result["prisma_flow"]["flow_counts"],
                         {"identified": 10, "duplicates_removed": 1, "after_dedup": 9, "screened": 9,
                          "excluded_title_abstract": 3, "assessed_full_text": 6, "excluded_full_text": 1,
                          "included": 5})
        self.assert_closure(result)
        # 证据逐字 + char_span 独立复核（含 PDF 自研提取）
        self.assertTrue(result["evidence_matrix"])
        for row in result["evidence_matrix"]:
            self.assertTrue(row["quote_verified"], f"{row['evidence_id']} 未通过管线内引文校验")
        self.assert_span_against_sources(result, case_path)
        # 冲突双列：32.5% × 28.1%（同 LGG、同 8 周、同 IBS-SSS 口径）
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual({conflict["side_a"]["value"], conflict["side_b"]["value"]}, {"32.5", "28.1"})
        self.assertIn("strain", conflict["shared_context"])
        self.assertEqual(conflict["shared_context"]["duration"], ["w8"])
        self.assertEqual(conflict["shared_context"]["scale"], ["ibs-sss"])
        # 回链覆盖率
        claimed = [c for c in result["synthesis"] if c["support_level"] != "no_evidence"]
        self.assertTrue(claimed)
        self.assertEqual(result["evidence"]["backlink"]["coverage"], 1.0)
        ids = {r["evidence_id"] for r in result["evidence_matrix"]}
        for concl in claimed:
            for eid in concl["evidence_ids"]:
                self.assertIn(eid, ids, concl["conclusion_id"])
        # case1 全字段齐备 → 质量评价无 not_reported
        self.assertEqual(result["quality_appraisal"]["not_reported_total"], 0)
        self.assertEqual(result["quality_appraisal"]["overall"],
                         {"high": 5, "moderate": 0, "low": 0, "not_assessable": 0})
        # xlsx 四工作表
        import zipfile

        with zipfile.ZipFile(out / "prisma_flow.xlsx") as zf:
            sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/")]
            self.assertEqual(len(sheets), 4)

    # ---- 3. case2 计数压力 ----
    def test_case2_primary_reason_unique_and_buckets_closed(self):
        result, work, out, case_path = self.run_case("case2_counts", "t1")
        flow = result["prisma_flow"]
        self.assertEqual(flow["flow_counts"]["included"], 1)
        self.assert_closure(result)
        rows = result["screening_log"]["rows"]
        excluded = [r for r in rows if r["outcome"] == "excluded"]
        # 每条排除记录：主因唯一、primary 落在对应阶段桶、无记录落入两个桶
        bucket_map = {}
        for stage, key in (("title_abstract", "screening"), ("full_text", "eligibility")):
            for bucket in flow[key]["exclusion_reasons"]:
                for rid in bucket["record_ids"]:
                    self.assertNotIn(rid, bucket_map, f"{rid} 落入多个原因桶")
                    bucket_map[rid] = (stage, bucket["criterion_id"])
        for r in excluded:
            self.assertTrue(r["primary_criterion_id"], r["record_id"])
            self.assertIn(r["record_id"], bucket_map)
            stage, cid = bucket_map[r["record_id"]]
            self.assertEqual(stage, r["excluded_at_stage"])
            self.assertEqual(cid, r["primary_criterion_id"])
        # M10 双因命中（类型 + 年份）→ 主因按规则取 C2_TYPE，年份记 secondary
        m10 = next(r for r in rows if r["record_id"] == "M10")
        self.assertEqual(m10["primary_criterion_id"], "C2_TYPE")
        self.assertEqual(m10["secondary_hits"], ["C1_YEAR"])
        hits = [h["criterion_id"] for h in m10["stage_decisions"][0]["criterion_hits"]]
        self.assertEqual(sorted(hits), ["C1_YEAR", "C2_TYPE"])
        # 逐条可查：每条排除记录的 criterion_hits 自带条款 id 与理由文本
        for r in excluded:
            stage_dec = next(x for x in r["stage_decisions"] if x["decision"] == "exclude")
            self.assertTrue(stage_dec["criterion_hits"])
            for h in stage_dec["criterion_hits"]:
                self.assertTrue(h["reason"], r["record_id"])
        # 各桶计数之和 = 阶段排除总数
        self.assertEqual(sum(b["count"] for b in flow["screening"]["exclusion_reasons"]), 7)
        self.assertEqual(sum(b["count"] for b in flow["eligibility"]["exclusion_reasons"]), 2)

    # ---- 4. case3 质量评价边界 ----
    def test_case3_not_reported_never_downgrades(self):
        result, work, out, case_path = self.run_case("case3_quality", "t1")
        self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"]["unmet"], ensure_ascii=False))
        quality = result["quality_appraisal"]
        rows = {r["record_id"]: r for r in quality["rows"]}
        self.assertEqual(quality["overall"]["low"], 0)  # 缺字段不判低质量
        self.assertEqual(quality["overall"]["not_assessable"], 0)
        # P06：缺 sample_size/methodology 字段且依据词未命中 limitation → 3 项 not_reported
        p06 = rows["P06"]
        self.assertEqual([i["item_id"] for i in p06["not_reported_items"]], ["Q2_SAMPLE", "Q3_METHOD", "Q5_LIMITATION"])
        for item in p06["items"]:
            if item["judgment"] == "not_reported":
                self.assertEqual(item["weight"], 0)
                self.assertTrue(item.get("not_reported_reason"))
        # 分母只含已报告条目：P06 仅 2 项报告（Q1/Q4）× 2 分 → max_score 4，ratio 1.0 → high（不降级）
        self.assertEqual(p06["max_score"], 4)
        self.assertEqual(p06["level"], "high")
        self.assertEqual(p06["ratio"], 1.0)
        # P09：3 项未报告仍 high；P08 全部报告 → 无 not_reported
        self.assertEqual(rows["P09"]["level"], "high")
        self.assertEqual(rows["P08"]["not_reported_items"], [])
        # 结构化字段法：P07 缺 sample_size（空串）但 methodology 在 → 仅 Q2 not_reported
        self.assertEqual([i["item_id"] for i in rows["P07"]["not_reported_items"]], ["Q2_SAMPLE"])
        # 综合确定性如实降级：strong 被 not_reported 封顶为 moderate，并列明未报告字段
        concl = {c["theme_id"]: c for c in result["synthesis"]}["TH_INTENSITY"]
        self.assertEqual(concl["support_level"], "moderate")
        self.assertTrue(concl["certainty"]["records_with_not_reported"])
        self.assertIn("P06", concl["certainty"]["records_with_not_reported"])
        self.assertGreater(quality["not_reported_total"], 0)
        # not_assessable 单元级检查：全部条目未报告 → 无法分级而非低质量
        import pipeline

        scale = pipeline.load_rules()["protocol"]["quality_scale"]
        bare = [{"record_id": "BARE", "title": "bare", "abstract": "", "text": "",
                 "keywords": [], "sample_size": "", "methodology": ""}]
        appraised = pipeline.appraise(bare, scale)
        self.assertEqual(appraised[0]["level"], "not_assessable")
        self.assertEqual(appraised[0]["max_score"], 0)
        self.assertIsNone(appraised[0]["ratio"])

    # ---- 5. case4 鲁棒 ----
    def test_case4_fallback_gap_and_missing_body(self):
        result, work, out, case_path = self.run_case("case4_robust", "t1")
        self.assertEqual(result["status"]["overall"], "empty")  # 综述主结果为空：协议缺失不虚构主题
        unmet_items = " ".join(u["item"] + u["reason"] for u in result["status"]["unmet"])
        self.assertIn("R_PROTOCOL_MISSING", unmet_items)
        self.assertEqual(result["screening_log"]["protocol_status"], "missing_fallback_declared")
        protocol_doc = load_result(work / "protocol.json")
        self.assertEqual(protocol_doc["protocol"]["input_gap"]["code"], "R_PROTOCOL_MISSING")
        # 全部筛选行 fallback=true 且整体进入人工复核队列
        rows = result["screening_log"]["rows"]
        self.assertTrue(all(r["fallback"] for r in rows))
        self.assertEqual(result["screening_log"]["fallback_rows"], len(rows))
        self.assertEqual(result["screening_log"]["manual_review"]["count"], len(rows))
        # V02 = V01 预印本重复 → 识别阶段去重
        dropped = result["prisma_flow"]["identification"]["duplicates"]
        self.assertEqual([d["record_id"] for d in dropped], ["V02"])
        self.assertEqual(dropped[0]["duplicate_of"], "V01")
        self.assertEqual(dropped[0]["code"], "R_DUP_DOI")
        # V08 类型超回退集合 → 题录级排除；V09 空文件 + V10 无摘要 → 诚实排除，正文 0 字符不编造
        by_id = {r["record_id"]: r for r in rows}
        self.assertEqual(by_id["V08"]["excluded_at_stage"], "title_abstract")
        self.assertEqual(by_id["V08"]["primary_criterion_id"], "FB_TYPE")
        for rid in ("V09", "V10"):
            self.assertEqual(by_id[rid]["outcome"], "excluded")
            self.assertEqual(by_id[rid]["primary_criterion_id"], "FB_ABSTRACT")
            self.assertEqual(by_id[rid]["body_chars"], 0)
        self.assertEqual(by_id["V09"]["extraction_method"], "missing")
        self.assert_closure(result)
        # 主结果为空但有审计：筛选/质量/计数链仍在
        self.assertEqual(result["synthesis"], [])
        gap_codes = {g["code"] for g in result["research_gaps"]}
        self.assertIn("R_PROTOCOL_NO_THEMES", gap_codes)
        self.assertTrue(result["screening_log"]["rows"])
        self.assertEqual(result["prisma_flow"]["flow_counts"]["included"], 5)
        self.assertGreater(result["quality_appraisal"]["not_reported_total"], 0)

    # ---- 6. 幂等 ----
    def test_idempotent_rerun(self):
        dirname = "case1_baseline"
        (_, work1, out1, case_path) = self.run_case(dirname, "idem1")
        work2 = self.root / "app" / dirname / "work_idem2"
        out2 = self.root / "app" / dirname / "out_idem2"
        out2.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path),
                               "--work", str(work2), "--out", str(out2)],
                              capture_output=True, text=True, timeout=300, env=ENV)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(same_without_generated(out1 / "result.json", out2 / "result.json"))
        self.assertEqual((work1 / "protocol.json").read_bytes(), (work2 / "protocol.json").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
