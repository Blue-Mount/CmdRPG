"""Managed launch/update policy. Invoked by the small stable installation shim."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.updater import UpdateError, checked_root, current, installation_lock, update


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--new', action='store_true')
    parser.add_argument('--run')
    parser.add_argument('--change-token', action='store_true')
    parser.add_argument('--check', action='store_true', help='检查更新，不连接游戏')
    args = parser.parse_args(argv)
    try:
        root = checked_root(args.root)
        with installation_lock(root):
            settings = json.loads((root / 'installation.json').read_text('utf-8'))
            # Keep existing proxy bypasses and TLS verification.
            from urllib.parse import urlsplit
            hosts = [urlsplit(settings[key]).hostname for key in ('url', 'updates')]
            entries = [p for key in ('NO_PROXY', 'no_proxy')
                       for p in os.environ.get(key, '').split(',') if p]
            bypass = ','.join(dict.fromkeys(entries + [h for h in hosts if h]))
            for key in ('NO_PROXY', 'no_proxy'):
                os.environ[key] = bypass
            path, manifest = update(root, settings['updates'])
            if args.check:
                print('当前客户端版本 %s' % manifest['sequence'])
                return 0
            os.environ['CMDRPG_CLIENT_VERSION'] = str(manifest['sequence'])
            os.environ['PYTHONUTF8'] = '1'
            if os.name != 'nt':
                raise UpdateError('一键凭据入口需要 Windows；其他系统请使用 python -m client')
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                       '-File', str(path / 'runtime/Start-Remote.ps1'),
                       '-Root', str(root), '-Python', sys.executable]
            if args.new:
                command += ['-New']
            if args.run:
                command += ['-Run', args.run]
            if args.change_token:
                command += ['-ChangeToken']
            # Keep the session lock until the actual game process exits.
            powershell_environment = dict(os.environ)
            # PowerShell 7 may export paths incompatible with Windows PowerShell.
            # Let the credential shell rebuild its own module search paths.
            powershell_environment.pop('PSModulePath', None)
            return subprocess.call(command, cwd=path, env=powershell_environment)
    except (OSError, ValueError, KeyError, UpdateError) as exc:
        print('启动失败：%s' % exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
