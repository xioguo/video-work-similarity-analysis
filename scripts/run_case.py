#!/usr/bin/env python3
"""Single local entry point: initialize, validate, calculate and render evidence reports."""
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
from string import Template
import sys
from urllib.parse import quote, urlparse

from score_content import calculate, normalize
from extract_frames import digest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = 'workflow-v1'
REFERENCE_THRESHOLD_PERCENT = 60  # User-confirmed internal reference; never a verdict.


def initial_case(case_id):
    return {'schema_version': 'case-v1', 'workflow_version': WORKFLOW,
            'method_version': 'content-v3', 'case_id': case_id, 'dataset_type': 'real',
            'analysis_model': {'id': None, 'source': 'unavailable', 'contributors': []},
            'works': {s: {'title': '', 'url': '', 'published_at': '', 'video': None,
                         'preview_video': None,
                         'duration_seconds': None, 'transcript': None,
                         'transcript_complete': False, 'frames_manifest': None,
                         'cover': None, 'cover_unavailable_reason': ''} for s in ('A', 'B')},
            'annotations': None, 'material_screening': None,
            'visual_reviews': [], 'cover_review': None, 'findings': [],
            'sampling': {'interval_seconds': 5, 'override_reason': ''},
            'source_review': {'status': 'blocked', 'notes': '尚未核验发布时间及公共来源',
                              'evidence_files': []},
            'blocked_steps': [], 'limitations': []}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def finite_positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def time_bounds(value):
    parts = re.split(r'[–—-]', str(value))
    if len(parts) != 2:
        raise ValueError('时间必须为起止区间，例如00:00–00:05')
    def seconds(part):
        if not re.fullmatch(r'\d+(?::\d{2}){1,2}(?:\.\d+)?', part.strip()):
            raise ValueError('无效时间码')
        result = 0.0
        for n in part.strip().split(':'):
            result = result * 60 + float(n)
        return result
    start, end = (seconds(p) for p in parts)
    if not 0 <= start < end:
        raise ValueError('时间区间无效')
    return start, end


def highlight(text, intervals):
    """Map normalized character intervals back to escaped original text."""
    positions = [i for i, char in enumerate(text) if char.isalnum() for _ in char.lower()]
    covered = set()
    for start, end in intervals:
        if 0 <= start < end <= len(positions):
            covered.update(range(positions[start], positions[end - 1] + 1))
    chunks, previous = [], False
    for i, char in enumerate(text):
        marked = i in covered
        if marked != previous:
            chunks.append('<mark>' if marked else '</mark>')
        chunks.append(html.escape(char))
        previous = marked
    if previous:
        chunks.append('</mark>')
    return ''.join(chunks)


def model_metadata(case):
    value = case.get('analysis_model') or {}
    if not isinstance(value, dict):
        raise ValueError('analysis_model须为包含id/source/contributors的对象')
    model_id, source = value.get('id'), value.get('source', 'unavailable')
    if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
        raise ValueError('模型ID必须为真实字符串或null')
    if source not in ('runtime', 'user_declared', 'unavailable'):
        raise ValueError('模型来源只接受runtime/user_declared/unavailable')
    if model_id and source == 'unavailable':
        raise ValueError('填写模型ID时须注明来源，不能无依据猜测')
    contributors = value.get('contributors', [])
    for item in contributors:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ('id', 'role')):
            raise ValueError('协作模型须填写实际ID和任务角色')
        if item.get('source') not in ('runtime', 'user_declared'):
            raise ValueError('协作模型须注明ID来源')
    return {'id': model_id.strip() if model_id else None,
            'source': source if model_id else 'unavailable', 'contributors': contributors}


def build(case_path, output):
    case_path, output = Path(case_path).resolve(), Path(output).resolve()
    case, base = read_json(case_path), case_path.parent
    if case.get('schema_version') != 'case-v1' or case.get('method_version') != 'content-v3' or case.get('workflow_version') != WORKFLOW:
        raise ValueError('案件版本不兼容；须明确迁移，不接受旧四项或自选权重')
    if any(k in case for k in ('weights', 'threshold', 'component_scores', 'composite_percent')):
        raise ValueError('权重与综合分由统一计算器生成，案件不得自定义或手填')
    if case.get('dataset_type') not in ('real', 'synthetic'):
        raise ValueError('dataset_type 必须区分真实案件与构造测试')
    cid = case['case_id']
    if not cid or any(c in cid for c in '/\\\0') or cid in ('.', '..'):
        raise ValueError('case_id 不能包含路径')
    files = {str(case_path): digest(case_path)}
    blockers, frame_sets, dense_sets, reviewed, manifests = [], {}, {}, [], {}
    analysis_model = model_metadata(case)

    def local(value):
        if not value:
            raise ValueError('未提供文件')
        path = (base / value).resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('文件缺失或为空: ' + str(value))
        files[str(path)] = digest(path)
        return path

    transcripts = {}
    for side in ('A', 'B'):
        work = case['works'][side]
        if not isinstance(work.get('title'), str) or not work['title'].strip():
            blockers.append(side + ' 作品标题尚未获取；不能用城市或编号替代完整标题')
        if work.get('preview_video'):
            try:
                local(work['preview_video'])
            except (ValueError, OSError) as exc:
                blockers.append(side + ' 预览视频不可用：' + str(exc))
        try:
            path = local(work.get('transcript'))
            if work.get('transcript_complete') is not True:
                raise ValueError('未确认全量转写')
            transcripts[side] = normalize(path.read_text(encoding='utf-8'))
            if not transcripts[side]:
                raise ValueError('转写没有有效文字')
        except (ValueError, OSError, UnicodeError) as exc:
            blockers.append(side + ' 全量转写未完成：' + str(exc))
        try:
            video = local(work.get('video'))
            mp = local(work.get('frames_manifest'))
            manifest = read_json(mp)
            interval = case.get('sampling', {}).get('interval_seconds', 5)
            if not finite_positive(interval) or (interval != 5 and not case['sampling'].get('override_reason')):
                raise ValueError('非默认采样间隔须有明确原因')
            duration = work.get('duration_seconds')
            if not finite_positive(duration):
                raise ValueError('缺少有效视频时长')
            if manifest.get('schema_version') != 'frames-v1' or manifest.get('sampling_scope') != 'full':
                raise ValueError('需要全长抽帧清单 frames-v1')
            if manifest.get('source_sha256') != digest(video):
                raise ValueError('抽帧来源与本案视频不符')
            if abs(manifest.get('duration_seconds', -99) - duration) > .25:
                raise ValueError('抽帧时长与案件不符')
            if manifest.get('interval_seconds') != interval or manifest.get('start_seconds') != 0 or abs(manifest.get('end_seconds', -99) - duration) > .25:
                raise ValueError('抽帧范围或间隔不符')
            frames = manifest.get('frames', [])
            expected = math.ceil(duration / interval)
            if len(frames) != expected:
                raise ValueError('抽帧数量未覆盖全片')
            indexed = {}
            for i, frame in enumerate(frames):
                if abs(frame['target_seconds'] - i * interval) > .001:
                    raise ValueError('采样时点缺失或乱序')
                path = local(str(mp.parent / frame['file']))
                if frame.get('sha256') != digest(path) or path.name in indexed:
                    raise ValueError('帧指纹不符或文件名重复')
                indexed[path.name] = {**frame, 'path': str(path)}
            frame_sets[side], manifests[side] = indexed, manifest
        except (ValueError, OSError, KeyError, TypeError) as exc:
            blockers.append(side + ' 全长画面抽帧未完成：' + str(exc))

    audit, score = None, None
    try:
        audit = read_json(local(case.get('annotations')))
        if audit.get('method_version') != 'content-v3':
            raise ValueError('标注缺少或使用不同方法版本')
        if len(transcripts) != 2:
            raise ValueError('双方全量转写未具备')
        for side in ('A', 'B'):
            units = audit[side + '_units']
            if not units:
                raise ValueError(side + ' 语义清单为空')
            previous_start = -1
            for unit in units:
                if not unit.get('time') or not unit.get('text') or normalize(unit['text']) not in transcripts[side]:
                    raise ValueError('单元缺时间或引文不在完整转写中：' + unit['id'])
                start, end = time_bounds(unit['time'])
                if start < previous_start or (finite_positive(case['works'][side].get('duration_seconds')) and end > case['works'][side]['duration_seconds'] + .25):
                    raise ValueError('单元顺序或片长边界错误：' + unit['id'])
                previous_start = start
                for req in unit.get('requirements', []):
                    if type(req.get('matched')) is not bool or not req.get('meaning'):
                        raise ValueError('要件须明确含义和布尔命中状态：' + unit['id'])
                    other = {u['id']: u for u in audit[('B' if side == 'A' else 'A') + '_units']}
                    for evidence in req.get('evidence', []):
                        if not evidence.get('text') or evidence.get('time') != other.get(evidence.get('id'), {}).get('time'):
                            raise ValueError('对方引文时间不符或为空：' + unit['id'])
        for pair in audit.get('structure_pairs', []):
            if not pair.get('shared_action') or not pair.get('boundary'):
                raise ValueError('结构映射须写共同动作和边界理由')
        score = calculate(audit)
        if score['balanced_overview_percent'] is None:
            raise ValueError('有效分母不足，不能生成综合百分比')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        score = None
        blockers.append('综合分不可计算：' + str(exc))

    screening = None
    required_pairs = set()
    if len(frame_sets) == 2:
        try:
            from frame_similarity import compare, features
            a_dir = Path(next(iter(frame_sets['A'].values()))['path']).parent
            b_dir = Path(next(iter(frame_sets['B'].values()))['path']).parent
            if set(p.name for p in a_dir.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}) != set(frame_sets['A']):
                raise ValueError('A帧目录混有清单外图片')
            if set(p.name for p in b_dir.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}) != set(frame_sets['B']):
                raise ValueError('B帧目录混有清单外图片')
            screening = compare(features(a_dir), features(b_dir))
            # Recompute; a model-supplied cached mean is never authoritative.
            best_hash = screening['best_candidate']
            best_pixel = max(screening['pixel_best_per_b'], key=lambda x: (x['pixel_score'], x['a_frame'], x['b_frame']))
            required_pairs.update((x['a_frame'], x['b_frame']) for x in (best_hash, best_pixel))
            best_by_b = {x['b_frame']: x for x in screening['best_per_b']}
            bf = list(frame_sets['B'])
            for pos in sorted({0, len(bf) // 2, len(bf) - 1}):
                pair = best_by_b[bf[pos]]
                required_pairs.add((pair['a_frame'], pair['b_frame']))
        except (ValueError, OSError, ImportError) as exc:
            blockers.append('画面候选计算未完成：' + str(exc))

    ids, done_pairs = set(), set()
    for review in case.get('visual_reviews', []):
        try:
            rid = review['id']
            if rid in ids:
                raise ValueError('重复复核编号')
            ids.add(rid)
            if review.get('status') != 'reviewed' or not review.get('same') or not review.get('different') or not review.get('judgment'):
                raise ValueError('缺少实际读图的相同点、不同点或结论')
            af = frame_sets['A'][review['a_frame']]
            bf = frame_sets['B'][review['b_frame']]
            if review.get('reuse_status') not in ('unknown', 'public_asset', 'confirmed_specific'):
                raise ValueError('素材身份状态无效')
            dense = {}
            # Each candidate gets a consecutive window; static pairs alone cannot close the task.
            for side, anchor in [('A', af), ('B', bf)]:
                mp = local(review.get('dense_' + side))
                dm = read_json(mp)
                if dm.get('source_sha256') != manifests[side]['source_sha256'] or dm.get('schema_version') != 'frames-v1':
                    raise ValueError('加密抽帧来源错误')
                if dm.get('interval_seconds') != 1:
                    raise ValueError('候选需每秒连续抽帧')
                start, end = dm['start_seconds'], dm['end_seconds']
                minimum = min(5, manifests[side]['duration_seconds'])
                if end - start + .01 < minimum or not start <= anchor['target_seconds'] < end:
                    raise ValueError('候选连续窗口未覆盖锚点或不足5秒')
                if len(dm['frames']) != math.ceil(end - start):
                    raise ValueError('候选连续窗口缺帧')
                for i, item in enumerate(dm['frames']):
                    if abs(item['target_seconds'] - (start + i)) > .001:
                        raise ValueError('候选时间序列不连续')
                    path = local(str(mp.parent / item['file']))
                    if digest(path) != item['sha256']:
                        raise ValueError('候选帧已变化')
                dense[side] = {'start': start, 'end': end, 'count': len(dm['frames'])}
            if not review.get('continuous_observation'):
                raise ValueError('缺少连续变化/运动/操作轨迹观察')
            if review['reuse_status'] == 'confirmed_specific' and not review.get('exclusive_details'):
                raise ValueError('确认连续复用需要专属细节')
            done_pairs.add((review['a_frame'], review['b_frame']))
            reviewed.append({**review, 'A': af, 'B': bf, 'dense': dense})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            blockers.append('画面复核未完成 ' + str(review.get('id', '未编号')) + '：' + str(exc))
    missing_pairs = sorted(required_pairs - done_pairs)
    if missing_pairs:
        blockers.append('画面候选尚未逐对复核：' + ', '.join(a + ' / ' + b for a, b in missing_pairs))
    if not reviewed:
        blockers.append('画面抽帧读图及连续片段复核尚未完成')

    covers = {}
    for side in ('A', 'B'):
        work = case['works'][side]
        try:
            if work.get('cover'):
                covers[side] = local(work['cover'])
            elif not work.get('cover_unavailable_reason'):
                raise ValueError('缺少封面或不可获取原因')
        except (ValueError, OSError) as exc:
            blockers.append(side + ' 封面：' + str(exc))
    if len(covers) == 2:
        cr = case.get('cover_review') or {}
        if cr.get('status') != 'reviewed' or not all(cr.get(k) for k in ('same', 'different', 'judgment')):
            blockers.append('双方封面已获取，但未完成封面文案与版式对照')

    source = case.get('source_review', {})
    try:
        if source.get('status') != 'reviewed' or not source.get('notes') or not source.get('evidence_files'):
            raise ValueError('时间线与公共来源核验未完成')
        for value in source['evidence_files']:
            local(value)
    except (ValueError, OSError) as exc:
        blockers.append('来源核查：' + str(exc))
    # Distinguish missing verification from verified public/authorized explanations.
    findings = []
    for finding in case.get('findings', []):
        try:
            if not finding.get('summary') or not finding.get('evidence_files'):
                raise ValueError('缺少事实说明和证据文件')
            for value in finding['evidence_files']:
                local(value)
            if finding.get('kind') == 'continuous_footage':
                if finding.get('visual_review_id') not in {r['id'] for r in reviewed}:
                    raise ValueError('连续素材发现未关联已复核候选')
            elif finding.get('kind') == 'expression':
                a_ids = {u['id'] for u in (audit or {}).get('A_units', [])}
                b_ids = {u['id'] for u in (audit or {}).get('B_units', [])}
                if finding.get('a_id') not in a_ids or finding.get('b_id') not in b_ids:
                    raise ValueError('表达发现未关联双方原话单元')
            else:
                raise ValueError('发现类型无效')
            # Retain factual observations, without deriving copying/originality grades.
            findings.append({k: finding[k] for k in ('summary', 'kind', 'evidence_files',
                'a_id', 'b_id', 'visual_review_id') if k in finding})
        except (ValueError, OSError) as exc:
            blockers.append('局部证据未完成：' + str(exc))
    for item in case.get('blocked_steps', []):
        if not item.get('reason'):
            raise ValueError('blocked_steps 必须记录具体原因')
        blockers.append(str(item.get('step', '步骤')) + '：' + item['reason'])
    blockers = list(dict.fromkeys(blockers))
    status = 'complete' if not blockers else ('partial' if score is not None or reviewed else 'blocked')
    # Method/renderer fingerprints and all evidence hashes support reproducibility.
    result = {'case_id': cid, 'dataset_type': case['dataset_type'], 'method_version': 'content-v3',
              'workflow_version': WORKFLOW, 'execution_status': status,
              'analysis_model': analysis_model,
              'work_titles': {s: case['works'][s].get('title') or '标题未获取' for s in ('A', 'B')},
              'report_style': 'evidence-editorial-v1',
              'report_policy': 'similarity-only-v1',
              'reference_threshold_percent': REFERENCE_THRESHOLD_PERCENT,
              'content': score, 'screening': screening, 'required_visual_pairs': [list(x) for x in sorted(required_pairs)],
              'reviewed_visual_pairs': [r['id'] for r in reviewed], 'blockers': blockers,
              'findings': findings, 'evidence_sha256': dict(sorted(files.items())),
              'tool_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [
                  ROOT / 'scripts/score_content.py', ROOT / 'scripts/frame_similarity.py',
                  ROOT / 'scripts/extract_frames.py', Path(__file__), ROOT / 'templates/report.html',
                  ROOT / 'SKILL.md', ROOT / 'references/execution-contract.md',
                  ROOT / 'references/semantic-text-coverage.md', ROOT / 'references/material-analysis.md']},
              'verification_boundary': '校验文件、区间、公式与必交项；不证明转写准确、语义正确或真实来源归属'}
    destinations = [output / name for name in ('交付核验.json', 'score.json',
        '证据对照_' + cid + '.html', '分析摘要_' + cid + '.md', '判定结论_' + cid + '.md')]
    if any(str(p) in files for p in destinations):
        raise ValueError('输出文件与原始输入重名，请选择独立的交付目录')
    output.mkdir(parents=True, exist_ok=True)
    (output / '交付核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    stored_score = score if score else {'method_version': 'content-v3',
        'balanced_overview_percent': None, 'status': 'N/A', 'blockers': blockers}
    (output / 'score.json').write_text(json.dumps(stored_score, ensure_ascii=False, indent=2), encoding='utf-8')
    render(case, audit, result, reviewed, output, base)
    return result


def render(case, audit, result, reviews, output, base):
    esc = lambda x: html.escape(str(x))
    pct = lambda x: 'N/A' if x is None else '%.2f%%' % x
    score, cid = result['content'], case['case_id']
    main = pct(score['balanced_overview_percent'] if score else None)
    def attribute_label(value):
        names = {'E': '表达', 'P': '推广', 'S': '步骤', 'T': '事实', 'F': '事实', 'G': '模板'}
        return '/'.join(dict.fromkeys(names.get(x, x) for x in str(value).split('/')))
    def file_uri(value):
        return quote(Path(os.path.relpath((base / value).resolve(), output)).as_posix(), safe='/')
    def replay(side, unit):
        label = html.escape(unit.get('time', '时间未获取'))
        preview = case['works'][side].get('preview_video')
        if preview and (base / preview).is_file():
            try:
                start, _ = time_bounds(unit['time'])
                return '<button data-side="' + side + '" data-time="' + str(start) + '">' + label + ' · 回放</button>'
            except ValueError:
                pass
        return '<span>' + label + '</span>'
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + esc(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    rows = []
    for key, name, weight in [('structure', '叙述结构（保序）', '37.5%'), ('semantic_actions', '语义动作', '37.5%'), ('semantic_text', '语义文字', '25%')]:
        row = [name, weight]
        for direction in ('A_to_B', 'B_to_A'):
            d = score[direction] if score else None
            denom = ('%s/%s' % (d['ordered_matches'], d['source_actions']) if key == 'structure' else
                     '%s/%s' % (d['equivalent_covered_actions_exact'], d['source_actions']) if key == 'semantic_actions' else
                     '%s/%s' % (d['matched_chars'], d['total_chars'])) if d else '缺少可核实分母'
            row.append(pct(d['components_percent'][key] if d else None) + '（' + denom + '）')
        rows.append(row)
    summary = table(['指标', '权重', 'A→B', 'B→A'], rows)
    direction_text = ('A→B：' + pct(score['A_to_B']['composite_percent']) + '；B→A：' + pct(score['B_to_A']['composite_percent'])) if score else '双向综合：N/A（见未完成项）'
    unit_rows = []
    for side in ('A', 'B'):
        for unit in (audit or {}).get(side + '_units', []):
            unit_rows.append([side, unit['id'], unit.get('time', ''), unit['text'],
                              unit.get('attribute', ''), unit.get('exclude_reason') or '纳入'])
    semantic_rows = []
    for side in ('A', 'B'):
        for unit in (audit or {}).get(side + '_units', []):
            for requirement in unit.get('requirements', []):
                semantic_rows.append([side + ':' + unit['id'], requirement.get('meaning', requirement.get('key', '')),
                    '命中' if requirement.get('matched') else '未命中',
                    '；'.join(str(e.get('id', '')) + ' ' + str(e.get('time', '')) + ' ' + str(e.get('text', '')) for e in requirement.get('evidence', []))])
    work_cards = '<div class="cols">'
    for side in ('A', 'B'):
        work = case['works'][side]
        work_cards += '<section id="player-' + side + '"><div class="work-label">作品 ' + side + '</div><h3 class="work-title">' + esc(work.get('title') or '标题未获取') + '</h3>'
        work_cards += '<p class="muted">' + esc(work.get('published_at') or '发布时间未核实') + ' · ' + esc(str(work.get('duration_seconds') or '未知') + '秒') + '</p>'
        cover = work.get('cover')
        poster = ' poster="' + esc(file_uri(cover)) + '"' if cover and (base / cover).is_file() else ''
        preview = work.get('preview_video')
        if preview and (base / preview).is_file():
            work_cards += '<video id="video-' + side + '" controls preload="metadata" src="' + esc(file_uri(preview)) + '"' + poster + '></video>'
        elif cover and (base / cover).is_file():
            work_cards += '<img class="poster" src="' + esc(file_uri(cover)) + '" alt="' + side + '封面">'
        else:
            work_cards += '<p class="empty">未提供可展示的预览画面。</p>'
        links = []
        if work.get('url') and urlparse(work['url']).scheme in ('http', 'https'):
            links.append('<a href="' + esc(work['url']) + '" target="_blank" rel="noopener noreferrer">原作品</a>')
        if work.get('transcript') and (base / work['transcript']).is_file():
            links.append('<a href="' + esc(file_uri(work['transcript'])) + '">完整转写</a>')
        work_cards += '<p>' + ' · '.join(links) + '</p></section>'
    work_cards += '</div>'
    # Group actual A->B evidence, keeping full quotes and precise matched text ranges.
    quote_cards, pair_count = '', 0
    target_units = {u['id']: u for u in (audit or {}).get('B_units', [])}
    for a in (audit if score else {}).get('A_units', []):
        if a.get('exclude_reason') or a.get('attribute') == 'G':
            continue
        grouped = {}
        for req in a.get('requirements', []):
            if not req.get('matched'):
                continue
            for evidence in req.get('evidence', []):
                if evidence.get('id') not in target_units:
                    continue
                pair = grouped.setdefault(evidence['id'], {'meanings': [], 'quotes': []})
                pair['meanings'].append(req.get('meaning', req['key']))
                pair['quotes'].append(evidence.get('text', ''))
        for bid, pair in grouped.items():
            b = target_units[bid]
            means = list(dict.fromkeys(pair['meanings']))
            a_intervals = [(s['normalized_start'], s['normalized_end_exclusive'])
                for s in a.get('text_coverage', {}).get('spans', []) if bid in s.get('supporting_other_units', [])]
            b_intervals = [(s['normalized_start'], s['normalized_end_exclusive'])
                for s in b.get('text_coverage', {}).get('spans', []) if a['id'] in s.get('supporting_other_units', [])]
            if not b_intervals:
                for value in pair['quotes']:
                    key = normalize(value)
                    if key and normalize(b['text']).count(key) == 1:
                        start = normalize(b['text']).index(key)
                        b_intervals.append((start, start + len(key)))
            pair_count += 1
            quote_cards += '<article><span class="badge sim">已核含义对应</span><span class="badge scope">' + esc(attribute_label(a.get('attribute', ''))) + ' · ' + esc(a['id']) + ' ↔ ' + esc(bid) + '</span><h3>' + str(pair_count) + '. ' + esc('；'.join(means)) + '</h3><div class="cols">'
            for side, unit, intervals in [('A', a, a_intervals), ('B', b, b_intervals)]:
                quote_cards += '<div><p class="who">作品 ' + side + ' ' + replay(side, unit) + '</p><p class="quote-text">' + highlight(unit['text'], intervals) + '</p></div>'
            quote_cards += '</div><p class="note">本对原话支持的要点：' + esc('；'.join(means)) + '。同一文字可能由多段共同支持，各对照不重复累加；完整命中区间见附录。</p></article>'
    if not quote_cards:
        quote_cards = '<section><p class="muted">当前未形成可展示的已核原话对照。请结合未完成项及附录核查，不填造匹配。</p></section>'
    visual_html = ''
    for r in reviews:
        figures = ''
        for side in ('A', 'B'):
            uri = Path(os.path.relpath(r[side]['path'], output)).as_posix()
            figures += '<figure><img src="' + esc(quote(uri, safe='/')) + '" alt="' + side + '抽帧"><figcaption>' + side + ' 约' + esc(r[side]['target_seconds']) + '秒</figcaption></figure>'
        visual_html += '<article><h3>' + esc(r['id']) + '</h3><div class="pair">' + figures + '</div>'
        visual_html += '<p>相同点：' + esc(r['same']) + '</p><p>不同点：' + esc(r['different']) + '</p><p>连续观察：' + esc(r['continuous_observation']) + '</p><p>判断：' + esc(r['judgment']) + '；素材状态：' + esc(r['reuse_status']) + '</p></article>'
        visual_html += '<p class="muted">连续复核范围：' + esc('；'.join(side + ' %.2f–%.2f秒，%d帧/1秒间隔' % (r['dense'][side]['start'], r['dense'][side]['end'], r['dense'][side]['count']) for side in ('A', 'B'))) + '</p>'
    if not visual_html:
        visual_html = '<p class="warn">未完成实际读图及连续片段复核，不能据此认定独立录制或素材复用为0%。</p>'
    screening = result['screening']
    screening_text = ('画面外观均值 %.2f%%；RGB像素接近均值 %.2f%%；A %d帧 / B %d帧。方向均为B逐帧选最佳A，仅用于候选筛查，均不计入综合。' % (screening['summary']['mean'], screening['pixel_summary']['mean'], screening['a_count'], screening['b_count'])) if screening else '画面外观/像素：N/A；尚未形成可复算筛查结果。'
    candidates = score['expression_candidates'] if score else None
    candidate_rows = [[s, pct(candidates[s]['text_coverage_percent']) if candidates else 'N/A',
                       '%s/%s' % (candidates[s]['matched_chars'], candidates[s]['total_chars']) if candidates else 'N/A'] for s in ('A', 'B')]
    order_edges = {(p['a_id'], p['b_id']): p for p in (audit or {}).get('structure_pairs', [])}
    ordered = table(['A动作', 'A时间', 'B动作', 'B时间', '共同动作', '对应边界'], [
        [p['a_id'], p.get('a_time'), p['b_id'], p.get('b_time'),
         order_edges.get((p['a_id'], p['b_id']), {}).get('shared_action', '见原始结构映射'),
         order_edges.get((p['a_id'], p['b_id']), {}).get('boundary', '见原始结构映射')]
        for p in (score or {}).get('ordered_alignment', [])])
    spans = table(['单元', '原文命中片段', '归一字符区间', '对方支持单元'], [
        [side + ':' + u['id'], s['original_span'], str(s['normalized_start']) + ':' + str(s['normalized_end_exclusive']), ', '.join(s['supporting_other_units'])]
        for side in ('A', 'B') for u in (audit or {}).get(side + '_units', [])
        for s in u.get('text_coverage', {}).get('spans', [])])
    blocks = '<ul>' + ''.join('<li>' + esc(x) + '</li>' for x in result['blockers']) + '</ul>' if result['blockers'] else '<p>必交项校验通过。机械校验不替代人工语义和来源核实。</p>'
    findings = '<ul>' + ''.join('<li>' + esc(f['summary']) + '</li>' for f in result['findings']) + '</ul>'
    versions = 'content-v3 · workflow-v1；构造示例，仅用于版式与流程验证' if case['dataset_type'] == 'synthetic' else 'content-v3 · workflow-v1'
    model_id = result['analysis_model']['id'] or '未获取（运行环境未提供）'
    model_source = {'runtime': '来自运行环境记录', 'user_declared': '用户提供，未由环境核实', 'unavailable': '不根据昵称推测'}[result['analysis_model']['source']]
    model_contributors = ''.join('<p class="header-meta">协作模型：' + esc(m['id']) + ' · ' + esc(m['role']) + '（' + ('运行环境记录' if m['source'] == 'runtime' else '用户提供') + '）</p>' for m in result['analysis_model']['contributors'])
    works = table(['作品', '标题', '链接', '发布时间'], [[s, case['works'][s].get('title', ''), case['works'][s].get('url', ''), case['works'][s].get('published_at') or '未核实'] for s in ('A', 'B')])
    cr = case.get('cover_review') or {}
    cover_html = (''.join('<p>' + label + '：' + esc(cr.get(key) or '未记录') + '</p>' for key, label in [('same','相同点'),('different','不同点'),('judgment','观察说明')]) if cr else
                  ''.join('<p>作品' + side + '：' + esc(case['works'][side].get('cover_unavailable_reason') or '封面尚未复核') + '</p>' for side in ('A','B')))
    cover_images = ''
    for side in ('A', 'B'):
        value = case['works'][side].get('cover')
        if value and (base / value).is_file():
            uri = Path(os.path.relpath((base / value).resolve(), output)).as_posix()
            cover_images += '<figure><img src="' + esc(quote(uri, safe='/')) + '" alt="' + side + '封面"><figcaption>' + side + '封面</figcaption></figure>'
    cover_html += '<div class="pair">' + cover_images + '</div>'
    content = Template((ROOT / 'templates/report.html').read_text(encoding='utf-8')).substitute(
        title=esc('证据对照_' + cid), versions=esc(versions), score=esc(main),
        report_heading=esc(result['work_titles']['A'] + ' × ' + result['work_titles']['B']),
        case_id=esc(cid), model_id=esc(model_id), model_source=esc(model_source),
        model_contributors=model_contributors, work_cards=work_cards, quote_cards=quote_cards,
        directions=esc(direction_text), status=esc({'complete':'完整完成','partial':'部分完成','blocked':'暂未完成'}[result['execution_status']]),
        summary=summary, works=works, blocks=blocks,
        units=table(['侧', '编号', '时间', '原文', '分类', '纳入/排除'], unit_rows),
        semantics=table(['单元', '语义要件', '命中情况', '对方原文证据'], semantic_rows),
        candidates=table(['源作品', '个性候选文字覆盖', '分子/分母'], candidate_rows),
        ordered=ordered, spans=spans,
        screening=esc(screening_text), visual=visual_html,
        sources=esc(case.get('source_review', {}).get('notes', '未核验')),
        covers=cover_html,
        findings=findings or '<p>未提供独立的局部发现。</p>',
        limitations=esc('；'.join(case.get('limitations', []))) or '语义标注与来源判断须人工复核；采样不能排除全部短片段漏检。')
    (output / ('证据对照_' + cid + '.html')).write_text(content, encoding='utf-8')
    md = '# ' + cid + ' 视频作品对照分析\n\n作品A：' + result['work_titles']['A'] + '\n\n作品B：' + result['work_titles']['B'] + '\n\n本次使用的模型ID：' + model_id + '（' + model_source + '）\n\n综合内容相似度：**' + main + '**\n\n' + direction_text + '\n\n执行状态：' + result['execution_status'] + '\n\n方法：content-v3（结构37.5%＋语义动作37.5%＋语义文字25%，双向取均值）。素材独立展示。\n\n|指标|权重|A→B|B→A|\n|---|---|---|---|\n'
    md += ''.join('|' + '|'.join(str(x).replace('|', '\\|') for x in row) + '|\n' for row in rows)
    if result['analysis_model']['contributors']:
        md += '\n协作模型：' + '；'.join(m['id'] + '（' + m['role'] + '）' for m in result['analysis_model']['contributors']) + '\n'
    md += '\n画面抽帧：' + screening_text + '\n\n' + '\n'.join('- ' + x for x in result['blockers'])
    md += '\n\n详见同目录HTML逐段引文、并列图、来源核验及交付核验.json。\n'
    (output / ('分析摘要_' + cid + '.md')).write_text(md, encoding='utf-8')
    legacy_summary = output / ('判定结论_' + cid + '.md')
    if legacy_summary.exists():
        legacy_summary.write_text(md, encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--case-id', required=True)
    init.add_argument('--output', required=True, type=Path)
    run = sub.add_parser('build')
    run.add_argument('--case', required=True, type=Path)
    run.add_argument('--output', type=Path)
    args = p.parse_args()
    if args.command == 'init':
        if args.output.exists():
            p.error('案件文件已存在，拒绝覆盖')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(initial_case(args.case_id), ensure_ascii=False, indent=2), encoding='utf-8')
        return
    result = build(args.case, args.output or args.case.parent)
    print(json.dumps({k: result[k] for k in ('case_id', 'execution_status', 'reference_threshold_percent', 'blockers')}, ensure_ascii=False))
    sys.exit(0 if result['execution_status'] == 'complete' else 2)


if __name__ == '__main__':
    main()
