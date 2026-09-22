#!/usr/bin/env python3
"""Reproduce simulator timings against vendored AE and the original Go binary."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

from prepare import CACHE, ROOT, sha256, verify_build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulator', required=True, help='Booted arm64 iOS simulator UDID')
    parser.add_argument('--output', type=Path, default=ROOT / 'library-benchmark-results.json')
    args = parser.parse_args()
    verify_build(json.loads((CACHE / 'build-manifest.json').read_text()), simulator=True)
    repo = ROOT.parents[1]
    work = CACHE / 'library-benchmark'
    work.mkdir(exist_ok=True)
    # Fixed historical implementation; do not restore deleted files in the worktree.
    revision = subprocess.check_output(['git', 'rev-parse', '98aa6b8'], cwd=repo, text=True).strip()
    archive = work / 'legacy.tar'
    with archive.open('wb') as out:
        subprocess.run(['git', 'archive', revision, 'moontake/Mooninfo.xcframework'], cwd=repo, stdout=out, check=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(work, filter='data')
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path'], text=True).strip()
    flags = ['-O3', '-target', 'arm64-apple-ios15.0-simulator', '-isysroot', sdk]
    ae = work / 'astronomy-reference'
    (ae/'include').mkdir(parents=True, exist_ok=True)
    # Benchmark-only dependency: never bundled or linked into the app.
    for name, digest in [('astronomy.c', '388920479d819713963ffd0f8ab0677944f66c6c94239ceddddd3b0d5d064432'),
                         ('include/astronomy.h', '83a31011957c2b87e22f0828820f370f1a495aa4d51357bb57d714cde839dc00')]:
        path = ae/name
        if not path.exists():
            url = 'https://raw.githubusercontent.com/cosinekitty/astronomy/61dc07020aaa6885d2c7f688a4d82beaf6edb9ef/source/c/' + path.name
            with urllib.request.urlopen(url, timeout=60) as response: path.write_bytes(response.read())
        if sha256(path) != digest: raise RuntimeError('Benchmark reference hash mismatch')
    framework = work / 'moontake/Mooninfo.xcframework/ios-arm64_x86_64-simulator'
    subprocess.run(['xcrun', 'clang', *flags, '-I', str(ae/'include'), '-c', str(ae/'astronomy.c'),
                    '-o', str(work/'astronomy.o')], check=True)
    subprocess.run(['xcrun', 'clang++', *flags, '-std=c++17', '-fmodules', '-fcxx-modules',
        '-I', str(ae/'include'), '-F', str(framework), str(ROOT/'library_benchmark.mm'),
        str(work/'astronomy.o'), str(CACHE/'iphonesimulator/liblunar.a'), '-framework', 'Mooninfo',
        '-framework', 'Foundation', '-framework', 'UIKit', '-o', str(work/'compare')], check=True)
    raw = subprocess.check_output(['xcrun', 'simctl', 'spawn', args.simulator, str(work/'compare'),
                                   str(CACHE/'moon-2000-2050.bin')], text=True)
    result = json.loads(raw)
    result.update(generatedAt=datetime.now(timezone.utc).isoformat(), simulator=args.simulator,
        host=platform.platform(), cpu=subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip(),
        legacyGitRevision=revision, astronomyEngineVersion='2.1.19', compileFlags=flags,
        method='Same process, single thread, -O3 C/C++, no LTO; historical Go framework as shipped. 20 warmup calls, 7 batches, rotated library order. 300 event searches or 3000 other calls per batch. Each library receives fresh precomputed time inputs. Median plus all batch values retained.',
        limitations='Different accuracy/model coverage and event tolerances (native 0.1 ms, AE 0.1 s). Excludes time conversion, native kernel load, UI, Swift lock and table lookup; includes original Go bridge/runtime. Simulator on host CPU, not device energy or cold-start benchmark. Go library lacks equivalent direction/phase-angle APIs.',
        datePatterns={'cameraDates':'1000 successive dates 8.64 seconds apart near 2026, repeated for 3000-call batches',
                      'spreadDates':'1000 shuffled dates across approximately 2020-2030, repeated for 3000-call batches'})
    artifacts = {'benchmarkSource':ROOT/'library_benchmark.mm', 'runner':ROOT/'compare_libraries.py',
        'astronomySource':ae/'astronomy.c', 'astronomyHeader':ae/'include/astronomy.h',
        'nativeArchive':CACHE/'iphonesimulator/liblunar.a', 'nativeKernel':CACHE/'moon-2000-2050.bin',
        'legacyArchive':framework/'Mooninfo.framework/Versions/A/Mooninfo', 'benchmarkBinary':work/'compare',
        'nativeBuildManifest':CACHE/'build-manifest.json'}
    result['artifactSHA256'] = {key:sha256(path) for key,path in artifacts.items()}
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    for key, value in result['microsecondsPerCall'].items():
        print(key, value['median'])


if __name__ == '__main__':
    main()
