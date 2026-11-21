#!/usr/bin/env python3
"""047 回归测试：最小示例基线 + realtest 4 案例真实文件库仿真（全离线、纯标准库）。

覆盖：别名/完全重复识别、跨文件版本归组与 canonical、quote 逐字/模糊锚点、
双栏提示、OCR 低置信降级、损坏/加密文件诚实跳过、空批注 empty 契约、
BM25 检索回链、ops 只读建议、交接清单落盘、两次运行幂等（除 generated_at）。

realtest 案例由 review/047_realtest/build_cases.py 现场构建（synthetic，
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
sys.path.insert(0, str(REPO / "review" / "047_realtest"))
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
        tmp = tempfile.TemporaryDirectory(prefix="annot047-realtest-")
        build_cases.build(Path(tmp.name) / "runtime")
        return tmp

    # ---------------------------------------------------------------- 最小示例基线
    def test_min_example_matches_expected_and_validates(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        with tempfile.TemporaryDirectory(prefix="annot047-min-") as d:
            out = Path(d) / "out"
            result = self.run_pipeline(case, out)
            self.validate(case, out / "result.json")
            got = strip_generated((out / "result.json").read_text(encoding="utf-8"))
            exp = strip_generated(expected.read_text(encoding="utf-8"))
            self.assertEqual(got, exp, "min_example 输出与 expected 基线不一致（规则改了未重生成基线）")
            anchors = {a["annotation_id"]: a["anchor"]["status"] for a in result["annotations"]}
            self.assertEqual(anchors["A3"], "page_mismatch")
            self.assertEqual(anchors["A5"], "unverifiable")
            self.assertEqual(anchors["A8"], "not_found")
            self.assertEqual(anchors["A1"], "verified")

    def test_min_example_idempotent(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="annot047-idem-") as d:
            out1, out2 = Path(d) / "o1", Path(d) / "o2"
            self.run_pipeline(case, out1)
            self.run_pipeline(case, out2)
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
            for name in ("handoff_manifest.json", "handoff_manifest.docx"):
                self.assertEqual(sha256_file(out1 / name), sha256_file(out2 / name), name)

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
            cdir = Path(tmp.name) / "runtime" / "REAL-047-LIBRARY-BASE"
            lib = cdir / "input" / "library"
            files_before = {p.name: sha256_file(p) for p in sorted(lib.iterdir()) if p.is_file()}
            self.assertEqual(len(files_before), 11)  # 9 文档 + 1 侧车 .ocr.txt + 1 侧车 .ocr.json
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            lib_report = result["library_report"]
            # 文件库：9 份文档建档（侧车不算文档），别名组与两个版本组
            self.assertEqual(len(lib_report["alias_groups"]), 1)
            alias = lib_report["alias_groups"][0]
            self.assertEqual(set(alias["members"]), {"paperA_final_v2.pdf", "paperA_final_v2_copy.pdf"})
            self.assertEqual(alias["canonical"], "paperA_final_v2.pdf")
            groups = {g["canonical"]["path"]: g for g in lib_report["version_groups"]}
            self.assertIn("paperA_final_v2.pdf", groups)
            self.assertEqual(len(groups["paperA_final_v2.pdf"]["members"]), 3)
            self.assertIn("reading_list_v2.md", groups)
            # 锚点：8 verified + 1 page_mismatch（A3 声明第 2 页实际第 1 页）
            anchors = {a["annotation_id"]: a["anchor"]["status"] for a in result["annotations"]}
            self.assertEqual(anchors["A3"], "page_mismatch")
            self.assertEqual(sum(1 for s in anchors.values() if s == "verified"), 9)
            span_ok = {a["annotation_id"]: a["anchor"].get("char_span_checked") for a in result["annotations"]}
            self.assertTrue(span_ok["A1"])
            # 提取链：PDF/DOCX/侧车/纯文本各有真实记录
            methods = {f["path"]: f["extraction_method"] for f in result["evidence"]["materials"]["files"]}
            self.assertEqual(methods["paperA_final_v2.pdf"], "pdf_textlayer")
            self.assertEqual(methods["lab_meeting_notes.docx"], "docx_xml")
            self.assertEqual(methods["scan_2021_notes.pdf"], "sidecar_ocr_text")
            self.assertEqual(methods["experiment_protocol.txt"], "plain_text")
            self.assertEqual(len(methods), 9)
            # 版本串味提示：副本 / 旧版各就位
            hints = {h["annotation_id"]: h["kind"] for h in lib_report["version_confusion_hints"]}
            self.assertEqual(hints["A4"], "duplicate_copy")
            self.assertEqual(hints["A5"], "stale_version")
            self.assertEqual(hints["A9"], "stale_version")
            self.assertEqual(result["notes"][4]["source"]["version"], "v1")  # A5 挂在 draft v1
            # BM25 检索闭环：命中 + 回链 + 摘录回答
            self.assertEqual(len(result["lookups"]), 3)
            for q in result["lookups"]:
                self.assertEqual(q["recall"], "ok")
                self.assertTrue(q["hits"] and q["answer"].startswith("库内证据摘录"))
                self.assertIn("page", q["hits"][0]["source"])
            # ops 只建议、输入只读、交接清单落盘
            ops = result["ops_log"]
            self.assertIn("merge_duplicate_candidates", [o["op"] for o in ops])
            self.assertTrue(all(o.get("input_readonly") for o in ops))
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], 9)
            out = cdir / "app" / "output"
            self.assertTrue((out / "handoff_manifest.json").is_file())
            self.assertTrue((out / "handoff_manifest.docx").is_file())
            # 输入文件在跑完后逐字节未变
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case2 版本串味
    def test_realtest_case2_version_mix(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-047-VERSION-MIX"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            groups = result["library_report"]["version_groups"]
            self.assertEqual(len(groups), 1)
            g = groups[0]
            self.assertEqual(g["canonical"], {"path": "methods_v2_renamed.pdf", "version": "v2"})
            self.assertEqual(len(g["members"]), 3)
            alias = result["library_report"]["alias_groups"][0]
            self.assertEqual(set(alias["members"]), {"methods_latest.pdf", "methods_v1.pdf"})
            hints = {h["annotation_id"]: h for h in result["library_report"]["version_confusion_hints"]}
            self.assertEqual(hints["V2"]["kind"], "stale_version")
            self.assertFalse(hints["V2"]["quote_in_canonical"])  # v1 独有引文，新版已无
            self.assertTrue(hints["V3"]["quote_in_canonical"])  # 两版共有，可迁移
            # 笔记保留版本口径：V2 内容是 v1，不得冒充最新版
            versions = {n["annotation_ids"][0]: n["source"]["version"] for n in result["notes"]}
            self.assertEqual(versions["V2"], "v1")
            self.assertEqual(versions["V1"], "v2")
            self.assertEqual({a["anchor"]["status"] for a in result["annotations"]}, {"verified"})
            self.assertTrue(result["uncertainty"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case3 锚点漂移
    def test_realtest_case3_anchor_drift(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-047-ANCHOR-DRIFT"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            anchors = {a["annotation_id"]: a["anchor"] for a in result["annotations"]}
            self.assertEqual(anchors["D1"]["status"], "verified")
            self.assertTrue(anchors["D1"]["two_column_hint"])  # 双栏页提示
            self.assertTrue(anchors["D1"]["char_span_checked"])
            self.assertEqual(anchors["D2"]["status"], "page_mismatch")
            self.assertEqual(anchors["D2"]["corrected_page"], 1)  # 声明 2 实际 1
            self.assertEqual(anchors["D3"]["status"], "fuzzy_matched")  # OCR 漂移如实降级
            self.assertGreaterEqual(anchors["D3"]["fuzzy"]["similarity"], 0.9)
            self.assertEqual(anchors["D5"]["status"], "not_found")  # 缺失引文诚实标注
            self.assertLess(anchors["D5"]["fuzzy"]["similarity"], 0.5)
            note_d4 = next(n for n in result["notes"] if n["annotation_ids"] == ["D4"])
            self.assertTrue(note_d4["ocr_degraded"])  # OCR 低置信降级
            self.assertFalse(note_d4["usable"])
            self.assertEqual(note_d4["confidence"], 0.8)
            # 状态如部分完成，不写成 complete
            self.assertEqual(result["status"]["overall"], "partial")
            self.assertTrue(result["status"]["unmet"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case4 鲁棒
    def test_realtest_case4_robust(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-047-ROBUST"
            lib = cdir / "input" / "library"
            files_before = {p.name: sha256_file(p) for p in sorted(lib.iterdir()) if p.is_file()}
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            self.assertEqual(result["status"]["overall"], "empty")
            self.assertEqual(result["annotations"], [])
            gap_codes = {g["path"]: g["code"] for g in result["library_report"]["gaps"]}
            self.assertEqual(gap_codes.get("broken_paper.pdf"), "R_FILE_CORRUPT")
            self.assertEqual(gap_codes.get("locked_scan.pdf"), "R_FILE_ENCRYPTED")
            self.assertEqual(gap_codes.get("empty_scan.pdf"), "R_NO_TEXT_LAYER")
            self.assertEqual(gap_codes.get("zero_byte.tmp"), "R_FILE_NO_EXTRACTOR")
            unmet_text = json.dumps(result["status"]["unmet"], ensure_ascii=False)
            self.assertIn("批注清单", unmet_text)
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
            self.assertEqual(result["lookups"][0]["recall"], "insufficient")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- realtest 幂等
    def test_realtest_case1_idempotent(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-047-LIBRARY-BASE"
            out1, out2 = cdir / "app" / "out1", cdir / "app" / "out2"
            self.run_pipeline(cdir / "input" / "case.json", out1, cdir / "app" / "work")
            self.run_pipeline(cdir / "input" / "case.json", out2, cdir / "app" / "work")
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
            for name in ("handoff_manifest.json", "handoff_manifest.docx"):
                self.assertEqual(sha256_file(out1 / name), sha256_file(out2 / name), name)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
