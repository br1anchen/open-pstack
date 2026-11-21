# usage: fill.py BRIEF BODY  — BODY holds header lines (playbook/timebox/commit/plan) then '---' then sections Goal..Forbidden
import sys, re
brief, body = sys.argv[1], open(sys.argv[2]).read()
head, _, sections = body.partition('\n---\n')
t = open(brief).read().split('\n')
hdr = dict(l.split(': ',1) for l in head.strip().split('\n'))
for i,l in enumerate(t):
    k = l.split(':',1)[0]
    if k in hdr and l.startswith(k+':'): t[i] = f'{k}: {hdr[k]}'
txt = '\n'.join(t)
a = txt.index('## Goal'); b = txt.index('## Report')
open(brief,'w').write(txt[:a] + sections.strip() + '\n\n' + txt[b:])
