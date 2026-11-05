#!/usr/bin/env python3
"""Prepare an isolated Venus kernel build, never modify the running device."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_venus_dt(source, baseline):
    """Record the two reviewed upstream DT corrections in this isolated clone."""
    parent = '83d0c460b621f7effe0a400f32d261c4f18335f2'
    source, baseline = source.resolve(strict=True), baseline.resolve(strict=True)
    if source.is_relative_to(baseline) or baseline.is_relative_to(source):
        raise SystemExit('Never apply candidate DT changes to the baseline')
    if run('git', '-C', str(source), 'rev-parse', 'HEAD') != parent or run('git', '-C', str(source), 'status', '--porcelain'):
        raise SystemExit('DT correction requires the clean exact r3 source')
    patch = Path(__file__).resolve().parents[2]/'patches/local/0008-arm64-dts-sm8250-venus-power-opp.patch'
    subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '--', 'arch/arm64/boot/dts/qcom/sm8250.dtsi'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Codex', '-c', 'user.email=codex@local.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-m',
                    'arm64: dts: sm8250: adapt upstream Venus OPP and power-domain corrections'], check=True)
    return {'parent_kernel_commit':parent,
            'kernel_commit':run('git', '-C', str(source), 'rev-parse', 'HEAD'),
            'dt_correction':{'file':'patches/local/'+patch.name, 'sha256':sha(patch),
                             'upstream_frequency_commit':'68ea007df9293fcb29d38219d73094bbf4b59673',
                             'upstream_power_domain_patch_url':'https://lore.kernel.org/r/20260604-iris-venus-fix-sm8250-v7-1-7bd2f0e5bae8@oss.qualcomm.com'}}


def apply_touch_fix(source, baseline):
    """Integrate the already-deployed r3 parser fix without its module rename."""
    source, baseline = source.resolve(strict=True), baseline.resolve(strict=True)
    if source.is_relative_to(baseline) or baseline.is_relative_to(source):
        raise SystemExit('Never apply candidate touch changes to the baseline')
    if run('git', '-C', str(source), 'status', '--porcelain'):
        raise SystemExit('Touch integration requires a clean isolated source')
    header = source/'drivers/input/touchscreen/synaptics_tcm_report.h'
    driver = source/'drivers/input/touchscreen/synaptics_tcm_oncell.c'
    before = '41038df820619fc05a11b35f9cc79afebe7fffd9098ffef9b94cea4fbb9de8d7'
    after = '4c1141de2444e2a6f0674e92dba984fe5ea2bce27d42d00c812c71569bcb0fb6'
    if sha(header) != before or sha(driver) != '4b3431a82fc977c40edfe8ebe0d526b2c4c9486cf30cdd563340029e52e40763':
        raise SystemExit('Unexpected touch parser/driver baseline')
    patch = Path(__file__).resolve().parents[2]/'patches/local/0009-input-synaptics-tcm-padding-tail.patch'
    subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)
    if sha(header) != after:
        raise SystemExit('Touch parser differs from the deployed fixed header')
    subprocess.run(['git', '-C', str(source), 'add', '--', 'drivers/input/touchscreen/synaptics_tcm_report.h'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Codex', '-c', 'user.email=codex@local.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-m',
                    'input: synaptics-tcm: integrate the deployed whole-slot padding-tail fix'], check=True)
    return {'kernel_commit':run('git', '-C', str(source), 'rev-parse', 'HEAD'),
            'touch_correction':{'file':'patches/local/'+patch.name, 'sha256':sha(patch),
                                'parser_header_sha256':after, 'driver_name_unchanged':True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline.resolve(strict=True)
    destination = args.destination.resolve(strict=False)
    if (destination.is_relative_to(baseline) or baseline.is_relative_to(destination)):
        raise SystemExit('Destination must be separate from the preserved baseline')
    if destination.exists():
        raise SystemExit('Destination already exists; preserve it and choose a new path')
    source, output = baseline / 'linux', baseline / 'out'
    expected_commit = '83d0c460b621f7effe0a400f32d261c4f18335f2'
    expected_config = 'e63bfb240b4f9ed8e9db4c68b07280acfb594bd0aefef1ee57a378520ad922fd'
    expected_symvers = '33820e5fd21e7d0de9834eea5a89c868d78d834c17803d8e34ee555c7a412661'
    if run('git', '-C', str(source), 'rev-parse', 'HEAD') != expected_commit:
        raise SystemExit('Baseline commit mismatch')
    if run('git', '-C', str(source), 'status', '--porcelain'):
        raise SystemExit('Baseline source is dirty')
    if sha(output / '.config') != expected_config or sha(output / 'Module.symvers') != expected_symvers:
        raise SystemExit('Baseline configuration or exports mismatch')
    destination.mkdir(parents=True)
    subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(source), str(destination/'linux')], check=True)
    subprocess.run(['git', '-C', str(destination/'linux'), 'checkout', '--detach', expected_commit], check=True)
    # Copy actual files; never use hard links for mutable Kbuild output.
    subprocess.run(['cp', '-a', '--reflink=auto', str(output), str(destination/'out')], check=True)
    for name in ['.config', 'Module.symvers']:
        if (output/name).stat().st_ino == (destination/'out'/name).stat().st_ino:
            raise SystemExit('Unexpected hard-linked output')
    newsource, newout = destination/'linux', destination/'out'
    subprocess.run([str(newsource/'scripts/config'), '--file', str(newout/'.config'),
                    '--set-str', 'LOCALVERSION', '-kebab-debian13-r4-venus',
                    '--module', 'VIDEO_QCOM_VENUS'], check=True)
    subprocess.run(['make', '-s', '-C', str(newsource), 'O='+str(newout),
                    'ARCH=arm64', 'LLVM=1', 'olddefconfig'], check=True)
    expected_candidate = 'e2543ac04e3ca5ffeabce188a313e4d72f4ae985bc0cfd3732dd12cb91ec4e12'
    if sha(newout/'.config') != expected_candidate:
        raise SystemExit('Unexpected configuration closure; inspect this preserved candidate before building')
    delta = run(str(newsource/'scripts/diffconfig'), str(output/'.config'), str(newout/'.config'))
    (destination/'config-delta.txt').write_text(delta+'\n')
    print(delta)
    info = json.loads((baseline/'build-info.json').read_text())
    correction = apply_venus_dt(newsource, baseline)
    info.update({key:value for key,value in correction.items() if key != 'dt_correction'})
    touch = apply_touch_fix(newsource, baseline)
    info['kernel_commit'] = touch['kernel_commit']
    info['candidate_additional_patches'] = [*info.get('candidate_additional_patches', []),
                                           correction['dt_correction'], touch['touch_correction']]
    info.update(build_profile='r4-venus-candidate', phase='source_and_config_prepared',
                candidate_prepared_at_utc=datetime.now(timezone.utc).isoformat(),
                config_sha256=sha(newout/'.config'),
                parent_config_sha256=expected_config,
                parent_module_symvers_sha256=expected_symvers,
                baseline_directory=str(baseline),
                firmware_note='DT override postmarketos/venus.mbn requires alias to qcom/vpu/vpu20_p4.mbn; not deployed',
                target='KB2000 / Debian 13 arm64; Venus candidate; hardware not yet validated')
    (destination/'build-info.json').write_text(json.dumps(info, indent=2)+'\n')
    if (sha(output/'.config') != expected_config or sha(output/'Module.symvers') != expected_symvers or
            run('git', '-C', str(source), 'rev-parse', 'HEAD') != expected_commit or
            run('git', '-C', str(source), 'status', '--porcelain')):
        raise SystemExit('Baseline unexpectedly changed')
    print(json.dumps({'destination':str(destination), 'baseline_identity_unchanged':True,
                      'candidate_config_sha256':info['config_sha256']}))


if __name__ == '__main__':
    main()
