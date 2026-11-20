"""Closing a git-backed run store (A6): fence the writers, publish the final state, tag it, check the tag on the remote, and only then delete the
branch. Each phase is recorded in pair.json (`.store_backend.close.done`) when it finishes, so a rerun after a crash continues with the first
phase that is not recorded, and every phase can run twice with the same end state. The closing of the roles and their threads is the kitchen's
own (`kitchen.sh close`) and runs before this.
"""

import sys

from .. import git, gitstore
from ..errors import PairError
from ..mux.amp_routing import ensure_run
from ..store import event, events, member, update

PHASES = ('fence', 'final', 'tag', 'verify', 'delete')


class ArchiveRefused(PairError):
    """The remote does not hold what the close recorded (a tag at another commit, a branch that moved after the tag): nothing is deleted."""


def record(store):
    close = gitstore.backend_record(store).get('close')
    return close if isinstance(close, dict) else {}


def tag_of(store):
    """The archive tag of this run: kitchen-archive/<slug>-<run id>."""
    return f'kitchen-archive/{gitstore.slug_of(store)}-{ensure_run(store)}'


def fence(store, name):
    """New writers are refused: pair.json says closing, published, so a role that refreshes before its next publish sees it."""

    def mutate(doc):
        member(doc, 'store_backend')['closing'] = True

    update(store, mutate)
    gitstore.publish_role(store, 'master', message='kitchen: close fences new writers')


def final(store, name):
    """The last state of the run goes to the branch, with the close event; the commit it ends at is the tip the tag will name."""
    branch = gitstore.branch_of(store)
    detail = f'store: archive {name}'
    if not any(row.split('\t')[3:6] == ['close', '-', detail] for row in events(store)):
        event(store, 'master', 'close', '-', detail)
    gitstore.publish_role(store, 'master', message=f'kitchen: final state of {branch}')
    if not gitstore.refresh(store):
        raise gitstore.PublishFailed(
            f'{branch} could not be fetched; the final state is not known to be on origin, so nothing is tagged'
        )
    if gitstore.unpublished(store, branch):
        raise gitstore.PublishFailed(
            f'{branch} has commits origin lacks; the final state is not on origin, so nothing is tagged'
        )
    tip = gitstore.head_of(store)

    def mutate(doc):
        close = member(member(doc, 'store_backend'), 'close')
        close['tip'] = tip
        close['tag'] = name

    update(store, mutate)


def tag(store, name):
    """The annotated tag at the final tip, here and on origin. A tag that exists at another commit is refused."""
    tip = record(store).get('tip') or ''
    have = git.out(
        store, 'rev-parse', '--verify', '--quiet', f'refs/tags/{name}^{{commit}}'
    )
    if have and have != tip:
        raise ArchiveRefused(
            f'the tag {name} is at {have[:9]}, not at the final tip {tip[:9]}; nothing is deleted'
        )
    if not have:
        made = gitstore.run_git(
            store,
            'tag',
            '-a',
            '-m',
            f'kitchen archive of {gitstore.branch_of(store)}',
            name,
            tip,
        )
        if made.returncode != 0:
            raise gitstore.PublishFailed(
                f'cannot tag {tip[:9]} as {name}: {made.stderr.strip()}'
            )
    pushed = gitstore.run_git(store, 'push', 'origin', f'refs/tags/{name}')
    if pushed.returncode != 0:
        raise gitstore.PublishFailed(
            f'the tag {name} was not pushed ({pushed.stderr.strip()}); the branch stays'
        )


def verify(store, name):
    """The tag origin lists resolves to the final tip: the branch is never deleted on the word of the push alone."""
    tip = record(store).get('tip') or ''
    listed = gitstore.remote_tip(store, f'refs/tags/{name}^{{}}')
    if listed is None:
        raise gitstore.PublishFailed(
            f'origin cannot be read, so {name} is not verified; the branch stays'
        )
    if listed != tip:
        raise ArchiveRefused(
            f'origin lists {name} at {listed[:9] or "nothing"}, not at the final tip {tip[:9]}; nothing is deleted'
        )


def delete(store, name):
    """Origin's branch goes, only while it is still at the tip the tag names: the tag is checked again first, and a branch that moved after the
    tag keeps its commits (a writer that did not see the fence)."""
    verify(store, name)
    branch = gitstore.branch_of(store)
    tip = record(store).get('tip') or ''
    now = gitstore.remote_tip(store, f'refs/heads/{branch}')
    if now is None:
        raise gitstore.PublishFailed(
            f'origin cannot be read, so {branch} is not deleted'
        )
    if now == '':
        return
    if now != tip:
        raise ArchiveRefused(
            f'{branch} is at {now[:9]} on origin, past the tagged tip {tip[:9]}: a writer published after the fence; nothing is deleted'
        )
    gone = gitstore.run_git(
        store,
        'push',
        'origin',
        f'--force-with-lease=refs/heads/{branch}:{tip}',
        '--delete',
        branch,
    )
    if gone.returncode != 0:
        raise gitstore.PublishFailed(
            f'{branch} was not deleted on origin ({gone.stderr.strip()})'
        )


ACTIONS = {
    'fence': fence,
    'final': final,
    'tag': tag,
    'verify': verify,
    'delete': delete,
}


def mark(store, phase):
    def mutate(doc):
        close = member(member(doc, 'store_backend'), 'close')
        done = close.setdefault('done', [])
        if phase not in done:
            done.append(phase)

    update(store, mutate)
    event(store, 'master', 'close-phase', '-', phase)
    sys.stdout.write(f'close: {phase} done\n')
    sys.stdout.flush()


def archive(store, crash=None):
    """Runs the phases the close has not recorded, in order, and returns the archive tag. `crash(point)` is a test's deterministic hook, called
    as `<phase>:ran` after a phase's action and `<phase>:marked` after its record; a raise there is a crash at that point."""
    name = tag_of(store)
    if set(PHASES) <= set(record(store).get('done') or []):
        sys.stdout.write(
            f'close: {gitstore.branch_of(store)} is already archived as {name}\n'
        )
        return name
    for phase in PHASES:
        if phase in (record(store).get('done') or []):
            continue
        ACTIONS[phase](store, name)
        if crash:
            crash(f'{phase}:ran')
        mark(store, phase)
        if crash:
            crash(f'{phase}:marked')
    sys.stdout.write(
        f'close: {gitstore.branch_of(store)} is archived as {name} and deleted on origin\n'
    )
    return name
