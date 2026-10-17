"""One-time migration into a NEW player directory; never modifies the legacy tree."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from runtime.updater import (UpdateError, atomic_json, checked_root, digest, canonical,
                             install, installation_lock, release_url, validate_manifest)

# Stable ABI-1 shim: selects a version only. All launch, credentials and update
# policy lives in the managed release. Old releases are never removed while used.
SHIM = '''import json,pathlib,re,subprocess,sys
root=pathlib.Path(__file__).resolve().parent
try:
    release=json.loads((root/'current.json').read_text('utf-8'))['release']
    if not isinstance(release,str) or not re.fullmatch('[0-9a-f]{64}',release):
        raise ValueError('Invalid current release')
    code=subprocess.call([sys.executable,str(root/'versions'/release/'runtime/launcher.py'),'--root',str(root),*sys.argv[1:]],cwd=root)
except Exception as exc:
    print('Cannot start client: %s'%exc)
    code=1
raise SystemExit(code)
'''


def migrate(destination, publication, *, legacy=None, url=None, updates=None,
            run_id=None, python=None):
    if sys.version_info < (3, 11):
        raise UpdateError('Python 3.11+ is required')
    destination = checked_root(destination)
    if destination.exists():
        raise UpdateError('玩家目标目录必须不存在；不会覆盖已有文件')
    legacy = Path(legacy).resolve() if legacy else None
    settings, previous = {}, {}
    if legacy:
        if (legacy / 'remote-launcher.json').exists():
            settings = json.loads((legacy / 'remote-launcher.json').read_text('utf-8-sig'))
        if (legacy / 'data/remote-client.json').exists():
            previous = json.loads((legacy / 'data/remote-client.json').read_text('utf-8-sig')).get('last', {})
            if previous.get('mode') != 'remote':
                raise UpdateError('旧配置不是远程连接，拒绝隐式改变连接模式')
    selected_url = url or previous.get('url') or settings.get('Url')
    if not selected_url:
        raise UpdateError('请指定 --url 或提供已有远程连接配置')
    selected_url = release_url(selected_url)
    if previous.get('url') and previous['url'].rstrip('/') != selected_url:
        raise UpdateError('新地址与旧连接记录不一致；请单独建立安装以免丢失未确认请求')
    updates = release_url(updates or selected_url + '/client-updates')
    publication = Path(publication).resolve()
    channel = json.loads((publication / 'channel.json').read_text('utf-8'))
    manifest = validate_manifest(channel['manifest'])
    release = digest(canonical(manifest))
    source = publication / 'releases' / release
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.client-install-', dir=destination.parent))
    try:
        with installation_lock(stage):
            install(stage, manifest, lambda name, size: (source / name).read_bytes())
            (stage / 'data').mkdir()
            if legacy:
                # Preserve bytes, including the original command IDs and scope.
                # No databases, logs, credentials or user scripts are copied.
                for name in ('remote-client.json', 'remote-client.pending.json'):
                    old = legacy / 'data' / name
                    if old.exists():
                        shutil.copyfile(old, stage / 'data' / name)
            atomic_json(stage / 'installation.json', {
                'schema': 1, 'url': selected_url, 'updates': updates,
                'run_id': previous.get('run_id') or run_id,
            })
            (stage / 'bootstrap.py').write_text(SHIM, encoding='utf-8')
            interpreter = str(Path(python or sys.executable).resolve())
            if not Path(interpreter).is_file() or any(c in interpreter for c in '%\r\n"'):
                raise UpdateError('Python 解释器路径不可用')
            for name, flag in [('Play', ''), ('New-Run', '--new'),
                               ('Change-Token', '--change-token'), ('Check-Update', '--check')]:
                (stage / (name + '.cmd')).write_text(
                    '@echo off\r\n"' + interpreter + '" "%~dp0bootstrap.py" ' + flag + '\r\n'
                    'if errorlevel 1 pause\r\n', encoding='utf-8', newline='')
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--legacy', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--updates')
    parser.add_argument('--run-id')
    parser.add_argument('--python')
    args = parser.parse_args()
    result = migrate(**vars(args))
    print('Installed: %s; double-click Play.cmd' % result)


if __name__ == '__main__':
    main()
