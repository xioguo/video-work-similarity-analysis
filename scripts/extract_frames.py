#!/usr/bin/env python3
"""Extract local video frames and a hashed sampling manifest. Never downloads media."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def ffmpeg_path(explicit=None):
    if explicit:
        return str(explicit)
    located = shutil.which('ffmpeg')
    if located:
        return located
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise ValueError('需要 ffmpeg 或 imageio-ffmpeg；缺少能力应在案件中记录 blocked') from exc


def extract(video, output, interval=5, start=0, end=None, ffmpeg=None):
    from PIL import Image, ImageDraw
    video, output = Path(video).resolve(), Path(output).resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError('本地视频缺失或为空')
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('采样间隔必须是有限正数')
    binary = ffmpeg_path(ffmpeg)
    probe = subprocess.run([binary, '-hide_banner', '-i', str(video)],
                           capture_output=True, text=True)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', probe.stderr)
    if not match:
        raise ValueError('无法读取视频时长；不要猜测帧数或覆盖范围')
    duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    stop = duration if end is None else min(end, duration)
    if not all(math.isfinite(x) for x in (start, stop)) or not 0 <= start < stop:
        raise ValueError('无效抽帧区间')
    if output.exists() and any(output.iterdir()):
        raise ValueError('输出目录非空；复用已核验清单或使用新的采样目录，避免混入旧帧')
    output.mkdir(parents=True, exist_ok=True)
    frame_dir = output / 'frames'
    frame_dir.mkdir()
    samples = []
    for i in range(math.ceil((stop - start) / interval)):
        target = start + i * interval
        image = frame_dir / ('f%06d.jpg' % i)
        command = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-ss', '%.6f' % target, '-i', str(video), '-frames:v', '1',
                   '-vf', 'scale=640:-2', '-q:v', '2', str(image)]
        subprocess.run(command, check=True, capture_output=True)
        if not image.is_file() or image.stat().st_size == 0:
            raise ValueError('未能提取目标时点；本轮采样不完整')
        samples.append({'file': str(image.relative_to(output)),
                        'target_seconds': target, 'sha256': digest(image)})
    # Paginate the entire sample set; one 8-frame sheet cannot represent a full video.
    sheets = []
    for offset in range(0, len(samples), 8):
        sheet = Image.new('RGB', (1280, 460), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(samples[offset:offset + 8]):
            with Image.open(output / item['file']) as source:
                source.thumbnail((320, 200))
                x, y = (i % 4) * 320, (i // 4) * 230
                sheet.paste(source, (x, y + 25))
                draw.text((x + 8, y + 5), '~%.2fs' % item['target_seconds'], fill='black')
        path = output / ('grid-%03d.jpg' % (offset // 8 + 1))
        sheet.save(path, quality=88)
        sheets.append(path.name)
    manifest = {'schema_version': 'frames-v1', 'source_video': str(video),
                'source_sha256': digest(video), 'duration_seconds': duration,
                'interval_seconds': interval, 'start_seconds': start,
                'end_seconds': stop,
                'time_kind': 'seek_target_approximate',
                'time_note': 'target_seconds 是定位请求时点，不保证为解码帧的精确显示时间',
                'sampling_scope': 'full' if start == 0 and stop == duration else 'window',
                'frames': samples, 'contact_sheets': sheets}
    (output / '抽帧清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--interval', type=float, default=5)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--end', type=float)
    p.add_argument('--ffmpeg')
    args = p.parse_args()
    result = extract(args.video, args.output, args.interval, args.start, args.end, args.ffmpeg)
    print(json.dumps({'frames': len(result['frames']), 'scope': result['sampling_scope'],
                      'manifest': str(args.output / '抽帧清单.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
