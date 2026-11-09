#!/usr/bin/env python3
"""Export unique adjudication cases to JSON, indexed by four review subsets."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

if __package__:
    from . import review_helpers as review
    from . import rdf_comparison as rdf
else:
    import review_helpers as review
    import rdf_comparison as rdf

ROOT = Path(__file__).resolve().parents[1]
KEY = ['Identifier', 'tripla_original']
CRITERIA = {
    'entidade_correta': 'Entity', 'predicado_correto': 'Predicate',
    'direcao_relacao_correta': 'Direction', 'fidelidade_semantica': 'Semantic fidelity',
}
ALL_CRITERIA = {'label_corresponde_wikidata': 'Label matches Wikidata', **CRITERIA}
SUBSETS = {
    'unilateral_approvals': {
        'title': 'Unilateral approvals (critical disagreement)',
        'definition': 'Complete relationship ratings: one rater approves all four substantive criteria and the other rejects at least one.',
    },
    'shared_rejection_diagnosis_divergence': {
        'title': 'Different diagnoses in shared rejections',
        'definition': 'Both reject the relationship, but their full sets of rejected criteria differ. Includes partial overlap and no shared reason.',
    },
    'competing_rdf_relations': {
        'title': 'Competing relations in final RDF graphs',
        'definition': 'One case per Identifier and unordered endpoint pair present in both final graphs, with different predicate/direction triple sets. All alternatives are retained; no one-to-one edit is assumed.',
    },
    'unilateral_unresolved_entities': {
        'title': 'Unilateral explicitly unresolved entity corrections',
        'definition': 'A relationship has an explicit unresolved-entity correction marker from exactly one rater. Marker absence is unknown, not proof of successful resolution.',
    },
}
CONTEXT = {
    'Texto_Original': 'source_text', 'relacao_traduzida': 'readable_triple',
    'observacao': 'comment', 'entidades_adequadas PREENCHER SE INCORRETO': 'suggested_entities',
    'predicados_adequados PREENCHER SE INCORRETO': 'suggested_predicates',
    'direcao_relacoes_adequada PREENCHER SE INCORRETO': 'suggested_direction',
    'rdf corrigido': 'suggested_rdf',
}


def optional_text(value):
    return None if value is None or pd.isna(value) or str(value).strip() == '' else str(value)


def case_record(kind: str, identifier: str, identity) -> dict:
    serialized = json.dumps([kind, identifier, identity], ensure_ascii=False, sort_keys=True)
    return {'case_id': hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:24],
            'case_type': kind, 'identifier': identifier, 'subset_ids': [],
            'adjudication': {'status': 'pending', 'decision': None, 'approved_ratings': None,
                             'approved_triples': [], 'reviewer': None, 'notes': None}}


def rater_evidence(row: pd.Series, rater: str) -> dict:
    ratings = {c: None if pd.isna(row[f'{c}_{rater}']) else bool(row[f'{c}_{rater}'])
               for c in ALL_CRITERIA}
    complete = all(ratings[c] is not None for c in CRITERIA)
    excel_row = row.get(f'excel_row_{rater}')
    return {'excel_row': int(excel_row) if pd.notna(excel_row) else None,
            'ratings': ratings, 'substantive_ratings_complete': complete,
            'fully_correct': all(ratings[c] for c in CRITERIA) if complete else None,
            'rejected_criteria': [c for c in CRITERIA if ratings[c] is False],
            **{name: optional_text(row.get(f'{col}_{rater}')) for col, name in CONTEXT.items()}}


def annotation_subsets(a: dict, b: dict, unresolved: dict) -> list[str]:
    """Classify a single annotation without duplicating its evidence."""
    subsets = []
    complete = a['substantive_ratings_complete'] and b['substantive_ratings_complete']
    if complete and a['fully_correct'] != b['fully_correct']:
        subsets.append('unilateral_approvals')
    errors_a, errors_b = set(a['rejected_criteria']), set(b['rejected_criteria'])
    if complete and errors_a and errors_b and errors_a != errors_b:
        subsets.append('shared_rejection_diagnosis_divergence')
    if unresolved['a1'] != unresolved['a2']:
        subsets.append('unilateral_unresolved_entities')
    return subsets


def annotation_cases(relationships: pd.DataFrame) -> list[dict]:
    flags = {r: review.unresolved_flags(relationships, r) for r in ['a1', 'a2']}
    records = []
    for index, row in relationships.iterrows():
        a, b = (rater_evidence(row, r) for r in ['a1', 'a2'])
        errors_a, errors_b = set(a['rejected_criteria']), set(b['rejected_criteria'])
        unresolved = {r: bool(flags[r].loc[index]) for r in ['a1', 'a2']}
        subsets = annotation_subsets(a, b, unresolved)
        if not subsets:
            continue
        record = case_record('annotation', str(row.Identifier), str(row.tripla_original))
        record.update(subset_ids=subsets, original_triple=str(row.tripla_original),
                      evidence={'a1': a, 'a2': b},
                      shared_rejected_criteria=sorted(errors_a & errors_b),
                      differing_criteria=[c for c in CRITERIA if a['ratings'][c] != b['ratings'][c]],
                      diagnosis_overlap=('partial' if errors_a & errors_b else 'none')
                      if 'shared_rejection_diagnosis_divergence' in subsets else None,
                      explicit_unresolved_marker=unresolved,
                      unresolved_marker_absence_means='unknown')
        records.append(record)
    return records


def endpoint_groups(graph) -> dict:
    groups = {}
    for triple in rdf.relation_set(graph):
        key = tuple(sorted((triple[0].n3(), triple[2].n3())))
        groups.setdefault(key, set()).add(triple)
    return groups


def triple_records(triples: set, graph) -> list[dict]:
    return [{'subject': s.n3(), 'predicate': p.n3(), 'object': o.n3(),
             'turtle': ' '.join(t.n3() for t in (s, p, o)) + ' .',
             'readable': ' → '.join(rdf.display_term(t, graph) for t in (s, p, o))}
            for s, p, o in sorted(triples, key=lambda t: tuple(x.n3() for x in t))]


def competing_cases(left: dict, right: dict) -> tuple[list[dict], dict]:
    rdf.validate_graph_keys(left, right)
    records = []
    excluded = {'unpaired_relation_triples_a1': 0, 'unpaired_relation_triples_a2': 0,
                'exclusive_label_triples_a1': 0, 'exclusive_label_triples_a2': 0}
    for identifier in sorted(left):
        a, b = endpoint_groups(left[identifier]), endpoint_groups(right[identifier])
        excluded['unpaired_relation_triples_a1'] += sum(len(a[k]) for k in a.keys()-b.keys())
        excluded['unpaired_relation_triples_a2'] += sum(len(b[k]) for k in b.keys()-a.keys())
        labels_a = set(left[identifier]) - rdf.relation_set(left[identifier])
        labels_b = set(right[identifier]) - rdf.relation_set(right[identifier])
        excluded['exclusive_label_triples_a1'] += len(labels_a-labels_b)
        excluded['exclusive_label_triples_a2'] += len(labels_b-labels_a)
        for endpoints in sorted(a.keys() & b.keys()):
            triples_a, triples_b = a[endpoints], b[endpoints]
            if triples_a == triples_b:
                continue
            record = case_record('rdf_relation_group', identifier, endpoints)
            record.update(subset_ids=['competing_rdf_relations'], endpoints=list(endpoints),
                          a1_triples=triple_records(triples_a, left[identifier]),
                          a2_triples=triple_records(triples_b, right[identifier]),
                          shared_triples=triple_records(triples_a & triples_b, left[identifier]),
                          only_a1=triple_records(triples_a-triples_b, left[identifier]),
                          only_a2=triple_records(triples_b-triples_a, right[identifier]),
                          predicate_sets_differ={p for s,p,o in triples_a} != {p for s,p,o in triples_b},
                          direction_sets_differ={(s,o) for s,p,o in triples_a} != {(s,o) for s,p,o in triples_b},
                          correspondence='Same unordered RDF endpoints; alternatives are not proven replacements.')
            records.append(record)
    return records, excluded


def final_graph_cases(a1: Path, a2: Path, reviews: dict) -> tuple[list[dict], dict]:
    """Load final graphs and attach provenance to each competing group."""
    left, left_audit = rdf.load_final_graphs(a1, 'rdf completo')
    right, right_audit = rdf.load_final_graphs(a2, 'rdf corrigido')
    graph_records, excluded_graph = competing_cases(left, right)
    audits = {'a1': left_audit.set_index('Identifier'), 'a2': right_audit.set_index('Identifier')}
    for case in graph_records:
        case['source_context'] = {}
        for r, field in [('a1', 'rdf completo'), ('a2', 'rdf corrigido')]:
            block = reviews[r].loc[reviews[r].Identifier == case['identifier']]
            case['source_context'][r] = {
                'field': field, 'excel_row': int(audits[r].loc[case['identifier'], 'excel_row']),
                'source_text': optional_text(block.iloc[0].get('Texto_Original')) if len(block) else None}
    return graph_records, excluded_graph


def index_subsets(records: list[dict]) -> dict:
    if len({case['case_id'] for case in records}) != len(records):
        raise ValueError('Duplicate case IDs')
    subsets = {}
    for key, description in SUBSETS.items():
        ids = [case['case_id'] for case in records if key in case['subset_ids']]
        subsets[key] = {**description, 'count': len(ids), 'case_ids': ids}
    return subsets


def build_export(a1: Path, a2: Path) -> dict:
    """Assemble cases and audit metadata; never write source or output files."""
    paths = {'a1': a1, 'a2': a2}
    hashes = {r: review.file_sha256(p) for r, p in paths.items()}
    reviews = {r: review.load_review(p, 'resultado_template_processado', KEY, list(ALL_CRITERIA))[0]
               for r, p in paths.items()}
    paired = review.align_reviews(reviews['a1'], reviews['a2'], KEY)
    common = review.shared_items(paired, KEY)
    relationships = common.loc[common.item_type == 'relationship']
    graph_records, excluded_graph = final_graph_cases(a1, a2, reviews)
    records = sorted(annotation_cases(relationships) + graph_records,
                     key=lambda item: (item['identifier'], item['case_type'], item['case_id']))
    subsets = index_subsets(records)
    complete = relationships[[f'{c}_{r}' for c in CRITERIA for r in ['a1','a2']]].notna().all(axis=1)
    result = {'schema_version': 2,
              'sources': {r: {'path': str(p.resolve()), 'sha256': hashes[r]} for r,p in paths.items()},
              'scope': {'annotation_unit': 'Identifier + original relationship triple',
                        'whole_triple_criteria': CRITERIA,
                        'rdf_unit': 'Identifier + unordered endpoint pair in both final graphs',
                        'subsets_may_overlap': True,
                        'unique_cases': len(records),
                        'subset_memberships': sum(s['count'] for s in subsets.values())},
              'coverage': {'matched_items': len(common), 'relationship_items': len(relationships),
                           'excluded_label_items': int((common.item_type == 'label').sum()),
                           'unmatched_items': int((paired['_merge'] != 'both').sum()),
                           'incomplete_relationship_items': int((~complete).sum()), **excluded_graph},
              'subsets': subsets, 'cases': records}
    if any(review.file_sha256(p) != hashes[r] for r,p in paths.items()):
        raise RuntimeError('Input workbook changed while reading; rerun the export')
    return result


def write_export(payload: dict, output: Path, *, overwrite: bool = False) -> None:
    if output.suffix.lower() != '.json':
        raise ValueError('Output must use the .json extension')
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w' if overwrite else 'x', encoding='utf-8') as stream:
        stream.write(text)


def validate_export(payload: dict) -> None:
    """Check schema, unique cases, and the four subset indexes without changing decisions."""
    if payload.get('schema_version') != 2:
        raise ValueError('Unsupported adjudication schema; expected version 2')
    cases, subsets = payload['cases'], payload['subsets']
    if set(subsets) != set(SUBSETS):
        raise ValueError('Expected the four adjudication subsets')
    for case in cases:
        memberships = case['subset_ids']
        if not memberships or len(memberships) != len(set(memberships)) or not set(memberships) <= subsets.keys():
            raise ValueError(f"Invalid memberships for {case['case_id']}")
    expected = index_subsets(cases)
    for key, subset in subsets.items():
        ids = subset['case_ids']
        if len(ids) != len(set(ids)) or set(ids) != set(expected[key]['case_ids']) or subset['count'] != expected[key]['count']:
            raise ValueError(f'Invalid subset index: {key}')
    memberships = sum(len(case['subset_ids']) for case in cases)
    if payload['scope']['unique_cases'] != len(cases) or payload['scope']['subset_memberships'] != memberships:
        raise ValueError('Case totals do not reconcile with the JSON scope')


def load_export(path: Path, sources: dict[str, Path]) -> tuple[dict, str]:
    """Read an existing review round and reject stale inputs; never regenerate it."""
    content = path.read_bytes()
    payload = json.loads(content.decode('utf-8'))
    validate_export(payload)
    for rater, source in sources.items():
        if payload['sources'][rater]['sha256'] != review.file_sha256(source):
            raise ValueError(f'Stale adjudication JSON for {rater}; generate a new round under a new filename')
    return payload, hashlib.sha256(content).hexdigest()


def adjudication_tables(payload: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize a validated payload using unique cases and overlapping memberships."""
    cases = payload['cases']
    rows = [{'case_id': case['case_id'], 'Identifier': case['identifier'],
             'case_type': case['case_type'], 'subset_ids': tuple(case['subset_ids']),
             'membership_count': len(case['subset_ids']), 'status': case['adjudication']['status'],
             'decision': case['adjudication'].get('decision')} for case in cases]
    table = pd.DataFrame(rows, columns=['case_id', 'Identifier', 'case_type', 'subset_ids',
                                       'membership_count', 'status', 'decision'])
    counts = [{'subset': key, 'title': subset['title'], 'cases': subset['count'],
               'pct_of_unique_cases': review.percent(subset['count'], len(cases)),
               'definition': subset['definition']} for key, subset in payload['subsets'].items()]
    return table, pd.DataFrame(counts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a1', type=Path, default=ROOT/'data/inter-rater-reliability/a1-rdf.xlsx')
    parser.add_argument('--a2', type=Path, default=ROOT/'data/inter-rater-reliability/a2-rdf.xlsx')
    parser.add_argument('--output', type=Path, default=ROOT/'data/disagreements.json')
    parser.add_argument('--overwrite', action='store_true', help='Discard existing output and any adjudication decisions')
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() in {args.a1.resolve(), args.a2.resolve()}:
            raise ValueError('Output must differ from source workbooks')
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f'{args.output} already exists; select another output to preserve decisions')
        payload = build_export(args.a1, args.a2)
        write_export(payload, args.output, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Export failed: {exc}', file=sys.stderr)
        return 1
    print(f"Saved {len(payload['cases'])} unique cases to {args.output}")
    for key, subset in payload['subsets'].items():
        print(f"  {key}: {subset['count']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
