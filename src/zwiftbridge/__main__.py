"""`python -m zwiftbridge ...` -- same commands as the `zwiftbridge` script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
