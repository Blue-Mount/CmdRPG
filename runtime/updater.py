"""HTTPS release verification and atomic immutable-version installation.

The caller holds InstallationLock for the entire update AND game session.
Only versions/<manifest digest> is written; user state never lives there.
"""
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
REQUIRED = {
    'client/__init__.py', 'client/__main__.py', 'client/cli.py',
    'client/menu.py', 'client/parser.py', 'client/render.py', 'client/transport.py',
    'runtime/__init__.py', 'runtime/updater.py', 'runtime/launcher.py',
    'runtime/Start-Remote.ps1', 'runtime/Remote-Token.ps1',
}


class UpdateError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def integer(value):
    return type(value) is int and 0 <= value <= 2**31 - 1


def safe_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
        raise UpdateError('非法发布文件路径')
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.endswith('.') or
           p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                      *('COM'+str(i) for i in range(10)),
                                      *('LPT'+str(i) for i in range(10))} for p in parts):
        raise UpdateError('非法发布文件路径')
    # Remote distributions have no server, databases, settings, or arbitrary scripts.
    if name not in REQUIRED and not (name.startswith('client/assets/') and
                                    Path(name).suffix.lower() in {'.json', '.txt', '.png'}):
        raise UpdateError('文件不属于客户端发布范围: ' + name)
    return name


def validate_manifest(value):
    if not isinstance(value, dict) or set(value) != {'schema', 'sequence', 'files'}:
        raise UpdateError('不支持的发布清单')
    if value['schema'] != 1 or not integer(value['sequence']) or value['sequence'] < 1:
        raise UpdateError('不支持的发布版本')
    files = value['files']
    if not isinstance(files, dict) or not REQUIRED <= files.keys() or len(files) > 256:
        raise UpdateError('发布缺少必需文件或文件过多')
    seen, total = set(), 0
    for name, spec in files.items():
        safe_path(name)
        if name.lower() in seen:
            raise UpdateError('重复发布路径')
        seen.add(name.lower())
        if (not isinstance(spec, dict) or set(spec) != {'sha256', 'size'} or
                not isinstance(spec['sha256'], str) or
                not re.fullmatch('[0-9a-f]{64}', spec['sha256']) or
                not integer(spec['size']) or spec['size'] > MAX_FILE):
            raise UpdateError('非法文件摘要或大小')
        total += spec['size']
    if total > MAX_TOTAL:
        raise UpdateError('发布过大')
    return value


def checked_root(root):
    root = Path(root).absolute()
    if any((parent / '.git').exists() for parent in (root, *root.parents)):
        raise UpdateError('拒绝更新开发仓库；请选择独立玩家安装目录')
    if root.resolve() != root or root.is_symlink():
        raise UpdateError('玩家目录不能使用符号链接或目录联接')
    for name in ('versions', 'current.json', 'installation.json', '.session.lock'):
        path = root / name
        if path.is_symlink() or (path.exists() and path.resolve() != path.absolute()):
            raise UpdateError('安装路径不能使用符号链接或目录联接')
    return root


@contextmanager
def installation_lock(root):
    root = checked_root(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.session.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError('客户端正在运行或更新；请先关闭另一个窗口') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.commit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_tree(path, manifest):
    validate_manifest(manifest)
    for name, spec in manifest['files'].items():
        target = path / name
        if target.resolve() != target.absolute() or target.is_symlink():
            raise UpdateError('版本文件不能使用链接')
        data = target.read_bytes()
        if len(data) != spec['size'] or digest(data) != spec['sha256']:
            raise UpdateError('文件完整性校验失败: ' + name)


def current(root):
    root = checked_root(root)
    pointer = json.loads((root / 'current.json').read_text('utf-8'))
    release = pointer.get('release')
    if not isinstance(release, str) or not re.fullmatch('[0-9a-f]{64}', release):
        raise UpdateError('当前版本指针损坏')
    path = root / 'versions' / release
    manifest = json.loads((path / 'manifest.json').read_text('utf-8'))
    if digest(canonical(manifest)) != release:
        raise UpdateError('版本清单摘要不匹配')
    verify_tree(path, manifest)
    return path, manifest


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def release_url(url):
    p = urlsplit(url)
    if (p.scheme not in ('https', 'http') or not p.hostname or p.username is not None or
            p.password is not None or p.query or p.fragment or
            (p.scheme == 'http' and p.hostname not in ('127.0.0.1', '::1', 'localhost'))):
        raise UpdateError('发布地址必须为 HTTPS（仅本机测试允许回环 HTTP）')
    return url.rstrip('/')


def fetch(url, limit):
    # No redirects, no bearer token, normal system CA validation. Bound reads
    # also reject truncated, oversized and indefinite-length responses.
    with build_opener(NoRedirect()).open(url, timeout=10) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError('下载超过声明大小')
        return data


def install(root, manifest, read_file):
    """Install a complete release. read_file(name, size) returns bounded bytes."""
    root = checked_root(root)
    validate_manifest(manifest)
    release = digest(canonical(manifest))
    versions = root / 'versions'
    versions.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=versions))
    try:
        for name, spec in manifest['files'].items():
            data = read_file(name, spec['size'])
            if len(data) != spec['size'] or digest(data) != spec['sha256']:
                raise UpdateError('下载完整性校验失败: ' + name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (stage / 'manifest.json').write_bytes(canonical(manifest))
        verify_tree(stage, manifest)
        # Validate Python syntax and standard-library-only remote imports before
        # switching. This never imports rules/storage or touches a database.
        probe = subprocess.run([sys.executable, '-I', '-c',
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "[compile(f.read_bytes(),str(f),'exec') for f in p.rglob('*.py')]; "
            "sys.path.insert(0,str(p)); import client.cli, runtime.launcher", str(stage)],
            capture_output=True, timeout=20)
        if probe.returncode:
            raise UpdateError('下载的客户端无法加载；保留当前版本')
        destination = versions / release
        if destination.exists():
            verify_tree(destination, manifest)
        else:
            stage.rename(destination)
        # The only commit point. A failed replace leaves the old pointer intact.
        atomic_json(root / 'current.json', {'release': release})
        return destination, manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def update(root, base_url, *, output=print):
    root = checked_root(root)
    old_path, old = current(root)
    minimum = 0
    try:
        base = release_url(base_url)
        channel = json.loads(fetch(base + '/channel.json', 128 * 1024))
        if (not isinstance(channel, dict) or set(channel) != {'schema', 'minimum', 'manifest'}
                or channel['schema'] != 1 or not integer(channel['minimum'])):
            raise UpdateError('不支持的发布频道')
        minimum = channel['minimum']
        manifest = validate_manifest(channel['manifest'])
        if manifest['sequence'] < minimum:
            raise UpdateError('发布版本低于服务器最低版本要求')
        release = digest(canonical(manifest))
        if manifest['sequence'] < old['sequence']:
            raise UpdateError('拒绝发布版本回退')
        if manifest['sequence'] == old['sequence']:
            if release != old_path.name:
                raise UpdateError('同一版本号出现不同内容；发布者必须增加版本号')
            return old_path, old
        selected = install(root, manifest, lambda name, size:
                           fetch(base + '/releases/' + release + '/' + name, size))
        output('客户端已更新到版本 %s' % manifest['sequence'])
        return selected
    except (OSError, ValueError, UpdateError, subprocess.SubprocessError, HTTPException) as exc:
        output('自动更新失败：%s' % exc)
        if old['sequence'] < minimum:
            raise UpdateError('服务器要求客户端版本 %s；必须升级后再连接' % minimum) from exc
        output('继续使用已验证的客户端版本 %s' % old['sequence'])
        return old_path, old
