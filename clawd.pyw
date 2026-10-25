"""Double-click launcher for Windows: starts Clawd without a console window."""
import sys

from clawd.app import main

sys.exit(main())
