#!/usr/bin/env python3
"""Export one CSV row per original relationship triple with disagreements."""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

if __package__:
    from . import review_helpers as review
else:
    import review_helpers as review

ROOT = Path(__file__).resolve().parents[1]
SHEET = 'resultado_template_processado'
KEY = ['Identifier', 'tripla_original']
CRITERIA = {
    'entidade_correta': 'Entity',
    'predicado_correto': 'Predicate',
    'direcao_relacao_correta': 'Direction',
    'fidelidade_semantica': 'Semantic fidelity',
}
CORRECTION_FIELDS = {
    'entidade_correta': 'entidades_adequadas PREENCHER SE INCORRETO',
    'predicado_correto': 'predicados_adequados PREENCHER SE INCORRETO',
    'direcao_relacao_correta': 'direcao_relacoes_adequada PREENCHER SE INCORRETO',
}
BASE_COLUMNS = ['identifier', 'original_text', 'original_triple']
BLOCK_COLUMNS = ['disagreement_type', 'a1_decision', 'a2_decision', 'decision']


def text(value) -> str:
    """Preserve source wording; represent missing values as empty cells."""
    return '' if pd.isna(value) or not str(value).strip() else str(value)


def ratings(row: pd.Series, rater: str) -> dict[str, bool | None]:
    """Read substantive judgments without treating missing as False."""
    result = {}
    for criterion in CRITERIA:
        value = review.binary(row[f'{criterion}_{rater}'])
        result[criterion] = None if pd.isna(value) else bool(value)
    return result


def correction_comparisons(row: pd.Series) -> list[dict]:
    """Compare suggestions where at least one rater rejected the criterion."""
    a, b = ratings(row, 'a1'), ratings(row, 'a2')
    comparisons = []
    for criterion, column in CORRECTION_FIELDS.items():
        if a[criterion] is not False and b[criterion] is not False:
            continue
        left, right = (text(row.get(f'{column}_{rater}')) for rater in ['a1', 'a2'])
        normalized = [' '.join(unicodedata.normalize('NFC', value).split()) for value in (left, right)]
        if not left and not right:
            status = 'both_missing'
        elif not left:
            status = 'only_a2'
        elif not right:
            status = 'only_a1'
        else:
            status = 'same_text' if normalized[0] == normalized[1] else 'different'
        comparisons.append({'criterion': criterion, 'a1': left, 'a2': right, 'status': status})
    return comparisons


def row_disagreements(row: pd.Series) -> list[tuple[str, str, str]]:
    """Export TRUE, original suggestions, or remove for uncorrected entity errors."""
    a, b = ratings(row, 'a1'), ratings(row, 'a2')
    complete = all(value is not None for value in [*a.values(), *b.values()])
    selected = {criterion for criterion in CRITERIA if complete and a[criterion] != b[criterion]}
    if any(value is False for value in a.values()) and any(value is False for value in b.values()):
        for comparison in correction_comparisons(row):
            if comparison['status'] == 'different':
                selected.add(comparison['criterion'])

    column = CORRECTION_FIELDS['entidade_correta']
    suggestions = [text(row.get(f'{column}_{rater}')) for rater in ['a1', 'a2']]
    unresolved = [bool(re.search(review.UNRESOLVED_PATTERN, review.normalize_annotation(value)))
                  for value in suggestions]
    if unresolved[0] != unresolved[1]:
        selected.add('entidade_correta')

    disagreements = []
    for criterion, label in CRITERIA.items():
        if criterion not in selected:
            continue
        decisions = []
        for rater, own, other in [('a1', a, b), ('a2', b, a)]:
            column = CORRECTION_FIELDS.get(criterion)
            suggestion = text(row.get(f'{column}_{rater}')) if column else ''
            entity_column = CORRECTION_FIELDS['entidade_correta']
            remove_entity = own['entidade_correta'] is False and not text(row.get(f'{entity_column}_{rater}'))
            if own[criterion] is True and other[criterion] is False:
                decision = 'TRUE'
            elif suggestion:
                decision = suggestion
            elif remove_entity:
                decision = 'remove'
            elif criterion == 'fidelidade_semantica' and own[criterion] is False:
                decision = 'FALSE'
            else:
                decision = ''
            decisions.append(decision)
        disagreements.append((label, *decisions))
    return disagreements


def export_rows(relationships: pd.DataFrame) -> pd.DataFrame:
    """Keep all disagreements for a triple on one row in numbered blocks."""
    rows = []
    block_count = 1
    for _, source in relationships.sort_values(KEY).iterrows():
        disagreements = row_disagreements(source)
        if not disagreements:
            continue
        row = {
            'identifier': source['Identifier'],
            'original_text': text(source['Texto_Original_a1']),
            'original_triple': source['tripla_original'],
        }
        for number, values in enumerate(disagreements, start=1):
            row.update({f'{column}_{number}': value for column, value in zip(BLOCK_COLUMNS, (*values, ''))})
        block_count = max(block_count, len(disagreements))
        rows.append(row)
    columns = BASE_COLUMNS + [f'{column}_{number}'
        for number in range(1, block_count + 1) for column in BLOCK_COLUMNS]
    return pd.DataFrame(rows, columns=columns).fillna('')


def build_export(a1: Path, a2: Path) -> pd.DataFrame:
    """Load validated workbooks and select shared relationship triples."""
    paths = [a1, a2]
    hashes = [review.file_sha256(path) for path in paths]
    frames = [review.load_review(path, SHEET, KEY, list(CRITERIA))[0] for path in paths]
    for frame in frames:
        review.validate_columns(frame, ['Texto_Original', *CORRECTION_FIELDS.values()])
    paired = review.align_reviews(*frames, KEY)
    common = review.shared_items(paired, KEY)
    result = export_rows(common.loc[common.item_type == 'relationship'])
    if hashes != [review.file_sha256(path) for path in paths]:
        raise RuntimeError('A source workbook changed during export; run again.')
    return result


def write_export(frame: pd.DataFrame, output: Path, *, overwrite: bool = False) -> None:
    """Write Excel-compatible UTF-8 CSV, protecting existing files by default."""
    if output.suffix.lower() != '.csv':
        raise ValueError('Output must use the .csv extension')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w' if overwrite else 'x', encoding='utf-8-sig', newline='') as stream:
        frame.to_csv(stream, index=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a1', type=Path, default=ROOT/'data/inter-rater-reliability/a1-rdf.xlsx')
    parser.add_argument('--a2', type=Path, default=ROOT/'data/inter-rater-reliability/a2-rdf.xlsx')
    parser.add_argument('--output', type=Path, default=ROOT/'data/disagreements.csv')
    parser.add_argument('--overwrite', action='store_true', help='Replace the CSV, including manually entered decisions')
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() in {args.a1.resolve(), args.a2.resolve()}:
            raise ValueError('Output must differ from source workbooks')
        if args.output.suffix.lower() != '.csv':
            raise ValueError('Output must use the .csv extension')
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f'{args.output} already exists; select another output or use --overwrite')
        frame = build_export(args.a1, args.a2)
        write_export(frame, args.output, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Export failed: {exc}', file=sys.stderr)
        return 1
    print(f'Saved {len(frame)} original triples to {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
