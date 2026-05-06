from __future__ import annotations

from enum import Enum
from dataclasses import dataclass

from ..raster import PixelFormat


class ImageEncoding(str, Enum):
    LEGACY_RAW = "legacy_raw"
    LEGACY_RLE = "legacy_rle"
    LUCK_NORMAL_RAW = "luck_normal_raw"
    LUCK_NORMAL_COMPRESSED = "luck_normal_compressed"
    LUCK_NORMAL_GRAY = "luck_normal_gray"
    V5G_DOT = "v5g_dot"
    V5G_GRAY = "v5g_gray"
    V5X_DOT = "v5x_dot"
    V5X_GRAY = "v5x_gray"
    V5C_A4 = "v5c_a4"
    V5C_A5 = "v5c_a5"
    DCK_DEFAULT = "dck_default"


class PaperMode(str, Enum):
    PLAIN = "plain"
    TAG = "tag"
    BLACK_TAG = "black_tag"
    FOLDER = "folder"
    TATTOO = "tattoo"
    CIRCLE_TAG = "circle_tag"

    @property
    def label(self) -> str:
        labels = {
            PaperMode.PLAIN: "Plain roll",
            PaperMode.TAG: "Tag",
            PaperMode.BLACK_TAG: "Black tag",
            PaperMode.FOLDER: "Folder",
            PaperMode.TATTOO: "Tattoo",
            PaperMode.CIRCLE_TAG: "Circle tag",
        }
        return labels[self]


@dataclass(frozen=True)
class ImagePipelineConfig:
    formats: tuple[PixelFormat, ...]
    encoding: ImageEncoding

    def __post_init__(self) -> None:
        if not self.formats:
            raise ValueError("Image pipeline formats must not be empty")
        normalized = tuple(
            value if isinstance(value, PixelFormat) else PixelFormat(str(value))
            for value in self.formats
        )
        if len(set(normalized)) != len(normalized):
            raise ValueError("Image pipeline formats must be unique")
        object.__setattr__(self, "formats", normalized)

    @property
    def default_format(self) -> PixelFormat:
        return self.formats[0]

    def supports(self, pixel_format: PixelFormat) -> bool:
        return pixel_format in self.formats

    def with_default_format(self, pixel_format: PixelFormat) -> "ImagePipelineConfig":
        if pixel_format not in self.formats:
            raise ValueError(
                f"Image pipeline does not support raster format {pixel_format.value}"
            )
        if pixel_format == self.formats[0]:
            return self
        reordered = (pixel_format,) + tuple(
            value for value in self.formats if value != pixel_format
        )
        return ImagePipelineConfig(formats=reordered, encoding=self.encoding)
