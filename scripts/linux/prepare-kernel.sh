#!/usr/bin/env bash
# Prepare a pinned kernel in a fresh, case-sensitive Linux build directory.
# Does not contact or modify the phone.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(realpath -m -- "${1:?Usage: prepare-kernel.sh /path/to/fresh/linux-build-dir [profile]}")
profile=${2:-default}
[[ "$profile" =~ ^[a-z0-9-]+$ ]] || { printf 'Invalid build profile.\n' >&2; exit 1; }
kernel_dir="$build_dir/linux"
output_dir="$build_dir/out"
bringup_repo="$project_dir/sources/bringup"
bringup_dir="$build_dir/bringup"

for tool in git python3 make clang ld.lld flex bison gcc; do
  command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done
[[ ! -e "$kernel_dir" ]] || { printf 'Source directory already exists: %s; choose a new build directory.\n' "$kernel_dir" >&2; exit 1; }
[[ ! -e "$bringup_dir" ]] || { printf 'Bring-up directory already exists; choose a new build directory.\n' >&2; exit 1; }
mkdir -p -- "$build_dir"
case_probe=$(mktemp -d "$build_dir/.case-probe.XXXXXX")
touch "$case_probe/lower"
if [[ -e "$case_probe/LOWER" ]]; then
  printf 'Build directory is case-insensitive; use a Linux ext4 directory.\n' >&2
  rm -- "$case_probe/lower"
  rmdir -- "$case_probe"
  exit 1
fi
rm -- "$case_probe/lower"
rmdir -- "$case_probe"

mapfile -t pinned < <(python3 - "$project_dir/sources.lock.json" "$profile" <<'PY'
import json, sys
lock = json.load(open(sys.argv[1], encoding='utf-8'))
r = {x['id']: x for x in lock['repositories']}
selected = {} if sys.argv[2] == 'default' else lock['kernel_profiles'][sys.argv[2]]
print(r['linux-sm8250']['commit'])
print(r['kebab-bringup']['commit'])
print(selected.get('config', 'config/debian13.config'))
PY
)
[[ ${#pinned[@]} -eq 3 ]] || { printf 'Invalid source lock/profile.\n' >&2; exit 1; }
base_sha=${pinned[0]}
bringup_sha=${pinned[1]}
config_fragment=${pinned[2]}
[[ "$config_fragment" =~ ^config/[A-Za-z0-9._-]+$ && -f "$project_dir/$config_fragment" ]] || { printf 'Invalid config fragment.\n' >&2; exit 1; }
[[ $(git -C "$project_dir/sources/linux" rev-parse HEAD) == "$base_sha" ]] || { printf 'Kernel source pin mismatch.\n' >&2; exit 1; }
[[ $(git -C "$bringup_repo" rev-parse HEAD) == "$bringup_sha" ]] || { printf 'Bring-up source pin mismatch.\n' >&2; exit 1; }
# Consume only fixed Git objects. Existing source working-tree edits are
# untouched and are not silently mixed into this reproducible build.
git clone --no-hardlinks --no-checkout -c core.autocrlf=false "$bringup_repo" "$bringup_dir"
git -C "$bringup_dir" checkout --detach "$bringup_sha"

# Check the source files against the fixed aport; avoid Windows CRLF damage.
python3 - "$bringup_dir/kernel" <<'PY'
import hashlib, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
data = (root / 'APKBUILD').read_text()
hashes = re.findall(r'^([a-f0-9]{128})  (.+)$', data, re.M)
checked = 0
for wanted, name in hashes:
    file = root / name
    if name.endswith('.tar.gz'):
        continue  # the checked-out Git objects supply the base kernel
    if not file.is_file() or hashlib.sha512(file.read_bytes()).hexdigest() != wanted:
        raise SystemExit(f'Source checksum mismatch: {name}')
    checked += 1
if checked != 23:
    raise SystemExit(f'Unexpected number of checked source files: {checked}')
print(f'Verified {checked} community source checksums')
PY

# Clone Git objects, never the case-collided Windows working files.
git clone --no-hardlinks --no-checkout -c core.autocrlf=false \
  "$project_dir/sources/linux" "$kernel_dir"
git -C "$kernel_dir" switch -c port/kebab-7.2-debian13 "$base_sha"
git -C "$kernel_dir" tag baseline-sm8250-7.2.0 "$base_sha"
mapfile -t patches < <(find "$bringup_dir/kernel" -maxdepth 1 -type f -name '0*.patch' | LC_ALL=C sort)
[[ ${#patches[@]} -eq 21 ]] || { printf 'Expected 21 patches, found %s.\n' "${#patches[@]}" >&2; exit 1; }
git -C "$kernel_dir" -c user.name=Codex -c user.email=codex@localhost am "${patches[@]}"
cp -- "$bringup_dir/kernel/sm8250-oneplus-kebab.dts" \
  "$kernel_dir/arch/arm64/boot/dts/qcom/sm8250-oneplus-kebab.dts"
git -C "$kernel_dir" add arch/arm64/boot/dts/qcom/sm8250-oneplus-kebab.dts
git -C "$kernel_dir" -c user.name=Codex -c user.email=codex@localhost \
  commit -m 'arm64: dts: import pinned kebab bring-up board description'

# Apply only project-local patches whose files and preimage are locked.
python3 - "$project_dir" "$kernel_dir" "$profile" <<'PY'
import hashlib, json, pathlib, subprocess, sys
project, kernel = map(pathlib.Path, sys.argv[1:3])
lock = json.loads((project/'sources.lock.json').read_text())
entries = lock.get('local_kernel_patches', []) if sys.argv[3] == 'default' else lock['kernel_profiles'][sys.argv[3]]['local_patches']
for entry in entries:
    patch = (project/entry['file']).resolve()
    source = (kernel/entry['input_driver']).resolve()
    if not patch.is_relative_to(project.resolve()) or not source.is_relative_to(kernel.resolve()):
        raise SystemExit('Local patch path escapes its repository')
    if hashlib.sha256(patch.read_bytes()).hexdigest() != entry['sha256']:
        raise SystemExit('Local patch checksum mismatch')
    if hashlib.sha256(source.read_bytes()).hexdigest() != entry['input_driver_sha256']:
        raise SystemExit('Local patch preimage drifted; review upstream overlap first')
    subprocess.run(['git', '-C', str(kernel), '-c', 'user.name=Codex',
                    '-c', 'user.email=codex@localhost', 'am', str(patch)], check=True)
PY

mkdir -p -- "$output_dir"
cp -- "$bringup_dir/kernel/config-postmarketos-qcom-sm8250.aarch64" "$output_dir/.config"
make -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 olddefconfig
cd -- "$build_dir"
"$kernel_dir/scripts/kconfig/merge_config.sh" -m -O "$output_dir" \
  "$output_dir/.config" "$project_dir/$config_fragment"
make -C "$kernel_dir" O="$output_dir" ARCH=arm64 LLVM=1 olddefconfig
if grep -Eq '^CONFIG_(VIDEO_QCOM_VENUS|SND_SOC_TFA9872)=(y|m)$' "$output_dir/.config"; then
  printf 'Unsafe/conflicting driver enabled in final config.\n' >&2
  exit 1
fi

python3 - "$project_dir" "$build_dir" "$base_sha" "$bringup_sha" "$profile" <<'PY'
import datetime, hashlib, json, pathlib, subprocess, sys
project, build, base, bringup, profile = sys.argv[1:]
build = pathlib.Path(build)
lock = json.load(open(pathlib.Path(project)/'sources.lock.json'))
entries = lock.get('local_kernel_patches', []) if profile == 'default' else lock['kernel_profiles'][profile]['local_patches']
def git(*args):
    return subprocess.check_output(['git', '-C', str(build/'linux'), *args], text=True).strip()
data = {
    'prepared_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'project': project, 'base_commit': base, 'bringup_commit': bringup,
    'kernel_commit': git('rev-parse', 'HEAD'), 'community_patches': 21,
    'local_patches': entries, 'build_profile': profile,
    'config_sha256': hashlib.sha256((build/'out/.config').read_bytes()).hexdigest(),
    'target': 'KB2000 / Debian 13 arm64; hardware not yet validated',
    'clang': subprocess.check_output(['clang', '--version'], text=True).splitlines()[0],
    'phase': 'source_and_config_prepared',
}
(build/'build-info.json').write_text(json.dumps(data, indent=2)+'\n')
print('Prepared:', build)
PY
