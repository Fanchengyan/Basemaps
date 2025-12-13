"""Message tools for QGIS plugin.

This module provides wrapper classes for QGIS message logging, message bar,
and message box dialogs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import QT_VERSION_STR
from qgis.PyQt.QtWidgets import QMessageBox, QWidget
from qgis.utils import iface

if TYPE_CHECKING:
    from qgis.core import Qgis as QgisType

# Qt5/Qt6 compatibility for QMessageBox button constants
if int(QT_VERSION_STR.split(".")[0]) >= 6:
    _BUTTON_OK = QMessageBox.StandardButton.Ok
    _BUTTON_CANCEL = QMessageBox.StandardButton.Cancel
    _BUTTON_YES = QMessageBox.StandardButton.Yes
    _BUTTON_NO = QMessageBox.StandardButton.No
else:
    _BUTTON_OK = QMessageBox.Ok
    _BUTTON_CANCEL = QMessageBox.Cancel
    _BUTTON_YES = QMessageBox.Yes
    _BUTTON_NO = QMessageBox.No

# Level mapping
_LEVELS: dict[str, QgisType.MessageLevel] = {
    "info": Qgis.Info,
    "warning": Qgis.Warning,
    "critical": Qgis.Critical,
    "success": Qgis.Success,
}

LogLevel = Literal["info", "warning", "critical", "success"]


class Logger:
    """QGIS message log wrapper with level-specific static methods.

    This class provides static methods for logging messages to QGIS message log
    panel with different severity levels.

    Attributes
    ----------
    DEFAULT_TAG : str
        Default tag for log messages, set to "Basemaps".

    Examples
    --------
    >>> Logger.info("Operation completed successfully")
    >>> Logger.warning("Configuration file not found", tag="Config")
    >>> Logger.critical("Failed to connect to server", notify_user=True)
    >>> Logger.success("Data exported successfully")
    """

    DEFAULT_TAG: str = "Basemaps"

    @staticmethod
    def _log(
        msg: str,
        level: QgisType.MessageLevel,
        tag: str | None = None,
        notify_user: bool = False,
    ) -> None:
        """Internal logging method.

        Parameters
        ----------
        msg : str
            The message to log.
        level : Qgis.MessageLevel
            The QGIS message level.
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool, optional
            Whether to notify the user in the message bar, by default False.
        """
        QgsMessageLog.logMessage(
            msg,
            tag=tag or Logger.DEFAULT_TAG,
            level=level,
            notifyUser=notify_user,
        )

    @staticmethod
    def info(
        msg: str,
        tag: str | None = None,
        notify_user: bool = False,
    ) -> None:
        """Log an info level message.

        Parameters
        ----------
        msg : str
            The message to log.
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool, optional
            Whether to notify the user in the message bar, by default False.

        Examples
        --------
        >>> Logger.info("Loading configuration...")
        >>> Logger.info("Connected to server", tag="Network")
        """
        Logger._log(msg, Qgis.Info, tag, notify_user)

    @staticmethod
    def warning(
        msg: str,
        tag: str | None = None,
        notify_user: bool = True,
    ) -> None:
        """Log a warning level message.

        Parameters
        ----------
        msg : str
            The message to log.
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool, optional
            Whether to notify the user in the message bar, by default True.

        Examples
        --------
        >>> Logger.warning("Configuration file not found, using defaults")
        """
        Logger._log(msg, Qgis.Warning, tag, notify_user)

    @staticmethod
    def critical(
        msg: str,
        tag: str | None = None,
        notify_user: bool = True,
    ) -> None:
        """Log a critical level message.

        Parameters
        ----------
        msg : str
            The message to log.
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool, optional
            Whether to notify the user in the message bar, by default True.

        Examples
        --------
        >>> Logger.critical("Failed to save data: permission denied")
        """
        Logger._log(msg, Qgis.Critical, tag, notify_user)

    @staticmethod
    def success(
        msg: str,
        tag: str | None = None,
        notify_user: bool = False,
    ) -> None:
        """Log a success level message.

        Parameters
        ----------
        msg : str
            The message to log.
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool, optional
            Whether to notify the user in the message bar, by default False.

        Examples
        --------
        >>> Logger.success("Export completed successfully")
        """
        Logger._log(msg, Qgis.Success, tag, notify_user)

    @staticmethod
    def log(
        msg: str,
        level: LogLevel = "info",
        tag: str | None = None,
        notify_user: bool | Literal["auto"] = "auto",
    ) -> None:
        """Log a message with dynamic level selection.

        Parameters
        ----------
        msg : str
            The message to log.
        level : {"info", "warning", "critical", "success"}, optional
            The log level, by default "info".
        tag : str | None, optional
            The tag for the log message. If None, uses DEFAULT_TAG.
        notify_user : bool or "auto", optional
            Whether to notify the user. If "auto", notifies for warning and
            critical levels, by default "auto".

        Raises
        ------
        ValueError
            If level is not one of the valid levels.

        Examples
        --------
        >>> Logger.log("Processing complete", level="success")
        >>> Logger.log("Connection timeout", level="warning", notify_user=True)
        """
        level_lower = level.lower()
        if level_lower not in _LEVELS:
            Logger.critical(f"Invalid log level: {level}. Using 'info' instead.")
            level_lower = "info"

        if notify_user == "auto":
            notify_user = level_lower in ("warning", "critical")

        Logger._log(msg, _LEVELS[level_lower], tag, notify_user)


class MessageBar:
    """QGIS message bar wrapper.

    This class provides a static method for showing messages in the QGIS
    main window message bar.

    Examples
    --------
    >>> MessageBar.show("Title", "Operation completed", Qgis.Success)
    >>> MessageBar.show("Warning", "File not found", Qgis.Warning, duration=5)
    """

    @staticmethod
    def show(
        title: str,
        text: str,
        level: QgisType.MessageLevel = Qgis.Info,
        duration: int = 10,
    ) -> None:
        """Show a message in the QGIS message bar.

        Parameters
        ----------
        title : str
            The title of the message.
        text : str
            The message text.
        level : Qgis.MessageLevel, optional
            The message level (Qgis.Info, Qgis.Warning, Qgis.Critical,
            Qgis.Success), by default Qgis.Info.
        duration : int, optional
            The duration in seconds to show the message, by default 10.
            Set to 0 for persistent message.

        Examples
        --------
        >>> MessageBar.show("Success", "Layer loaded", Qgis.Success, duration=5)
        """
        iface.messageBar().pushMessage(
            title,
            text,
            level=level,
            duration=duration,
        )


class MessageBox:
    """Message box dialogs wrapper.

    This class provides static methods for showing various types of message
    box dialogs with Qt5/Qt6 compatibility.

    Examples
    --------
    >>> MessageBox.ok("Operation completed successfully")
    >>> result = MessageBox.yes_no("Do you want to continue?", title="Confirm")
    >>> if result == MessageBox.YES:
    ...     print("User clicked Yes")
    """

    # Export button constants for comparison
    YES = _BUTTON_YES
    NO = _BUTTON_NO
    OK = _BUTTON_OK
    CANCEL = _BUTTON_CANCEL

    @staticmethod
    def ok(
        text: str,
        title: str = "Info",
        parent: QWidget | None = None,
    ) -> None:
        """Show a message box with an OK button.

        Parameters
        ----------
        text : str
            The message text.
        title : str, optional
            The title of the message box, by default "Info".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Examples
        --------
        >>> MessageBox.ok("Operation completed successfully")
        >>> MessageBox.ok("File saved", title="Success")
        """
        mb = QMessageBox(parent)
        mb.setText(text)
        mb.setStandardButtons(_BUTTON_OK)
        mb.setWindowTitle(title)
        mb.exec()

    @staticmethod
    def ok_cancel(
        text: str,
        title: str = "Warning",
        parent: QWidget | None = None,
    ) -> int:
        """Show a message box with OK and Cancel buttons.

        Parameters
        ----------
        text : str
            The message text.
        title : str, optional
            The title of the message box, by default "Warning".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Returns
        -------
        int
            The button clicked (MessageBox.OK or MessageBox.CANCEL).

        Examples
        --------
        >>> result = MessageBox.ok_cancel("Do you want to proceed?")
        >>> if result == MessageBox.OK:
        ...     print("User clicked OK")
        """
        mb = QMessageBox(parent)
        mb.setText(text)
        mb.setStandardButtons(_BUTTON_OK | _BUTTON_CANCEL)
        mb.setDefaultButton(_BUTTON_CANCEL)
        mb.setWindowTitle(title)
        return mb.exec()

    @staticmethod
    def yes_no(
        text: str,
        title: str = "Question",
        parent: QWidget | None = None,
    ) -> int:
        """Show a message box with Yes and No buttons.

        Parameters
        ----------
        text : str
            The message text.
        title : str, optional
            The title of the message box, by default "Question".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Returns
        -------
        int
            The button clicked (MessageBox.YES or MessageBox.NO).

        Examples
        --------
        >>> result = MessageBox.yes_no("Delete this item?", title="Confirm")
        >>> if result == MessageBox.YES:
        ...     delete_item()
        """
        mb = QMessageBox(parent)
        mb.setText(text)
        mb.setStandardButtons(_BUTTON_YES | _BUTTON_NO)
        mb.setDefaultButton(_BUTTON_NO)
        mb.setWindowTitle(title)
        return mb.exec()

    @staticmethod
    def warning(
        text: str,
        title: str = "Warning",
        parent: QWidget | None = None,
    ) -> None:
        """Show a warning message box with an OK button.

        Parameters
        ----------
        text : str
            The warning message text.
        title : str, optional
            The title of the message box, by default "Warning".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Examples
        --------
        >>> MessageBox.warning("Configuration file not found")
        """
        QMessageBox.warning(parent, title, text)

    @staticmethod
    def critical(
        text: str,
        title: str = "Error",
        parent: QWidget | None = None,
    ) -> None:
        """Show a critical/error message box with an OK button.

        Parameters
        ----------
        text : str
            The error message text.
        title : str, optional
            The title of the message box, by default "Error".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Examples
        --------
        >>> MessageBox.critical("Failed to save file: permission denied")
        """
        QMessageBox.critical(parent, title, text)

    @staticmethod
    def information(
        text: str,
        title: str = "Information",
        parent: QWidget | None = None,
    ) -> None:
        """Show an information message box with an OK button.

        Parameters
        ----------
        text : str
            The information message text.
        title : str, optional
            The title of the message box, by default "Information".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Examples
        --------
        >>> MessageBox.information("Export completed successfully")
        """
        QMessageBox.information(parent, title, text)

    @staticmethod
    def question(
        text: str,
        title: str = "Question",
        parent: QWidget | None = None,
    ) -> int:
        """Show a question message box with Yes and No buttons.

        This is an alias for yes_no() with QMessageBox.question style.

        Parameters
        ----------
        text : str
            The question text.
        title : str, optional
            The title of the message box, by default "Question".
        parent : QWidget | None, optional
            The parent widget, by default None.

        Returns
        -------
        int
            The button clicked (MessageBox.YES or MessageBox.NO).

        Examples
        --------
        >>> result = MessageBox.question("Save changes before closing?")
        >>> if result == MessageBox.YES:
        ...     save_changes()
        """
        return QMessageBox.question(
            parent,
            title,
            text,
            _BUTTON_YES | _BUTTON_NO,
            _BUTTON_NO,
        )
