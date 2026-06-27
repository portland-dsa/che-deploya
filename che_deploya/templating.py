"""Resolve the ``{field}`` placeholders used in spec path strings.

One substitution mechanism for every templated path - ``src``, ``dest``,
``resource_loc``, ``Secret.dest_dir`` - so the placeholder vocabulary
(``{repo_root}``, ``{root}``, ``{component}``, ``{stage}``) stays uniform and a
typo in a placeholder name fails loudly instead of silently passing through.
"""

from __future__ import annotations


def resolve(template: str, **fields: str) -> str:
    """Substitute the given ``{field}`` placeholders in ``template``.

    Only the placeholders actually present are substituted; a placeholder in the
    template with no matching keyword raises ``KeyError`` (a misspelled field),
    while a keyword not used by the template is simply ignored - callers pass the
    full field set and let each template take what it needs.
    """

    class _Missing(dict):
        def __missing__(self, key: str):
            raise KeyError(key)

    return template.format_map(_Missing(fields))
