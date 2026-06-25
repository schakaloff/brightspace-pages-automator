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
