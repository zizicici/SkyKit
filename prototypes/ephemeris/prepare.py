#!/usr/bin/env python3
"""Download pinned inputs, crop original DE440 coefficients, build the native prototype."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import urllib.request

import numpy as np
from jplephem.spk import SPK

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('SKYKIT_CACHE', ROOT / '.cache')).resolve()
INPUTS = {
    'de440s.bsp': ('https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp',
                    'c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2'),
    'erfa.tar.gz': ('https://codeload.github.com/liberfa/erfa/tar.gz/refs/tags/v2.0.1',
                    'd5469fbd0b212b3c7270c1da15c9bd82f37da9218fc89627f98283d27b416cbf'),
    'TDBtimes.txt': ('https://raw.githubusercontent.com/ytliu0/ChineseCalendar/d6aae82b63b79a6f8659ea3e064024b7d8ac3077/src/TDBtimes.txt',
                     '38f5d14b6c55a25d0c9530edaba583372b22559716d8af94163da3e37486cc81'),
}
NATIVE_SOURCES = ['native/lunar.cpp', 'native/lunar.h', 'native/benchmark.cpp', 'prepare.py']


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download(name):
    url, digest = INPUTS[name]
    path = CACHE / name
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.download')
        print('Downloading', name, flush=True)
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open('wb') as out:
            shutil.copyfileobj(source, out)
        if sha256(temporary) != digest:
            raise RuntimeError(f'Unexpected SHA256: {name}')
        temporary.replace(path)
    if sha256(path) != digest:
        raise RuntimeError(f'Unexpected SHA256: {name}')
    return path


def crop():
    # 2000-01-01 00:00 TDB <= t < 2051-01-01 00:00 TDB.
    # Keep a 2-day halo for solar/lunar light time at both boundaries.
    start, end = -0.5, 18627.5
    kernel = SPK.open(str(CACHE / 'de440s.bsp'))
    output = CACHE / 'moon-2000-2050.bin'
    manifest = dict(format='LUNAR001 little endian', startTDBDaysSinceJ2000=start,
                    endTDBDaysSinceJ2000Exclusive=end, segments=[])
    with output.open('wb') as out:
        out.write(struct.pack('<8sIdd', b'LUNAR001', 4, start, end))
        for center, body in [(0, 3), (0, 10), (3, 399), (3, 301)]:
            segment = kernel[center, body]
            assert segment.data_type == 2
            epoch, interval, coefficients = segment.load_array()
            epoch -= 2451545.0
            first = math.floor((start - 2 - epoch) / interval)
            last = math.ceil((end + 2 - epoch) / interval)
            assert 0 <= first < last <= coefficients.shape[1]
            # [axis, record, degree] -> [record, axis, degree], without fitting,
            # quantization, truncation of polynomial terms, or compression loss.
            data = np.asarray(coefficients[:, first:last, :].transpose(1, 0, 2), dtype='<f8')
            count, _, terms = data.shape
            out.write(struct.pack('<IIddII', center, body, epoch + first * interval,
                                  interval, count, terms))
            out.write(data.tobytes())
            manifest['segments'].append(dict(center=center, body=body, records=count,
                                            terms=terms, bytes=data.nbytes))
    kernel.close()
    manifest.update(bytes=output.stat().st_size, sha256=sha256(output))
    (CACHE / 'kernel-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def erfa_sources():
    root = CACHE / 'erfa-2.0.1'
    # Never trust a previously extracted (possibly edited/incomplete) directory.
    # The archive is pinned and verified; restore its original files every build.
    with tarfile.open(download('erfa.tar.gz')) as archive:
        archive.extractall(CACHE, filter='data')
    source = root / 'src'
    required = set()

    def visit(name):
        if name in required:
            return
        path = source / (name + '.c')
        if not path.exists():
            raise RuntimeError(f'Missing ERFA function: {name}')
        required.add(name)
        for symbol in re.findall(r'\bera([A-Z]\w*)\s*\(', path.read_text()):
            visit(symbol.lower())

    for name in ['ecm06', 'xys06a', 'c2ixys', 'c2tcio', 'era00', 'pom00', 'sp00', 'gd2gc', 'ab', 'ld']:
        visit(name)
    return source, [source / (name + '.c') for name in sorted(required)]


def build(apple_sdk=None):
    include, sources = erfa_sources()
    folder = CACHE / (apple_sdk or 'macos')
    folder.mkdir(exist_ok=True)
    for old in [*folder.glob('*.o'), folder / 'liblunar.a']:
        old.unlink(missing_ok=True)
    sdk = apple_sdk or 'macosx'
    sysroot = subprocess.check_output(['xcrun', '--sdk', sdk, '--show-sdk-path'], text=True).strip()
    flags = ['-O3', '-fPIC', '-I', str(include), '-isysroot', sysroot]
    if apple_sdk:
        target = 'arm64-apple-ios15.0' + ('-simulator' if sdk == 'iphonesimulator' else '')
        flags += ['-target', target]
    objects = []
    for path in sources + [ROOT / 'native' / 'lunar.cpp']:
        obj = folder / (path.stem + '.o')
        compiler = 'clang++' if path.suffix == '.cpp' else 'clang'
        options = ['-std=c++17'] if compiler == 'clang++' else ['-std=c99']
        subprocess.run(['xcrun', '--sdk', sdk, compiler, *flags, *options, '-c', str(path), '-o', str(obj)], check=True)
        objects.append(str(obj))
    subprocess.run(['xcrun', 'ar', 'rcs', str(folder / 'liblunar.a'), *objects], check=True)
    if not apple_sdk:
        subprocess.run(['xcrun', 'clang++', '-dynamiclib', *flags, *objects, '-o', str(folder / 'liblunar.dylib')], check=True)
    if apple_sdk != 'iphoneos':
        subprocess.run(['xcrun', '--sdk', sdk, 'clang++', '-std=c++17', *flags,
                        str(ROOT / 'native' / 'benchmark.cpp'), *objects, '-o', str(folder / 'benchmark')], check=True)
    inputs = sources + list(include.glob('*.h'))
    outputs = [Path(obj) for obj in objects] + [folder / 'liblunar.a']
    if not apple_sdk:
        outputs.append(folder / 'liblunar.dylib')
    if apple_sdk != 'iphoneos':
        outputs.append(folder / 'benchmark')
    return dict(sdk=sdk, erfaSourceFiles=len(sources), archiveBytes=(folder / 'liblunar.a').stat().st_size,
                nativeSourceSHA256={name: sha256(ROOT / name) for name in NATIVE_SOURCES},
                erfaSourceSHA256={str(path.relative_to(CACHE)): sha256(path) for path in inputs},
                outputSHA256={str(path.relative_to(CACHE)): sha256(path) for path in outputs})


def verify_build(manifest, simulator=False):
    """Reject stale binaries, modified references, and mixed build generations."""
    for name, (_, digest) in INPUTS.items():
        if sha256(CACHE / name) != digest:
            raise RuntimeError(f'Input changed: {name}; run prepare.py')
    if sha256(CACHE / 'moon-2000-2050.bin') != manifest['kernel']['sha256']:
        raise RuntimeError('Cropped kernel changed; run prepare.py')
    required = {'macosx'} | ({'iphonesimulator'} if simulator else set())
    builds = {entry['sdk']: entry for entry in manifest['builds']}
    if required - builds.keys():
        raise RuntimeError(f'Missing {sorted(required - builds.keys())} build; run prepare.py --apple-check')
    # Every build included in the published report must match, even if only the
    # host library is executed in this invocation.
    for sdk in builds:
        build = builds[sdk]
        if set(build.get('nativeSourceSHA256', {})) != set(NATIVE_SOURCES):
            raise RuntimeError('Build lacks source provenance; run prepare.py')
        for field, base in [('nativeSourceSHA256', ROOT), ('erfaSourceSHA256', CACHE), ('outputSHA256', CACHE)]:
            if not build.get(field):
                raise RuntimeError(f'Build lacks {field}; run prepare.py')
            for name, digest in build[field].items():
                if sha256(base / name) != digest:
                    raise RuntimeError(f'Stale or changed {sdk} artifact: {name}; run prepare.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apple-check', action='store_true', help='Also compile iOS arm64 and simulator targets')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    # A failed rebuild must not leave an apparently valid old manifest.
    (CACHE / 'build-manifest.json').unlink(missing_ok=True)
    for name in INPUTS:
        download(name)
    manifest = crop()
    builds = [build()]
    if args.apple_check:
        builds += [build('iphoneos'), build('iphonesimulator')]
    result = dict(kernel=manifest, builds=builds, inputs=INPUTS)
    (CACHE / 'build-manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
