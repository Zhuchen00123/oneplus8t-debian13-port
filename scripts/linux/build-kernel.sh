#!/usr/bin/env bash
# Build only. No fastboot, ADB, SSH, package install or phone deployment.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(realpath -e -- "${1:?Usage: build-kernel.sh build-dir dtb|kernel}")
mode=${2:-dtb}
[[ "$mode" == dtb || "$mode" == kernel ]] || { printf 'Unknown build mode: %s\n' "$mode" >&2; exit 1; }
kernel_dir="$build_dir/linux"
output_dir="$build_dir/out"
[[ -f "$build_dir/build-info.json" ]] || { printf 'Run prepare-kernel.sh first.\n' >&2; exit 1; }
python3 - "$build_dir" <<'PY'
import hashlib, json, pathlib, subprocess, sys
b = pathlib.Path(sys.argv[1]); info = json.loads((b/'build-info.json').read_text())
head = subprocess.check_output(['git', '-C', str(b/'linux'), 'rev-parse', 'HEAD'], text=True).strip()
dirty = subprocess.check_output(['git', '-C', str(b/'linux'), 'status', '--porcelain'], text=True)
if head != info['kernel_commit'] or dirty:
    raise SystemExit('Kernel source changed after preparation; review and record a new build-info first')
if hashlib.sha256((b/'out/.config').read_bytes()).hexdigest() != info['config_sha256']:
    raise SystemExit('Kernel config changed after preparation; review before building')
PY
jobs=${JOBS:-4}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { printf 'JOBS must be a positive integer.\n' >&2; exit 1; }
# kernelrelease is a no-sync target; sync generated auto.conf first so it
# actually sees CONFIG_LOCALVERSION from the merged Debian configuration.
make -s -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 syncconfig
release=$(make -s -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 kernelrelease)
build_id=$(python3 - "$build_dir/build-info.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); print(d['kernel_commit'][:12]+'-'+d['config_sha256'][:12])
PY
)
artifact_dir="$project_dir/artifacts/$release/$build_id"
mkdir -p -- "$artifact_dir"
exec > >(tee "$artifact_dir/$mode-build.log") 2>&1

if [[ "$mode" == dtb ]]; then
  make -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 -j"$jobs" \
    qcom/sm8250-oneplus-kebab.dtb
else
  make -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 -j"$jobs" \
    Image.gz modules qcom/sm8250-oneplus-kebab.dtb
  make -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 \
    INSTALL_MOD_PATH="$output_dir/modules-stage" INSTALL_MOD_STRIP=1 modules_install
  cp -- "$output_dir/arch/arm64/boot/Image" "$output_dir/arch/arm64/boot/Image.gz" "$artifact_dir/"
  tar -C "$output_dir/modules-stage" -czf "$artifact_dir/modules.tar.gz" "lib/modules/$release"
fi
cp -- "$output_dir/arch/arm64/boot/dts/qcom/sm8250-oneplus-kebab.dtb" "$artifact_dir/"
cp -- "$output_dir/.config" "$artifact_dir/kernel.config"
cp -- "$build_dir/build-info.json" "$artifact_dir/"
printf '%s\n' "$release" > "$artifact_dir/kernel.release"
dtc -I dtb -O dts -o "$artifact_dir/compiled.dts" "$artifact_dir/sm8250-oneplus-kebab.dtb"
python3 - "$artifact_dir" "$mode" <<'PY'
import datetime, hashlib, json, pathlib, sys
a = pathlib.Path(sys.argv[1]); mode = sys.argv[2]
files = ['sm8250-oneplus-kebab.dtb', 'kernel.config', 'build-info.json', 'kernel.release', 'compiled.dts']
if mode == 'kernel': files += ['Image', 'Image.gz', 'modules.tar.gz']
hashes = {name: hashlib.sha256((a/name).read_bytes()).hexdigest() for name in files}
(a/(mode+'-manifest.json')).write_text(json.dumps({
    'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'build_mode': mode, 'hardware_validated': False, 'files_sha256': hashes,
}, indent=2)+'\n')
print('Build artifacts:', a)
PY
