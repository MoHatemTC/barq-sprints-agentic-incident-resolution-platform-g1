"""Deprecated: stressor extraction is retired (plan step 6).

The six stressor articles duplicated full-manual content and depended on
``app.retrieval.extraction.stressor_articles``, which is absent from this
checkout. Full-manual extraction (``scripts/manual/extract_manual.py``) plus
the reviewed manifest (``data/corpus/manual_kb_manifest.json``) supersede it.
Seeding tolerates an absent stressor file; legacy stressor records are
retired through the reconciliation plan, not re-extracted.
"""

import sys


def main() -> int:
    print(__doc__, file=sys.stderr)
    print("Error: extract_stressors.py is deprecated; nothing was extracted.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
