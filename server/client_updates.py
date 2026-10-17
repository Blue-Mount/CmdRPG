"""Serve only explicitly listed client artifacts; never mount a repository."""
import json
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from runtime.updater import UpdateError, canonical, digest, integer, safe_path, validate_manifest


def read_channel(directory):
    channel = json.loads((Path(directory) / 'channel.json').read_text('utf-8'))
    if (not isinstance(channel, dict) or set(channel) != {'schema', 'minimum', 'manifest'}
            or channel['schema'] != 1 or not integer(channel['minimum'])):
        raise UpdateError('Invalid release channel')
    validate_manifest(channel['manifest'])
    if channel['minimum'] > channel['manifest']['sequence']:
        raise UpdateError('Minimum version is not published')
    return channel


def router(directory):
    root = Path(directory).resolve()
    routes = APIRouter(prefix='/client-updates')

    @routes.get('/installer.zip')
    def installer():
        target = root / 'installer.zip'
        if not target.is_file() or target.resolve() != target.absolute():
            raise HTTPException(404, 'Client installer not published')
        return FileResponse(target, media_type='application/zip', filename='CmdRPG-installer.zip',
                            headers={'Cache-Control': 'no-store'})

    @routes.get('/channel.json')
    def channel():
        try:
            return JSONResponse(read_channel(root), headers={'Cache-Control': 'no-store'})
        except (OSError, ValueError, UpdateError):
            raise HTTPException(503, 'Client release unavailable')

    @routes.get('/releases/{release}/{name:path}')
    def file(release: str, name: str):
        try:
            if not re.fullmatch('[0-9a-f]{64}', release):
                raise ValueError('Invalid release')
            safe_path(name)
            folder = root / 'releases' / release
            manifest = validate_manifest(json.loads((folder / 'manifest.json').read_text('utf-8')))
            if digest(canonical(manifest)) != release or name not in manifest['files']:
                raise ValueError('Not listed')
            target = folder / name
            if target.resolve() != target.absolute() or not target.is_file():
                raise ValueError('Invalid artifact')
            return FileResponse(target, media_type='application/octet-stream',
                                headers={'Cache-Control': 'public, max-age=31536000, immutable'})
        except (OSError, ValueError, UpdateError):
            raise HTTPException(404, 'Client artifact not found')

    return routes
