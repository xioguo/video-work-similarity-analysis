#!/usr/bin/env python3
"""Aggregate audited, bidirectional spoken-content annotations (content-v3).

This program validates references and computes coverage; it does not infer meaning,
authorship, permissions or plagiarism. Input uses A_units, B_units and structure.
"""
import argparse
import json
import math
import re
from fractions import Fraction
from pathlib import Path

VERSION = 'content-v3'
WEIGHTS = {'structure': .375, 'semantic_actions': .375, 'semantic_text': .25}


def normalize(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def effective(units):
    result, groups = [], set()
    for unit in units:
        if unit.get('exclude_reason') or unit.get('attribute') == 'G':
            continue
        group = unit.get('group', unit['id'])
        if group in groups:
            raise ValueError('Repeated group needs an explicit exclusion: ' + group)
        groups.add(group)
        result.append(unit)
    return result


def longest_ordered_matches(left, right, compatible):
    """One source/target action can appear at most once in the order alignment."""
    table = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i, a in enumerate(left, 1):
        for j, b in enumerate(right, 1):
            table[i][j] = max(table[i - 1][j], table[i][j - 1])
            if (a['id'], b['id']) in compatible:
                table[i][j] = max(table[i][j], table[i - 1][j - 1] + 1)
    chain = []
    i, j = len(left), len(right)
    while i and j:
        a, b = left[i - 1], right[j - 1]
        if ((a['id'], b['id']) in compatible and
                table[i][j] == table[i - 1][j - 1] + 1):
            chain.append({'a_id': a['id'], 'b_id': b['id'],
                          'a_time': a.get('time'), 'b_time': b.get('time')})
            i, j = i - 1, j - 1
        elif table[i - 1][j] >= table[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(chain))


def validate_units(source, targets):
    """Check actual evidence strings, span boundaries and unions, not semantics."""
    details = []
    for unit in source:
        requirements = unit['requirements']
        if not requirements:
            raise ValueError('No semantic requirements: ' + unit['id'])
        keys = [r['key'] for r in requirements]
        if len(keys) != len(set(keys)):
            raise ValueError('Repeated requirement: ' + unit['id'])
        hits = 0
        for req in requirements:
            evidence = req.get('evidence', [])
            if req['matched'] and not evidence:
                raise ValueError('Matched meaning lacks evidence: ' + unit['id'])
            for item in evidence:
                if item['id'] not in targets:
                    raise ValueError('Unknown evidence id: ' + item['id'])
                if normalize(item['text']) not in normalize(targets[item['id']]['text']):
                    raise ValueError('Evidence quote absent: ' + unit['id'] + ' -> ' + item['id'])
            hits += bool(req['matched'])
        text = normalize(unit['text'])
        covered = set()
        coverage = unit['text_coverage']
        for span in coverage['spans']:
            start, end = span['normalized_start'], span['normalized_end_exclusive']
            if not 0 <= start < end <= len(text):
                raise ValueError('Invalid text interval: ' + unit['id'])
            if text[start:end] != normalize(span['original_span']):
                raise ValueError('Text interval differs from original quote: ' + unit['id'])
            support = span.get('supporting_other_units', [])
            if not support or any(x not in targets for x in support):
                raise ValueError('Missing text support: ' + unit['id'])
            covered.update(range(start, end))
        if coverage['total_chars'] != len(text) or coverage['matched_chars'] != len(covered):
            raise ValueError('Stored text count differs from union: ' + unit['id'])
        details.append({'id': unit['id'], 'time': unit.get('time'),
                        'attribute': unit['attribute'], 'group': unit.get('group'),
                        'requirements_matched': hits, 'requirements_total': len(requirements),
                        'action_fraction_exact': str(Fraction(hits, len(requirements))),
                        'matched_chars': len(covered), 'total_chars': len(text)})
    return details


def direction(details, order_count):
    count = len(details)
    if not count:
        return {'status': 'N/A: no eligible source actions', 'composite_percent': None}
    action_count = sum((Fraction(x['requirements_matched'], x['requirements_total'])
                        for x in details), Fraction(0))
    chars = sum(x['total_chars'] for x in details)
    covered = sum(x['matched_chars'] for x in details)
    values = {'structure': 100 * order_count / count,
              'semantic_actions': float(100 * action_count / count),
              'semantic_text': 100 * covered / chars if chars else None}
    total = (sum(WEIGHTS[k] * values[k] for k in WEIGHTS)
             if all(x is not None for x in values.values()) else None)
    return {'source_actions': count, 'ordered_matches': order_count,
            'equivalent_covered_actions_exact': str(action_count),
            'matched_chars': covered, 'total_chars': chars,
            'components_percent': values, 'composite_percent': total,
            'units': details}


def candidate_summary(pool):
    """Validate a separately annotated expression-candidate pool; no originality claim."""
    output = {}
    for label in ('A', 'B'):
        units = pool.get(label + '_units', [])
        total, matched = 0, 0
        for unit in units:
            text = normalize(unit['text'])
            union = set()
            for span in unit.get('normalized_spans', []):
                start, end = span['start'], span['end']
                if not 0 <= start < end <= len(text) or text[start:end] != normalize(span['text']):
                    raise ValueError('Candidate span invalid: ' + unit['id'])
                union.update(range(start, end))
            if unit['total_chars'] != len(text) or unit['matched_chars'] != len(union):
                raise ValueError('Candidate union differs: ' + unit['id'])
            total += len(text)
            matched += len(union)
        output[label] = {'candidate_units': len(units), 'matched_chars': matched,
                         'total_chars': total,
                         'text_coverage_percent': 100 * matched / total if total else None,
                         'status': 'Expression candidates only; source originality has not been established'}
    output['confirmed_individual_F2'] = {'coverage_percent': None,
                                       'status': pool.get('F2_status', 'N/A: originality evidence not supplied')}
    return output


def calculate(data):
    aa, bb = data['A_units'], data['B_units']
    if len({x['id'] for x in aa + bb}) != len(aa + bb):
        raise ValueError('IDs must be unique across both works')
    a, b = effective(aa), effective(bb)
    a_all, b_all = {x['id']: x for x in aa}, {x['id']: x for x in bb}
    da, db = validate_units(a, b_all), validate_units(b, a_all)
    if 'structure_pairs' in data:
        compatible = {(p['a_id'], p['b_id']) for p in data['structure_pairs']}
        if any(ai not in a_all or bi not in b_all for ai, bi in compatible):
            raise ValueError('Structure references an unknown unit')
    else:
        structure = data['structure']
        sa, sb = structure['sequence_A'], structure['sequence_B']
        if any(i not in a_all for _, i in sa) or any(i not in b_all for _, i in sb):
            raise ValueError('Structure references an unknown unit')
        compatible = {(ai, bi) for symbol, ai in sa for other, bi in sb if symbol == other}
    chain = longest_ordered_matches(a, b, compatible)
    ab, ba = direction(da, len(chain)), direction(db, len(chain))
    pair = [ab['composite_percent'], ba['composite_percent']]
    return {'method_version': VERSION,
            'score_name': '综合内容相似度（描述性，非抄袭概率）',
            'normalized_content_weights': WEIGHTS,
            'original_content_weights': {'structure': 30, 'semantic_actions': 30, 'semantic_text': 20},
            'A_to_B': ab, 'B_to_A': ba,
            'balanced_overview_percent': sum(pair) / 2 if all(x is not None for x in pair) else None,
            'ordered_alignment': chain,
            'expression_candidates': candidate_summary(data.get('candidate_E_only', {})),
            'material': {'included_in_composite': False,
                         'confirmed_reuse_coverage': None,
                         'status': 'See separately audited material evidence; unknown is not zero'},
            'automatic_plagiarism_verdict': None,
            'limitations': ['Facts, demonstrations and promotions are included only in descriptive content coverage.',
                           'Semantic action and text coverage reuse the same annotations; they are not independent evidence.',
                           'Ordered alignment uses declared semantic compatibility and is not authorship proof.',
                           'No calibrated decision threshold is supplied; evidence strength and source review remain separate.']}


def self_test():
    def u(uid, text, hit=True, group=None, excluded=None):
        return {'id': uid, 'text': text, 'attribute': 'E', 'group': group or uid,
                'exclude_reason': excluded,
                'requirements': [{'key': 'r', 'matched': hit,
                                  'evidence': [{'id': 'b' if uid == 'a' else 'a', 'text': text}] if hit else []}],
                'text_coverage': {'total_chars': len(text), 'matched_chars': len(text) if hit else 0,
                                  'spans': [{'original_span': text, 'normalized_start': 0,
                                             'normalized_end_exclusive': len(text),
                                             'supporting_other_units': ['b' if uid == 'a' else 'a']}] if hit else []}}
    a, b = u('a', '相同'), u('b', '相同')
    d = {'A_units': [a], 'B_units': [b],
         'structure': {'sequence_A': [['x', 'a']], 'sequence_B': [['x', 'b']]}}
    assert calculate(d)['balanced_overview_percent'] == 100
    # A common fact can be semantically identical while still proving no authorship.
    a['attribute'] = b['attribute'] = 'P'
    assert calculate(d)['balanced_overview_percent'] == 100
    assert calculate(d)['expression_candidates']['A']['text_coverage_percent'] is None
    a['attribute'] = b['attribute'] = 'E'
    # Repeating a supporting fragment does not increase the union.
    a['text_coverage']['spans'] *= 2
    assert calculate(d)['A_to_B']['matched_chars'] == 2
    # Explicitly excluded duplicate groups do not dilute either direction.
    duplicate = {**a, 'id': 'a_repeat', 'exclude_reason': 'repeated viewpoint'}
    d['A_units'].append(duplicate)
    assert calculate(d)['balanced_overview_percent'] == 100
    # Reordering preserves semantic matches but reduces the actual ordered chain.
    left = [{'id': 'a1'}, {'id': 'a2'}]
    right = [{'id': 'b2'}, {'id': 'b1'}]
    assert len(longest_ordered_matches(left, right, {('a1', 'b1'), ('a2', 'b2')})) == 1
    assert calculate(d)['material']['confirmed_reuse_coverage'] is None
    assert calculate(d)['automatic_plagiarism_verdict'] is None
    # Different characters can receive full audited semantic coverage.
    aa, bb = u('a', '无需花钱'), u('b', '不用付费')
    aa['requirements'][0]['evidence'][0]['text'] = bb['text']
    bb['requirements'][0]['evidence'][0]['text'] = aa['text']
    assert calculate({'A_units': [aa], 'B_units': [bb],
                      'structure_pairs': [{'a_id': 'a', 'b_id': 'b'}]})['balanced_overview_percent'] == 100
    print('Passed: identical and synonymous coverage, common facts without authorship claim, span union, explicit deduplication, order sensitivity, unknown material, no automatic verdict')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.input or not args.output:
        p.error('Provide --input audited.json and --output score.json')
    if args.input.resolve() == args.output.resolve():
        p.error('Do not overwrite audited annotations')
    result = calculate(json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('method_version', 'balanced_overview_percent', 'normalized_content_weights')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
