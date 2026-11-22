#!/usr/bin/env python3
"""Synthetic regression tests for 060; no formal case data or prepared answers."""
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


class PipelineTests(unittest.TestCase):
    def run_case(self, case: dict) -> tuple[dict, Path, tempfile.TemporaryDirectory]:
        temporary = tempfile.TemporaryDirectory(prefix="figure060-test-")
        root = Path(temporary.name)
        case_path = root / "case.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        out = root / "out"
        proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out)],
                              capture_output=True, text=True, timeout=300,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result_path = out / "result.json"
        self.assertTrue(result_path.exists(), proc.stderr + proc.stdout)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        generic = subprocess.run([sys.executable, "-B", str(HERE / "validate.py"), "--case", str(case_path), "--result", str(result_path)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)
        specific = subprocess.run([sys.executable, "-B", str(HERE / "validate_figures.py"), "--result", str(result_path)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(specific.returncode, 0, specific.stdout + specific.stderr)
        return result, out, temporary

    def assert_complete(self, result: dict, out: Path, expected_figures: int):
        self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"], ensure_ascii=False))
        self.assertEqual(result["figures"]["n_delivered"], expected_figures)
        for figure in result["figures"]["figures"]:
            self.assertTrue(figure["qa"]["all_pass"], json.dumps(figure["qa"], ensure_ascii=False))
            self.assertTrue(figure["reproduce"]["end_to_end_sha_match"])
            for extension in ("pdf", "png", "svg"):
                self.assertTrue((out / figure["files"][extension]["file"]).is_file())

    def test_existing_min_example(self):
        case = json.loads((SKILL_DIR / "examples/min_example/input/case.json").read_text(encoding="utf-8"))
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 2)
            scatter = result["figures"]["figures"][1]
            self.assertAlmostEqual(scatter["analysis"]["fit"]["r"], 0.778252615001042, places=5)
        finally:
            temporary.cleanup()

    def test_all_chart_families_and_dynamic_contract(self):
        rows = []
        for i in range(1, 13):
            rows.append({"category": "A" if i % 2 else "B", "value": i * 1.5, "time": f"2026-01-{i:02d}",
                         "group": "G1" if i % 2 else "G2", "component": "C1" if i % 3 else "C2",
                         "amount": float(i), "v1": float(i), "v2": float(14 - i), "v3": float(i * i)})
        case = {
            "case_id": "EXAMPLE-060-ALL-CHARTS",
            "output_contract": {"deliverable": "图件", "质量与复现证据": "证据", "异常说明": "状态"},
            "data_dictionary": {"time": {"type": "temporal"}},
            "dataset": {"columns": list(rows[0]), "rows": rows},
            "figure_requests": [
                {"figure_id": "CMP", "purpose": "comparison", "x": "category", "y": "value"},
                {"figure_id": "TREND", "purpose": "trend", "x": "time", "y": "value", "group": "group"},
                {"figure_id": "DIST", "purpose": "distribution", "x": "value", "group": "group"},
                {"figure_id": "STACK", "purpose": "composition", "x": "category", "y": "amount", "group": "component"},
                {"figure_id": "COUNT", "purpose": "composition", "chart_hint": "category_count", "x": "component"},
                {"figure_id": "HEAT", "purpose": "correlation", "chart_hint": "heatmap", "variables": ["v1", "v2", "v3"]},
            ],
        }
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 6)
            self.assertIn("deliverable", result)
            self.assertIn("质量与复现证据", result)
            self.assertIn("异常说明", result)
        finally:
            temporary.cleanup()

    def test_nonfinite_sentinel_duplicates_and_pdf_request(self):
        rows = [
            {"group": "A", "x": 1, "y": 2}, {"group": "A", "x": 2, "y": "NaN"},
            {"group": "A", "x": 3, "y": 6}, {"group": "A", "x": 4, "y": -999},
            {"group": "A", "x": 5, "y": 10}, {"group": "A", "x": 5, "y": 10},
        ]
        case = {
            "case_id": "EXAMPLE-060-DIRTY", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
            "data_dictionary": {"y": {"type": "continuous", "missing_values": [-999]}},
            "dataset": {"columns": ["group", "x", "y"], "rows": rows},
            "figure_requests": [{"figure_id": "DIRTY", "purpose": "correlation", "x": "x", "y": "y", "format": "pdf"}],
        }
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            self.assertEqual(figure["analysis"]["n_used"], 4)
            quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["n_duplicate_rows"], 1)
            self.assertEqual(quality["missing_by_column"]["y"], 2)
            self.assertTrue((out / "figure_1.pdf").is_file())
        finally:
            temporary.cleanup()

    def test_constant_scatter_has_undefined_fit_not_zero(self):
        rows = [{"x": 1, "y": float(i)} for i in range(1, 6)]
        case = {"case_id": "EXAMPLE-060-CONSTANT", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["x", "y"], "rows": rows},
                "figure_requests": [{"figure_id": "CONST", "purpose": "correlation", "x": "x", "y": "y"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            fit = result["figures"]["figures"][0]["analysis"]["fit"]
            self.assertIsNone(fit["r"])
            self.assertIsNone(fit["slope"])
        finally:
            temporary.cleanup()

    def test_technical_replicates_use_independent_units(self):
        rows = []
        for category, centres in (("A", (1.0, 3.0)), ("B", (2.0, 4.0))):
            for unit, centre in zip(("U1", "U2"), centres):
                rows.extend([{"category": category, "unit": unit, "value": centre - 0.2},
                             {"category": category, "unit": unit, "value": centre + 0.2}])
        case = {"case_id": "EXAMPLE-060-UNITS", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["category", "unit", "value"], "rows": rows},
                "figure_requests": [{"figure_id": "UNITS", "purpose": "comparison", "chart_hint": "bar", "x": "category", "y": "value",
                                     "error_bar": "sd", "unit_id": "unit"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            self.assertEqual(figure["analysis"]["n_used"], 8)
            self.assertEqual([item["n"] for item in figure["analysis"]["series"]], [2, 2])
            self.assertTrue(all(item["independent_unit_summary"] for item in figure["analysis"]["series"]))
        finally:
            temporary.cleanup()

    def test_all_invalid_returns_partial_result(self):
        case = {"case_id": "EXAMPLE-060-INVALID", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["x", "y"], "rows": [{"x": "NaN", "y": "Inf"}, {"x": "", "y": None}]},
                "figure_requests": [{"figure_id": "BAD", "purpose": "correlation", "x": "x", "y": "y"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assertEqual(result["status"]["overall"], "partial")
            self.assertTrue(result["status"]["unmet"])
            self.assertTrue((out / "result.json").is_file())
        finally:
            temporary.cleanup()

    def test_venue_alias_and_category_layout_growth(self):
        rows = []
        for index in range(1, 19):  # 18 个类别触发横向布局 + 超出双栏模板高度的自动增高
            rows.append({"age": float(index * 30), "value": float(index) * 1.1})
        case = {"case_id": "EXAMPLE-060-LAYOUT", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["age", "value"], "rows": rows},
                "figure_requests": [{"figure_id": "LAY", "purpose": "comparison", "chart_hint": "bar",
                                     "x": "age", "y": "value", "error_bar": "sd",
                                     "venue": "default_double_column"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            self.assertEqual(figure["venue"]["width_mm"], 183.0)  # 别名归一到 double_column
            self.assertTrue(figure["venue"]["height_mm"] > 62.0)  # 按类别数自动增高
            self.assertTrue(figure.get("layout_adjustment"))
        finally:
            temporary.cleanup()

    def test_workdir_relative_input_fallback(self):
        temporary = tempfile.TemporaryDirectory(prefix="figure060-work-")
        try:
            root = Path(temporary.name)
            (root / "input").mkdir()
            (root / "app" / "work").mkdir(parents=True)
            rows = [{"group": "A", "value": 1.0}, {"group": "B", "value": 2.0},
                    {"group": "A", "value": 1.5}, {"group": "B", "value": 2.5}]
            import csv as _csv
            with (root / "input" / "data.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = _csv.DictWriter(handle, fieldnames=["group", "value"])
                writer.writeheader()
                writer.writerows(rows)
            case = {"case_id": "EXAMPLE-060-WORKDIR", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                    "dataset": {"path": "data.csv"},  # 相对原始 input/ 目录
                    "figure_requests": [{"figure_id": "WORK", "purpose": "comparison", "x": "group", "y": "value",
                                         "error_bar": "sd"}]}
            case_path = root / "app" / "work" / "case.json"
            case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            out = root / "app" / "output"
            proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out)],
                                  capture_output=True, text=True, timeout=300,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"]["overall"], "complete", json.dumps(result["status"], ensure_ascii=False))
        finally:
            temporary.cleanup()

    def test_unsupported_format_gap_not_partial(self):
        rows = [{"x": 1, "y": 2.1}, {"x": 2, "y": 3.4}, {"x": 3, "y": 4.2}, {"x": 4, "y": 5.0}]
        case = {"case_id": "EXAMPLE-060-EMF", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["x", "y"], "rows": rows},
                "figure_requests": [{"figure_id": "EMF", "purpose": "correlation", "x": "x", "y": "y",
                                     "formats": ["emf", "pdf"]}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            self.assertTrue(figure["format_gaps"])  # emf 记为范围外缺口
            self.assertIn("emf", str(result["状态说明"]["gaps"]))
            self.assertTrue((out / "figure_1.pdf").is_file())
        finally:
            temporary.cleanup()

    def test_heatmap_labels_rotate_and_use_dictionary_short_names(self):
        rows = []
        for index in range(1, 26):
            rows.append({"cement_component_one_kg_m3": float(index),
                         "blast_furnace_slag_component_two_kg_m3": float(26 - index),
                         "fly_ash_component_three_kg_m3": float(index % 7) + 0.5,
                         "water_component_four_kg_m3": float(index % 5) + 1.0,
                         "superplasticizer_component_five_kg_m3": float(index % 3) + 0.25})
        case = {"case_id": "EXAMPLE-060-HEATLABEL", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "data_dictionary": {
                    "cement_component_one_kg_m3": {"label": "Cement (component 1)(kg in a m^3 mixture)", "type": "continuous"},
                    "blast_furnace_slag_component_two_kg_m3": {"label": "Blast Furnace Slag (component 2)(kg in a m^3 mixture)", "type": "continuous"},
                    "fly_ash_component_three_kg_m3": {"label": "Fly Ash (component 3)(kg in a m^3 mixture)", "type": "continuous"},
                    "water_component_four_kg_m3": {"label": "Water (component 4)(kg in a m^3 mixture)", "type": "continuous"},
                    "superplasticizer_component_five_kg_m3": {"label": "Superplasticizer (component 5)(kg in a m^3 mixture)", "type": "continuous"},
                },
                "dataset": {"columns": list(rows[0]), "rows": rows},
                "figure_requests": [{"figure_id": "HEAT5", "purpose": "correlation", "chart_hint": "heatmap",
                                     "variables": list(rows[0]), "venue": "default_single_column"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            heatmap = figure["analysis"]["heatmap"]
            self.assertIn(heatmap["x_label_angle"], (45, 90))
            self.assertLessEqual(heatmap["axis_label_size_pt"], figure["analysis"]["base_size"])
            svg = (out / "figure_1.svg").read_text(encoding="utf-8")
            if heatmap["x_label_angle"] == 90:
                self.assertIn("rotate", svg)  # 旋转真实写入 SVG，防完整主题擦除回归
            self.assertIn("Cement", svg)  # 数据字典短名进入轴标签
            self.assertNotIn("(kg in a m^3 mixture)", svg)  # 括号单位从轴标签剥离
        finally:
            temporary.cleanup()

    def test_significance_marks_rendered_verbatim(self):
        rows = []
        for group, base in (("野生型", 8.0), ("突变体A", 9.5), ("突变体B", 10.8)):
            for repeat in range(12):
                rows.append({"基因型": group, "表达量": round(base + ((repeat * 37) % 11 - 5) * 0.3, 3)})
        case = {"case_id": "EXAMPLE-060-SIG", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["基因型", "表达量"], "rows": rows},
                "figure_requests": [{"figure_id": "SIG", "purpose": "comparison", "chart_hint": "bar",
                                     "x": "基因型", "y": "表达量", "error_bar": "se",
                                     "title": "三种基因型表达量",
                                     "significance": {"method": "one-way ANOVA + Tukey HSD", "items": [
                                         {"groups": ["野生型", "突变体A"], "label": "*"},
                                         {"groups": ["突变体A", "突变体B"], "label": "**"},
                                         {"groups": ["野生型", "突变体B"], "label": "***"}]}}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            rendered = figure["analysis"]["significance_rendered"]
            self.assertEqual(rendered["n_pair_marks"], 3)
            self.assertEqual(rendered["layout"], "brackets above groups")
            self.assertIn("透传", figure["caption"]["standalone_text"])
            svg = (out / "figure_1.svg").read_text(encoding="utf-8")
            for mark in ("*", "**", "***"):
                self.assertIn(mark, svg)
        finally:
            temporary.cleanup()

    def test_no_r_fallback_renders_real_png(self):
        temporary = tempfile.TemporaryDirectory(prefix="figure060-nor-")
        try:
            root = Path(temporary.name)
            fake_bin = root / "fakebin"
            fake_bin.mkdir()
            fake_rscript = fake_bin / "Rscript"
            fake_rscript.write_text("#!/bin/sh\necho 'Error: R unavailable' >&2\nexit 1\n")
            fake_rscript.chmod(0o755)
            rows = [{"category": "A" if i % 2 else "B", "value": 1.0 + i * 0.3} for i in range(10)]
            case = {"case_id": "EXAMPLE-060-NOR", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                    "dataset": {"columns": ["category", "value"], "rows": rows},
                    "figure_requests": [{"figure_id": "NOR", "purpose": "comparison", "chart_hint": "bar",
                                         "x": "category", "y": "value", "error_bar": "sd"}]}
            case_path = root / "case.json"
            case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            out = root / "out"
            proc = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out)],
                                  capture_output=True, text=True, timeout=300,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PATH": f"{fake_bin}:{os.environ['PATH']}"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"]["overall"], "partial")  # 无 R 必须诚实 partial
            unmet_items = " ".join(u["item"] for u in result["status"]["unmet"])
            self.assertIn("R 绘图依赖", unmet_items)
            figure = result["figures"]["figures"][0]
            fallback = figure.get("fallback") or {}
            self.assertEqual(fallback.get("png"), "figure_1.png")  # 降级仍出真实 PNG
            self.assertGreaterEqual(int(fallback.get("dpi") or 0), 300)
            self.assertTrue((out / "figure_1.png").is_file())
            self.assertFalse((out / "figure_1.pdf").is_file())  # 不伪造 PDF
        finally:
            temporary.cleanup()

    def test_log_axis_semantics(self):
        rows = []
        for group in ("G1", "G2", "G3"):
            for repeat in range(15):
                value = 10 ** (0.7 + ((repeat * 13 + ord(group[-1])) % 30) / 10)  # 5~4000，约 2.9 个数量级
                rows.append({"group": group, "value": round(value, 2)})
        case = {"case_id": "EXAMPLE-060-LOG", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                "dataset": {"columns": ["group", "value"], "rows": rows},
                "figure_requests": [{"figure_id": "LOG", "purpose": "comparison", "chart_hint": "bar",
                                     "x": "group", "y": "value", "error_bar": "sd", "log_y": True,
                                     "title": "对数轴比较"}]}
        result, out, temporary = self.run_case(case)
        try:
            self.assert_complete(result, out, 1)
            figure = result["figures"]["figures"][0]
            y_scale = figure["analysis"]["y_scale"]
            self.assertTrue(y_scale["applied"])
            self.assertGreaterEqual(y_scale["decades"], 2.0)
            self.assertIn("log10", figure["caption"]["standalone_text"])
            # log 轴上均值柱自动改为均值标记（柱长在 log 轴无意义）
            self.assertEqual(figure["analysis"]["actual_chart_type"], "points_with_mean")
        finally:
            temporary.cleanup()

    def test_workorder_suggester_end_to_end(self):
        temporary = tempfile.TemporaryDirectory(prefix="figure060-sug-")
        try:
            root = Path(temporary.name)
            input_dir = root / "input" / "iris"
            input_dir.mkdir(parents=True)
            work_dir = root / "app" / "work"
            work_dir.mkdir(parents=True)
            rows = []
            for species in ("setosa", "versicolor", "virginica"):
                for i in range(10):
                    rows.append({"species": species, "petal_length_cm": round(1.4 + i * 0.2 + len(species), 2),
                                 "petal_width_cm": round(0.2 + i * 0.1 + len(species) * 0.3, 2),
                                 "sepal_length_cm": round(5.0 + i * 0.1, 2)})
            import csv as _csv
            with (input_dir / "iris.sqlite.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = _csv.DictWriter(handle, fieldnames=["species", "petal_length_cm", "petal_width_cm", "sepal_length_cm"])
                writer.writeheader()
                writer.writerows(rows)
            case = {"case_id": "SUGGEST-E2E", "task_instruction": (
                "三个物种花瓣长度分布差异怎么呈现？四个测量维度的相关结构如何？"
                "花瓣长与花瓣宽的关联按物种分色展示并给出拟合。"),
                    "data_dictionary": {"species": {"label": "物种", "type": "categorical"},
                                        "petal_length_cm": {"label": "花瓣长度", "type": "continuous", "unit": "cm"},
                                        "petal_width_cm": {"label": "花瓣宽度", "type": "continuous", "unit": "cm"},
                                        "sepal_length_cm": {"label": "萼片长度", "type": "continuous", "unit": "cm"}},
                    "dataset": {"path": "iris.sqlite.csv"},  # 平铺文件名：数据在 input/iris/ 子目录
                    "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"}}
            (work_dir / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            suggester = HERE / "suggest_workorder.py"
            proc = subprocess.run([sys.executable, "-B", str(suggester), "--case", str(work_dir / "case.json"),
                                   "--out", str(work_dir / "work_case.json")],
                                  capture_output=True, text=True, timeout=60,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            work = json.loads((work_dir / "work_case.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(work["figure_requests"]), 3)  # 比较/相关/分布/拟合 多意图
            purposes = {f["purpose"] for f in work["figure_requests"]}
            self.assertIn("comparison", purposes)
            self.assertIn("correlation", purposes)
            fit_figures = [f for f in work["figure_requests"] if f.get("fit_group")]
            self.assertTrue(fit_figures)  # 拟合意图 → 散点分组拟合
            out = root / "app" / "output"
            final = subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(work_dir / "work_case.json"),
                                    "--out", str(out)], capture_output=True, text=True, timeout=300,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"]["overall"], "complete",
                             json.dumps(result["status"], ensure_ascii=False))
        finally:
            temporary.cleanup()

    def test_environment_resources_yaml_recorded(self):
        temporary = tempfile.TemporaryDirectory(prefix="figure060-env-")
        try:
            root = Path(temporary.name)
            env_dir = root / "environment"
            env_dir.mkdir()
            (env_dir / "resources.yaml").write_text(
                "tools:\n  - name: scientific-python\n  - name: SQLite\nfonts:\n  - family: Arial\n",
                encoding="utf-8")
            rows = [{"x": 1, "y": 2.0}, {"x": 2, "y": 3.0}, {"x": 3, "y": 4.0}]
            case = {"case_id": "EXAMPLE-060-ENV", "output_contract": {"figures": "图", "data_to_visual_map": "映射", "evidence_map": "证据", "状态说明": "状态"},
                    "dataset": {"columns": ["x", "y"], "rows": rows},
                    "figure_requests": [{"figure_id": "ENV", "purpose": "correlation", "x": "x", "y": "y"}]}
            case_path = root / "case.json"
            case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            out = root / "out"
            subprocess.run([sys.executable, "-B", str(PIPELINE), "--case", str(case_path), "--out", str(out)],
                           capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            deps = json.loads((out / "dependencies.json").read_text(encoding="utf-8"))
            env_decl = deps.get("environment_declaration") or {}
            self.assertIn("scientific-python", env_decl.get("declared_tools", []))
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertIn("environment_declaration", result["evidence_map"])
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
