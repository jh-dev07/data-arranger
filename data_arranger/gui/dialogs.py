from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLabel, QLineEdit, QTextBrowser, QVBoxLayout)

from ..formats import supported_extensions
from ..organizer import PLACEHOLDERS, Settings


class LayoutDialog(QDialog):
    """Edit the folder/file naming templates."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Folder layout and naming")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        help_ = QLabel(
            "Templates build the folder path inside the output folder. Use / to "
            "make sub-folders. Available fields:<br><code>"
            + "  ".join("{%s}" % p for p in PLACEHOLDERS) + "</code><br>"
            "<code>{language}</code> is the language name (e.g. Chinese) and "
            "<code>{lang}</code> its code (zh).")
        help_.setWordWrap(True)
        lay.addWidget(help_)
        form = QFormLayout()
        self.doc = QLineEdit(settings.doc_template)
        self.media = QLineEdit(settings.media_template)
        self.other = QLineEdit(settings.other_template)
        self.rename_tpl = QLineEdit(settings.rename_template)
        self.remove_empty = QCheckBox("After moving, delete source folders left empty")
        self.remove_empty.setChecked(settings.remove_empty_dirs)
        form.addRow("Documents:", self.doc)
        form.addRow("Images / Videos / Audio:", self.media)
        form.addRow("Other files:", self.other)
        form.addRow("New file name (when renaming):", self.rename_tpl)
        form.addRow("", self.remove_empty)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel |
                                   QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(buttons)

    def _defaults(self):
        d = Settings()
        self.doc.setText(d.doc_template)
        self.media.setText(d.media_template)
        self.other.setText(d.other_template)
        self.rename_tpl.setText(d.rename_template)
        self.remove_empty.setChecked(d.remove_empty_dirs)

    def apply_to(self, settings: Settings):
        settings.doc_template = self.doc.text().strip() or Settings.doc_template
        settings.media_template = self.media.text().strip() or Settings.media_template
        settings.other_template = self.other.text().strip() or Settings.other_template
        settings.rename_template = self.rename_tpl.text().strip() or Settings.rename_template
        settings.remove_empty_dirs = self.remove_empty.isChecked()


class FormatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Supported formats")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        html = []
        for cat, exts in sorted(supported_extensions().items()):
            html.append("<h3>%s</h3><p>%s</p>" % (cat, ", ".join("." + e for e in exts)))
        html.append(
            "<h3>Notes</h3><ul>"
            "<li>Files with other extensions are still arranged under <i>Other</i>; "
            "files with a missing or wrong extension are recognised by their content when possible.</li>"
            "<li>CNKI CAJ/HN/PDG files and scanned PDFs store pages as images. Their "
            "metadata comes from the file header, table of contents and file name; "
            "OCR is not performed.</li>"
            "<li>DjVu text is read fully when DjVuLibre (djvused/djvutxt) is installed.</li>"
            "</ul>")
        view.setHtml("".join(html))
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
