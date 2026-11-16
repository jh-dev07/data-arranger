import sys


def main():
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
    if len(sys.argv) > 1:
        win.src_edit.setText(sys.argv[1])
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
