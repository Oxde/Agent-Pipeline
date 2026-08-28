"""The one place the version lives.

pyproject reads it via setuptools' dynamic attr, the CLI serves it as
--version, and every saved ledger stamps it — so a postmortem can always say
which engine wrote the state it is looking at.
"""

__version__ = "0.2.0"
