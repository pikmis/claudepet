"""Renders Clawd's idle frame into clawd.ico (used by the release build)."""
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawd.tray import app_icon  # noqa: E402


def main() -> None:
    app = QGuiApplication([])  # noqa: F841 - pixmaps need an application object
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "clawd.ico")
    pixmap = app_icon().pixmap(256, 256)
    if not pixmap.save(str(target), "ICO"):
        raise SystemExit(f"cannot write {target}")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
