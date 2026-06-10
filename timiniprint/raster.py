from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class PixelFormat(str, Enum):
    BW1 = "bw1"
    GRAY4 = "gray4"
    GRAY8 = "gray8"


class DitherMode(str, Enum):
    NONE = "none"
    FLOYD_STEINBERG = "floyd_steinberg"
    BAYER_4 = "bayer_4"
    BAYER_8 = "bayer_8"
    ATKINSON = "atkinson"


@dataclass(frozen=True)
class RasterBuffer:
    pixels: Sequence[int]
    width: int
    pixel_format: PixelFormat = PixelFormat.BW1

    def validate(self) -> None:
        if self.width <= 0:
            raise ValueError("Width must be greater than zero")
        if len(self.pixels) % self.width != 0:
            raise ValueError("Pixels length must be a multiple of width")

        if self.pixel_format == PixelFormat.BW1:
            invalid = next((value for value in self.pixels if value not in (0, 1)), None)
            if invalid is not None:
                raise ValueError("BW1 raster values must be 0 or 1")
            return

        upper_bound = 15 if self.pixel_format == PixelFormat.GRAY4 else 255
        invalid = next((value for value in self.pixels if value < 0 or value > upper_bound), None)
        if invalid is not None:
            raise ValueError(f"{self.pixel_format.value} raster values must fit in 0..{upper_bound}")

    @property
    def height(self) -> int:
        self.validate()
        return len(self.pixels) // self.width

    def slice_rows(self, start_row: int, row_count: int) -> "RasterBuffer":
        if start_row < 0 or row_count < 0:
            raise ValueError("Row offsets must be non-negative")
        start = start_row * self.width
        end = start + (row_count * self.width)
        return RasterBuffer(
            pixels=list(self.pixels[start:end]),
            width=self.width,
            pixel_format=self.pixel_format,
        )

    def packed_bytes(self) -> bytes:
        self.validate()
        if self.pixel_format == PixelFormat.GRAY8:
            return bytes(self.pixels)
        if self.pixel_format == PixelFormat.GRAY4:
            packed = bytearray()
            pixels = list(self.pixels)
            if len(pixels) % 2 != 0:
                raise ValueError("GRAY4 raster length must be even")
            for idx in range(0, len(pixels), 2):
                packed.append((pixels[idx] << 4) | pixels[idx + 1])
            return bytes(packed)
        raise ValueError("Only grayscale rasters can be packed into bytes")


@dataclass(frozen=True)
class RasterSet:
    rasters: Mapping[PixelFormat, RasterBuffer]

    def validate(self) -> None:
        if not self.rasters:
            raise ValueError("Raster set must not be empty")

        width = None
        height = None
        for pixel_format, raster in self.rasters.items():
            raster.validate()
            if pixel_format != raster.pixel_format:
                raise ValueError("Raster set keys must match raster pixel formats")
            if width is None:
                width = raster.width
                height = raster.height
                continue
            if raster.width != width or raster.height != height:
                raise ValueError("All rasters in a raster set must have matching dimensions")

    @property
    def width(self) -> int:
        self.validate()
        return next(iter(self.rasters.values())).width

    @property
    def height(self) -> int:
        self.validate()
        return next(iter(self.rasters.values())).height

    def get(self, pixel_format: PixelFormat) -> RasterBuffer | None:
        return self.rasters.get(pixel_format)

    def require(self, pixel_format: PixelFormat) -> RasterBuffer:
        raster = self.get(pixel_format)
        if raster is None:
            raise ValueError(f"Missing {pixel_format.value} raster in raster set")
        return raster

    @classmethod
    def from_single(cls, raster: RasterBuffer) -> "RasterSet":
        raster.validate()
        return cls(rasters={raster.pixel_format: raster})
