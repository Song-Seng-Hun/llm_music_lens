"""Prepare the pinned optional SOME model; no executable archive contents run.
Usage: python scripts/prepare-vocal-model.py [existing some-0.0.1.oudep]
No argument downloads the pinned public data archive. No API key is needed.
"""
from pathlib import Path
import hashlib,sys,urllib.request,zipfile

ROOT=Path(__file__).resolve().parents[1]
URL='https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/some-0.0.1.oudep'
ARCHIVE_BYTES=435782034
ARCHIVE_SHA='b98a350b60740e3a153b15e137ed025832956d918c44853048bec93af657f478'
MODEL_SHA='9d55f9d239e9d70d1bf039506cf1c428b657df7bbb8b94104ca882af6b721e06'
MODEL_BYTES=471381796
archive=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'.cache/some-0.0.1.oudep'
if not archive.exists():
    if len(sys.argv)>1: raise FileNotFoundError(archive)
    archive.parent.mkdir(parents=True,exist_ok=True)
    temporary=archive.with_suffix('.download')
    with urllib.request.urlopen(URL,timeout=120) as r,temporary.open('wb') as f:
        total=0
        while chunk:=r.read(1024*1024):
            total+=len(chunk)
            if total>ARCHIVE_BYTES: raise ValueError('Unexpected archive size')
            f.write(chunk)
    if total!=ARCHIVE_BYTES or hashlib.sha256(temporary.read_bytes()).hexdigest()!=ARCHIVE_SHA:
        raise ValueError('Archive identity mismatch; model not installed')
    temporary.replace(archive)
if archive.stat().st_size!=ARCHIVE_BYTES or hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA:
    raise ValueError('Archive identity mismatch; model not installed')
with zipfile.ZipFile(archive) as z:
    # Select one literal member, never extract paths or execute installer code.
    entry=z.getinfo('0918_continuous128_clean_3spk.onnx')
    if entry.file_size!=MODEL_BYTES: raise ValueError('Unexpected model size')
    model=z.read(entry)
if hashlib.sha256(model).hexdigest()!=MODEL_SHA: raise ValueError('Model identity mismatch')
target=ROOT/'public/models/some/model.onnx'
target.parent.mkdir(parents=True,exist_ok=True)
temporary=target.with_suffix('.preparing')
temporary.write_bytes(model)
temporary.replace(target)
print(f'Prepared SOME model: {MODEL_BYTES} bytes, SHA256 {MODEL_SHA}')
print('Code MIT; model CC BY-NC-SA 4.0. See docs/vocal-specialist.md. Run node scripts/prepare-analysis.mjs next.')
