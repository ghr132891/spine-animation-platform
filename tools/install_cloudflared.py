"""Install a pinned, checksum-verified official client into this project only."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.request

VERSION = '2026.10.0'
ASSET = 'cloudflared-darwin-arm64.tgz'
SHA256 = 'a2f79ff7b9420aa537d74af239f376da170bbabeb529aec416002adac6a72e70'
URL = f'https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{ASSET}'

def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This pinned installer supports macOS arm64 only; use the official client for other platforms.')
    with urllib.request.urlopen(URL, timeout=45) as response:
        data = response.read(25 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit('Official archive checksum mismatch; installation cancelled.')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == 'cloudflared']
        if len(members) != 1 or members[0].size > 80 * 1024 * 1024:
            raise SystemExit('Invalid client archive.')
        binary = archive.extractfile(members[0]).read()
    target = Path(__file__).resolve().parent / 'bin' / 'cloudflared'
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_bytes(binary)
    os.chmod(temp, 0o755)
    temp.replace(target)
    print(json.dumps({'version': VERSION, 'archive_sha256': SHA256, 'path': str(target)}))

if __name__ == '__main__':
    main()
