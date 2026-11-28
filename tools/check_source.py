"""Check duplicate Python definitions, dashboard IDs and JavaScript syntax after merges."""
import ast
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class Dashboard(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids.extend(v for k, v in attrs if k == 'id')


def main():
    errors = []
    for folder in ('gameagent', 'webapp', 'tests'):
        for path in (ROOT / folder).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            for scope in ast.walk(tree):
                if isinstance(scope, (ast.Module, ast.ClassDef)):
                    names = Counter(n.name for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                    errors.extend(f'{path.relative_to(ROOT)}: duplicate definition {name}' for name, count in names.items() if count > 1)
    markup = (ROOT / 'webapp/static/studio.html').read_text(encoding='utf-8')
    dashboard = Dashboard(); dashboard.feed(markup)
    errors.extend(f'duplicate HTML ID: {name}' for name, count in Counter(dashboard.ids).items() if count > 1)
    if not shutil.which('node'):
        raise SystemExit('Node.js is required for JavaScript syntax validation.')
    with TemporaryDirectory() as temp:
        scripts = re.findall(r'<script>(.*?)</script>', markup, re.S)
        paths = list((ROOT / 'webapp/static').glob('*.js'))
        for index, source in enumerate(scripts + [p.read_text(encoding='utf-8') for p in paths]):
            path = Path(temp) / f'script-{index}.mjs'; path.write_text(source, encoding='utf-8')
            result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            if result.returncode: errors.append(result.stderr)
    if errors:
        raise SystemExit('\n'.join(errors))
    print('No duplicate Python definitions or dashboard IDs; JavaScript syntax checks passed.')


if __name__ == '__main__':
    main()
