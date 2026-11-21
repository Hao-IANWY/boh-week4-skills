#!/usr/bin/env python3
"""共享运行时与校验器的回归测试；只使用标准库。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import kit
import validate


class SharedRuntimeTests(unittest.TestCase):
    def test_kit_self_check(self):
        self.assertEqual(kit.self_check(), 0)

    def test_complete_is_downgraded_when_unmet_was_recorded_first(self):
        case = {"case_id": "T-1", "output_contract": {"deliverable": "结果"}}
        res = kit.Result(case, skill="test")
        res.set("deliverable", {"value": 1})
        res.unmet("外部核验", "未提供权威来源")
        res.status["overall"] = "complete"
        payload = res.build()
        self.assertEqual(payload["status"]["overall"], "partial")

    def test_single_cjk_keyword_does_not_steal_contract(self):
        payload = {"figures": [{"id": "F1"}], "tables": [{"id": "T1"}]}
        self.assertIsNone(kit._heuristic_match("图表映射", payload))
        self.assertEqual(kit._heuristic_match("图", payload), "figures")


class TableInputTests(unittest.TestCase):
    def test_csv_and_sqlite_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.csv").write_text("id,value\nA,1.5\nB,2.5\n", encoding="utf-8")
            table = kit.read_table_snapshot("x.csv", root)
            self.assertEqual(table["columns"], ["id", "value"])
            self.assertEqual(len(table["rows"]), 2)
            db = root / "x.db"
            con = kit.sqlite3.connect(db)
            con.execute("CREATE TABLE samples (id TEXT, value REAL)")
            con.execute("INSERT INTO samples VALUES ('A', 1.5)")
            con.commit()
            con.close()
            table = kit.read_table_snapshot("x.db", root)
            self.assertEqual(table["name"], "samples")
            self.assertEqual(table["rows"], [{"id": "A", "value": 1.5}])
            self.assertEqual(kit.sqlite_table_names(db), ["samples"])
            inline = kit.read_table_snapshot({"columns": [{"name": "id", "type": "key"}, {"name": "value"}],
                                               "rows": [{"id": "A", "value": 1.5}]})
            self.assertEqual(inline["columns"], ["id", "value"])
            self.assertEqual(inline["rows"], [{"id": "A", "value": 1.5}])


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, case, result):
        case_path = self.root / "case.json"
        result_path = self.root / "result.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return case_path, result_path

    @staticmethod
    def _result(**overrides):
        result = {
            "case_id": "T-WRAP",
            "deliverable": {"value": 1},
            "evidence_map": {"source": "fixture"},
            "evidence": {"source": "fixture"},
            "status": {"overall": "complete", "completed": ["run"], "unmet": []},
            "uncertainty": [],
        }
        result.update(overrides)
        return result

    def test_wrapped_case_and_output_contract_aliases(self):
        case = {"case": {"case_id": "T-WRAP", "outputContract": ["deliverable", "evidence_map"]}}
        paths = self._write(case, self._result())
        report = validate.check(*paths)
        self.assertFalse(report["fails"], report["fails"])

    def test_complete_with_unmet_fails(self):
        case = {"case_id": "T-WRAP", "output_contract": {"deliverable": "结果"}}
        result = self._result(status={"overall": "complete", "completed": [], "unmet": [{"item": "x"}]})
        report = validate.check(*self._write(case, result))
        self.assertTrue(any(x.startswith("status.consistency") for x in report["fails"]))

    def test_complete_with_not_produced_fails(self):
        case = {"case_id": "T-WRAP", "output_contract": {"deliverable": "结果"}}
        result = self._result(deliverable={"status": "not_produced", "reason": "missing"})
        report = validate.check(*self._write(case, result))
        self.assertTrue(any(x.startswith("status.consistency") for x in report["fails"]))

    def test_empty_result_allows_empty_contract_with_explanation(self):
        case = {"case_id": "T-WRAP", "output_contract": {"deliverable": "结果", "evidence_map": "证据"}}
        result = self._result(
            deliverable=[],
            status={"overall": "empty", "completed": ["load"], "unmet": [{"item": "deliverable", "reason": "无材料"}]},
        )
        report = validate.check(*self._write(case, result))
        self.assertFalse(report["fails"], report["fails"])

    def test_empty_result_still_requires_unmet(self):
        case = {"case_id": "T-WRAP", "output_contract": {"deliverable": "结果", "evidence_map": "证据"}}
        result = self._result(
            deliverable=[],
            status={"overall": "empty", "completed": ["load"], "unmet": []},
        )
        report = validate.check(*self._write(case, result))
        self.assertTrue(any(x.startswith("status.consistency") for x in report["fails"]))

    def test_reports_do_not_leak_between_calls(self):
        case = {"case_id": "T-WRAP", "output_contract": {"deliverable": "结果"}}
        bad = self._result(status={"overall": "complete", "completed": [], "unmet": [{"item": "x"}]})
        paths = self._write(case, bad)
        self.assertTrue(validate.check(*paths)["fails"])
        paths = self._write(case, self._result())
        self.assertFalse(validate.check(*paths)["fails"])


if __name__ == "__main__":
    unittest.main()
