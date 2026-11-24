import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QPoint, Qt, QSettings
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
import sbharness.__main__ as desktop
from sbharness.skills import BUILTINS, Skill


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_chat_renders_markdown_without_rule_disclosure(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                window.add_message('assistant', '**你好。**')
                self.assertEqual(window.chat.toPlainText().strip(), 'SB Harness\n你好。')
                self.assertNotIn('本轮演出', window.chat.toPlainText())
                self.assertFalse(window.mode.parent() is window.chat.parent())
                bubble = window.chat.entries[0].findChild(desktop.MessageBubble)
                self.assertGreaterEqual(bubble.browser.height(), bubble.browser.document().size().height() + 12)
                self.assertTrue({'Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei', 'DengXian'} & set(bubble.browser.document().defaultFont().families()))
                window.close()

    def test_assistant_text_reflows_to_actual_viewport_width(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                window.resize(760, 650)
                text = (
                    '关于“被告是不是傻子”一案的书\n\n一、争议确认\n'
                    '用户以一个问号主张被告系傻子。理由是财务报告被写成了启动申请书，'
                    '代码至今未交付。被告对此不提异议，双方均未达成共识。\n\n'
                    '二、证物到庭\n证物一：那份财务报告从纸页里站起来，并推翻了此前的事实认定。'
                )
                window.add_message('assistant', text)
                bubble = window.chat.entries[-1].findChild(desktop.MessageBubble)
                window.show()
                self.app.processEvents()
                viewport_width = bubble.browser.viewport().width()
                self.assertGreaterEqual(bubble.width(), 400)
                self.assertAlmostEqual(bubble.browser.document().textWidth(), viewport_width, delta=1)
                self.assertGreaterEqual(bubble.browser.height(), bubble.browser.document().size().height() + 12)
                self.assertIn('推翻了此前的事实认定。', bubble.plain_text())
                window.close()

    def test_heavy_intensity_is_default_and_story_reset_preserves_chat(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                self.assertEqual(window.intensity.currentIndex(), 3)
                window.add_message('user', '写一份预算')
                window.current_conversation['story'] = '冰箱正在审计预算。'
                messages_before = list(window.current_conversation['messages'])
                timeline_before = list(window.current_conversation['timeline'])
                with patch.object(desktop.QMessageBox, 'information'):
                    window.reset_story()
                self.assertEqual(window.current_conversation['story'], '')
                self.assertTrue(window.current_conversation['story_reset'])
                self.assertEqual(window.current_conversation['messages'], messages_before)
                self.assertEqual(window.current_conversation['timeline'], timeline_before)
                window.save_story_state({
                    'conversation_id': window.current_conversation['id'],
                    'summary': '预算已经改用摩拉结算。',
                })
                self.assertEqual(window.current_conversation['story'], '预算已经改用摩拉结算。')
                self.assertFalse(window.current_conversation['story_reset'])
                window.close()

    def test_previous_default_intensity_migrates_to_heavy(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            settings.setValue('intensity', 1)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                self.assertEqual(window.intensity.currentIndex(), 3)
                self.assertEqual(settings.value('abstractness_defaults_version'), 1)
                window.close()

    def test_permission_migration_and_shared_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            settings.setValue('auto', True)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                self.assertEqual(window.permission_mode, 'approve_safe')
                self.assertEqual(settings.value('permission_mode'), 'approve_safe')
                self.assertEqual(settings.value('permission_defaults_version'), 1)
                self.assertEqual(window.permission_settings_box.currentData(), 'approve_safe')
                window.permission_settings_box.setCurrentIndex(window.permission_settings_box.findData('ask'))
                self.assertEqual(window.permission_mode, 'ask')
                self.assertEqual(settings.value('permission_mode'), 'ask')
                self.assertIn('每次询问', window.permission_button.text())
                with patch.object(desktop.QMessageBox, 'question', return_value=desktop.QMessageBox.Yes):
                    window.permission_settings_box.setCurrentIndex(window.permission_settings_box.findData('full'))
                self.assertEqual(window.permission_mode, 'full')
                self.assertTrue(settings.value('full_access_acknowledged', type=bool))
                self.assertIn('完全访问', window.permission_button.text())
                window.close()

    def test_permission_migration_defaults_and_false_legacy_value(self):
        for existing, expected in ((False, 'ask'), (None, 'ask')):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp:
                settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
                if existing is not None:
                    settings.setValue('auto', existing)
                with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                    window = desktop.Window()
                    self.assertEqual(window.permission_mode, expected)
                    window.close()

    def test_forced_skill_is_visible_and_persists_across_window_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                first = desktop.Window()
                available = {first.force.itemData(i) for i in range(1, first.force.count())}
                self.assertEqual(available, {item[0] for item in BUILTINS})
                index = first.force.findData('doubao')
                self.assertGreaterEqual(index, 0)
                self.assertIn('豆包人格', first.force.itemText(index))
                first.force.setCurrentIndex(index)
                self.assertEqual(settings.value('forced_skill'), 'doubao')
                first.close()
                second = desktop.Window()
                self.assertEqual(second.force.currentData(), 'doubao')
                second.close()

    def test_full_access_decline_restores_shared_permission_state(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                with patch.object(desktop.QMessageBox, 'question', return_value=desktop.QMessageBox.No):
                    window.set_permission_mode('full', 'chat')
                self.assertEqual(window.permission_mode, 'ask')
                self.assertEqual(window.permission_settings_box.currentData(), 'ask')
                self.assertEqual(settings.value('permission_mode'), 'ask')
                window.close()

    def test_composer_enter_shift_enter_ctrl_enter_and_ime(self):
        composer = desktop.MessageComposer()
        sent = []
        composer.send_requested.connect(lambda: sent.append(True))
        enter = lambda modifiers=Qt.NoModifier: composer.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Return, modifiers, '\r')
        )
        enter()
        self.assertEqual(len(sent), 1)
        enter(Qt.ShiftModifier)
        self.assertIn('\n', composer.toPlainText())
        enter(Qt.ControlModifier)
        self.assertEqual(len(sent), 2)
        composer.inputMethodEvent(QInputMethodEvent('候选', []))
        enter()
        self.assertEqual(len(sent), 2)
        commit = QInputMethodEvent('', [])
        commit.setCommitString('字')
        composer.inputMethodEvent(commit)
        enter()
        self.assertEqual(len(sent), 3)
        composer.close()

    def test_composer_height_is_compact_and_grows_with_content(self):
        composer = desktop.MessageComposer()
        self.app.processEvents()
        self.assertLessEqual(composer.height(), 70)
        composer.setPlainText('第一行\n第二行\n第三行\n第四行')
        self.app.processEvents()
        self.assertGreater(composer.height(), 70)
        self.assertLessEqual(composer.height(), 140)
        composer.clear()
        self.app.processEvents()
        self.assertLessEqual(composer.height(), 70)
        composer.close()

    def test_approval_prompt_is_nonmodal_and_stop_can_cancel_it(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                class WorkerStub:
                    def __init__(self):
                        self.approved = None
                        self.answer_event = threading.Event()
                        self.cancel = threading.Event()
                        self.stopped = False
                    def stop(self):
                        self.stopped = True
                        self.cancel.set()
                        self.answer_event.set()
                worker = WorkerStub()
                window.worker = worker
                window.request_approval('运行命令：echo test')
                self.app.processEvents()
                self.assertIsNotNone(window.approval_dialog)
                self.assertFalse(worker.answer_event.is_set())
                window.stop()
                self.app.processEvents()
                self.assertTrue(worker.stopped)
                self.assertTrue(worker.answer_event.is_set())
                self.assertIsNone(window.approval_dialog)
                window.worker = None
                window.close()

    def test_cancelled_worker_does_not_emit_a_late_approval_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = self.make_worker(temp, 'none')
            prompts = []
            worker.approval.connect(prompts.append, Qt.DirectConnection)
            worker.cancel.set()
            self.assertFalse(worker.approve('should not appear'))
            self.assertEqual(prompts, [])

    def test_appeal_restores_us_region_and_wallet_currency(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
            with patch.object(desktop, 'DATA', Path(temp) / 'data'), patch.object(desktop, 'SKILLS', Path(temp) / 'skills'), patch.object(desktop, 'QSettings', return_value=settings):
                window = desktop.Window()
                window.state['profile']['country'] = 'CN'
                window.wallet.change_country('CN')
                window.country_box.setCurrentIndex(window.country_box.findData('CN'))
                with patch.object(window.ban_overlay, 'show_denied'):
                    window.appeal()
                self.assertEqual(window.state['profile']['country'], 'US')
                self.assertEqual(window.country_box.currentData(), 'US')
                self.assertEqual(window.wallet.currency, 'USD')
                self.assertEqual(window.state['wallet']['ledger'][-1]['kind'], 'currency_conversion')
                self.assertEqual(window.state['wallet']['ledger'][-1]['to'], 'USD')
                window.close()

    def test_ban_copy_has_enough_vertical_room(self):
        overlay = desktop.BanOverlay()
        overlay.resize(1032, 397)
        overlay.show_blocked(desktop.REGION_BAN_COPY)
        self.app.processEvents()
        self.assertGreaterEqual(overlay.reason.minimumHeight(), 112)
        self.assertGreaterEqual(overlay.reason.height(), overlay.reason.heightForWidth(overlay.reason.width()))
        self.assertEqual(overlay.reason.text(), desktop.REGION_BAN_COPY)
        self.assertIn('Claude', desktop.REGION_BAN_COPY)
        self.assertIn('保障团队', desktop.REGION_BAN_COPY)
        self.assertIn('过于成功', desktop.APPEAL_DENIED_COPY)
        overlay.allow_close = True
        overlay.close()

    def make_worker(self, root, skill):
        config = {'workspace':root, 'permission_mode':'ask', 'base':'https://example.com/v1', 'key':'x', 'model':'m', 'mode':'认真犯病'}
        return desktop.Worker(config, [{'role':'user','content':'你好'}], [Skill(skill, skill, '', [])], None)

    def test_deep_thinking_waits_until_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = self.make_worker(temp, 'deepseek'); statuses = []; done = []; story = []
            worker.status.connect(statuses.append, Qt.DirectConnection)
            worker.done.connect(lambda result, _: done.append(result), Qt.DirectConnection)
            worker.story_updated.connect(story.append, Qt.DirectConnection)
            thread = threading.Thread(target=worker.run); thread.start()
            deadline = time.monotonic() + 2
            while not statuses and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual(statuses, ['正在思考…']); self.assertTrue(thread.is_alive())
            worker.stop(); thread.join(2)
            self.assertFalse(thread.is_alive()); self.assertEqual(done, ['已停止'])
            self.assertEqual(len(story), 2)
            self.assertIn('持续不返回', story[0]['summary'])
            self.assertIn('已被用户停止', story[1]['summary'])

    def test_restriction_waits_for_appeal_then_denies(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = self.make_worker(temp, 'claude'); appeals = []; states = []; done = []; story = []
            worker.appeal.connect(appeals.append, Qt.DirectConnection)
            worker.ban_state.connect(states.append, Qt.DirectConnection)
            worker.done.connect(lambda result, _: done.append(result), Qt.DirectConnection)
            worker.story_updated.connect(story.append, Qt.DirectConnection)
            thread = threading.Thread(target=worker.run); thread.start()
            deadline = time.monotonic() + 2
            while not appeals and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual(appeals, [True]); self.assertTrue(thread.is_alive())
            worker.appeal_event.set(); thread.join(2)
            self.assertFalse(thread.is_alive()); self.assertEqual(done, ['']); self.assertEqual(appeals, [True, False])
            self.assertEqual(states, ['blocked', 'denied'])
            self.assertEqual(len(story), 2)
            self.assertIn('申诉窗口', story[0]['summary'])
            self.assertIn('复核已结束', story[1]['summary'])


class WindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open_window(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
        self.enterContext(patch.object(desktop, 'DATA', Path(temp) / 'data'))
        self.enterContext(patch.object(desktop, 'SKILLS', Path(temp) / 'skills'))
        self.enterContext(patch.object(desktop, 'QSettings', return_value=settings))
        window = desktop.Window()
        self.addCleanup(window.close)
        return window

    def settle(self, rounds=4):
        for _ in range(rounds):
            self.app.processEvents()


class LayoutFixTests(WindowTestCase):
    def test_sidebar_labels_show_the_sidebar_colour(self):
        window = self.open_window()
        window.show()
        self.settle()
        label = window.sidebar.findChild(desktop.QLabel, 'brand')
        point = label.mapTo(window.sidebar, QPoint(1, 1))
        pixmap = window.sidebar.grab()
        ratio = pixmap.devicePixelRatio()
        colour = pixmap.toImage().pixelColor(int(point.x() * ratio), int(point.y() * ratio))
        self.assertEqual(colour.name().upper(), window.theme.sidebar.upper())

    def test_api_profile_selector_is_filled_on_start(self):
        window = self.open_window()
        self.assertEqual(window.profile_box.count(), len(window.api_profiles))
        self.assertEqual(window.profile_box.currentData(), window.active_profile_id)
        self.assertEqual(window.profile_box.currentText(), window.api_profiles[0]['label'])

    def test_chat_follows_window_width_without_clipping(self):
        window = self.open_window()
        window.resize(1180, 760)
        window.show()
        self.settle()
        window.add_message('user', '帮我把这周的预算整理成一份报告，存到工作目录里')
        window.add_message('assistant', '冰箱审批部门放行了两项。' * 20)
        window._tool_event({
            'call_id': 'long', 'name': 'run_command',
            'args': {'command': 'Get-ChildItem ' + 'x' * 60}, 'status': 'running',
        })
        long_title = '一个很长的对话标题' * 8
        window.conversation_title.setText(long_title)
        self.settle()
        window.resize(680, 560)
        self.settle(6)
        viewport = window.chat.viewport().width()
        self.assertLessEqual(window.chat.widget().width(), viewport)
        for entry in window.chat.entries:
            right = entry.mapTo(window.chat.viewport(), QPoint(entry.width(), 0)).x()
            self.assertLessEqual(right, viewport)
        self.assertLess(window.conversation_title.geometry().right(), window.story_button.geometry().left())
        self.assertEqual(window.conversation_title.text(), long_title)
        self.assertIn(window.chat.cards['long'].title_text(), window.chat.toPlainText())

    def test_sidebar_list_elides_and_project_switch_shows_the_project(self):
        window = self.open_window()
        window.resize(1180, 760)
        window.show()
        self.settle()
        window.add_message('user', '把一份很长很长的会议纪要改写成冰箱法庭的判决书然后再改回来')
        self.settle()
        history = window.history_list
        self.assertEqual(history.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertLessEqual(history.visualItemRect(history.item(0)).right(), history.viewport().width())
        self.assertEqual(window.project_name_label.text(), window.state['projects'][0]['name'])
        for button in (window.skill_button, window.settings_button):
            self.assertEqual(button.text(), '')
            self.assertFalse(button.icon().isNull())


class StyleTests(WindowTestCase):
    def test_every_style_redraws_the_same_conversation(self):
        window = self.open_window()
        window.resize(1180, 760)
        window.show()
        window.add_message('user', '帮我整理预算')
        window._tool_event({'call_id': 'r1', 'name': 'read_file', 'args': {'path': 'budget.csv'}, 'status': 'done'})
        window.add_message('assistant', '**报告已经起草。**')
        self.settle()
        expected = window.chat.toPlainText()
        for theme_id, theme in desktop.THEMES.items():
            with self.subTest(style=theme_id):
                window.apply_theme(theme_id)
                self.settle()
                self.assertEqual(window.theme.id, theme_id)
                self.assertEqual(window.sidebar.isVisible(), theme.nav == 'sidebar')
                self.assertEqual(window.rail.isVisible(), theme.nav == 'rail')
                self.assertEqual(window.switch_button.isVisible(), theme.nav == 'none')
                self.assertEqual(window.chat.toPlainText(), expected)
                self.assertEqual(window.chat.cards['r1'].kind, theme.tool_style)
                self.assertTrue(window.style_cards[theme_id].isChecked())
                self.assertIn(theme.accent.lower(), window.styleSheet().lower())

    def test_style_choice_persists_and_settings_cards_switch_it(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat)
        self.enterContext(patch.object(desktop, 'DATA', Path(temp) / 'data'))
        self.enterContext(patch.object(desktop, 'SKILLS', Path(temp) / 'skills'))
        self.enterContext(patch.object(desktop, 'QSettings', return_value=settings))
        first = desktop.Window()
        self.assertEqual(first.theme.id, desktop.DEFAULT_THEME)
        first.style_cards['console'].click()
        self.assertEqual(first.theme.id, 'console')
        self.assertEqual(settings.value('theme'), 'console')
        first.close()
        second = desktop.Window()
        self.addCleanup(second.close)
        self.assertEqual(second.theme.id, 'console')
        self.assertFalse(second.sidebar.isVisibleTo(second))
        self.assertTrue(second.style_cards['console'].isChecked())

    def test_inline_approval_allows_and_keeps_the_decision(self):
        window = self.open_window()
        window.apply_theme('docket')

        class WorkerStub:
            def __init__(self):
                self.approved = None
                self.answer_event = threading.Event()
                self.cancel = threading.Event()

            def stop(self):
                self.cancel.set()
                self.answer_event.set()

        worker = WorkerStub()
        window.worker = worker
        self.addCleanup(setattr, window, 'worker', None)
        window._tool_event({'call_id': 'w1', 'name': 'write_file', 'args': {'path': 'report.md'}, 'status': 'waiting'})
        window.request_approval('写入文件\n目标：report.md\n内容：\n第一行')
        row = window.chat.cards['w1']
        self.assertIs(window.approval_dialog, row)
        self.assertTrue(row.approval.isVisibleTo(row))
        self.assertIn('目标：report.md', row.approval.detail.toPlainText())
        row.approval.allow_button.click()
        self.assertTrue(worker.approved)
        self.assertTrue(worker.answer_event.is_set())
        self.assertIsNone(window.approval_dialog)
        stored = next(item['event'] for item in window.current_conversation['timeline'] if item.get('kind') == 'tool')
        self.assertEqual(stored['approval'], 'approved')
        window.apply_theme('yolk')
        self.assertEqual(window.chat.cards['w1'].tool_event['approval'], 'approved')

    def test_send_button_turns_into_stop_while_running(self):
        window = self.open_window()
        for theme_id in ('yolk', 'console'):
            with self.subTest(style=theme_id):
                window.apply_theme(theme_id)
                window._set_running(True)
                self.assertEqual(window.action_button.property('state'), 'stop')
                self.assertEqual(window.action_button.accessibleName(), '停止')
                window._set_running(False)
                self.assertEqual(window.action_button.property('state'), 'send')
                self.assertEqual(window.action_button.accessibleName(), '发送')
        self.assertEqual(window.action_button.text(), 'Enter 发送')

    def test_behaviour_chip_and_settings_stay_in_sync(self):
        window = self.open_window()
        window._set_intensity(1)
        window._set_mode('纯种 SB')
        self.assertEqual(window.intensity.currentIndex(), 1)
        self.assertEqual(window.mode.currentText(), '纯种 SB')
        self.assertEqual(window.behavior_button.text(), '纯种 SB')
        self.assertEqual(window.settings.value('mode'), '纯种 SB')
        self.assertEqual(int(window.settings.value('intensity')), 1)


if __name__ == '__main__': unittest.main()
