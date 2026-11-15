from PySide6.QtWidgets import QDialogButtonBox

from bluebench.ble.formats import ValueFormat
from bluebench.ui import value_dialog
from bluebench.ui.value_dialog import WriteDialog


def ok_button(d):
    return d.buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_validation_and_write_type(qapp):
    d = WriteDialog("LED", can_request=False, can_command=True)
    assert d.cmd_radio.isChecked() and not d.req_radio.isEnabled()
    d.value_edit.setCurrentText("0x0")
    assert not ok_button(d).isEnabled()
    d.value_edit.setCurrentText("01-FF")
    assert ok_button(d).isEnabled() and d.data == b"\x01\xff"
    d.accept()
    assert d.with_response is False


def test_history_restores_format(qapp):
    value_dialog._history.clear()
    d = WriteDialog("Name", can_request=True, can_command=False, default_format=ValueFormat.UTF8)
    d.value_edit.setCurrentText("hello")
    d.accept()
    assert value_dialog._history[0] == ("hello", ValueFormat.UTF8)

    d2 = WriteDialog("Other", can_request=True, can_command=False)  # defaults to Hex
    d2._history_picked(0)
    assert d2.fmt is ValueFormat.UTF8 and d2.data == b"hello"
