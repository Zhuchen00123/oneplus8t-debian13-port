#!/usr/bin/env bash
# Save the complete pinned/patched source tree and config independently of remotes.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(realpath -e -- "${1:?Usage: backup-kernel.sh persistent-build-dir}")
kernel_dir="$build_dir/linux"
[[ -f "$build_dir/build-info.json" ]] || { printf 'Missing build metadata.\n' >&2; exit 1; }
[[ -z $(git -C "$kernel_dir" status --porcelain) ]] || { printf 'Kernel source is dirty; commit or preserve the changes first.\n' >&2; exit 1; }
destination="$project_dir/backups/kernel-$(date +%Y%m%d-%H%M%S-%N)"
mkdir -p -- "$destination/patches"
git -C "$kernel_dir" archive --format=tar HEAD | gzip -1 > "$destination/kernel-source.tar.gz"
gzip -t "$destination/kernel-source.tar.gz"
tar -tzf "$destination/kernel-source.tar.gz" | wc -l > "$destination/archive-entry-count.txt"
git -C "$kernel_dir" format-patch -o "$destination/patches" baseline-sm8250-7.2.0..HEAD >/dev/null
cp -- "$build_dir/build-info.json" "$destination/"
cp -- "$build_dir/out/.config" "$destination/kernel.config"
git -C "$kernel_dir" show-ref > "$destination/git-refs.txt"
python3 - "$destination" "$kernel_dir" <<'PY'
import datetime, hashlib, json, pathlib, subprocess, sys
dest = pathlib.Path(sys.argv[1])
files = {}
for file in sorted(dest.rglob('*')):
    if file.is_file():
        with file.open('rb') as stream:
            files[str(file.relative_to(dest))] = hashlib.file_digest(stream, 'sha256').hexdigest()
data = {
    'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'scope': 'complete committed kernel tree plus config and applied commits as patches; not phone backup',
    'patch_count': len(list((dest/'patches').glob('*.patch'))),
    'git_commit': subprocess.check_output(['git', '-C', sys.argv[2], 'rev-parse', 'HEAD'], text=True).strip(),
    'git_tree': subprocess.check_output(['git', '-C', sys.argv[2], 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
    'files_sha256': files,
}
(dest/'manifest.json').write_text(json.dumps(data, indent=2)+'\n')
print('Verified kernel source backup:', dest)
PY
