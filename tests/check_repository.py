"""Check local documentation links and accidental generated/private files."""
from pathlib import Path
import re
from urllib.parse import unquote

root = Path(__file__).resolve().parents[1]
errors = []
for path in root.rglob('*'):
    relative = path.relative_to(root)
    if not path.is_file() or any(part in ('.git', 'build', '__pycache__', '.venv') for part in relative.parts):
        continue
    if path.suffix in ('.so', '.ko', '.deb', '.o', '.log'):
        errors.append(f'Generated artifact: {relative}')
    if path.suffix not in ('.md', '.py', '.sh', '.c', '.js', '.json', '.yml', '.xml'):
        continue
    text = path.read_text()
    # Generic patterns only: the hygiene check must not itself embed secrets.
    for pattern in (r'/(?:home|Users)/[A-Za-z0-9_-]+/', r'@[A-Za-z0-9_-]+\.local\b'):
        if re.search(pattern, text):
            errors.append(f'Machine-specific path or host in {relative}')
    if path.suffix == '.md':
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if target.startswith(('https://', 'http://', '#', 'mailto:')):
                continue
            target = unquote(target.split('#', 1)[0])
            if not (path.parent / target).exists():
                errors.append(f'Broken local link in {relative}: {target}')
if errors:
    raise SystemExit('\n'.join(errors))
print('Documentation links and repository hygiene passed.')
