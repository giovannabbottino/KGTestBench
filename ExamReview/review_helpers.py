"""Shared validation and non-mutating calculations for both review notebooks."""
from pathlib import Path
from numbers import Real
import hashlib
import unicodedata
import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score


UNRESOLVED_PATTERN = r"\b(?:nao (?:determinad[oa]s?|encontrad[oa]s?|identificad[oa]s?)|not (?:determined|found|identified))\b"


def binary(value):
    if pd.isna(value) or (isinstance(value, str) and not value.strip()):
        return pd.NA
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, Real) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        mapping = {"true": True, "false": False, "1": True, "0": False,
                   "sim": True, "não": False, "nao": False}
        if value.strip().casefold() in mapping:
            return mapping[value.strip().casefold()]
    raise ValueError(f"Invalid binary rating: {value!r}")


def validate_columns(frame: pd.DataFrame, required: list[str]) -> None:
    """Reject an incomplete schema without changing the input."""
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")


def normalize_items(raw: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Return identified triple rows; only block metadata is forward-filled."""
    rows = raw.copy(deep=True)
    rows["excel_row"] = np.arange(len(rows)) + 2
    for column in key:
        rows[column] = rows[column].astype("string").str.strip().replace("", pd.NA)
    rows["Identifier"] = rows["Identifier"].ffill()
    if "Texto_Original" in rows:
        rows["Texto_Original"] = rows["Texto_Original"].replace(r"^\s*$", pd.NA, regex=True).ffill()
    return rows.loc[rows["tripla_original"].notna()].copy()


def validate_keys(rows: pd.DataFrame, key: list[str]) -> None:
    """Require nonempty, complete, unique item keys."""
    if rows.empty or rows[key].isna().any().any():
        raise ValueError("no valid items or incomplete key")
    duplicates = rows.duplicated(key, keep=False)
    if duplicates.any():
        examples = rows.loc[duplicates, key].head(5).to_dict("records")
        raise ValueError(f"duplicate keys: {examples}")


def parse_rating(value, line: int, column: str):
    """Add the original Excel location to a binary parsing error."""
    try:
        return binary(value)
    except ValueError as exc:
        raise ValueError(f"row {line}, {column}: {exc}") from exc


def normalize_ratings(rows: pd.DataFrame, criteria: list[str]) -> pd.DataFrame:
    """Return nullable boolean ratings without modifying source rows."""
    result = rows.copy(deep=True)
    for column in criteria:
        values = [parse_rating(value, line, column)
                  for line, value in zip(rows["excel_row"], rows[column])]
        result[column] = pd.array(values, dtype="boolean")
    return result


def prepare_review(raw: pd.DataFrame, key: list[str], criteria: list[str]) -> pd.DataFrame:
    """Pure preparation pipeline, independent of files and display."""
    validate_columns(raw, key + criteria)
    rows = normalize_items(raw, key)
    validate_keys(rows, key)
    return normalize_ratings(rows, criteria)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_audit(name: str, sheet: str, raw_count: int,
                rows: pd.DataFrame, digest: str) -> dict:
    return {"file": name, "sheet": sheet, "rows_read": raw_count,
            "excluded_without_triple": raw_count - len(rows), "items": len(rows),
            "groups": rows.Identifier.nunique(), "sha256": digest}


def load_review(path: Path, sheet: str, key: list[str], criteria: list[str]):
    """File I/O boundary; preparation itself has no external effects."""
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = pd.read_excel(path, sheet_name=sheet, engine="openpyxl", dtype=object,
                        keep_default_na=False)
    try:
        rows = prepare_review(raw, key, criteria)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    return rows, build_audit(path.name, sheet, len(raw), rows, file_sha256(path))


def align_reviews(left: pd.DataFrame, right: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Match items by key, retaining unmatched items for the audit."""
    return left.merge(right, on=key, how="outer", suffixes=("_a1", "_a2"),
                      validate="one_to_one", indicator=True)


def validate_context(frame: pd.DataFrame, key: list[str]) -> None:
    columns = ["Texto_Original_a1", "Texto_Original_a2"]
    if not all(column in frame for column in columns):
        return
    left, right = [frame[c].fillna("").astype(str).str.strip() for c in columns]
    if not left.equals(right):
        examples = frame.loc[left != right, key].head(5).to_dict("records")
        raise ValueError(f"Textual contexts differ between raters: {examples}")


def shared_items(paired: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Return validated shared items with a rating-independent stratum."""
    common = paired.loc[paired["_merge"] == "both"].copy()
    if common.empty:
        raise ValueError("The files have no shared triples.")
    validate_context(common, key)
    is_label = common.tripla_original.str.contains(
        r"(?:kg:rdf-schema#label|rdfs:label)(?:\s|$)", regex=True)
    return common.assign(item_type=np.where(is_label, "label", "relationship"))


def rating_columns(frame: pd.DataFrame, criterion: str):
    return frame[f"{criterion}_a1"], frame[f"{criterion}_a2"]


def complete_pairs(left: pd.Series, right: pd.Series):
    valid = left.notna() & right.notna()
    return left[valid].astype(int).to_numpy(), right[valid].astype(int).to_numpy()


def coverage_metrics(left: pd.Series, right: pd.Series) -> dict:
    n = int((left.notna() & right.notna()).sum())
    return {"n_shared_items": len(left), "n_pairs": n,
            "coverage_pct": 100 * n / len(left) if len(left) else np.nan,
            "missing_only_a1": int((left.isna() & right.notna()).sum()),
            "missing_only_a2": int((left.notna() & right.isna()).sum()),
            "missing_both": int((left.isna() & right.isna()).sum())}


def agreement_probabilities(a: np.ndarray, b: np.ndarray):
    if not len(a):
        return np.nan, np.nan, np.nan, np.nan
    po, pa, pb = float((a == b).mean()), float(a.mean()), float(b.mean())
    pe = pa * pb + (1 - pa) * (1 - pb)
    return po, pe, pa, pb


def calculate_kappa(a: np.ndarray, b: np.ndarray, po: float, pe: float):
    if not len(a):
        return np.nan, "no pairs"
    if np.isclose(pe, 1.0, rtol=0, atol=1e-12):
        return np.nan, "undefined: pe=1"
    kappa = float(cohen_kappa_score(a, b, labels=[0, 1]))
    assert np.isclose(kappa, (po - pe) / (1 - pe))
    return kappa, "calculated"


def scott_pi(a: np.ndarray, b: np.ndarray) -> float:
    """Chance-adjusted agreement using pooled binary category proportions."""
    a, b = np.asarray(a), np.asarray(b)
    if a.ndim != 1 or b.ndim != 1 or a.shape != b.shape:
        raise ValueError("Provide equally sized one-dimensional paired ratings")
    if not np.isin(a, [0, 1]).all() or not np.isin(b, [0, 1]).all():
        raise ValueError("Scott's pi requires complete binary ratings")
    if not len(a):
        return float('nan')
    pooled_positive = (float(a.mean()) + float(b.mean())) / 2
    expected = pooled_positive**2 + (1 - pooled_positive)**2
    if np.isclose(expected, 1.0, rtol=0, atol=1e-12):
        return float('nan')
    return (float((a == b).mean()) - expected) / (1 - expected)


def agreement_metrics(a: np.ndarray, b: np.ndarray) -> dict:
    po, pe, pa, pb = agreement_probabilities(a, b)
    kappa, status = calculate_kappa(a, b, po, pe)
    return {"agreements": int((a == b).sum()), "disagreements": int((a != b).sum()),
            "agreement_pct": 100 * po, "expected_pct": 100 * pe,
            "positive_a1_pct": 100 * pa, "positive_a2_pct": 100 * pb,
            "kappa_cohen": kappa, "status": status}


def metrics(frame: pd.DataFrame, criterion: str, label: str | None = None) -> dict:
    """Calculate one criterion using explicit inputs, without modifying them."""
    left, right = rating_columns(frame, criterion)
    return {"criterion": criterion if label is None else label,
            **coverage_metrics(left, right),
            **agreement_metrics(*complete_pairs(left, right))}


def summarize_criteria(frame: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([metrics(frame, criterion, label) for criterion, label in labels.items()])


def agreement_matrix(frame: pd.DataFrame, criterion: str) -> np.ndarray:
    """Return a 2x2 count matrix, including unobserved combinations."""
    a, b = complete_pairs(*rating_columns(frame, criterion))
    return np.bincount(2 * a + b, minlength=4).reshape(2, 2)


def percent(numerator: int, denominator: int) -> float:
    return 100 * numerator / denominator if denominator else np.nan


def category_agreement(frame: pd.DataFrame, criterion: str, title: str) -> dict:
    ff, ft, tf, tt = map(int, agreement_matrix(frame, criterion).ravel())
    disagreements = tf + ft
    return {
        "criterion": title, "n_items": len(frame), "n_pairs": tt + ff + disagreements,
        "both_correct": tt, "both_incorrect": ff,
        "a1_correct_a2_incorrect": tf, "a1_incorrect_a2_correct": ft,
        "any_correct": tt + disagreements, "any_incorrect": ff + disagreements,
        "positive_agreement_pct": percent(2 * tt, 2 * tt + disagreements),
        "negative_agreement_pct": percent(2 * ff, 2 * ff + disagreements),
        "both_correct_among_any_correct_pct": percent(tt, tt + disagreements),
        "both_incorrect_among_any_incorrect_pct": percent(ff, ff + disagreements),
        "a1_positive_confirmed_by_a2_pct": percent(tt, tt + tf),
        "a2_positive_confirmed_by_a1_pct": percent(tt, tt + ft),
    }


def category_table(frame: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([category_agreement(frame, c, title) for c, title in labels.items()])


def predicate_direction_counts(frame: pd.DataFrame, rater: str) -> dict:
    incorrect = frame[f"predicado_correto_{rater}"].eq(False).fillna(False)
    direction = frame.loc[incorrect, f"direcao_relacao_correta_{rater}"]
    wrong = int(direction.eq(False).sum())
    correct = int(direction.eq(True).sum())
    return {"rater": rater, "predicate_incorrect": len(direction),
            "direction_incorrect": wrong, "direction_correct": correct,
            "direction_missing": int(direction.isna().sum()),
            "direction_incorrect_among_observed_pct": percent(wrong, wrong + correct)}


def normalize_annotation(value) -> str:
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def unresolved_flags(frame: pd.DataFrame, rater: str) -> pd.Series:
    column = f"entidades_adequadas PREENCHER SE INCORRETO_{rater}"
    if column not in frame:
        raise ValueError(f"Missing correction field: {column}")
    return frame[column].map(normalize_annotation).str.contains(UNRESOLVED_PATTERN, regex=True)


def whole_triple_agreement(frame: pd.DataFrame, criteria: list[str]) -> pd.DataFrame:
    if not criteria or len(criteria) != len(set(criteria)):
        raise ValueError("Provide a nonempty list of distinct criteria")
    columns = [f"{criterion}_{rater}" for criterion in criteria for rater in ["a1", "a2"]]
    complete = frame[columns].notna().all(axis=1)
    result = frame.loc[complete].copy()
    for rater in ["a1", "a2"]:
        result[f"correct_{rater}"] = result[[f"{c}_{rater}" for c in criteria]].all(axis=1)
    result["both_correct"] = result.correct_a1 & result.correct_a2
    result["both_incorrect"] = ~result.correct_a1 & ~result.correct_a2
    result["a1_correct_only"] = result.correct_a1 & ~result.correct_a2
    result["a2_correct_only"] = ~result.correct_a1 & result.correct_a2
    result["same_error_criterion"] = pd.concat([
        result[f"{c}_a1"].eq(False) & result[f"{c}_a2"].eq(False) for c in criteria
    ], axis=1).any(axis=1)
    result["identical_ratings"] = pd.concat([
        result[f"{c}_a1"].eq(result[f"{c}_a2"]) for c in criteria
    ], axis=1).all(axis=1)
    return result


def joint_error_details(frame: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    """Describe shared and rater-specific errors on jointly rejected complete triples."""
    complete = whole_triple_agreement(frame, list(labels))
    result = complete.loc[complete.both_incorrect].copy()
    for criterion in labels:
        result[f"shared_error__{criterion}"] = (
            result[f"{criterion}_a1"].eq(False) & result[f"{criterion}_a2"].eq(False))
    for rater in ["a1", "a2"]:
        result[f"errors_{rater}"] = [
            tuple(label for criterion, label in labels.items() if not row[f"{criterion}_{rater}"])
            for _, row in result.iterrows()]
    result["shared_errors"] = [
        tuple(label for criterion, label in labels.items() if row[f"shared_error__{criterion}"])
        for _, row in result.iterrows()]
    result["shared_error_count"] = result.shared_errors.map(len)
    result["shared_error_pattern"] = result.shared_errors.map(
        lambda values: " + ".join(values) if values else "No shared criterion")
    result["same_error_set"] = result.errors_a1.eq(result.errors_a2)
    return result


def joint_error_summary(details: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    """One row per criterion, always using the same jointly rejected denominator."""
    records = []
    for criterion, label in labels.items():
        a1 = details[f"{criterion}_a1"].eq(False)
        a2 = details[f"{criterion}_a2"].eq(False)
        shared = int((a1 & a2).sum())
        records.append({"criterion": label, "jointly_rejected_triples": len(details),
                        "both_flag_error": shared, "both_flag_error_pct": percent(shared, len(details)),
                        "only_a1_flags_error": int((a1 & ~a2).sum()),
                        "only_a2_flags_error": int((~a1 & a2).sum()),
                        "neither_flags_error": int((~a1 & ~a2).sum())})
    return pd.DataFrame(records)


def joint_error_patterns(details: pd.DataFrame) -> pd.DataFrame:
    """Exclusive combinations of shared errors, not independent criterion totals."""
    table = details.groupby("shared_error_pattern").size().rename("triples").reset_index()
    table["pct_of_jointly_rejected"] = table.triples.map(lambda n: percent(int(n), len(details)))
    return table.sort_values(["triples", "shared_error_pattern"], ascending=[False, True]).reset_index(drop=True)
