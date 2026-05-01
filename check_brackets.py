import re

with open('sirius_chat/webui/static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove single-line comments
content = re.sub(r'//.*', '', content)
# Remove multi-line comments
content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
# Remove single-quoted strings
content = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", content)
# Remove double-quoted strings
content = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', content)
# Remove template literals
content = re.sub(r'`[^`\\]*(?:\\.[^`\\]*)*`', '``', content)

stack = []
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    for ch in line:
        if ch in '({[':
            stack.append((ch, i))
        elif ch in ')}]':
            if not stack:
                print(f'Unmatched closing {ch} at line {i}')
            else:
                last, li = stack.pop()
                pairs = {'(': ')', '[': ']', '{': '}'}
                if pairs[last] != ch:
                    print(f'Mismatched: opened {last} at line {li}, closed {ch} at line {i}')

if stack:
    print('Unclosed brackets:')
    for ch, i in stack[-10:]:
        print(f'  {ch} at line {i}')
else:
    print('Brackets balanced')
