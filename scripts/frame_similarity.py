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
    if args.component_scores is not None or args.weights is not None or config.get('component_scores') is not None or config.get('weights') is not None:
        parser.error('Old composite interface retired: pixel scores are screening only. Migrate content aggregation to content-v3 (structure/actions/semantic text 37.5/37.5/25), and save a screening-only config without weights/component_scores.')
    for field in ('a_frames', 'b_frames', 'output', 'weights', 'component_scores', 'interval_a', 'interval_b'):
        if getattr(args, field) is None and field in config:
            setattr(args, field, Path(config[field]) if field in ('a_frames', 'b_frames', 'output') else config[field])
    if not all((args.a_frames, args.b_frames, args.output)):
        parser.error('Provide A/B frame directories and output, or a saved config')
    if any(x is not None and x <= 0 for x in (args.interval_a, args.interval_b)):
        parser.error('Sampling intervals must be positive seconds')
    if args.output.resolve().parent in {args.a_frames.resolve(), args.b_frames.resolve()}:
        parser.error('Save derived output separately from source frame directories')
    result = compare(features(args.a_frames), features(args.b_frames))
    if args.save_config:
        saved = {field: str(getattr(args, field).resolve()) for field in ('a_frames', 'b_frames', 'output')}
        saved.update({'interval_a': args.interval_a, 'interval_b': args.interval_b, 'method_version': 'material-screening-v1'})
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    result['sampling_interval_seconds'] = {'a': args.interval_a, 'b': args.interval_b}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('a_count', 'b_count', 'comparison_count', 'summary', 'pixel_summary', 'best_candidate')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
