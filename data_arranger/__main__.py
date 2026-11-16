import os
import sys


def _quiet_stdio():
    # A windowed .exe has no console; some libraries print progress bars.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def main():
    _quiet_stdio()
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        from .selftest import run
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.abspath("selftest_result.json")
        return run(out)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Data Arranger")
    app.setOrganizationName("DataArranger")
    app.setStyle("Fusion")

    from .gui.main_window import MainWindow
    win = MainWindow()
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        win.src_edit.setText(sys.argv[1])
    win.show()
    return app.exec_()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
