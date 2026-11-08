"""Compare final RDF graphs without modifying source workbooks."""
from pathlib import Path
import re
import pandas as pd
from rdflib import Graph, URIRef, BNode, RDFS


def decode_layout(text: str) -> str:
    """Decode spreadsheet newline escapes outside Turtle quoted literals only."""
    parts = re.split(r'''("""[\s\S]*?"""|\x27\x27\x27[\s\S]*?\x27\x27\x27|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)''', text)
    return ''.join(part if i % 2 else part.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\r', '\n')
                   for i, part in enumerate(parts))


def parse_graph(text: str, context: str) -> tuple[Graph, str]:
    try:
        graph = Graph().parse(data=text, format='turtle')
        mode = 'as stored'
    except Exception:
        try:
            graph = Graph().parse(data=decode_layout(text), format='turtle')
            mode = 'layout escapes decoded'
        except Exception as exc:
            raise ValueError(f'{context}: invalid Turtle: {exc}') from exc
    if any(isinstance(term, BNode) for triple in graph for term in triple):
        raise ValueError(f'{context}: blank nodes need graph-isomorphism-aware comparison')
    return graph, mode


def load_final_graphs(path: Path, column: str) -> tuple[dict, pd.DataFrame]:
    raw = pd.read_excel(path, sheet_name='resultado_template_processado', dtype=object,
                       keep_default_na=False)
    missing = {'Identifier', column} - set(raw.columns)
    if missing:
        raise ValueError(f'{path.name}: missing columns: {sorted(missing)}')
    raw['Identifier'] = raw['Identifier'].astype('string').str.strip().replace('', pd.NA).ffill()
    selected = raw.loc[raw[column].notna() & raw[column].astype(str).str.strip().ne('')]
    graphs, records = {}, []
    for index, row in selected.iterrows():
        identifier = row['Identifier']
        if pd.isna(identifier) or identifier in graphs:
            raise ValueError(f'{path.name}: missing or duplicate graph Identifier: {identifier}')
        graph, mode = parse_graph(str(row[column]), f'{path.name}:{index+2}:{identifier}')
        graphs[identifier] = graph
        records.append({'Identifier': identifier, 'file': path.name, 'column': column,
                        'excel_row': index + 2, 'triples': len(graph), 'parsing': mode})
    if not graphs:
        raise ValueError(f'{path.name}: no graphs in {column}')
    missing_graphs = set(raw['Identifier'].dropna()) - graphs.keys()
    if missing_graphs:
        raise ValueError(f'{path.name}: missing final graphs: {sorted(missing_graphs)}')
    return graphs, pd.DataFrame(records)


def relation_set(graph: Graph) -> set:
    """Exclude rdfs:label only; preserve literals and every other predicate."""
    return {triple for triple in graph if triple[1] != RDFS.label}


def set_metrics(left: set, right: set) -> dict:
    shared, union = len(left & right), len(left | right)
    return {'a1': len(left), 'a2': len(right), 'shared': shared,
            'only_a1': len(left-right), 'only_a2': len(right-left),
            'jaccard_pct': 100*shared/union if union else float('nan'),
            'shared_of_a1_pct': 100*shared/len(left) if left else float('nan'),
            'shared_of_a2_pct': 100*shared/len(right) if right else float('nan'),
            'dice_pct': 200*shared/(len(left)+len(right)) if left or right else float('nan')}


def projections(triples: set) -> dict:
    return {
        'Relation triples': triples,
        'Endpoint entity IRIs': {term for s, p, o in triples for term in [s, o] if isinstance(term, URIRef)},
        'Predicates': {p for s, p, o in triples},
        'Directed endpoint pairs': {(s, o) for s, p, o in triples},
        'Undirected endpoint pairs': {frozenset((s, o)) for s, p, o in triples},
    }


def compare_graphs(left: dict, right: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    validate_graph_keys(left, right)
    records, pooled = [], {}
    for identifier in sorted(left):
        a, b = relation_set(left[identifier]), relation_set(right[identifier])
        views_a = graph_views(left[identifier], a)
        views_b = graph_views(right[identifier], b)
        for dimension in views_a:
            records.append({'Identifier': identifier, 'dimension': dimension,
                            **set_metrics(views_a[dimension], views_b[dimension])})
            buckets = pooled.setdefault(dimension, [set(), set()])
            buckets[0].update((identifier, item) for item in views_a[dimension])
            buckets[1].update((identifier, item) for item in views_b[dimension])
    totals = [{'dimension': dimension, **set_metrics(*sets)} for dimension, sets in pooled.items()]
    return pd.DataFrame(records), pd.DataFrame(totals)


def validate_graph_keys(left: dict, right: dict) -> None:
    if not left or not right:
        raise ValueError('Provide at least one graph per rater')
    if left.keys() != right.keys():
        raise ValueError(f'Unmatched graph identifiers: {left.keys() ^ right.keys()}')


def graph_views(graph: Graph, relations: set) -> dict:
    return {'All RDF triples': set(graph),
            'Label triples': {t for t in graph if t[1] == RDFS.label}, **projections(relations)}


def mismatch_candidates(left: dict, right: dict) -> pd.DataFrame:
    """Enumerate evidence, not inferred edits. A triple can have several candidates."""
    validate_graph_keys(left, right)
    records = []
    for identifier in sorted(left):
        a, b = relation_set(left[identifier]), relation_set(right[identifier])
        for source_name, source, target in [('a1', a-b, b-a), ('a2', b-a, a-b)]:
            for s, p, o in sorted(source, key=lambda t: tuple(map(str,t))):
                candidates = {
                    'Predicate only': {t for t in target if t[0] == s and t[2] == o and t[1] != p},
                    'Direction only': {(o,p,s)} & target if s != o else set(),
                    'One endpoint only': {t for t in target if t[1] == p and ((t[0] == s and t[2] != o) or (t[0] != s and t[2] == o))},
                    'Direction and predicate': {t for t in target if t[0] == o and t[2] == s and t[1] != p and s != o},
                }
                for kind, matches in candidates.items():
                    for other in sorted(matches, key=lambda t: tuple(map(str,t))):
                        records.append({'Identifier': identifier, 'source': source_name, 'kind': kind,
                                        'triple': tuple(term.n3() for term in (s,p,o)),
                                        'candidate': tuple(term.n3() for term in other)})
    return pd.DataFrame(records, columns=['Identifier','source','kind','triple','candidate'])


def display_term(term, graph: Graph) -> str:
    if isinstance(term, URIRef):
        labels = sorted(graph.objects(term, RDFS.label),
                        key=lambda value: (getattr(value, 'language', None) != 'en', value.n3()))
        label = labels[0] if labels else None
        local = str(term).rsplit('/',1)[-1]
        return f'{label} [{local}]' if label is not None else local
    return term.n3()


def exclusive_triples(left: dict, right: dict) -> pd.DataFrame:
    validate_graph_keys(left, right)
    rows = []
    for identifier in sorted(left):
        a,b = relation_set(left[identifier]),relation_set(right[identifier])
        for side, triples, graph in [('a1',a-b,left[identifier]),('a2',b-a,right[identifier])]:
            for s,p,o in sorted(triples,key=lambda t:tuple(map(str,t))):
                rows.append({'Identifier':identifier, 'source':side, 'subject':display_term(s,graph),
                             'predicate':display_term(p,graph), 'object':display_term(o,graph),
                             'triple':tuple(t.n3() for t in (s,p,o))})
    return pd.DataFrame(rows, columns=['Identifier', 'source', 'subject', 'predicate', 'object', 'triple'])
