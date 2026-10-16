class Context:
    """What a variant's pair.sh sets before it sources pair-core.sh."""

    __slots__ = ('skill', 'skill_root', 'state_root', 'store_label', 'roles', 'store_dirs', 'slug_max', 'unfilled_re')

    def __init__(self, skill, skill_root, state_root, store_label, roles, store_dirs, slug_max, unfilled_re):
        self.skill = skill
        self.skill_root = skill_root
        self.state_root = state_root
        self.store_label = store_label
        self.roles = roles
        self.store_dirs = store_dirs
        self.slug_max = slug_max
        self.unfilled_re = unfilled_re
