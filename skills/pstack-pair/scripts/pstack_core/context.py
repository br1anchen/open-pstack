class Context:
    """What a variant's class attributes configure: skill, state root, labels, roles, store directories, slug limit and unfilled pattern."""

    __slots__ = ('skill', 'skill_root', 'state_root', 'store_label', 'roles', 'store_dirs', 'slug_max', 'unfilled_re', '_variant')

    def __init__(self, skill, skill_root, state_root, store_label, roles, store_dirs, slug_max, unfilled_re):
        self.skill = skill
        self.skill_root = skill_root
        self.state_root = state_root
        self.store_label = store_label
        self.roles = roles
        self.store_dirs = store_dirs
        self.slug_max = slug_max
        self.unfilled_re = unfilled_re
        self._variant = None

    @property
    def variant(self):
        """The skill's Variant, loaded on first use so a command that needs no hook pays for none."""
        if self._variant is None:
            from .variant import load_variant
            self._variant = load_variant(self)
        return self._variant
