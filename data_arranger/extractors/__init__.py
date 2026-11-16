"""Extractor dispatch: extension -> function(rec) -> text sample."""
from . import chm, cnki, djvu, markup, media, office, pdf

_DISPATCH = {}


def _reg(fn, *exts):
    for e in exts:
        _DISPATCH[e] = fn


_reg(pdf.extract, "pdf")
_reg(office.extract_ooxml, "docx", "docm", "dotx", "pptx", "pptm", "ppsx", "potx", "xlsx", "xlsm")
_reg(office.extract_ole, "doc", "dot", "wps", "wpt", "ppt", "pps", "pot", "dps", "xls", "et")
_reg(office.extract_odf, "odt", "odp", "ods")
_reg(office.extract_rtf, "rtf")
_reg(office.extract_epub, "epub")
_reg(office.extract_mobi, "mobi", "azw", "azw3", "prc")
_reg(office.extract_fb2, "fb2")
_reg(cnki.extract, "caj", "cag", "kdh", "nh", "teb", "caa")
_reg(chm.extract, "chm")
_reg(djvu.extract, "djvu", "djv")
_reg(markup.extract_html, "html", "htm", "xhtml", "shtml")
_reg(markup.extract_mhtml, "mht", "mhtml")
_reg(markup.extract_xml, "xml")
_reg(markup.extract_text, "txt", "text", "md", "markdown", "rst", "log", "nfo", "tex", "bib", "csv", "tsv")


def get_extractor(ext: str, category: str):
    fn = _DISPATCH.get(ext)
    if fn:
        return fn
    if category == "Images":
        return media.extract_image
    if category in ("Audio", "Videos"):
        return media.extract_av
    return None
