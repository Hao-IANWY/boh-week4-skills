#!/usr/bin/env python3
"""046 回归测试：最小示例基线 + realtest 4 案例真实文件库仿真（全离线、纯标准库）。

覆盖：摘录逐字来自命中片段、回链 file/page/char_range、副本不重复计数、
canonical 版本引用、stale_version/duplicate_copy 提示、
partially_supported（间接证据）/ insufficient_evidence（不用常识补答）、
损坏/加密/无文本层/权限不足四码诚实跳过、ops_log 只读建议、前后哈希对照、幂等。

realtest 案例由 review/046_realtest/build_cases.py 现场构建（synthetic，
构建期自带 PDF round-trip 自检），本文件不改写任何输入库文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
PIPELINE = HERE / "pipeline.py"
REPO = SKILL_DIR.parent.parent
sys.path.insert(0, str(REPO / "review" / "046_realtest"))
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
        tmp = tempfile.TemporaryDirectory(prefix="qa046-realtest-")
        build_cases.build(Path(tmp.name) / "runtime")
        return tmp

    # ---------------------------------------------------------------- 最小示例基线
    def test_min_example_matches_expected_and_validates(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        expected = SKILL_DIR / "examples/min_example/expected/result.json"
        with tempfile.TemporaryDirectory(prefix="qa046-min-") as d:
            out = Path(d) / "out"
            result = self.run_pipeline(case, out)
            self.validate(case, out / "result.json")
            got = strip_generated((out / "result.json").read_text(encoding="utf-8"))
            exp = strip_generated(expected.read_text(encoding="utf-8"))
            self.assertEqual(got, exp, "min_example 输出与 expected 基线不一致（规则改了未重生成基线）")
            # 三态：Q1 冲突并列（v2 1% vs v1 0.5%），Q2 有据，Q3 库内无据
            by_id = {a["question_id"]: a for a in result["answers"]}
            self.assertEqual(by_id["Q1"]["verdict"], "conflicting")
            used = set(by_id["Q1"]["used_claims"])
            conflict_ids = {cid for row in by_id["Q1"]["conflicts"] for cid in row["claims"]}
            self.assertTrue(conflict_ids and conflict_ids <= used)
            self.assertEqual(by_id["Q2"]["verdict"], "answered")
            self.assertEqual(by_id["Q3"]["verdict"], "insufficient")
            self.assertEqual(by_id["Q3"]["uncertainty"]["level"], "insufficient_evidence")
            # 副本 K2 不重复计数：chunk 去重后 K1/K2 同内容只留一个来源
            self.assertTrue(result["evidence"]["kb"]["dedupe"]["dropped"])

    def test_min_example_idempotent(self):
        case = SKILL_DIR / "examples/min_example/input/case.json"
        with tempfile.TemporaryDirectory(prefix="qa046-idem-") as d:
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
            cdir = Path(tmp.name) / "runtime" / "REAL-046-LIBRARY-BASE"
            lib = cdir / "input" / "library"
            files_before = {p.name: sha256_file(p) for p in sorted(lib.iterdir()) if p.is_file()}
            self.assertEqual(len(files_before), 11)  # 9 文档 + 1 侧车 .ocr.txt + 1 侧车 .ocr.json
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            rep = result["library_report"]
            # 9 份文档建档（侧车不算文档），别名组与两个版本组
            self.assertEqual(rep["files_scanned"], 9)
            self.assertEqual(len(rep["alias_groups"]), 1)
            alias = rep["alias_groups"][0]
            self.assertEqual(set(alias["members"]), {"paperA_final_v2.pdf", "paperA_final_v2_copy.pdf"})
            self.assertEqual(alias["canonical"], "paperA_final_v2.pdf")
            groups = {g["canonical"]["path"]: g for g in rep["version_groups"]}
            self.assertIn("paperA_final_v2.pdf", groups)
            self.assertEqual(len(groups["paperA_final_v2.pdf"]["members"]), 3)
            self.assertIn("reading_list_v2.md", groups)
            # 提取链逐文件落档
            methods = {f["path"]: f["extraction_method"] for f in rep["files"]}
            self.assertEqual(methods["paperA_final_v2.pdf"], "pdf_textlayer")
            self.assertEqual(methods["lab_meeting_notes.docx"], "docx_xml")
            self.assertEqual(methods["scan_2021_notes.pdf"], "sidecar_ocr_text")
            self.assertEqual(methods["experiment_protocol.txt"], "plain_text")
            self.assertEqual(len(methods), 9)
            # 问答闭环
            chunk_text, canon_by_file = self._chunk_text_map(cdir / "input" / "case.json", cdir / "app" / "work")
            by_id = {a["question_id"]: a for a in result["answers"]}
            # Q1 采样率：final 96 kHz vs draft 48 kHz → conflicting 并列，旧版 superseded
            self.assertEqual(by_id["Q1"]["verdict"], "conflicting")
            q1_files = {c["path"] for c in by_id["Q1"]["citations"]}
            self.assertEqual(q1_files, {"paperA_final_v2.pdf", "paperA_draft_v1.pdf"})
            self.assertEqual(by_id["Q1"]["uncertainty"]["level"], "partially_supported")
            # 副本不重复计数：paperA_final_v2_copy.pdf 不作为独立证据来源出现
            self.assertNotIn("paperA_final_v2_copy.pdf", q1_files)
            # Q2 跨文件综合：周会纪要 DOCX + reading list v2 两文件共同支撑 canonical 结论
            self.assertEqual(by_id["Q2"]["verdict"], "answered")
            q2_files = {c["path"] for c in by_id["Q2"]["citations"]}
            self.assertGreaterEqual(len(q2_files), 2, f"Q2 应跨文件综合：{q2_files}")
            self.assertIn("lab_meeting_notes.docx", q2_files)
            self.assertIn("reading_list_v2.md", q2_files)
            self.assertEqual(by_id["Q2"]["uncertainty"]["level"], "supported")
            # 摘录逐字来自命中片段：每个 used claim 文本逐字出现在其 chunk 中
            self._assert_claims_verbatim(result, by_id["Q2"], chunk_text, canon_by_file)
            # Q3 扫描件侧车可答且 supported
            self.assertEqual(by_id["Q3"]["verdict"], "answered")
            self.assertEqual(by_id["Q3"]["citations"][0]["path"], "scan_2021_notes.pdf")
            self.assertEqual(by_id["Q3"]["uncertainty"]["level"], "supported")
            self._assert_claims_verbatim(result, by_id["Q3"], chunk_text, canon_by_file)
            # 回链字段：file/page/char_range/line 区间齐全
            cit = by_id["Q3"]["citations"][0]
            self.assertEqual(cit["page"], 1)
            self.assertEqual(len(cit["char_range"]), 2)
            self.assertGreaterEqual(cit["char_range"][1], cit["char_range"][0])
            self.assertLessEqual(cit["char_range"][1] - cit["char_range"][0], 500)
            # ops：只建议、输入只读、前后哈希一致
            ops = result["ops_log"]
            self.assertIn("merge_duplicate_candidates", [o["op"] for o in ops])
            self.assertIn("consolidate_versions", [o["op"] for o in ops])
            self.assertIn("rebuild_index", [o["op"] for o in ops])
            self.assertTrue(all(o.get("input_readonly") for o in ops))
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual(integrity["rechecked"], 9)
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
        finally:
            build_cases.cleanup_perms(Path(tmp.name))
            tmp.cleanup()

    def _chunk_text_map(self, case_path: Path, work_dir: Path | None = None) -> dict:
        """用 pipeline 自身逻辑独立重建 chunk 正文表（逐字校验的事实来源）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("qa046_pipeline", PIPELINE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        case = mod.load_case(str(case_path))
        rules = mod.load_rules()
        lib = mod.build_library(case, Path(case_path).resolve().parent,
                                Path(work_dir) if work_dir else Path("."))
        for rec in lib["recs"]:
            acq = mod.acquire_text(rec, rules["qa"])
            rec["_acq"] = acq
            rec["pages"] = acq["pages"]
        chunks, canon_by_file, _ledger = mod.build_kb(lib["recs"], rules)
        kept, _dropped = mod.dedupe_chunks(chunks, {f["file_id"]: f for f in lib["recs"]})
        return {c["chunk_id"]: c["text"] for c in kept}, canon_by_file

    def _assert_claims_verbatim(self, result: dict, answer: dict, chunk_text: dict, canon_by_file: dict):
        """摘录式回答只用命中片段：每个 claim 文本必须逐字出现在其 chunk 正文中；
        answer_text 去标记后 = used claim 文本的顺序拼接（不做改写式发挥）。"""
        for cl in answer["claims"]:
            ck_text = chunk_text[cl["citation"]["chunk_id"]]
            self.assertIn(cl["text"], ck_text,
                          f"claim 非命中片段逐字子串：{cl['text'][:50]}")
            row = next(c for c in result["evidence"]["kb"]["chunk_table"]
                       if c["chunk_id"] == cl["citation"]["chunk_id"])
            cr = cl["citation"]["char_range"]
            self.assertEqual(cr, row["char_range"])
            # 回链坐标自洽：claim 文本落在该文件规范文本的 char_range 切片内
            canon = canon_by_file[cl["citation"]["file_id"]]
            self.assertIn(cl["text"], canon[cr[0]:cr[1] + 2],
                          f"回链坐标不含 claim：{cl['text'][:50]}")
        if answer["citations"]:
            body = answer["answer_text"]
            claims = {c["claim_id"]: c["text"] for c in answer["claims"]}
            for c in answer["citations"]:
                self.assertIn(claims[c["claim_id"]], body)
                self.assertIn(f"[{c['claim_id']}]", body)

    # ---------------------------------------------------------------- case2 版本串味
    def test_realtest_case2_version_mix(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-046-VERSION-MIX"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            rep = result["library_report"]
            groups = rep["version_groups"]
            self.assertEqual(len(groups), 1)
            g = groups[0]
            self.assertEqual(g["canonical"], {"path": "methods_v2_renamed.pdf", "version": "v2"})
            self.assertEqual(len(g["members"]), 3)
            alias = rep["alias_groups"][0]
            self.assertEqual(set(alias["members"]), {"methods_latest.pdf", "methods_v1.pdf"})
            self.assertEqual(alias["canonical"], "methods_v1.pdf")  # latest 为诱饵名，降权
            by_id = {a["question_id"]: a for a in result["answers"]}
            # Q1 答案在 canonical 版本：引用指向 methods_v2_renamed.pdf，supported
            q1 = by_id["Q1"]
            self.assertEqual(q1["verdict"], "answered")
            self.assertEqual(q1["uncertainty"]["level"], "supported")
            self.assertEqual({c["path"] for c in q1["citations"]}, {"methods_v2_renamed.pdf"})
            # Q2 只有 v1 有：副本 latest 不重复计数，旧版标 stale_version
            q2 = by_id["Q2"]
            self.assertEqual(q2["verdict"], "answered")
            self.assertEqual(q2["uncertainty"]["level"], "partially_supported")
            self.assertEqual({c["path"] for c in q2["citations"]}, {"methods_v1.pdf"})
            self.assertEqual(q2["citations"][0]["evidence_origin"], "stale_version")
            kinds = {h["kind"] for h in q2["evidence_hints"]}
            self.assertIn("stale_version", kinds)
            # 全库断言里 methods_latest.pdf 不作为证据出现（去重到 v1）
            for a in result["answers"]:
                for c in a["citations"]:
                    self.assertNotEqual(c["path"], "methods_latest.pdf")
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
        finally:
            build_cases.cleanup_perms(Path(tmp.name))
            tmp.cleanup()

    # ---------------------------------------------------------------- case3 证据不足与间接证据
    def test_realtest_case3_evidence_gap(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-046-EVIDENCE-GAP"
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            self.assertEqual(result["status"]["overall"], "partial")
            by_id = {a["question_id"]: a for a in result["answers"]}
            # Q1 只有柱温的间接证据（回收率库内从未出现）→ partially_supported
            q1 = by_id["Q1"]
            self.assertEqual(q1["verdict"], "answered")
            self.assertEqual(q1["uncertainty"]["level"], "partially_supported")
            self.assertEqual(q1["citations"][0]["strength"], "indirect")
            self.assertIn("柱温", q1["answer_text"])
            self.assertNotIn("回收率", q1["answer_text"])  # 缺的部分不编造
            # Q2 库内完全无支撑 → insufficient_evidence，答案留空，不用常识补
            q2 = by_id["Q2"]
            self.assertEqual(q2["verdict"], "insufficient")
            self.assertEqual(q2["uncertainty"]["level"], "insufficient_evidence")
            self.assertEqual(q2["answer_text"], "")
            self.assertEqual(q2["missing"]["unmatched_keys"], ["转录组", "读长"])
            self.assertIn("未用常识补", q2["missing"]["reason"])
            # 缺证明细含 what_is_missing
            self.assertIn("what_is_missing", q2["missing"])
        finally:
            build_cases.cleanup_perms(Path(tmp.name))
            tmp.cleanup()

    # ---------------------------------------------------------------- case4 鲁棒
    def test_realtest_case4_robust(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root 下权限不足文件仍可读，R_NO_PERMISSION 分支不可测")
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-046-ROBUST"
            lib = cdir / "input" / "library"
            files_before = {}
            for p in sorted(lib.iterdir()):
                if p.is_file():
                    mode = stat.S_IMODE(p.stat().st_mode)
                    if mode:
                        files_before[p.name] = sha256_file(p)
            result = self.run_pipeline(cdir / "input" / "case.json",
                                       cdir / "app" / "output", cdir / "app" / "work")
            self.validate(cdir / "input" / "case.json", cdir / "app" / "output" / "result.json")
            # 四码分立，诚实跳过
            gap_codes = {g["path"]: g["code"] for g in result["library_report"]["gaps"]}
            self.assertEqual(gap_codes.get("broken_paper.pdf"), "R_FILE_CORRUPT")
            self.assertEqual(gap_codes.get("locked_scan.pdf"), "R_FILE_ENCRYPTED")
            self.assertEqual(gap_codes.get("no_text_scan.pdf"), "R_NO_TEXT_LAYER")
            self.assertEqual(gap_codes.get("restricted_notes.pdf"), "R_NO_PERMISSION")
            # 正常文件照常作答；坏文件相关问题如实降级
            by_id = {a["question_id"]: a for a in result["answers"]}
            self.assertEqual(by_id["Q1"]["verdict"], "answered")
            self.assertEqual(by_id["Q1"]["uncertainty"]["level"], "supported")
            for qid in ("Q2", "Q3"):
                self.assertEqual(by_id[qid]["verdict"], "insufficient")
                self.assertEqual(by_id[qid]["uncertainty"]["level"], "insufficient_evidence")
                self.assertEqual(by_id[qid]["answer_text"], "")
            # 缺证记录如实列出未检索文件（含加密/损坏件）
            not_searched = {f["path"] for f in by_id["Q2"]["missing"]["unreadable_files_not_searched"]}
            self.assertIn("locked_scan.pdf", not_searched)
            # 权限不足文件哈希列入 unchecked，不冒充已验证；其余文件哈希未变
            integrity = result["evidence"]["library"]["integrity"]
            self.assertTrue(integrity["hashes_unchanged"])
            self.assertEqual([u["path"] for u in integrity["unchecked"]], ["restricted_notes.pdf"])
            for name, before in files_before.items():
                self.assertEqual(sha256_file(lib / name), before, f"输入被改写：{name}")
            # ops 只建议
            self.assertTrue(all(o.get("input_readonly") for o in result["ops_log"]))
        finally:
            build_cases.cleanup_perms(Path(tmp.name))
            tmp.cleanup()

    # ---------------------------------------------------------------- realtest 幂等
    def test_realtest_case1_idempotent(self):
        tmp = self.build_realtest()
        try:
            cdir = Path(tmp.name) / "runtime" / "REAL-046-LIBRARY-BASE"
            out1, out2 = cdir / "app" / "out1", cdir / "app" / "out2"
            self.run_pipeline(cdir / "input" / "case.json", out1, cdir / "app" / "work")
            self.run_pipeline(cdir / "input" / "case.json", out2, cdir / "app" / "work")
            self.assertEqual(strip_generated((out1 / "result.json").read_text(encoding="utf-8")),
                             strip_generated((out2 / "result.json").read_text(encoding="utf-8")))
        finally:
            build_cases.cleanup_perms(Path(tmp.name))
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
