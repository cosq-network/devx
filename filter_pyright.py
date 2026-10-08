import json, sys

data = json.load(sys.stdin)
for d in data.get('generalDiagnostics', []):
    f = d.get('file', '')
    if 'src\\devx' in f and 'test' not in f:
        line = d['range']['start']['line'] + 1
        char = d['range']['start']['character'] + 1
        sev = d['severity']
        msg = d['message']
        print(f'{f}:{line}:{char} - {sev}: {msg}')