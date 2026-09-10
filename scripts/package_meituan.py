"""Package runtime allowlist, with manifest at ZIP root."""
from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / 'meituan-observer'
destination = root / 'dist' / 'meituan-popup-0.4.0'
files = ['manifest.json', 'devtools.html', 'devtools.js', 'diagnostics.mjs',
         'direct.mjs', 'panel.html', 'panel.js', 'replay.mjs', 'sanitize.mjs',
         'identity.mjs', 'worker.mjs', 'popup.html', 'popup.css', 'popup.mjs', 'INSTALL.txt']
destination.mkdir(parents=True, exist_ok=True)
for name in files:
    shutil.copy2(source / name, destination / name)
archive = destination.parent / (destination.name + '.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in files:
        z.write(destination / name, name)
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None and set(z.namelist()) == set(files)
print(destination)
print(archive)
