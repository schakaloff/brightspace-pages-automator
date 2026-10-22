import sys
sys.path.insert(0, "src")


def test_step_button_initial_state_locked(qtbot):
    from gui_sidebar import StepButton
    btn = StepButton(1, "checker", "Checker")
    qtbot.addWidget(btn)
    assert btn.get_state() == StepButton.LOCKED
    assert not btn.isEnabled()


def test_step_button_unlock(qtbot):
    from gui_sidebar import StepButton
    btn = StepButton(1, "checker", "Checker")
    qtbot.addWidget(btn)
    btn.set_state(StepButton.PENDING)
    assert btn.get_state() == StepButton.PENDING
    assert btn.isEnabled()


def test_sidebar_step_clicked_signal(qtbot):
    from gui_sidebar import Sidebar
    sidebar = Sidebar([(1, "checker", "Checker"), (2, "collect", "Collect")])
    qtbot.addWidget(sidebar)
    received = []
    sidebar.step_clicked.connect(received.append)
    # Step 1 starts locked — unlock it first
    sidebar.set_step_state(1, "pending")
    sidebar._step_buttons[1].click()
    assert received == [1]


def test_sidebar_set_active_marks_active(qtbot):
    from gui_sidebar import Sidebar, StepButton
    sidebar = Sidebar([(1, "checker", "Checker")])
    qtbot.addWidget(sidebar)
    sidebar.set_step_state(1, StepButton.PENDING)
    sidebar.set_active(1)
    assert sidebar._step_buttons[1].get_state() == StepButton.ACTIVE


def test_collapsed_sidebar_keeps_navigation_settings_and_keyboard_toggle(qtbot):
    from PySide6.QtCore import Qt
    from gui_sidebar import Sidebar, StepButton
    sidebar = Sidebar([(1, 'checker', 'Check & fix'), (6, 'kaltura', 'Media')])
    qtbot.addWidget(sidebar)
    sidebar.resize(sidebar.width(), 500)
    sidebar.show()
    sidebar.set_step_state(1, StepButton.PENDING)
    sidebar.set_step_state(6, StepButton.PENDING)
    sidebar.set_active(1)
    changed, selected, settings = [], [], []
    sidebar.collapsed_changed.connect(changed.append)
    sidebar.step_clicked.connect(selected.append)
    sidebar.settings_clicked.connect(lambda: settings.append(True))
    qtbot.mouseClick(sidebar._toggle_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sidebar.width() == sidebar.COLLAPSED_WIDTH)
    assert sidebar._brand.isHidden()
    assert sidebar._toggle_btn.accessibleName() == 'Expand sidebar'
    assert sidebar._step_buttons[6].accessibleName() == 'Media'
    assert sidebar._step_buttons[6].toolTip().startswith('Media')
    qtbot.mouseClick(sidebar._step_buttons[6], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(sidebar._settings_btn, Qt.MouseButton.LeftButton)
    assert selected == [6] and settings == [True]
    assert sidebar._step_buttons[1].get_state() == StepButton.ACTIVE
    sidebar._toggle_btn.setFocus()
    qtbot.keyClick(sidebar._toggle_btn, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: sidebar._brand.isVisible())
    assert sidebar.width() == sidebar.EXPANDED_WIDTH
    assert changed == [True, False]


def test_sidebar_can_reverse_direction_mid_animation(qtbot):
    from gui_sidebar import Sidebar
    sidebar = Sidebar([(1, 'checker', 'Check & fix')])
    qtbot.addWidget(sidebar)
    sidebar.show()
    sidebar.set_collapsed(True)
    qtbot.wait(40)
    sidebar.set_collapsed(False)
    qtbot.waitUntil(lambda: sidebar._brand.isVisible())
    assert sidebar.width() == sidebar.EXPANDED_WIDTH
    assert not sidebar.is_collapsed()
