from pathlib import Path

from qgis.PyQt import uic
from qgis.PyQt.QtGui import QIcon

cwd = Path(__file__).parent


IconBasemaps = QIcon(str(cwd / "icon.svg"))
UIBasemapsBase, _ = uic.loadUiType(str(cwd / "basemaps_dialog_base.ui"))
