"""Build an editable handoff ZIP without runtime credentials or experiment data."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('dist', 'docs', 'integrations', 'tests', 'tools')
ROOT_FILES = ('README.md', 'START_HERE_KO.md', 'CONTRIBUTING.md', '.gitignore',
              'run.py', 'recovery_api.py', 'aiven_connection.py', 'aiven.env.example',
              'requirements-control.txt', 'requirements-upload.txt')
EXCLUDED = {'runtime', '__pycache__', '.git', '.venv', 'node_modules', 'exports',
            '\uac00\uc0c1\uc2dc\ud5d8', '\ub370\uc774\ud130'}


def allowed(path):
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED for part in relative.parts): return False
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT): return False
    if path.name.startswith('.env') or path.suffix.lower() in {
        '.key', '.pem', '.crt', '.p12', '.pfx', '.db', '.sqlite3', '.log', '.jsonl',
        '.pid', '.pyc', '.zip', '.bak'}: return False
    if path.suffix.lower()=='.csv' and path.name!='sample_energy_log.csv': return False
    return True


def main():
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    package='AIRGUARD_editable_'+stamp
    destination=ROOT/'exports'/(package+'.zip')
    destination.parent.mkdir(exist_ok=True)
    paths=[ROOT/name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT/directory).rglob('*') if path.is_file() and allowed(path))
    paths=sorted(set(paths),key=lambda p:p.as_posix())
    # Check current operational secrets without ever printing their values.
    secrets=[]
    key=ROOT/'runtime/dashboard_lease.key'
    if key.is_file(): secrets.append(key.read_text().strip().encode())
    env=ROOT/'runtime/aiven/.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            name,separator,value=line.partition('=')
            if separator and name.strip()=='DB_PASSWORD':
                secrets.append(value.strip().strip('"\'').encode())
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'files':{}}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in paths:
            relative=path.relative_to(ROOT).as_posix()
            content=path.read_bytes()
            if any(secret and len(secret)>=8 and secret in content for secret in secrets):
                raise RuntimeError('Private credential detected in '+relative)
            manifest['files'][relative]={'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
            archive.writestr(package+'/'+relative,content)
        archive.writestr(package+'/MANIFEST_SHA256.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(paths)+1
        for relative,entry in manifest['files'].items():
            assert hashlib.sha256(archive.read(package+'/'+relative)).hexdigest()==entry['sha256']
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.sha256').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    print(json.dumps({'zip':str(destination),'files':len(paths)+1,'bytes':destination.stat().st_size,
        'sha256':digest},ensure_ascii=False))


if __name__=='__main__':main()
