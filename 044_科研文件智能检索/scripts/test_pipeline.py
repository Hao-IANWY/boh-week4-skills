#!/usr/bin/env python3
"""044 回归测试：最小示例基线 + realtest 4 案例真实文件库仿真（全离线、纯标准库）。

覆盖：文件库目录扫描与逐文件哈希、侧车/PDF/DOCX/纯文本提取链、别名合并、
版本组 canonical、跨文件语义对齐（中文提问→英文 PDF，词表扩展可解释）、
硬过滤不放宽（filtered_but_relevant 单列）、stale_version/duplicate_copy 提示、
双栏阅读顺序提示、印刷页与物理页双记、损坏/加密/无文本层/无权限诚实跳过、
空结果如实（库内无支撑 vs 无权限覆盖说明）、ops_log 只读建议、
两次运行幂等（除 generated_at）。

realtest 案例由 review/044_realtest/build_cases.py 现场构建（synthetic，
构建期自带 PDF round-trip 自检），本文件不改写任何输入。
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
REPO = SKILL_DIR.parent.parent
sys.path.insert(0, str(REPO / "review" / "044_realtest"))
import build_cases  # noqa: E402


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strip_generated(text: str) -> str:
    data = json.loads(text)
    data.pop("generated_at", None)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class PipelineTests(unittest.TestCase):
    def run_pipeline(self, case_path: Path, out_dir: Path, work_dir: Path | None = None):
        cmd = [sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out_dir)]
        if work_dir is not None:
            cmd += ["--work", str(work_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result_path = out_dir / "result.json"
        self.assertTrue(result_path.exists(), proc.stderr + proc.stdout)
        return json.loads(result_path.read_text(encoding="utf-8"))

    def validate(self, case_path: Path, result_path: Path):
        proc = subprocess.run([sys.executable, "-B", str(HERE / "validate.py"),
                               "--case", str(case_path), "--result", str(result_path)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def build_realtest(self) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory(prefix="retr044-realtest-")
        build_cases.build(Path(tmp.name) / "runtime")
        return tmp

    # ---------------------------------------------------------------- 最小示例基线
    def test_min_example_matches_expected_and_validates(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        with tempfile.TemporaryDirectory(prefix="retr044-min-") as d:
            out = Path(d) / "out"
            result = self.run_pipeline(case, out)
            self.validate(case, out / "result.json")
            got = strip_generated((out / "result.json").read_text(encoding="utf-8"))
            exp = strip_generated(expected.read_text(encoding="utf-8"))
            self.assertEqual(got, exp, "min_example 输出与 expected 基线不一致（规则改了未重生成基线）")
            # Q1：F1 canonical 无提示，F3 旧版 stale_version，副本 duplicate_copy 单列
            q1 = result["matches"][0]
            self.assertEqual(q1["query_id"], "Q1")
            hints = {h["file"]["file_id"]: h.get("version_hint", {}).get("kind") for h in q1["hits"]}
            self.assertIsNone(hints["F1"])
            self.assertEqual(hints["F3"], "stale_version")
            dup_paths = [n["path"] for n in result["query_views"][0]["duplicate_copies_of_hits"]]
            self.assertTrue(any("副本" in p for p in dup_paths))
            # Q2：硬过滤拦下，绝不放宽
            self.assertEqual(result["matches"][1]["hit_count"], 0)
            self.assertEqual(result["gaps"][0]["code"], "R_FILTER_BLOCKED_HITS")
            self.assertTrue(result["query_views"][1]["filtered_but_relevant"])
            # 契约字段未被投影吞掉
            self.assertIn("matches", result and result.keys())
            self.assertTrue((out / "ops_log.json").is_file())

    def test_min_example_idempotent(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="retr044-idem-") as d:
            out1, out2 = Path(d) / "o1", Path(d) / "o2"
            self.run_pipeline(case, out1)
            self.run_pipeline(case, out2)
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
            self.assertEqual(sha256_file(out1 / "ops_log.json"), sha256_file(out2 / "ops_log.json"))

    def test_pdf_min_roundtrip_self_check(self):
        proc = subprocess.run([sys.executable, "-B", str(HERE / "pdf_min.py")],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn('"ok": true', proc.stdout)

    # ---------------------------------------------------------------- case1 基准库
    def test_realtest_case1_library_base(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-044-LIBRARY-BASE"
            lib = cdir / "input" / "library"
            files_before = {p.name: sha256_file(p) for p in sorted(lib.iterdir()) if p.is_file()}
            self.assertEqual(len(files_before), 11)  # 9 文档 + 1 侧车 .ocr.txt + 1 侧车 .ocr.json
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            self.assertEqual(result["library_report"]["mode"], "library")
            self.assertEqual(len(result["library_report"]["files"]), 9)
            # 提取链逐文件真实记录
            methods = {f["path"]: f["extraction_method"] for f in result["library_report"]["files"]}
            self.assertEqual(methods["paperA_final_v2.pdf"], "pdf_textlayer")
            self.assertEqual(methods["lab_meeting_notes.docx"], "docx_xml")
            self.assertEqual(methods["scan_2021_notes.pdf"], "sidecar_ocr_text")
            self.assertEqual(methods["experiment_protocol.txt"], "plain_text")
            self.assertEqual(methods["reading_list_v2.md"], "plain_text")
            # 别名组合并 + 两个版本组 canonical 正确
            self.assertEqual(len(result["library_report"]["alias_groups"]), 1)
            alias = result["library_report"]["alias_groups"][0]
            self.assertEqual(alias["canonical"], "paperA_final_v2.pdf")
            vgs = {v["canonical"]["path"]: v for v in result["library_report"]["version_groups"]}
            self.assertIn("paperA_final_v2.pdf", vgs)
            self.assertEqual(vgs["paperA_final_v2.pdf"]["canonical"]["version"], "final-v2")
            self.assertEqual(len(vgs["paperA_final_v2.pdf"]["members"]), 3)
            self.assertIn("reading_list_v2.md", vgs)
            # LQ1 跨文件语义对齐：中文提问命中英文 PDF 与同主题 DOCX（词表扩展可解释）
            lq1 = result["matches"][0]
            self.assertEqual(lq1["query_id"], "LQ1")
            hit_paths = {h["file"]["path"] for h in lq1["hits"]}
            self.assertIn("paperA_final_v2.pdf", hit_paths)
            self.assertIn("lab_meeting_notes.docx", hit_paths)
            self.assertGreaterEqual(len(hit_paths), 3)  # 至少 3 个不同文件
            final_hit = next(h for h in lq1["hits"] if h["file"]["path"] == "paperA_final_v2.pdf")
            self.assertIn("sampling_rate", final_hit["covered_groups"])
            vias = {k["via"] for k in final_hit["matched_keys"]}
            self.assertTrue(any(v.startswith("expansion:") for v in vias), vias)
            self.assertTrue(final_hit["quote_check"]["exact_match"])
            draft_hit = next(h for h in lq1["hits"] if h["file"]["path"] == "paperA_draft_v1.pdf")
            self.assertEqual(draft_hit.get("version_hint", {}).get("kind"), "stale_version")
            # LQ2 硬过滤执行：类型+年份+项目号，命中 paperB 且声明过滤满足
            lq2 = result["matches"][1]
            self.assertEqual(lq2["hit_count"], 1)
            self.assertEqual(lq2["hits"][0]["file"]["path"], "paperB_methods_v1.pdf")
            self.assertTrue(lq2["hits"][0]["filters_satisfied"])
            # LQ3 exclude_scanned：扫描件相关命中被拦下列单，绝不放宽
            lq3_view = result["query_views"][2]
            blocked_paths = [b["path"] for b in lq3_view["filtered_but_relevant"]]
            self.assertIn("scan_2021_notes.pdf", blocked_paths)
            # 输入只读 + ops_log 只建议
            integrity = result["library_report"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], 9)
            self.assertTrue(all(o.get("input_readonly") for o in result["ops_log"]))
            self.assertIn("merge_duplicate_candidates", [o["op"] for o in result["ops_log"]])
            self.assertTrue((cdir / "app" / "output" / "ops_log.json").is_file())
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case2 版本串味
    def test_realtest_case2_version_mix(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-044-VERSION-MIX"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            vgs = result["library_report"]["version_groups"]
            self.assertEqual(len(vgs), 1)
            self.assertEqual(vgs[0]["canonical"], {"path": "methods_v2_renamed.pdf", "version": "v2"})
            self.assertEqual(len(vgs[0]["members"]), 3)
            alias = result["library_report"]["alias_groups"][0]
            self.assertEqual(set(alias["members"]), {"methods_latest.pdf", "methods_v1.pdf"})
            self.assertEqual(alias["canonical"], "methods_v1.pdf")  # 诱饵名 latest 降权
            # LQ1 命中 canonical v2，无 stale 提示
            lq1 = result["matches"][0]
            self.assertEqual(lq1["hits"][0]["file"]["path"], "methods_v2_renamed.pdf")
            self.assertEqual(lq1["hits"][0]["file"]["version"], "v2")
            self.assertNotIn("version_hint", lq1["hits"][0])
            # LQ2 命中 v1 独有内容：stale_version 提示 + latest 副本 duplicate_copy 提示
            lq2 = result["matches"][1]
            hit = lq2["hits"][0]
            self.assertEqual(hit["file"]["path"], "methods_v1.pdf")
            self.assertEqual(hit["version_hint"]["kind"], "stale_version")
            self.assertEqual(hit["version_hint"]["canonical_path"], "methods_v2_renamed.pdf")
            dup = result["query_views"][1]["duplicate_copies_of_hits"]
            self.assertEqual(dup[0]["path"], "methods_latest.pdf")
            self.assertEqual(dup[0]["kind"], "duplicate_copy")
            # 版本口径进入 uncertainty，不冒充最新版
            self.assertTrue(result["uncertainty"])
            self.assertTrue(result["library_report"]["integrity"]["hashes_unchanged"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case3 定位漂移
    def test_realtest_case3_locate_drift(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-044-LOCATE-DRIFT"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            # LQ1 双栏页：阅读顺序提示 + 逐字自检
            lq1 = result["matches"][0]["hits"][0]
            self.assertEqual(lq1["file"]["path"], "two_column_review.pdf")
            self.assertTrue(lq1["location"]["two_column_hint"])
            self.assertIn("left column", lq1["location"]["read_order"])
            self.assertTrue(lq1["quote_check"]["exact_match"])
            # LQ2 跨页续表：首选命中落物理页 3 / 印刷页 7（错位双记）
            lq2_hits = result["matches"][1]["hits"]
            top = lq2_hits[0]
            self.assertEqual(top["file"]["path"], "cruise_report_2023.txt")
            self.assertEqual(top["location"]["page"], 3)
            self.assertEqual(top["location"]["page_label"], "7")
            self.assertEqual(top["location"]["label_source"], "explicit")
            self.assertTrue(top["paragraph_first_sentence"])
            self.assertIn("2200", top["quote"])
            self.assertTrue(top["quote_check"]["exact_match"])
            char_start, char_end = top["location"]["char_range"]
            self.assertLess(char_start, char_end)
            self.assertTrue(result["library_report"]["integrity"]["hashes_unchanged"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case4 鲁棒
    def test_realtest_case4_robust(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-044-ROBUST"
            lib = cdir / "input" / "library"
            before = {p.name: (sha256_file(p) if os.access(p, os.R_OK) else None) for p in sorted(lib.iterdir())}
            modes_before = {p.name: (p.stat().st_mode & 0o777) for p in sorted(lib.iterdir())}
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            self.assertEqual(result["status"]["overall"], "partial")
            # 四类问题文件分别记码，诚实跳过
            gap_codes = {g["path"]: g["code"] for g in result["library_report"]["gaps"]}
            self.assertEqual(gap_codes.get("broken_paper.pdf"), "R_FILE_CORRUPT")
            self.assertEqual(gap_codes.get("locked_scan.pdf"), "R_FILE_ENCRYPTED")
            self.assertEqual(gap_codes.get("empty_scan.pdf"), "R_NO_TEXT_LAYER")
            self.assertEqual(gap_codes.get("restricted_notes.md"), "R_FILE_NO_PERMISSION")
            excluded = {e["path"]: e["code"] for e in result["excluded_files"]}
            self.assertEqual(excluded.get("restricted_notes.md"), "R_FILE_NO_PERMISSION")
            # LQ1 正常命中；LQ2 无命中：如实空结果 + 覆盖说明（无权限文件不可判定）
            self.assertEqual(result["matches"][0]["hit_count"], 1)
            self.assertEqual(result["matches"][0]["hits"][0]["file"]["path"], "notes_v1.md")
            self.assertEqual(result["matches"][1]["hit_count"], 0)
            self.assertEqual(result["gaps"][0]["code"], "R_NO_HIT_IN_SCOPE")
            self.assertIn("coverage_note", result["gaps"][0])
            self.assertIn("无支撑", result["gaps"][0]["coverage_note"])
            # 只读核验（无权限文件不参与哈希重算，权限位不被改动）
            integrity = result["library_report"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], 4)
            for name, digest in before.items():
                if digest is not None:
                    self.assertEqual(sha256_file(lib / name), digest, f"输入被改写：{name}")
            for name, mode in modes_before.items():
                self.assertEqual((lib / name).stat().st_mode & 0o777, mode, f"权限位被改动：{name}")
            # 修复建议只建议不执行
            repairs = [o for o in result["ops_log"] if o["op"] == "repair_file_gap"]
            self.assertEqual(len(repairs), 4)
            self.assertTrue(all(o.get("input_readonly") for o in repairs))
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- realtest 幂等
    def test_realtest_case1_idempotent(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-044-LIBRARY-BASE"
            out1, out2 = cdir / "app" / "out1", cdir / "app" / "out2"
            self.run_pipeline(cdir / "input" / "case.json", out1, cdir / "app" / "work")
            self.run_pipeline(cdir / "input" / "case.json", out2, cdir / "app" / "work")
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
            self.assertEqual(sha256_file(out1 / "ops_log.json"), sha256_file(out2 / "ops_log.json"))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
