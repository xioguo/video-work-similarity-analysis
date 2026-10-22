#!/usr/bin/env python3
"""Rank sampled frame appearance with 256-bit dHash; not video identity detection."""
import argparse
import json
import math
import statistics
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

CENTER = (0.15, 0.08, 0.85, 0.82)

def dhash(image, box=None):
    if box:
        image = image.crop(tuple(int(v * s) for v, s in zip(
            box, (image.width, image.height, image.width, image.height))))
    values = list(image.convert('L').resize((17, 16), Image.Resampling.BICUBIC).getdata())
    return sum(int(values[y * 17 + x] > values[y * 17 + x + 1]) << (y * 16 + x)
               for y in range(16) for x in range(16))

def features(directory):
    paths = sorted(p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'})
    if not paths:
        raise ValueError(f'No sampled images in {directory}')
    result = []
    for path in paths:
        with Image.open(path) as image:
            result.append((path.name, dhash(image), dhash(image, CENTER), image.convert('RGB').resize((320, 180), Image.Resampling.LANCZOS)))
    return result

def compare(a, b):
    pairs, best, pixel_best = [], [], []
    for bn, bh, bc, bi in b:
        candidates = []
        for an, ah, ac, ai in a:
            whole = bin(ah ^ bh).count('1')
            center = bin(ac ^ bc).count('1')
            distance = (whole + center) / 2
            candidates.append({'a_frame': an, 'b_frame': bn,
                               'whole_distance': whole, 'center_distance': center,
                               'mean_distance': distance,
                               'appearance_score': 100 * (1 - distance / 256),
                               'pixel_mae': statistics.mean(ImageStat.Stat(ImageChops.difference(ai, bi)).mean),
                               'pixel_score': 100 * (1 - statistics.mean(ImageStat.Stat(ImageChops.difference(ai, bi)).mean) / 255)})
        pairs.extend(candidates)
        best.append(min(candidates, key=lambda p: (p['mean_distance'], p['a_frame'])))
        pixel_best.append(max(candidates, key=lambda p: (p['pixel_score'], p['a_frame'])))
    scores = [p['appearance_score'] for p in best]
    return {'a_count': len(a), 'b_count': len(b), 'comparison_count': len(pairs),
            'direction': 'B -> best A per sampled frame', 'hash_bits': 256,
            'center_box_normalized': list(CENTER), 'resize': '17x16 grayscale, Pillow BICUBIC',
            'formula': '100 * (1 - mean(whole_distance, center_distance) / 256)',
            'summary': {'mean': statistics.mean(scores), 'median': statistics.median(scores),
                        'max': max(scores)},
            'best_candidate': min(pairs, key=lambda p: (p['mean_distance'], p['a_frame'], p['b_frame'])),
            'best_per_b': best, 'all_pairs': pairs,
            'pixel_method': {'mode': 'RGB', 'size': [320, 180], 'resize': 'Pillow LANCZOS; stretch full frame', 'formula': '100 * (1 - RGB pixel MAE / 255)', 'direction': 'B -> highest pixel score A per sampled frame'},
            'pixel_summary': {'mean': statistics.mean(p['pixel_score'] for p in pixel_best), 'median': statistics.median(p['pixel_score'] for p in pixel_best), 'max': max(p['pixel_score'] for p in pixel_best)},
            'pixel_best_per_b': pixel_best,
            'limitations': ['Sampled appearance only; not source identity, reuse duration or plagiarism probability.',
                            'Shared backgrounds, generic windows, overlays and crop/scale affect scores.',
                            'No geometric alignment, optical flow or continuous video fingerprinting.',
                            'Filenames are identifiers; obtain actual/approximate timecodes from source manifests.']}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('素材分析配置.json'))
    parser.add_argument('--save-config', action='store_true', help='Save resolved inputs and scores to --config for no-argument reruns')
    parser.add_argument('--a-frames', type=Path)
    parser.add_argument('--b-frames', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--weights', help='Four positive/nonnegative weights: D1,D2,D4,D5, normalized to 100')
    parser.add_argument('--component-scores', help='Three verified percentages D1 beat, D2 action count, D4 semantic text coverage; optional fourth must equal live D5 pixel mean')
    parser.add_argument('--interval-a', type=float)
    parser.add_argument('--interval-b', type=float)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8')) if args.config.exists() else {}
    if config.get('component_scores') and config.get('formula_version') != '四项加权v2.1' and args.component_scores is None:
        parser.error('Old/unversioned D4 may be character-only. Reverify semantic text coverage and provide --component-scores explicitly before saving v2.1 config.')
    for field in ('a_frames', 'b_frames', 'output', 'weights', 'component_scores', 'interval_a', 'interval_b'):
        if getattr(args, field) is None and field in config:
            setattr(args, field, Path(config[field]) if field in ('a_frames', 'b_frames', 'output') else config[field])
    if not all((args.a_frames, args.b_frames, args.output)):
        parser.error('Provide A/B frame directories and output, or a saved config')
    args.weights = args.weights or '30,30,20,20'
    if any(x is not None and x <= 0 for x in (args.interval_a, args.interval_b)):
        parser.error('Sampling intervals must be positive seconds')
    if args.output.resolve().parent in {args.a_frames.resolve(), args.b_frames.resolve()}:
        parser.error('Save derived output separately from source frame directories')
    result = compare(features(args.a_frames), features(args.b_frames))
    if args.component_scores:
        weights = [float(x) for x in args.weights.split(',')]
        scores = [float(x) for x in args.component_scores.split(',')]
        if len(weights) != 4 or len(scores) not in (3, 4) or not all(math.isfinite(x) for x in weights + scores) or min(weights) < 0 or sum(weights) <= 0 or any(not 0 <= x <= 100 for x in scores):
            parser.error('Expected four nonnegative weights with positive sum and three/four percentages')
        if len(scores) == 4 and abs(scores[3] - result['pixel_summary']['mean']) > 0.00001:
            parser.error('D5 component must equal computed pixel mean; prefer three scores and calculate D5 live')
        scores = scores[:3] + [result['pixel_summary']['mean']]
        normalized = [x / sum(weights) for x in weights]
        combined = sum(w * score for w, score in zip(normalized, scores))
        result['composite'] = {'version': '四项加权v2.1', 'order': ['D1', 'D2', 'D4', 'D5_pixel'], 'normalized_weights_percent': [x * 100 for x in normalized], 'component_scores': scores, 'component_definitions': ['beat recurrence', 'expressive action count recurrence', 'verified A semantic text union coverage', 'sampled RGB pixel closeness'], 'score': combined, 'internal_review_threshold': 60, 'exceeds_threshold': combined > 60, 'limitation': 'Internal review score, not statistically validated plagiarism probability. D5 alone does not use the 60 percent threshold. Record each denominator in report.'}
    if args.save_config:
        saved = {field: str(getattr(args, field).resolve()) for field in ('a_frames', 'b_frames', 'output')}
        saved.update({'weights': args.weights, 'component_scores': ','.join(args.component_scores.split(',')[:3]) if args.component_scores else None, 'interval_a': args.interval_a, 'interval_b': args.interval_b, 'formula_version': '四项加权v2.1'})
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    result['sampling_interval_seconds'] = {'a': args.interval_a, 'b': args.interval_b}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('a_count', 'b_count', 'comparison_count', 'summary', 'pixel_summary', 'best_candidate')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
