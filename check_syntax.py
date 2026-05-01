with open('sirius_chat/webui/static/app.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_string = None
in_template = False
in_comment = False
error_line = None

for i, line in enumerate(lines, 1):
    j = 0
    while j < len(line):
        ch = line[j]
        if in_comment:
            if ch == '*' and j+1 < len(line) and line[j+1] == '/':
                in_comment = False
                j += 2
                continue
            j += 1
            continue
        if in_string:
            if ch == '\\':
                j += 2
                continue
            if ch == in_string:
                in_string = None
            j += 1
            continue
        if in_template:
            if ch == '\\':
                j += 2
                continue
            if ch == '`':
                in_template = False
            j += 1
            continue
        if ch == '/' and j+1 < len(line):
            if line[j+1] == '/':
                break
            if line[j+1] == '*':
                in_comment = True
                j += 2
                continue
        if ch in "'\"":
            in_string = ch
        elif ch == '`':
            in_template = True
        j += 1

if in_string:
    print(f'Unclosed string ({in_string})')
elif in_template:
    print('Unclosed template literal')
elif in_comment:
    print('Unclosed block comment')
else:
    print('All strings and comments closed')
