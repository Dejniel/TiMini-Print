from __future__ import annotations

from typing import List, Sequence

from PIL import Image
from PIL import ImageFilter
from PIL import ImageOps
from PIL import ImageStat

from .converters.base import Page
from ..protocol.types import PixelFormat, RasterBuffer, RasterSet


def apply_page_transforms(pages: Sequence[Page], rotate_90_clockwise: bool = False) -> List[Page]:
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


def image_to_bw_pixels(img: Image.Image, dither: bool) -> List[int]:
    if dither:
        img = img.convert("1")
        data = list(img.getdata())
        return [1 if p == 0 else 0 for p in data]
    img = img.convert("L")
    data = list(img.getdata())
    avg = sum(data) / len(data) if data else 0
    threshold = int(max(0, min(255, avg - 13)))
    return [1 if p <= threshold else 0 for p in data]


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


def _gray_values_to_raster(
    gray_values: List[int],
    width: int,
    pixel_format: PixelFormat,
) -> RasterBuffer:
    if pixel_format == PixelFormat.GRAY8:
        return RasterBuffer(pixels=gray_values, width=width, pixel_format=pixel_format)
    if pixel_format == PixelFormat.GRAY4:
        pixels = [15 - min(15, (value + 15) // 16) for value in gray_values]
        return RasterBuffer(pixels=pixels, width=width, pixel_format=pixel_format)
    raise ValueError(f"Unsupported grayscale raster format: {pixel_format.value}")


def _image_to_gray_values(
    img: Image.Image,
    *,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> List[int]:
    gray_image = _preprocess_gray_image(img, gamma_value) if gamma_handle else img.convert("L")
    return list(gray_image.getdata())


def image_to_gray_raster(
    img: Image.Image,
    pixel_format: PixelFormat,
    *,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> RasterBuffer:
    return _gray_values_to_raster(
        _image_to_gray_values(
            img,
            gamma_handle=gamma_handle,
            gamma_value=gamma_value,
        ),
        img.width,
        pixel_format,
    )


def image_to_raster(
    img: Image.Image,
    pixel_format: PixelFormat,
    *,
    dither: bool,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> RasterBuffer:
    if pixel_format == PixelFormat.BW1:
        return RasterBuffer(
            pixels=image_to_bw_pixels(img, dither=dither),
            width=img.width,
            pixel_format=PixelFormat.BW1,
        )
    return image_to_gray_raster(
        img,
        pixel_format,
        gamma_handle=gamma_handle,
        gamma_value=gamma_value,
    )


def image_to_raster_set(
    img: Image.Image,
    pixel_formats: Sequence[PixelFormat],
    *,
    dither: bool,
    gamma_handle: bool = False,
    gamma_value: float | None = None,
) -> RasterSet:
    if not pixel_formats:
        raise ValueError("At least one raster format must be requested")

    unique_formats = []
    seen = set()
    for pixel_format in pixel_formats:
        if pixel_format not in seen:
            unique_formats.append(pixel_format)
            seen.add(pixel_format)

    rasters = {}
    gray_values: List[int] | None = None
    for pixel_format in unique_formats:
        if pixel_format == PixelFormat.BW1:
            rasters[pixel_format] = image_to_raster(
                img,
                pixel_format,
                dither=dither,
            )
            continue
        if gray_values is None:
            gray_values = _image_to_gray_values(
                img,
                gamma_handle=gamma_handle,
                gamma_value=gamma_value,
            )
        rasters[pixel_format] = _gray_values_to_raster(gray_values, img.width, pixel_format)
    return RasterSet(rasters=rasters)
