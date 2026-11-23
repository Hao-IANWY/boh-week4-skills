#!/usr/bin/env python3
"""043 回归测试：最小示例基线 + realtest 4 案例真实文件库仿真（全离线、纯标准库）。

覆盖：别名/完全重复识别（同哈希异名）、版本组 canonical（版本序最大、诱饵名降权）、
版本链差异说明、字段 provenance 分级（人工给定 > 伴随表 > 文件名 > 未知）、伴随表与
文件名冲突留痕、别名继承、损坏/加密/无文本层 PDF 诚实跳过、无权限目录显式记码、
同名不同内容登记、ops_log 只读证明、输入前后哈希对照、归档校验、两次运行幂等
（除 generated_at）。

realtest 案例由 review/043_realtest/build_cases.py 现场构建（synthetic，
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
sys.path.insert(0, str(REPO / "review" / "043_realtest"))
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
        tmp = tempfile.TemporaryDirectory(prefix="lib043-realtest-")
        build_cases.build(Path(tmp.name) / "runtime")
        return tmp

    # ---------------------------------------------------------------- 最小示例基线
    def test_min_example_matches_expected_and_validates(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        with tempfile.TemporaryDirectory(prefix="lib043-min-") as d:
            out = Path(d) / "out"
            result = self.run_pipeline(case, out)
            self.validate(case, out / "result.json")
            got = strip_generated((out / "result.json").read_text(encoding="utf-8"))
            exp = strip_generated(expected.read_text(encoding="utf-8"))
            self.assertEqual(got, exp, "min_example 输出与 expected 基线不一致（规则改了未重生成基线）")
            dups = result["主结果"]["duplicate_groups"]
            self.assertEqual(len(dups), 1)
            self.assertEqual(dups[0]["canonical"], "PROJ-A/2024_raw/exp_run01.csv")
            chains = result["主结果"]["version_chains"]
            self.assertEqual(chains[0]["latest"], "PROJ-A/2024_analysis/analysis_v2.md")
            self.assertTrue(result["证据"]["integrity"]["hashes_unchanged"])

    def test_min_example_idempotent(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="lib043-idem-") as d:
            out1, out2 = Path(d) / "o1", Path(d) / "o2"
            self.run_pipeline(case, out1)
            self.run_pipeline(case, out2)
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))

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
            cdir = Path(tmp.name) / "runtime" / "REAL-043-LIBRARY-BASE"
            lib = cdir / "input" / "library"
            files_before = {p.relative_to(lib).as_posix(): sha256_file(p)
                            for p in sorted(lib.rglob("*")) if p.is_file()}
            self.assertEqual(len(files_before), 11)  # 9 文档（含 metadata.csv）+ 1 .ocr.txt + 1 .ocr.json 侧车
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            main = result["主结果"]
            # 9 份文档建档（侧车剔除但入台账）
            self.assertEqual(main["counts"]["source_files"], 9)
            self.assertEqual(main["counts"]["sidecars"], 2)
            self.assertEqual(main["library_mode"]["sidecars"][0]["owner"], "scan_2021_fieldlog")
            # 别名识别：同哈希异名 → canonical + aliases
            alias = main["duplicate_groups"][0]
            self.assertEqual(alias["canonical"], "paperA_final_v2.pdf")
            self.assertEqual(alias["aliases"], ["paperA_final_v2_renamed.pdf"])
            # 版本组：canonical=final-v2（行政性版本说明），链带差异说明
            chain = main["version_chains"][0]
            self.assertEqual(chain["canonical"], {"path": "paperA_final_v2.pdf", "version": "final-v2"})
            self.assertEqual(chain["version_note_source"], "version_note")
            self.assertEqual([c["path"] for c in chain["chain"]],
                             ["paperA_draft_v1.pdf", "paperA_final_v2.pdf", "paperA_final_v2_renamed.pdf"])
            diff = chain["chain"][1]["diff_vs_previous"]
            self.assertIn("lines_added", diff) and self.assertGreater(diff["lines_added"], 0)
            # 提取链：PDF/DOCX/侧车/纯文本各有真实记录
            methods = {f["source_path"]: f["extraction_method"] for f in main["files"]}
            self.assertEqual(methods["paperA_final_v2.pdf"], "pdf_textlayer")
            self.assertEqual(methods["lab_meeting_notes.docx"], "docx_xml")
            self.assertEqual(methods["scan_2021_fieldlog.pdf"], "sidecar_ocr_text")
            self.assertEqual(methods["analysis_notes.md"], "plain_text")
            # 字段出处：伴随表命中、别名继承
            prov = {f["source_path"]: f["field_provenance"] for f in main["files"]}
            self.assertEqual(prov["paperA_final_v2.pdf"]["project"], "伴随表[metadata.csv]")
            self.assertEqual(prov["paperA_final_v2_renamed.pdf"]["project"],
                             "别名继承[paperA_final_v2.pdf]")
            self.assertEqual(prov["reading_log.txt"]["project"], "未知")
            # 请求应答 + ops_log 只读 + 输入未动 + 归档校验
            q2 = next(q for q in main["requests"] if q["request"] == "Q2")
            self.assertEqual(q2["duplicate_groups"], 1)
            ops = result["证据"]["ops_log"]["entries"]
            self.assertIn("merge_duplicate_candidates", [o["op"] for o in ops])
            self.assertTrue(all(o.get("input_readonly") and not o.get("executed") for o in ops))
            integrity = result["证据"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], len(files_before))
            archive = result["证据"]["archive_manifest"]
            self.assertTrue(archive["verify"]["ok"])
            self.assertEqual(archive["file_count"], 9)
            self.assertEqual(archive["total_bytes"], sum(f["bytes"] for f in main["files"]))
            for rel, before in files_before.items():
                self.assertEqual(sha256_file(lib / rel), before, f"输入被改写：{rel}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case2 版本串味
    def test_realtest_case2_version_mix(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-043-VERSION-MIX"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            main = result["主结果"]
            chain = main["version_chains"][0]
            self.assertEqual(chain["canonical"], {"path": "methods_v2_renamed.pdf", "version": "v2"})
            self.assertEqual(chain["count"], 3)
            # 诱饵名降权：latest 系误命名副本，canonical 落在 v1 真名上
            alias = main["duplicate_groups"][0]
            self.assertEqual(alias["canonical"], "methods_v1.pdf")
            self.assertEqual(alias["aliases"], ["methods_latest.pdf"])
            # 谁替代谁：latest 与 v1 同内容（链内相邻 same_content），v2 替代 v1 内容有变化
            links = {c["path"]: c["diff_vs_previous"] for c in chain["chain"]}
            self.assertTrue(links["methods_v1.pdf"]["same_content"])  # latest→v1 相邻同哈希
            self.assertFalse(links["methods_v2_renamed.pdf"]["same_content"])
            self.assertGreater(links["methods_v2_renamed.pdf"]["lines_added"], 0)
            # 版本说明指向库外文件：显式缺口，不凭空建档
            gaps = result["失败与恢复"]["version_note_gaps"]
            self.assertEqual([(g["code"], g["path"]) for g in gaps],
                             [("S_NOTE_UNMATCHED", "paperX_draft_v3.pdf")])
            issue_codes = {i["code"] for i in result["失败与恢复"]["issues"]}
            self.assertIn("S_NOTE_UNMATCHED", issue_codes)
            self.assertEqual(main["counts"]["source_files"], 4)  # 空目录探针不产生文档
            self.assertTrue(result["证据"]["integrity"]["hashes_unchanged"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case3 元数据缺口
    def test_realtest_case3_metadata_gap(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-043-METADATA-GAP"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            main = result["主结果"]
            files = {f["source_path"]: f for f in main["files"]}
            # 冲突：伴随表 2023 覆盖文件名 2022，双方值留痕
            conflicts = result["证据"]["field_conflicts"]
            self.assertIn("sedDNA_protocol_2022_v1.pdf", conflicts)
            fc = conflicts["sedDNA_protocol_2022_v1.pdf"][0]
            self.assertEqual((fc["field"], fc["kept"], fc["kept_provenance"], fc["dropped"], fc["dropped_provenance"]),
                             ("year", "2023", "伴随表[metadata.csv]", "2022", "文件名解析"))
            self.assertEqual(files["sedDNA_protocol_2022_v1.pdf"]["metadata"]["year"], "2023")
            self.assertEqual(files["sedDNA_protocol_2022_v1.pdf"]["field_provenance"]["year"], "伴随表[metadata.csv]")
            # 人工给定优先级最高：裸文件 project/year 来自补充表
            self.assertEqual(files["untitled_scan_notes.md"]["metadata"]["project"], "DEEPMETA")
            self.assertEqual(files["untitled_scan_notes.md"]["field_provenance"]["project"].startswith("人工给定"), True)
            # 未知不补造：metadata.csv 与 bathy 的 project 记「未知」，进低填充清单
            self.assertEqual(files["metadata.csv"]["field_provenance"]["project"], "未知")
            self.assertEqual(files["bathy_notes_2021_v1.pdf"]["field_provenance"]["project"], "未知")
            low = {x["path"] for x in result["失败与恢复"]["low_fill_rate_files"]}
            self.assertIn("metadata.csv", low)
            codes = [i["code"] for i in result["失败与恢复"]["issues"]]
            self.assertIn("W_FIELD_CONFLICT", codes)
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case4 鲁棒
    def test_realtest_case4_robust(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-043-ROBUST"
            lib = cdir / "input" / "library"
            sealed = lib / "restricted_sealed"
            sealed.chmod(0o000)  # 只读探测：运行后必须记 S_DIR_UNREADABLE
            files_before = {p.relative_to(lib).as_posix(): sha256_file(p)
                            for p in sorted(lib.rglob("*")) if p.is_file()}
            try:
                result = self.run_pipeline(cdir / "input" / "case.json",
                                           cdir / "app" / "output", cdir / "app" / "work")
            finally:
                sealed.chmod(0o755)  # 恢复权限（不恢复会污染 tmp 清理）
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            # 损坏/加密/无文本层分别诚实记码
            gaps = {g["path"]: g["gap_code"] for g in result["证据"]["extraction"] if g["gap_code"]}
            self.assertEqual(gaps.get("broken_report.pdf"), "R_FILE_CORRUPT")
            self.assertEqual(gaps.get("locked_memberlist.pdf"), "R_FILE_ENCRYPTED")
            self.assertEqual(gaps.get("scan_no_ocr.pdf"), "R_NO_TEXT_LAYER")
            # 无权限目录显式记码，不静默
            skipped_codes = {(s["code"], Path(s["path"]).name) for s in result["失败与恢复"]["skipped"]}
            self.assertIn(("S_DIR_UNREADABLE", "restricted_sealed"), skipped_codes)
            self.assertNotIn("restricted_sealed/sealed_notes.md", gaps)
            # 同名不同内容登记
            collisions = [i for i in result["失败与恢复"]["issues"] if i["code"] == "W_NAME_COLLISION"]
            self.assertEqual(len(collisions), 1)
            self.assertEqual(collisions[0]["item"], "notes.md")
            # 修复建议进入 ops_log，全部只读
            ops = result["证据"]["ops_log"]["entries"]
            self.assertIn("repair_file_gap", [o["op"] for o in ops])
            self.assertTrue(all(o["input_readonly"] for o in ops))
            self.assertTrue(result["证据"]["integrity"]["hashes_unchanged"])
            for rel, before in files_before.items():
                self.assertEqual(sha256_file(lib / rel), before, f"输入被改写：{rel}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- realtest 幂等
    def test_realtest_case1_idempotent(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-043-LIBRARY-BASE"
            out1, out2 = cdir / "app" / "out1", cdir / "app" / "out2"
            self.run_pipeline(cdir / "input" / "case.json", out1, cdir / "app" / "work")
            self.run_pipeline(cdir / "input" / "case.json", out2, cdir / "app" / "work")
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
