import importlib.util
import os
import re
import subprocess

from .review import git, jq_raw, noted_through, open_notes, steps_file
from .store import alt, bash_int, header_field, last_line, pair_json, unit_name


class ReportReadiness:
    """Whether a report may be shown to the master as a reply. open is the newline-joined open note paths
    (bash report_open), why the reason it is withheld (bash report_why)."""

    __slots__ = ('ready', 'open', 'why')

    def __init__(self, ready, open='', why=''):
        self.ready = ready
        self.open = open
        self.why = why


def commit_of(root, rev):
    return git(root, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}')


class Variant:
    """The pair's hooks. A variant's scripts/variant.py subclasses this and sets VARIANT."""

    def report_ready(self, ctx, store, report):
        """report_ready() in pair-core.sh. Only a done report is judged, and only the unit's own report:
        the -sN and -qN reports answer a steer or a consult. Open blocking notes withhold it, and so does
        a head that predates the unit's last recorded step. A unit with no steps, or a head that does not
        resolve (older stores, fixtures), is judged on notes alone."""
        if header_field(report, 'status') != 'done':
            return ReportReadiness(True)
        base = unit_name(report)
        if re.search(r'-[sq][0-9]', base):
            return ReportReadiness(True)
        opened = open_notes(store, report)
        if opened:
            names = ','.join(re.sub(r'.*-(n[0-9]+)\.md$', r'\1', p) for p in opened)
            return ReportReadiness(False, '\n'.join(opened), f'open blocking notes {names}')
        file = steps_file(store, base)
        try:
            if os.path.getsize(file) == 0:
                return ReportReadiness(True)
        except OSError:
            return ReportReadiness(True)
        m = re.match(r'[0-9a-f]{7,40}', header_field(report, 'head'))
        if not m:
            return ReportReadiness(True)
        head = m.group(0)
        pair = pair_json(store)
        root = jq_raw(alt(pair.get('git_root'), pair.get('cwd')))
        if commit_of(root, head) is None:
            return ReportReadiness(True)
        row = last_line(file)
        cols = row.split('\t')
        k, last = cols[0], cols[1] if len(cols) > 1 else row
        if commit_of(root, last) is None:
            return ReportReadiness(True)
        if subprocess.run(['git', '-C', root, 'merge-base', '--is-ancestor', last, head],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
            return ReportReadiness(True)
        return ReportReadiness(False, '', f"the report's head {head[:9]} predates step {k} {last[:9]}")


    def steps_due(self, ctx, store, brief):
        """Whether a wait should return to the master for the running brief's steps."""
        return steps_due_in(ctx, store, brief)

    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """Further gates on an agreed plan, called with the plan path, its review path and its sequence number. A refusal is a Die
        with exit 6. The base pair requires nothing more."""

    def require_agreed_plan(self, ctx, store, brief):
        """The gate in front of a brief that implements a plan: the agreed plan, and this variant's plan_gate_extra."""
        from .gates import require_agreed_plan
        require_agreed_plan(ctx, store, brief, self.plan_gate_extra)


def steps_due_in(ctx, store, brief):
    """steps_due() in pair-core.sh: a step landed that neither a note nor an earlier steps wake has covered."""
    from .replies import unreviewed_steps
    if unreviewed_steps(ctx, store) is None:
        return False
    shown = alt(alt(pair_json(store).get('shown'), {}).get(unit_name(brief)), 0)
    file = steps_file(store, brief)
    with open(file, 'rb') as f:
        last = sum(1 for line in f.read().split(b'\n') if line)
    shown_count = bash_int(jq_raw(shown))
    return shown_count is not None and last > shown_count and last > noted_through(store, brief)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_name(skill):
    return 'pstack_variant_' + skill.replace('-', '_')


def load_skill_variant(skills_dir, skill):
    """The VARIANT class of skills_dir/skill/scripts/variant.py, or Variant when that skill has none."""
    path = f'{skills_dir}/{skill}/scripts/variant.py'
    if not os.path.isfile(path):
        return Variant
    return load_module(path, module_name(skill)).VARIANT


def load_variant(ctx):
    """The variant of ctx's skill: scripts/variant.py beside its pair.sh, or the base."""
    path = f'{ctx.skill_root}/scripts/variant.py'
    if not os.path.isfile(path):
        return Variant()
    return load_module(path, module_name(ctx.skill)).VARIANT()
