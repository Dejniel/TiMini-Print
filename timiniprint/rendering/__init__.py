from PIL import Image


# TODO: Remove this Python 3.8 compatibility shim for older Pillow after requiring Python 3.10+.
if not hasattr(Image.Image, "get_flattened_data"):
    def _get_flattened_data(self, band=None):
        return tuple(self.getdata(band))

    Image.Image.get_flattened_data = _get_flattened_data


from .formats import (
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    TEXT_EXTENSIONS,
    document_kind,
    mm_to_px,
    normalized_width,
)

__all__ = [
    "IMAGE_EXTENSIONS",
    "PDF_EXTENSIONS",
    "SUPPORTED_DOCUMENT_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "document_kind",
    "mm_to_px",
    "normalized_width",
]
