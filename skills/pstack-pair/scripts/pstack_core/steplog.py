"""The steps the sidekick records for a unit: steps/<unit>.tsv, one row per committed step (number, sha, epoch, resolved notes, summary)."""

import glob
import os
from dataclasses import dataclass

from .store import last_line, read_text, unit_name, write_text
from .numbers import int_or_none
from .errors import EmptyStepLog, StepLogUnreadable


@dataclass(frozen=True)
class Step:
    number: str
    sha: str
    epoch: int | None
    resolves: tuple[str, ...]
    summary: str

    @classmethod
    def parse(cls, row):
        """A row of the file; the number stays the text of its column. A row with no tab is a bare sha, as the first writers left it."""
        cols = row.split('\t')
        if len(cols) == 1:
            return cls(row, row, None, (), '')
        cols += [''] * (5 - len(cols))
        return cls(
            cols[0],
            cols[1],
            int_or_none(cols[2]),
            tuple(i for i in cols[3].split(',') if i),
            '\t'.join(cols[4:]),
        )

    @property
    def index(self):
        """The step number as an integer, or None for a row whose first column is no number."""
        return int_or_none(self.number)

    def row(self):
        return f'{self.number}\t{self.sha}\t{self.epoch}\t{",".join(self.resolves)}\t{self.summary}'


def note_order(note_id):
    """Notes sort by unit prefix, then number: n2 before n10, 001-n1 before 002-n1."""
    return note_id.rpartition('-')[0], int(note_id.rpartition('n')[2])


class StepLog:
    def __init__(self, store, unit):
        self.store = store
        self.unit = unit_name(unit)
        self.path = f'{store}/steps/{self.unit}.tsv'

    @classmethod
    def all(cls, store):
        """The log of every unit that has one, in file-name order."""
        return [
            cls(store, os.path.basename(p)[:-4])
            for p in sorted(
                glob.glob(os.path.join(glob.escape(store), 'steps', '*.tsv'))
            )
        ]

    def rows(self):
        """Every recorded step in order; none for a missing file. An unreadable file raises OSError."""
        try:
            return [
                Step.parse(line) for line in read_text(self.path).split('\n') if line
            ]
        except FileNotFoundError:
            return []

    def last(self):
        """The file's last line as a step (a blank last line is a step with no sha), or None for a missing or empty file."""
        try:
            if os.path.getsize(self.path) == 0:
                return None
        except FileNotFoundError:
            return None
        return Step.parse(last_line(self.path))

    def at(self, number):
        """The step recorded under this number, or None."""
        return next((s for s in self.rows() if s.index == number), None)

    def has(self, sha):
        return any(s.sha == sha for s in self.rows())

    def next_number(self):
        """One more than the rows there are. A file with none is an EmptyStepLog, an unreadable one a StepLogUnreadable."""
        if not os.path.isfile(self.path):
            return 1
        try:
            count = len(self.rows())
        except OSError as e:
            raise StepLogUnreadable('read', self.path, e) from None
        if count == 0:
            raise EmptyStepLog()
        return count + 1

    def append(self, number, sha, epoch, resolves, summary):
        """Adds the step; its summary is one line of one cell."""
        step = Step(
            str(number),
            sha,
            epoch,
            tuple(i for i in resolves.split(',') if i),
            summary.replace('\t', ' ').replace('\n', ' '),
        )
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(step.row() + '\n')
        return step

    def add_resolves(self, sha, resolves):
        """True when the log has a row for this commit; the row then gains the given note ids (in note order, once each)."""
        try:
            lines = read_text(self.path).split('\n')
        except OSError:
            return False
        found = False
        for n, line in enumerate(lines):
            cols = line.split('\t')
            if len(cols) > 3 and cols[1] == sha:
                found = True
                ids = {
                    *(i for i in cols[3].split(',') if i),
                    *(i for i in resolves.split(',') if i),
                }
                cols[3] = ','.join(sorted(ids, key=note_order))
                lines[n] = '\t'.join(cols)
                break
        if found and resolves:
            write_text(f'{self.path}.tmp', '\n'.join(lines))
            os.replace(f'{self.path}.tmp', self.path)
        return found
