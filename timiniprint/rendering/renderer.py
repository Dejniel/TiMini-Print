from __future__ import annotations

from io import BytesIO
from typing import Sequence

from PIL import Image
from PIL import ImageFilter
from PIL import ImageOps
from PIL import ImageStat

from .converters.base import Page
from .dither import DitherMode, render_bw_image
from ..raster import PixelFormat, RasterBuffer, RasterSet


def apply_page_transforms(pages: Sequence[Page], rotate_90_clockwise: bool = False) -> list[Page]:
    if not rotate_90_clockwise:
        return list(pages)
    return [
        Page(
            image=page.image.transpose(Image.Transpose.ROTATE_270),
            dither=page.dither,
            is_text=page.is_text,
        )
        for page in pages
    ]


def _auto_gray_gamma(gray: Image.Image) -> float:
    mean = ImageStat.Stat(gray).mean[0]
    if mean >= 180:
        if mean < 190:
            return 1.05
        if mean < 210:
            return 1.1
        if mean < 230:
            return 1.2
        if mean >= 240:
            return 1.3
        return 1.0
    if mean < 130:
        return 0.9
    if mean < 150:
        return 0.95
    if mean < 170:
        return 1.0
    return 1.0


def _gray_enhance_alpha(gray: Image.Image) -> float:
    return 1.07 if ImageStat.Stat(gray).mean[0] >= 200 else 1.06


def _apply_gamma(gray: Image.Image, gamma: float) -> Image.Image:
    if gamma == 1.0:
        return gray
    lut = [max(0, min(255, round(((value / 255.0) ** gamma) * 255.0))) for value in range(256)]
    return gray.point(lut)


def _preprocess_gray_image(img: Image.Image, gamma_value: float | None = None) -> Image.Image:
    gray = img.convert("L")
    blurred = gray.filter(ImageFilter.GaussianBlur(radius=1.0))
    gamma = _auto_gray_gamma(blurred) if gamma_value is None else gamma_value
    transformed = _apply_gamma(blurred, gamma)
    enhanced = transformed.point(
        [max(0, min(255, round(value * _gray_enhance_alpha(transformed)))) for value in range(256)]
    )
    equalized = ImageOps.equalize(enhanced)
    return equalized.filter(ImageFilter.Kernel((3, 3), [0, -1, 0, -1, 5, -1, 0, -1, 0], scale=1))


def _quantize_gray4_print_image(img: Image.Image) -> Image.Image:
    gray = img.convert("L")
    out = Image.new("L", gray.size)
    out.putdata([min(15, (value + 15) // 16) * 16 for value in gray.getdata()])
    return out


def prepare_print_image(
    img: Image.Image,
    pixel_format: PixelFormat,
    *,
    dither_mode: DitherMode,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> Image.Image:
    """Apply print raster preprocessing, before protocol-specific encoding."""
    if pixel_format == PixelFormat.BW1:
        return render_bw_image(img, dither_mode)

    gray = _preprocess_gray_image(img, gamma_value) if gamma_handle else img.convert("L")
    if pixel_format == PixelFormat.GRAY8:
        return gray
    if pixel_format == PixelFormat.GRAY4:
        return _quantize_gray4_print_image(gray)
    raise ValueError(f"Unsupported raster format: {pixel_format.value}")


def encode_print_image(img: Image.Image, pixel_format: PixelFormat) -> RasterBuffer:
    if pixel_format == PixelFormat.BW1:
        return RasterBuffer(
            pixels=[1 if p <= 127 else 0 for p in img.convert("L").getdata()],
            width=img.width,
            pixel_format=PixelFormat.BW1,
        )
    if pixel_format == PixelFormat.GRAY8:
        return RasterBuffer(
            pixels=list(img.convert("L").getdata()),
            width=img.width,
            pixel_format=pixel_format,
        )
    if pixel_format == PixelFormat.GRAY4:
        return RasterBuffer(
            pixels=[
                15 - min(15, (value + 15) // 16)
                for value in img.convert("L").getdata()
            ],
            width=img.width,
            pixel_format=pixel_format,
        )
    raise ValueError(f"Unsupported raster format: {pixel_format.value}")


def render_preview_png(
    img: Image.Image,
    pixel_format: PixelFormat,
    *,
    dither_mode: DitherMode,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> bytes:
    preview = prepare_print_image(
        img,
        pixel_format,
        dither_mode=dither_mode,
        gamma_handle=gamma_handle,
        gamma_value=gamma_value,
    )
    if pixel_format == PixelFormat.GRAY4:
        preview = preview.point([min(255, round(value * 17 / 16)) for value in range(256)])
    out = BytesIO()
    preview.convert("L").save(out, format="PNG")
    return out.getvalue()


def render_raster_set(
    img: Image.Image,
    pixel_formats: Sequence[PixelFormat],
    *,
    dither_mode: DitherMode,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> RasterSet:
    if not pixel_formats:
        raise ValueError("At least one raster format must be requested")

    rasters = {}
    seen = set()
    for pixel_format in pixel_formats:
        if pixel_format in seen:
            continue
        seen.add(pixel_format)
        rasters[pixel_format] = encode_print_image(
                prepare_print_image(
                    img,
                    pixel_format,
                    dither_mode=dither_mode,
                    gamma_handle=gamma_handle,
                    gamma_value=gamma_value,
            ),
            pixel_format,
        )
    return RasterSet(rasters=rasters)
