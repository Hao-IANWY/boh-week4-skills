#!/usr/bin/env python3
"""045 回归测试：最小示例基线 + realtest 4 案例真实文件库仿真（全离线、纯标准库）。

覆盖：分类归属与标签命中可核对（hits 逐条带 field/term/offsets）、版本组 canonical 正确、
命名降级（缺字段 null_token + 冲突版本感知消歧）、去重不误删不误覆盖、
ops_log 只读建议与回滚说明、损坏/加密/无文本层/权限不足/未知格式诚实跳过、
未处理项清单、幂等（两次运行除 generated_at 逐字节一致）。

realtest 案例由 review/045_realtest/build_cases.py 现场构建（synthetic，
构建期自带 PDF round-trip 自检），本文件不改写任何输入（含 chmod 000 的权限件）。
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
sys.path.insert(0, str(REPO / "review" / "045_realtest"))
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
        tmp = tempfile.TemporaryDirectory(prefix="lit045-realtest-")
        build_cases.build(Path(tmp.name) / "runtime")
        return tmp

    # ---------------------------------------------------------------- 最小示例基线
    def test_min_example_matches_expected_and_validates(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        with tempfile.TemporaryDirectory(prefix="lit045-min-") as d:
            out = Path(d) / "out"
            result = self.run_pipeline(case, out)
            self.validate(case, out / "result.json")
            got = strip_generated((out / "result.json").read_text(encoding="utf-8"))
            exp = strip_generated(expected.read_text(encoding="utf-8"))
            self.assertEqual(got, exp, "min_example 输出与 expected 基线不一致（规则改了未重生成基线）")
            view = {r["doc_id"]: r for r in result["records_view"]}
            self.assertEqual(view["D1"]["status"], "classified")
            self.assertEqual(view["D2"]["doc_type"], "preprint")
            self.assertEqual(view["D8"]["duplicate_of"], "D1")
            self.assertEqual(view["D7"]["status"], "low_confidence")
            self.assertEqual(view["D9"]["status"], "unclassified")

    def test_min_example_idempotent(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="lit045-idem-") as d:
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
            cdir = Path(tmp.name) / "runtime" / "REAL-045-LIBRARY-BASE"
            lib = cdir / "input" / "library"
            files_before = {p.name: sha256_file(p) for p in sorted(lib.iterdir()) if p.is_file()}
            self.assertEqual(len(files_before), 10)  # 8 文档 + 1 伴随元数据 CSV + 1 OCR 侧车
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            lr = result["library_report"]
            # 8 份文献文档建档（CSV 作伴随表剔除），9 个真实文件全部哈希留档
            self.assertEqual(lr["documents"], 8)
            self.assertEqual(lr["files_scanned"], 9)
            self.assertEqual(len(lr["auxiliary_files"]), 1)
            self.assertEqual(lr["auxiliary_files"][0]["path"], "library_metadata.csv")
            # 别名/完全重复：canonical 优先非 (1) 命名
            self.assertEqual(len(lr["alias_groups"]), 1)
            alias = lr["alias_groups"][0]
            self.assertEqual(set(alias["members"]),
                             {"paper_jcim_2021_final.pdf", "paper_jcim_2021_final (1).pdf"})
            self.assertEqual(alias["canonical"], "paper_jcim_2021_final.pdf")
            # 版本组：预印本/正式版/副本/补充材料 4 成员，canonical=正式版 final-v2
            self.assertEqual(len(lr["version_groups"]), 1)
            vg = lr["version_groups"][0]
            self.assertEqual(vg["canonical"], {"path": "paper_jcim_2021_final.pdf", "version": "final-v2"})
            self.assertEqual(len(vg["members"]), 4)
            # 分类：归属 + 标签命中可核对（hits 带 field/term/offsets）
            recs = {r["path"]: r for r in result["classification"]}
            expect_types = {"paper_jcim_2021_final.pdf": "journal_article",
                            "arxiv_preprint_2020.pdf": "preprint",
                            "si_2021.pdf": "supplementary",
                            "zenodo_splits_2023.md": "dataset",
                            "thesis_zhang_2022.txt": "thesis"}
            for path, cat in expect_types.items():
                rec = recs[path]
                self.assertEqual(rec["status"], "classified")
                self.assertEqual(rec["doc_type"]["primary"], cat)
                self.assertTrue(rec["doc_type"]["labels"][0]["hits"], f"{path} 无命中依据")
            # 元数据 CSV 并入库：命名有真实作者/年份（非 null_token）
            self.assertEqual(recs["paper_jcim_2021_final.pdf"]["metadata"]["authors"], ["A. Kim", "L. Duarte"])
            self.assertIn("Kim-2021-", recs["paper_jcim_2021_final.pdf"]["filename"]["to"])
            # 副本判重 + 低置信 + 未分类各就位
            self.assertTrue(recs["paper_jcim_2021_final (1).pdf"]["duplicate"]["is_duplicate"])
            self.assertEqual(recs["paper_jcim_2021_final (1).pdf"]["duplicate"]["kind"], "byte_alias")
            self.assertEqual(recs["group_meeting_2023.docx"]["status"], "low_confidence")
            self.assertEqual(recs["scan_2019_notes.pdf"]["status"], "unclassified")
            # 提取链逐文件落档
            methods = lr["extraction_methods"]
            self.assertEqual(methods["paper_jcim_2021_final.pdf"]["method"], "pdf_textlayer")
            self.assertEqual(methods["group_meeting_2023.docx"]["method"], "docx_xml")
            self.assertEqual(methods["scan_2019_notes.pdf"]["method"], "sidecar_ocr_text")
            self.assertEqual(methods["zenodo_splits_2023.md"]["method"], "plain_text")
            for m in methods.values():
                self.assertGreater(m["n_chars"], 0)
                self.assertEqual(len(m["text_sha256"]), 64)
                self.assertEqual(len(m["file_sha256"]), 64)
            # ops 只建议、带依据/幂等键/回滚、输入只读证明
            ops = result["operations"]
            self.assertIn("merge_duplicate_candidates", [o["action"] for o in ops])
            self.assertIn("consolidate_versions", [o["action"] for o in ops])
            for op in ops:
                self.assertTrue(op["input_readonly"], op["action"])
                self.assertFalse(op["executed"])
                self.assertTrue(op["idempotency_key"])
                self.assertIn("rollback", op)
                self.assertTrue(op.get("basis") or op.get("reason"))
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], 9)
            # 输入文件在跑完后逐字节未变（不误删不误覆盖）
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case2 版本与去重
    def test_realtest_case2_version_and_dedup(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-045-VERSION-AND-DEDUP"
            lib = cdir / "input" / "library"
            files_before = sorted(p.name for p in lib.iterdir() if p.is_file())
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            lr = result["library_report"]
            # 版本组 canonical=正式版；别名组 canonical=正式版（非 (1) 副本）
            vg = lr["version_groups"][0]
            self.assertEqual(vg["canonical"], {"path": "gnn_retro_jcim_2021.pdf", "version": "final-v2"})
            self.assertEqual(len(vg["members"]), 4)
            roles = {m["path"]: m["version"] for m in vg["members"]}
            self.assertEqual(roles["gnn_retro_arxiv_2020.pdf"], "preprint-v1")
            self.assertEqual(roles["gnn_retro_si_2021.pdf"], "supplementary")
            alias = lr["alias_groups"][0]
            self.assertEqual(set(alias["members"]),
                             {"gnn_retro_jcim_2021.pdf", "gnn_retro_jcim_2021 (1).pdf"})
            self.assertEqual(alias["canonical"], "gnn_retro_jcim_2021.pdf")
            # 内容版本族 primary=正式版；副本标记 duplicate，kind=byte_alias
            recs = {r["path"]: r for r in result["classification"]}
            self.assertTrue(recs["gnn_retro_jcim_2021 (1).pdf"]["duplicate"]["is_duplicate"])
            self.assertEqual(recs["gnn_retro_jcim_2021 (1).pdf"]["duplicate"]["kind"], "byte_alias")
            fam = result["evidence"]["inventory"]["version_families"][0]
            self.assertEqual(fam["primary_doc"], recs["gnn_retro_jcim_2021.pdf"]["doc_id"])
            # 不误删：全部 4 文件仍在库内；操作只有建议
            self.assertEqual(sorted(p.name for p in lib.iterdir() if p.is_file()), files_before)
            for op in result["operations"]:
                self.assertTrue(op["input_readonly"])
                self.assertFalse(op["executed"])
            self.assertEqual(result["status"]["overall"], "complete")
            self.assertTrue(result["evidence"]["library"]["integrity"]["hashes_unchanged"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case3 元数据缺口与命名冲突
    def test_realtest_case3_metadata_gaps(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-045-METADATA-GAPS"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            recs = {r["path"]: r for r in result["classification"]}
            naming = result["evidence"]["naming"]
            # 命名目标冲突 → 版本感知消歧：两个不同目标名，绝不覆盖
            conflicts = naming["conflicts"]
            self.assertEqual(len(conflicts), 2)
            recs_by_id = {r["doc_id"]: r for r in result["classification"]}
            dsts = {recs_by_id[c["doc_id"]]["filename"]["to"] for c in conflicts}
            self.assertEqual(len(dsts), 2)
            for name in dsts:
                self.assertIn("__", name)
            # 超长标题 + 缺作者/年份 → 降级（null_token 占位，未补造）
            long_rec = recs["long_title_final.pdf"]
            self.assertIn("zz-author", long_rec["filename"]["to"])
            self.assertIn("nd", long_rec["filename"]["to"])
            self.assertTrue(long_rec["filename"]["degraded"])
            self.assertIn("author", long_rec["filename"]["missing_fields"])
            self.assertNotIn("year", long_rec["metadata"])
            self.assertNotIn("authors", long_rec["metadata"])
            # 无元数据文件：弱信号落 review 档（人工确认队列）
            self.assertEqual(recs["workshop_notes_no_meta.md"]["status"], "low_confidence")
            codes = {m["code"] for m in result["manual_review"]}
            self.assertIn("R_LOW_CONFIDENCE", codes)
            # 乱码文件名识别：原文件不动，命名采用标题，入人工确认队列
            garble = next(r for r in result["classification"] if r["filename_garbled"])
            self.assertIn("R_FILENAME_GARBLE", codes)
            self.assertTrue(garble["filename"]["to"].startswith("Sato-2022-Graph-Neural"))
            self.assertIn("filename_garbled", garble["filename"]["degraded_reasons"])
            self.assertEqual(result["status"]["overall"], "partial")  # 低置信待复核
            self.assertTrue(result["evidence"]["library"]["integrity"]["hashes_unchanged"])
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- case4 鲁棒
    def test_realtest_case4_robust(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-045-ROBUST"
            lib = cdir / "input" / "library"
            hashes_before = {}
            for p in sorted(lib.iterdir()):
                if p.is_file():
                    os.chmod(p, 0o644)  # 读哈希后恢复权限交由 build_cases 语义核对
                    hashes_before[p.name] = sha256_file(p)
            os.chmod(lib / "secret_paper.pdf", 0)  # 恢复权限不足状态
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            # 五类异常分别记码，全部进未处理项清单（不静默跳过）
            gap_codes = {g["path"]: g["code"] for g in result["library_report"]["gaps"]}
            self.assertEqual(gap_codes.get("broken_paper.pdf"), "R_FILE_CORRUPT")
            self.assertEqual(gap_codes.get("locked_scan.pdf"), "R_FILE_ENCRYPTED")
            self.assertEqual(gap_codes.get("secret_paper.pdf"), "R_FILE_NO_PERMISSION")
            self.assertEqual(gap_codes.get("scan_no_text.pdf"), "R_NO_TEXT_LAYER")
            self.assertEqual(gap_codes.get("weird.bin"), "R_FILE_NO_EXTRACTOR")
            self.assertEqual(len(result["unprocessed"]), 5)
            for item in result["unprocessed"]:
                self.assertTrue(item["suggested_action"])
            # 正常文件照常分类（thesis 高置信），状态 partial 而非 complete/empty
            self.assertEqual(result["status"]["overall"], "partial")
            rec = next(r for r in result["classification"] if r["path"] == "thesis_good_2022.txt")
            self.assertEqual(rec["status"], "classified")
            self.assertEqual(rec["doc_type"]["primary"], "thesis")
            # 空目录如实记录；输入只读证明（权限件无法重读时如实标注，不静默）
            self.assertEqual(result["library_report"]["empty_dirs"], ["empty_folder"])
            integrity = result["evidence"]["library"]["integrity"]
            self.assertFalse(integrity["input_modified"])
            self.assertIn("secret_paper.pdf", [g["path"] for g in result["library_report"]["gaps"]])
            os.chmod(lib / "secret_paper.pdf", 0o644)
            for name, before in hashes_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
        finally:
            tmp.cleanup()

    # ---------------------------------------------------------------- realtest 幂等
    def test_realtest_case1_idempotent(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-045-LIBRARY-BASE"
            out1, out2 = cdir / "app" / "out1", cdir / "app" / "out2"
            self.run_pipeline(cdir / "input" / "case.json", out1, cdir / "app" / "work")
            self.run_pipeline(cdir / "input" / "case.json", out2, cdir / "app" / "work")
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
