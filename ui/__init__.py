import io
import re
from pathlib import Path

from qgis.PyQt import uic
from qgis.PyQt.QtGui import QIcon

cwd = Path(__file__).parent

IconBasemaps = QIcon(str(cwd / "icon.svg"))

# Qt6 Designer writes fully-scoped enums like:
#   QAbstractItemView::SelectionMode::ExtendedSelection
# PyQt5's uic needs the short form:
#   QAbstractItemView::ExtendedSelection
# Strip the intermediate scope name so both PyQt5 and PyQt6 can compile it.
ui_content = (cwd / "basemaps_dialog_base.ui").read_text()
ui_content = re.sub(r"(\w+)::(\w+)::(\w+)", r"\1::\3", ui_content)
UIBasemapsBase, _ = uic.loadUiType(io.StringIO(ui_content))
