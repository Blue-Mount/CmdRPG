"""Build an explicit, immutable remote-client release, then publish atomically.

python -m tools.release_client --sequence 1 --output D:\\CmdRPG-releases
No GitHub credential or server/database input is accepted by this tool.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from runtime.updater import (UpdateError, atomic_json, canonical, digest,
                             installation_lock, safe_path, validate_manifest, verify_tree)

SOURCE = Path(__file__).resolve().parents[1]


def build(source, output, sequence, minimum=0):
    output = Path(output).resolve()
    with installation_lock(output):
        return _build(source, output, sequence, minimum)


def _build(source, output, sequence, minimum):
    source, output = Path(source).resolve(), Path(output).resolve()
    names = json.loads((source / 'release/client-files.json').read_text('utf-8'))
    if not isinstance(names, list) or len(set(names)) != len(names):
        raise UpdateError('发布文件清单必须为不重复的列表')
    files = {}
    for name in names:
        safe_path(name)
        path = source / name
        if path.resolve() != path or path.is_symlink():
            raise UpdateError('拒绝发布链接文件')
        data = path.read_bytes()
        files[name] = {'size': len(data), 'sha256': digest(data)}
    manifest = validate_manifest({'schema': 1, 'sequence': sequence, 'files': files})
    if type(minimum) is not int or not 0 <= minimum <= sequence:
        raise UpdateError('最低版本必须介于 0 和发布版本之间')
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'channel.json').exists():
        previous = json.loads((output / 'channel.json').read_text('utf-8'))
        if sequence <= previous['manifest']['sequence']:
            raise UpdateError('发布序号必须严格递增')
    releases = output / 'releases'
    releases.mkdir(exist_ok=True)
    release = digest(canonical(manifest))
    staging = Path(tempfile.mkdtemp(prefix='.publish-', dir=releases))
    try:
        for name in names:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        (staging / 'manifest.json').write_bytes(canonical(manifest))
        verify_tree(staging, manifest)
        destination = releases / release
        if destination.exists():
            verify_tree(destination, manifest)
        else:
            staging.rename(destination)
        channel = {'schema': 1, 'minimum': minimum, 'manifest': manifest}
        # A portable first-install/migration bundle. It contains an explicit
        # release plus only the standard-library installer, never a repository.
        bundle = output / '.installer.zip.tmp'
        try:
            with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('channel.json', canonical(channel))
                for name in [*names, 'manifest.json']:
                    archive.write(destination / name, 'releases/' + release + '/' + name)
                for name in ('tools/__init__.py', 'tools/install_client.py',
                             'runtime/__init__.py', 'runtime/updater.py'):
                    archive.write(source / name, name)
                archive.writestr('Install-Client.cmd',
                    '@echo off\r\ncd /d "%~dp0"\r\n'
                    'py -3 -m tools.install_client --publication "%~dp0." --legacy "%~1" --destination "%~2"\r\n'
                    'if errorlevel 1 pause\r\n')
                archive.writestr('INSTALL.txt',
                    'Python 3.11+ required. Close the old game first.\n'
                    'Extract this bundle into a temporary folder. Run:\n'
                    'Install-Client.cmd D:\\CmdRPG D:\\CmdRPG-player\n'
                    'Destination must not exist. Old installation is preserved.\n'
                    'If py is unavailable: use your python.exe -m tools.install_client '
                    '--publication . --legacy D:\\CmdRPG --destination D:\\CmdRPG-player\n'
                    'Then open D:\\CmdRPG-player\\Play.cmd.\n')
            bundle.replace(output / 'installer.zip')
        finally:
            bundle.unlink(missing_ok=True)
        atomic_json(output / 'channel.json', channel)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--minimum', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = build(SOURCE, args.output, args.sequence, args.minimum)
    print('Published client %s (%s)' % (args.sequence, digest(canonical(manifest))))


if __name__ == '__main__':
    main()
