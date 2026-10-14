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
# Source SHA above pins these offsets; append frame rest, frame-to-note and fractional frame MIDI outputs.
RUNTIME_SHA='7b5a4eb8e5b1486902e3918516020a8f0f78d0d22c02ebd4d5410b2dc60e8e75'
RUNTIME_BYTES=471381924
EXTRA_OUTPUTS=bytes.fromhex(
    '622a0a102f4c6573735f315f6f75747075745f3012160a14080912100a0208010a0a12086e5f6672616d6573'
    '62290a0f2f4d756c5f325f6f75747075745f3012160a14080712100a0208010a0a12086e5f6672616d6573'
    '62270a0d2f4469765f6f75747075745f3012160a14080112100a0208010a0a12086e5f6672616d6573'
)
GRAPH_END=471381790
OLD_LENGTH=bytes.fromhex('86eee2e001')
NEW_LENGTH=bytes.fromhex('86efe2e001')
if model[18:24]!=b'\x3a'+OLD_LENGTH or len(EXTRA_OUTPUTS)!=128:
    raise ValueError('Pinned graph layout mismatch; model not installed')
patched=b''.join((model[:19],NEW_LENGTH,memoryview(model)[24:GRAPH_END],EXTRA_OUTPUTS,memoryview(model)[GRAPH_END:]))
if len(patched)!=RUNTIME_BYTES or hashlib.sha256(patched).hexdigest()!=RUNTIME_SHA:
    raise ValueError('Runtime model identity mismatch; model not installed')
restored=b''.join((patched[:19],OLD_LENGTH,memoryview(patched)[24:GRAPH_END],memoryview(patched)[GRAPH_END+128:]))
if restored!=model:
    raise ValueError('Graph patch inverse mismatch; model not installed')
del restored
model=patched
target=ROOT/'public/models/some/model.onnx'
target.parent.mkdir(parents=True,exist_ok=True)
temporary=target.with_suffix('.preparing')
temporary.write_bytes(model)
temporary.replace(target)
print(f'Prepared SOME model: source {MODEL_BYTES} bytes SHA256 {MODEL_SHA}; runtime {RUNTIME_BYTES} bytes SHA256 {RUNTIME_SHA}; weights/nodes unchanged, three output descriptors added')
print('Code MIT; model CC BY-NC-SA 4.0. See docs/vocal-specialist.md. Run node scripts/prepare-analysis.mjs next.')
