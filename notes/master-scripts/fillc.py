# usage: fillc.py CONSULT BODY — BODY: 'timebox: N' line, '---', then sections Question..Constraints
import sys
c, body = sys.argv[1], open(sys.argv[2]).read()
head, _, secs = body.partition('\n---\n')
t = open(c).read()
tb = head.strip().split(': ',1)[1]
t = '\n'.join(f'timebox: {tb}' if l.startswith('timebox:') else l for l in t.split('\n'))
a = t.index('## Question'); b = t.index('## Reply')
open(c,'w').write(t[:a] + secs.strip() + '\n\n' + t[b:])
