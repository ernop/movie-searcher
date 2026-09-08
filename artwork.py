"""Bounded raster previews for library artwork and screenshots."""
from io import BytesIO

from fastapi import HTTPException, Response
from PIL import Image, ImageOps, UnidentifiedImageError


def thumbnail_response(path, width):
    try:
        with Image.open(path) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError('Artwork exceeds pixel limit')
            source.seek(0)
            source.thumbnail((width, width * 2))
            preview = ImageOps.exif_transpose(source).convert('RGB')
            output = BytesIO()
            preview.save(output, 'JPEG', quality=82)
        return Response(output.getvalue(), media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600'})
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(422, 'Artwork could not be decoded as a bounded raster image.') from None
