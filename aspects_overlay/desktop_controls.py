"""Desktop Stop window in a separate Qt process (outside nested Gamescope)."""
import json
import os
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment


class DesktopControls:
    def __init__(self, target, cancel):
        desktop = os.environ.get('ASPECTS_DESKTOP_DISPLAY', '')
        if not target.gamescope or not desktop or desktop == target.display:
            raise RuntimeError('Не найден рабочий стол для отдельной кнопки «Стоп».')
        self.cancel, self.active = cancel, True
        self.buffer = b''
        self.process = QProcess()
        env = QProcessEnvironment.systemEnvironment()
        env.insert('DISPLAY', desktop)
        env.insert('QT_QPA_PLATFORM', 'xcb')
        self.process.setProcessEnvironment(env)
        self.process.setProgram(sys.executable)
        self.process.setArguments(['-m', 'aspects_overlay.desktop_controls'])
        self.process.start()
        if not self.process.waitForStarted(3000) or not self.process.waitForReadyRead(5000):
            self.close()
            raise RuntimeError('Не удалось открыть окно «Стоп» на рабочем столе.')
        initial = bytes(self.process.readAllStandardOutput())
        if initial != b'READY\n':
            self.close()
            raise RuntimeError('Окно «Стоп» не подтвердило готовность.')
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.finished.connect(self.exited)

    def read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line == b'STOP' and self.active:
                self.cancel()

    def exited(self, *_):
        if self.active:
            self.cancel()

    def send(self, message, finished=False):
        if finished:
            self.active = False
        self.process.write((json.dumps({'text': message, 'finished': finished})+'\n').encode())

    def close(self):
        self.active = False
        self.process.closeWriteChannel()
        self.process.waitForFinished(1000)


def main():
    from PySide6.QtCore import QSocketNotifier, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
    app = QApplication([])

    class Panel(QWidget):
        def __init__(self):
            super().__init__()
            self.finished = False
            self.setWindowTitle('Aspects Overlay — расстановка')
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setFixedSize(440, 200)
            layout = QVBoxLayout(self)
            self.label = QLabel('Идёт расстановка в Minecraft.\nМышь в игре заблокирована до завершения.\nМожно переключиться на другие приложения.')
            self.label.setWordWrap(True)
            layout.addWidget(self.label)
            self.button = QPushButton('Стоп')
            self.button.setMinimumHeight(44)
            self.button.clicked.connect(self.stop)
            layout.addWidget(self.button)

        def stop(self):
            if self.finished:
                self.close()
            else:
                print('STOP', flush=True)
                self.label.setText('Останавливаю расстановку…')
                self.button.setEnabled(False)

        def closeEvent(self, event):
            if not self.finished:
                print('STOP', flush=True)
            event.accept()

    panel = Panel()
    buffer = bytearray()
    notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read)

    def read(*_):
        data = os.read(sys.stdin.fileno(), 4096)
        if not data:
            notifier.setEnabled(False)
            panel.finished = True
            app.quit()
            return
        buffer.extend(data)
        while b'\n' in buffer:
            line, _, remainder = buffer.partition(b'\n')
            buffer[:] = remainder
            message = json.loads(line)
            panel.label.setText(message['text'])
            if message.get('finished'):
                panel.finished = True
                panel.button.setText('Закрыть')
                panel.button.setEnabled(True)

    notifier.activated.connect(read)
    panel.show()
    QTimer.singleShot(150, lambda: print('READY', flush=True))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
