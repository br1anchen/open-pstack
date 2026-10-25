---
title: Guide
see: [front](ignored.md)
---
# Guide

Inline [text](other.md) and [with fragment](other.md#part) and [bare](#top).
Image ![alt](../img/a.png "title") and angle [a b](<my file.md>) and [parens](a(1).md#x).
Reference [use][ref] and [ref2].

[ref]: other.md#part
[ref2]: <my file.md> "Title"
[^1]: footnote, not a definition
[ext]: https://example.com/def.md

URL [web](https://example.com/x.md) and [mail](mailto:a@b.c) and [proto](//cdn/x.md).

`[code](other.md)` and ``[code2](other.md#c)`` are spans, but [live](other.md#live).

```md
[fenced](other.md)
[ref3]: other.md
```

Nested [![badge](../img/a.png)](other.md#nest). Escaped \[not](other.md) and [empty]().
