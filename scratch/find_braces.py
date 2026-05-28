import re

with open('D:/Dev_Env/ascendo/bin/validate-windows.ps1', 'r', encoding='utf-8') as f:
    text = f.read()

# remove comments
text = re.sub(r'#.*', '', text)
# remove double quoted strings
text = re.sub(r'\"(.*?)\"', '\"\"', text, flags=re.DOTALL)
# remove single quoted strings
text = re.sub(r'\'(.*?)\'', '\'\'', text, flags=re.DOTALL)
# remove powershell here-strings if any (maybe later)

open_count = text.count('{')
close_count = text.count('}')
print(f'Open: {open_count}, Close: {close_count}')

stack = []
lines = text.split('\n')
for i, line in enumerate(lines):
    for char in line:
        if char == '{':
            stack.append(i+1)
        elif char == '}':
            if stack:
                stack.pop()
            else:
                print(f'Unmatched close at line {i+1}')

print(f'Unmatched opens at lines: {stack}')
