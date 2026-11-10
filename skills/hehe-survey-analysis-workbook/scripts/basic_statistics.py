"""Configured descriptive statistics. CSV needs only Python; SAV needs pyreadstat."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def missing(value, codes=()):
    return value is None or (isinstance(value, float) and math.isnan(value)) or (
        isinstance(value, str) and not value.strip()
    ) or any(equal(value, code) for code in codes)


def equal(raw, expected):
    if raw is None or expected is None:
        return raw is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return math.isfinite(float(raw)) and float(raw) == expected
        except (TypeError, ValueError):
            return False
    return str(raw).strip() == str(expected).strip()


def number(value, where):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{where}: expected a finite number, got {value!r}") from exc
    if not math.isfinite(result):
        raise ConfigurationError(f"{where}: expected a finite number, got {value!r}")
    return result


def validate_rule(rule, columns, where):
    if not isinstance(rule, dict) or not rule:
        raise ConfigurationError(f"{where}: explicit filter required; use {{\"all\": true}} for everyone")
    if rule == {"all": True}:
        return
    if set(rule) in ({"all"}, {"any"}):
        key = next(iter(rule))
        if not isinstance(rule[key], list) or not rule[key]:
            raise ConfigurationError(f"{where}.{key}: nonempty rule list required")
        for i, child in enumerate(rule[key]):
            validate_rule(child, columns, f"{where}.{key}[{i}]")
        return
    field, op = rule.get("field"), rule.get("op")
    if field not in columns:
        raise ConfigurationError(f"{where}: unknown field {field!r}")
    if op not in {"eq", "ne", "in", "not_in", "gt", "ge", "lt", "le", "missing", "not_missing"}:
        raise ConfigurationError(f"{where}: unsupported operator {op!r}")
    if op in {"in", "not_in"} and (not isinstance(rule.get("values"), list) or not rule["values"]):
        raise ConfigurationError(f"{where}: nonempty values list required")
    if op in {"eq", "ne", "gt", "ge", "lt", "le"} and "value" not in rule:
        raise ConfigurationError(f"{where}: value is required")
    if op in {"gt", "ge", "lt", "le"}:
        number(rule["value"], where)


def matches(row, rule):
    if rule == {"all": True}:
        return True
    if "all" in rule:
        return all(matches(row, child) for child in rule["all"])
    if "any" in rule:
        return any(matches(row, child) for child in rule["any"])
    raw, op = row[rule["field"]], rule["op"]
    absent = missing(raw, rule.get("missing", ()))
    if op == "missing":
        return absent
    if op == "not_missing":
        return not absent
    if absent:
        return False  # Missing is never silently included by ne/not_in.
    if op in {"eq", "ne"}:
        value = equal(raw, rule["value"])
        return value if op == "eq" else not value
    if op in {"in", "not_in"}:
        value = any(equal(raw, item) for item in rule["values"])
        return value if op == "in" else not value
    left, right = number(raw, f"filter field {rule['field']}"), float(rule["value"])
    return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[op]


def load_data(path, config):
    labels = {}
    if path.suffix.lower() == ".csv":
        with path.open(encoding=config.get("encoding", "utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle, delimiter=config.get("delimiter", ","))
            columns = reader.fieldnames or []
            if len(columns) != len(set(columns)) or not columns:
                raise ConfigurationError("CSV must have unique, nonempty column names")
            rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in rows):
                raise ConfigurationError("CSV contains a row with a different number of fields")
    elif path.suffix.lower() == ".sav":
        try:
            import pyreadstat
        except ImportError as exc:
            raise RuntimeError("SAV input needs pyreadstat: python -m pip install pyreadstat") from exc
        frame, meta = pyreadstat.read_sav(str(path), apply_value_formats=False, user_missing=True)
        columns, rows = list(frame.columns), frame.to_dict("records")
        labels = meta.variable_value_labels
    else:
        raise ConfigurationError("Input must be CSV or SAV; export other formats as CSV for this helper")
    return rows, set(columns), labels


def validate_config(config, columns, sav_labels):
    if not config.get("sample_version"):
        raise ConfigurationError("sample_version is required")
    inclusion = config.get("inclusion", {})
    if not isinstance(inclusion, dict) or inclusion.get("confirmed") is not True:
        raise ConfigurationError("inclusion.confirmed must explicitly record the user's confirmed scope")
    validate_rule(inclusion.get("filter"), columns, "inclusion.filter")
    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise ConfigurationError("groups must be a list")
    ids = ["total"]
    for i, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group.get("id") or not group.get("label"):
            raise ConfigurationError(f"groups[{i}]: id and label required")
        ids.append(group["id"])
        validate_rule(group.get("filter"), columns, f"groups[{i}].filter")
    if len(ids) != len(set(ids)):
        raise ConfigurationError("Group ids must be unique; total is reserved")
    weight = config.get("weight")
    if weight is not None and (not isinstance(weight, dict) or weight.get("field") not in columns or not weight.get("version") or not isinstance(weight.get("zero_in_n"), bool)):
        raise ConfigurationError("weight requires an existing field, version and explicit zero_in_n boolean")
    questions = config.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ConfigurationError("questions must be a nonempty list")
    question_ids = []
    for q in questions:
        if not isinstance(q, dict):
            raise ConfigurationError("Every question must be an object")
        qid = q.get("id")
        if not isinstance(qid, str) or not qid or q.get("type") not in {"single", "multiple", "scale"}:
            raise ConfigurationError(f"Question {qid!r}: id and type single/multiple/scale required")
        question_ids.append(qid)
        if not isinstance(q.get("missing", []), list):
            raise ConfigurationError(f"{qid}.missing must be a list")
        validate_rule(q.get("eligible"), columns, f"{qid}.eligible")
        if q["type"] != "multiple" and q.get("field") not in columns:
            raise ConfigurationError(f"{qid}: unknown field {q.get('field')!r}")
        options = q.get("options")
        if not isinstance(options, list) or not options:
            raise ConfigurationError(f"{qid}: ordered options list required, including zero-frequency options")
        values = []
        for i, option in enumerate(options):
            if not isinstance(option, dict):
                raise ConfigurationError(f"{qid}.options[{i}]: option must be an object")
            if q["type"] == "multiple":
                if option.get("field") not in columns or "selected_value" not in option or "unselected_value" not in option:
                    raise ConfigurationError(f"{qid}.options[{i}]: field, selected_value and unselected_value required")
                if equal(option["selected_value"], option["unselected_value"]):
                    raise ConfigurationError(f"{qid}.options[{i}]: selected and unselected codes must differ")
                if missing(option["selected_value"], q.get("missing", ())) or missing(option["unselected_value"], q.get("missing", ())):
                    raise ConfigurationError(f"{qid}.options[{i}]: response codes cannot also be missing codes")
                key = option["field"]
            else:
                if "value" not in option or missing(option["value"]):
                    raise ConfigurationError(f"{qid}.options[{i}]: nonmissing value required")
                key = option["value"]
            if any(equal(key, previous) for previous in values):
                raise ConfigurationError(f"{qid}: duplicated option value/field {key!r}")
            values.append(key)
            if not option.get("label") and q["type"] != "multiple":
                option["label"] = sav_labels.get(q["field"], {}).get(option["value"])
            if not option.get("label"):
                raise ConfigurationError(f"{qid}.options[{i}]: label missing from config and SAV")
        if q["type"] == "multiple" and not isinstance(q.get("all_zero_valid"), bool):
            raise ConfigurationError(f"{qid}: explicit all_zero_valid boolean required")
        if q["type"] == "scale":
            scores = q.get("scores", {})
            if not isinstance(scores, dict):
                raise ConfigurationError(f"{qid}.scores must be an object")
            for option in options:
                key = str(option["value"])
                if key not in scores:
                    raise ConfigurationError(f"{qid}: scores must explicitly map code {key!r}; null excludes it from mean/TOP2")
                if scores[key] is not None:
                    number(scores[key], f"{qid}.scores[{key}]")
            if "top_values" in q and (not isinstance(q["top_values"], list) or len(q["top_values"]) != 2 or equal(*q["top_values"])):
                raise ConfigurationError(f"{qid}: top_values requires two distinct semantic top-category codes")
            if any(not any(equal(code, value) for value in values) for code in q.get("top_values", [])):
                raise ConfigurationError(f"{qid}: top_values must reference configured option values")
            if any(scores[str(option["value"])] is None for option in options if any(equal(option["value"], code) for code in q.get("top_values", []))):
                raise ConfigurationError(f"{qid}: top_values must have numeric scores")
    if len(question_ids) != len(set(question_ids)):
        raise ConfigurationError("Question ids must be unique")


def response(row, q):
    codes, options = q.get("missing", ()), q["options"]
    if q["type"] != "multiple":
        raw = row[q["field"]]
        if missing(raw, codes):
            return None, "missing"
        for i, option in enumerate(options):
            if equal(raw, option["value"]):
                return i, "valid"
        return None, "invalid"
    raw = [row[option["field"]] for option in options]
    absent = [missing(value, codes) for value in raw]
    if all(absent):
        return None, "missing"
    if any(absent):
        return None, "incomplete"
    selected = []
    for value, option in zip(raw, options):
        if equal(value, option["selected_value"]):
            selected.append(True)
        elif equal(value, option["unselected_value"]):
            selected.append(False)
        else:
            return None, "invalid"
    if not any(selected) and not q["all_zero_valid"]:
        return None, "zero_not_valid"
    return selected, "valid"


def calculate(rows, config):
    groups = [{"id": "total", "label": "总计", "filter": {"all": True}}, *config.get("groups", [])]
    weight = config.get("weight")
    included = []
    for line, row in enumerate(rows, 2):
        if not matches(row, config["inclusion"]["filter"]):
            continue
        w = number(row[weight["field"]], f"row {line}, weight {weight['field']}") if weight else 1.0
        if w < 0:
            raise ConfigurationError(f"row {line}: negative weight is not allowed")
        if weight and w == 0 and not weight["zero_in_n"]:
            continue
        included.append((row, w))
    result = []
    for q in config["questions"]:
        for group in groups:
            eligible = [(r, w) for r, w in included if matches(r, q["eligible"]) and matches(r, group["filter"])]
            valid, exclusions = [], {}
            for row, w in eligible:
                answer, state = response(row, q)
                if state == "valid":
                    valid.append((answer, w))
                else:
                    exclusions[state] = exclusions.get(state, 0) + 1
            base = math.fsum(w for _, w in valid)
            common = {"question_id": q["id"], "question_label": q.get("label", q["id"]), "group_id": group["id"], "group_label": group["label"], "actual_n": len(valid), "weight_sum": base if weight else None, "weight_version": weight["version"] if weight else "unweighted", "sample_version": config["sample_version"], "eligible_n": len(eligible), "missing_n": sum(exclusions.values()), "excluded_by_reason": exclusions}

            def emit(metric, option, numerator, denominator=base, actual_n=None, weight_sum=None):
                record = dict(common, result_id=f"{q['id']}::{group['id']}::{metric}::{len(result)+1}", metric=metric, option=option, numerator=numerator, effective_base=denominator, value=numerator / denominator if denominator else None)
                if actual_n is not None:
                    record["actual_n"] = actual_n
                    record["weight_sum"] = weight_sum if weight else None
                    record["missing_n"] = len(eligible) - actual_n
                    record["excluded_by_reason"] = dict(exclusions, unscored=len(valid) - actual_n)
                result.append(record)

            numerators = []
            for i, option in enumerate(q["options"]):
                numerator = math.fsum(w for answer, w in valid if (answer[i] if q["type"] == "multiple" else answer == i))
                numerators.append(numerator)
                emit("proportion", option["label"], numerator)
            emit("total", "总计", math.fsum(numerators))
            emit("sample_size", "样本量", len(valid), 1)
            if q["type"] == "scale":
                scored = [(q["scores"][str(q["options"][answer]["value"])], answer, w) for answer, w in valid if q["scores"][str(q["options"][answer]["value"]) ] is not None]
                score_base = math.fsum(w for _, _, w in scored)
                emit("mean", "均值", math.fsum(float(score) * w for score, _, w in scored), score_base, len(scored), score_base)
                if "top_values" in q:
                    numerator = math.fsum(w for _, answer, w in scored if any(equal(q["options"][answer]["value"], code) for code in q["top_values"]))
                    emit("top2", "TOP2", numerator, score_base, len(scored), score_base)
    return result, len(included)


def run(source, config_path, output, force=False):
    source, config_path, output = [Path(p).resolve() for p in (source, config_path, output)]
    if output in {source, config_path}:
        raise ConfigurationError("Output must not overwrite data or configuration")
    if output.exists() and not force:
        raise FileExistsError(f"Output exists; choose a new filename or use --force: {output}")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ConfigurationError("Configuration must be a JSON object")
    rows, columns, labels = load_data(source, config)
    validate_config(config, columns, labels)
    records, calculation_n = calculate(rows, config)
    user_included_n = sum(matches(row, config["inclusion"]["filter"]) for row in rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w" if force else "x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    return {"output": str(output), "source": str(source), "config": str(config_path), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(), "source_rows": len(rows), "included_rows": user_included_n, "user_included_rows": user_included_n, "calculation_rows": calculation_n, "zero_weight_excluded_from_calculation": user_included_n - calculation_n, "results": len(records)}


def main():
    parser = argparse.ArgumentParser(description="按明确配置计算基础统计，输出后台JSONL；不修改原数据。")
    parser.add_argument("source")
    parser.add_argument("config")
    parser.add_argument("output")
    parser.add_argument("--force", action="store_true", help="Explicitly replace an existing result file")
    args = parser.parse_args()
    try:
        metadata = run(args.source, args.config, args.output, args.force)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"统计失败：{exc}\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
