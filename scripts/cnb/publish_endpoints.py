"""Publish legacy discovery files so existing clients follow the shared workspace."""
import json, os, pathlib, subprocess

BASE = pathlib.Path('/srv/kebab-strelay/private')

def publish(prefix, shared):
    author = json.loads((BASE / 'endpoint-author.json').read_text())
    env = dict(os.environ, GIT_ASKPASS=str(BASE / 'git-askpass.py'), GIT_TERMINAL_PROMPT='0')
    def git(folder, *args):
        p = subprocess.run(['git', '-C', str(folder), *args], env=env, capture_output=True, timeout=45)
        if p.returncode:
            raise RuntimeError('Endpoint repository synchronization failed')
        return p.stdout
    root = BASE / 'endpoint-repos'
    root.mkdir(mode=0o700, exist_ok=True)
    targets = [
        ('zc354/mmmmcccccbuild', 'endpoint=%s-9010.cnb.run:443/%s\n' % (prefix, shared['mc_token'])),
        ('zc354/cnb-mcp', 'endpoint=%s-9000.cnb.run:443\nmcp=https://%s-9000.cnb.run/mcp\nbase=https://%s-9000.cnb.run/\npublic=https://%s/\npublic_mcp=https://%s/mcp\n' %
         (prefix, prefix, prefix, shared['cf_hostname'], shared['cf_hostname']))
    ]
    for repo, text in targets:
        folder = root / repo.split('/')[-1]
        if not (folder / '.git').exists():
            p = subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'main',
                                'https://cnb.cool/' + repo + '.git', str(folder)],
                               env=env, capture_output=True, timeout=60)
            if p.returncode:
                raise RuntimeError('Endpoint repository clone failed')
        git(folder, 'pull', '--ff-only', '--quiet')
        endpoint = folder / 'endpoint.txt'
        if endpoint.read_text() != text:
            endpoint.write_text(text)
            git(folder, 'add', 'endpoint.txt')
            git(folder, '-c', 'user.name=' + author['name'], '-c', 'user.email=' + author['email'],
                '-c', 'commit.gpgsign=false', 'commit', '-m', 'Follow shared CNB relay endpoint')
        # A previous push can fail after a successful local commit. Retry publication
        # even when the local endpoint already matches, and trust the remote value.
        if git(folder, 'show', 'origin/main:endpoint.txt').decode() != text:
            git(folder, 'push', 'origin', 'HEAD:main')
        git(folder, 'fetch', 'origin', 'main')
        if git(folder, 'show', 'origin/main:endpoint.txt').decode() != text:
            raise RuntimeError('Endpoint publication not confirmed')
