"""Generate synthetic PDF fixtures using system Cairo/Noto; not a runtime dependency.

Text PDF embeds subset fonts/Unicode mappings. Scan PDF paints pixels only, no OCR layer.
PNG previews show the same source drawing, not an independent rendering of the PDF.
"""
import ctypes as C
from pathlib import Path
import sys


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    cairo = C.CDLL("libcairo.so.2")
    P, D, I, S = C.c_void_p, C.c_double, C.c_int, C.c_char_p
    signatures = {
        "pdf_surface_create": (P, [S, D, D]), "pdf_surface_set_metadata": (None, [P, I, S]),
        "image_surface_create": (P, [I, I, I]), "create": (P, [P]),
        "select_font_face": (None, [P, S, I, I]), "set_font_size": (None, [P, D]),
        "set_source_rgb": (None, [P, D, D, D]), "paint": (None, [P]),
        "move_to": (None, [P, D, D]), "show_text": (None, [P, S]),
        "show_page": (None, [P]), "destroy": (None, [P]),
        "set_source_surface": (None, [P, P, D, D]),
        "surface_write_to_png": (I, [P, S]), "surface_finish": (None, [P]),
        "surface_status": (I, [P]), "surface_destroy": (None, [P]), "status": (I, [P]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(cairo, "cairo_" + name)
        function.restype, function.argtypes = result, args
        setattr(cairo, name, function)

    def pdf(name):
        surface = cairo.pdf_surface_create(str(output/name).encode(), 420, 240)
        # CAIRO_PDF_METADATA_CREATE_DATE/MOD_DATE: stable rather than wall-clock metadata.
        for field in (5, 6):
            cairo.pdf_surface_set_metadata(surface, field, b"2026-10-18T00:00:00Z")
        return surface

    def draw(context, lines):
        cairo.set_source_rgb(context, 1, 1, 1)
        cairo.paint(context)
        cairo.set_source_rgb(context, 0, 0, 0)
        cairo.select_font_face(context, b"Noto Sans CJK SC", 0, 0)
        cairo.set_font_size(context, 24)
        for y, text in zip((80, 130), lines):
            cairo.move_to(context, 30, y)
            cairo.show_text(context, text.encode())
        assert cairo.status(context) == 0

    text_pdf = pdf("chinese.pdf")
    context = cairo.create(text_pdf)
    for page, lines in enumerate((("部署说明", "数据库备份"), ("恢复步骤", "验证数据完整性")), 1):
        draw(context, lines)
        cairo.show_page(context)
        raster = cairo.image_surface_create(0, 420, 240)  # ARGB32
        raster_context = cairo.create(raster)
        draw(raster_context, lines)
        assert cairo.surface_write_to_png(raster, str(output/f"source-page-{page}.png").encode()) == 0
        if page == 1:
            scan = pdf("scanned.pdf")
            scan_context = cairo.create(scan)
            cairo.set_source_surface(scan_context, raster, 0, 0)
            cairo.paint(scan_context)
            cairo.show_page(scan_context)
            cairo.destroy(scan_context)
            cairo.surface_finish(scan)
            assert cairo.surface_status(scan) == 0
            cairo.surface_destroy(scan)
        cairo.destroy(raster_context)
        cairo.surface_destroy(raster)
    cairo.destroy(context)
    cairo.surface_finish(text_pdf)
    assert cairo.surface_status(text_pdf) == 0
    cairo.surface_destroy(text_pdf)


if __name__ == "__main__":
    main()
