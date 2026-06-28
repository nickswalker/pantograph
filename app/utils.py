import logging
import os
import json
from datetime import datetime
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
from app.config import Config

# Guard against decompression-bomb images exhausting worker memory.
Image.MAX_IMAGE_PIXELS = 50_000_000


def get_registration_deadline_info():
    """Get registration deadline information for template display."""
    try:
        deadline = Config.REGISTRATION_CLOSES_AT
        now = datetime.now(deadline.tzinfo)

        return {
            'deadline': deadline,
            'deadline_str': deadline.strftime('%B %d, %Y at %I:%M %p'),
            'is_closed': now > deadline,
            'days_remaining': (deadline - now).days if now <= deadline else 0
        }
    except Exception as e:
        import logging
        logging.warning(f"Failed to parse registration deadline: {e}")
        return {
            'deadline': None,
            'deadline_str': 'TBD',
            'is_closed': False,
            'days_remaining': 9999
        }


def format_hh_mm_from_seconds(total_seconds):
    """Converts total seconds to a HH:MM formatted string."""
    if total_seconds is None:
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:d}:{minutes:02d}"

def format_mm_ss_from_seconds(total_seconds):
    """Converts total seconds to a MM:SS formatted string."""
    if total_seconds is None:
        return ""
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:d}:{seconds:02d}"

def parse_hh_mm_to_seconds(duration_str):
    """Converts a HH:MM string to total seconds.
    Raises ValueError if format is incorrect or minutes are invalid.
    """
    if not duration_str:
        return 0

    parts = duration_str.split(':')
    if len(parts) != 2:
        raise ValueError("Duration must be in HH:MM format.")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        raise ValueError("Duration components must be integers.")

    if not (0 <= minutes < 60):
        raise ValueError("Minutes component must be between 00 and 59.")

    return hours * 3600 + minutes * 60

def parse_mm_ss_to_seconds(pace_str):
    """Converts a MM:SS string to total seconds.
    Raises ValueError if format is incorrect or seconds are invalid.
    """
    if not pace_str:
        return 0

    parts = pace_str.split(':')
    if len(parts) != 2:
        raise ValueError("Pace must be in MM:SS format.")

    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
    except ValueError:
        raise ValueError("Pace components must be integers.")

    if not (0 <= seconds < 60):
        raise ValueError("Seconds component must be between 00 and 59.")

    return minutes * 60 + seconds

def load_station_names():
    """Load station names from lrr_1_line.geojson file"""
    try:
        with open('data/lrr_1_line.geojson', 'r') as f:
            data = json.load(f)
        sorted_features = sorted(data["features"], key=lambda item: item['properties']['id'], reverse=True)
        station_names = [feature['properties']['name'] for feature in sorted_features]
        return station_names
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"Error loading station names: {e}")
        return []

def load_exchange_points() -> dict:
    try:
        with open('data/lrr_1_line.geojson', 'r') as f:
            data = json.load(f)
        sorted_features = sorted(data["features"], key=lambda item: item['properties']['id'], reverse=True)
        exchange_points = {}
        for feature in sorted_features:
            props = feature['properties']
            exchange_points[props['id']] = {
                'name': props['name'],
                'latitude': feature["geometry"]["coordinates"][1],
                'longitude': feature["geometry"]["coordinates"][0],
            }
        return exchange_points
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"Error loading exchange points: {e}")
        return {}


def get_exif_data(image: Image):
    """Extract EXIF data from image, including all IFD blocks"""
    from PIL.ExifTags import IFD

    raw_exif = image.getexif() or {}
    exif_data = {}

    # Create IFD lookup table
    IFD_CODE_LOOKUP = {i.value: i.name for i in IFD}

    # Process each tag in the EXIF data
    for tag_code, value in raw_exif.items():
        # Check if this tag is an IFD block
        if tag_code in IFD_CODE_LOOKUP:
            # This is an IFD block, extract nested data
            ifd_tag_name = IFD_CODE_LOOKUP[tag_code]
            try:
                ifd_data = raw_exif.get_ifd(tag_code)
                for nested_key, nested_value in ifd_data.items():
                    # Try GPS tags first, then regular EXIF tags
                    nested_tag_name = GPSTAGS.get(nested_key) or TAGS.get(nested_key, nested_key)
                    # Consider prefixing with IFD name to avoid collisions
                    prefixed_name = f"{nested_tag_name}" if isinstance(nested_tag_name, str) else f"{ifd_tag_name}_{nested_key}"
                    exif_data[prefixed_name] = nested_value

                    # Also add without prefix for backward compatibility with common tags
                    if nested_tag_name in ['GPSLatitude', 'GPSLongitude', 'GPSLatitudeRef', 'GPSLongitudeRef', 'DateTimeOriginal', 'OffsetTimeOriginal']:
                        exif_data[nested_tag_name] = nested_value
            except (AttributeError, OSError):
                # Some IFDs might not be accessible, skip them
                pass
        else:
            # Root-level tag
            tag_name = TAGS.get(tag_code, tag_code)
            exif_data[tag_name] = value

    return exif_data


def get_gps_data(exif_data):
    """Convert GPS EXIF data to decimal degrees"""
    if 'GPSLatitude' not in exif_data or 'GPSLongitude' not in exif_data:
        return None

    def convert_to_degrees(value):
        d, m, s = value
        return d + (m / 60.0) + (s / 3600.0)

    lat = convert_to_degrees(exif_data['GPSLatitude'])
    lon = convert_to_degrees(exif_data['GPSLongitude'])

    # Adjust for hemisphere
    if exif_data.get('GPSLatitudeRef') == 'S':
        lat = -lat
    if exif_data.get('GPSLongitudeRef') == 'W':
        lon = -lon

    return lat, lon


def get_capture_time(exif_data):
    """Extract capture time from EXIF data dict, like the working script version"""
    date_time = exif_data.get('DateTimeOriginal') or exif_data.get('DateTime')
    timezone = exif_data.get('OffsetTimeOriginal') or exif_data.get('OffsetTime')
    if not date_time:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(f"{date_time} {timezone or ''}".strip(), '%Y:%m:%d %H:%M:%S %z' if timezone else '%Y:%m:%d %H:%M:%S')
    except ValueError:
        return None



def is_allowed_image(filename):
    """Check if filename has allowed image extension"""
    return os.path.splitext(filename)[1].lower() in Config.ALLOWED_EXTENSIONS


def validate_image_content(file_content):
    """Validate image file content using magic numbers (file signatures)"""

    # Image file signatures (magic numbers)
    image_signatures = {
        b'\xFF\xD8\xFF': ['jpg', 'jpeg'],  # JPEG
        b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A': ['png'],  # PNG
        b'\x47\x49\x46\x38\x37\x61': ['gif'],  # GIF87a
        b'\x47\x49\x46\x38\x39\x61': ['gif'],  # GIF89a
        b'\x42\x4D': ['bmp'],  # BMP
        b'\x52\x49\x46\x46': ['webp'],  # WEBP (needs further validation)
        b'\x49\x49\x2A\x00': ['tiff'],  # TIFF (little endian)
        b'\x4D\x4D\x00\x2A': ['tiff'],  # TIFF (big endian)
    }

    if len(file_content) < 12:
        return False

    # Check magic numbers
    for signature, formats in image_signatures.items():
        if file_content.startswith(signature):
            return True

    # Special case for WEBP - needs additional validation
    if file_content.startswith(b'\x52\x49\x46\x46') and len(file_content) >= 12:
        if file_content[8:12] == b'WEBP':
            return True

    # Special case for HEIC/HEIF - check for ftyp box with HEIC brands
    if len(file_content) >= 24:
        # HEIC files start with ftyp box, check for various HEIC brand types
        heic_brands = [b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs', b'avci']

        # Look for ftyp box (starts at offset 4) and check brand at offset 8
        if file_content[4:8] == b'ftyp':
            brand = file_content[8:12]
            if brand in heic_brands:
                return True

            # Also check compatible brands starting at offset 16
            for i in range(16, min(len(file_content) - 3, 64), 4):
                if file_content[i:i+4] in heic_brands:
                    return True

    return False


def secure_filename_enhanced(filename):
    """Enhanced secure filename that removes more potential security risks"""
    from werkzeug.utils import secure_filename as werkzeug_secure_filename
    import re

    # Use werkzeug's secure_filename first
    filename = werkzeug_secure_filename(filename)

    # Additional security measures
    # Remove any remaining special characters and normalize
    filename = re.sub(r'[^\w\-_.]', '', filename)

    # Ensure filename is not empty
    if not filename or filename.startswith('.'):
        filename = 'upload.bin'

    # Limit filename length
    if len(filename) > 100:
        name, ext = os.path.splitext(filename)
        filename = name[:95] + ext

    return filename


def find_team_by_id(team_id):
    """Find team in database by id"""
    from app.models import Team
    return Team.query.filter_by(id=team_id).first()


def find_team_by_gallery_hash(gallery_hash):
    """Find team in database by gallery_hash"""
    from app.models import Team
    return Team.query.filter_by(gallery_hash=gallery_hash).first()


def get_mime_type(file_path):
    """Get MIME type from file extension"""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'


def convert_to_jpeg(image: Image, jpeg_path, quality=85):
    """Convert image to JPEG format using pillow-heif"""
    try:
        # pillow-heif is already registered in __init__.py

        # Convert to RGB if necessary (HEIC can be in other color spaces)
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Save as JPEG with specified quality
        image.save(jpeg_path, 'JPEG', quality=quality, optimize=True)
        return True
    except Exception as e:
        print(f"Error converting HEIC to JPEG: {e}")
        return False


def is_heic_file(filename):
    """Check if file is HEIC format"""
    return filename.lower().endswith(('.heic', '.heif'))


# --- Gallery thumbnails ---

def thumbnail_basename(stored_basename):
    """Thumbnail filename for a stored image basename (always a .jpg)."""
    return os.path.splitext(stored_basename)[0] + '.jpg'


def _write_thumbnail(img, dest_path):
    """Resize ``img`` to the configured max size and write a JPEG to dest_path.

    Bakes in EXIF orientation (the re-encoded thumbnail drops EXIF, so the
    rotation must be applied to the pixels) and downsizes with LANCZOS.
    """
    img = ImageOps.exif_transpose(img)  # apply orientation, returns a copy when needed
    img.thumbnail((Config.THUMBNAIL_MAX_SIZE, Config.THUMBNAIL_MAX_SIZE), Image.LANCZOS)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    img.save(dest_path, 'JPEG', quality=Config.THUMBNAIL_QUALITY, optimize=True)


def generate_thumbnail_from_image(img, dest_path):
    """Best-effort thumbnail from an already-decoded PIL image (upload path).

    Operates on a copy so the caller's image is untouched. Never raises — a
    failed thumbnail just means the gallery falls back to the original.
    """
    try:
        _write_thumbnail(img.copy(), dest_path)
        return True
    except Exception as e:
        logging.warning(f"Thumbnail generation failed for {dest_path}: {e}")
        return False


def generate_thumbnail_from_file(src_path, dest_path):
    """Best-effort thumbnail from a file on disk, decoding cheaply (backfill path).

    Uses JPEG ``draft`` mode so a large source decodes at reduced resolution
    instead of materializing the full bitmap.
    """
    try:
        with Image.open(src_path) as img:
            try:
                img.draft('RGB', (Config.THUMBNAIL_MAX_SIZE, Config.THUMBNAIL_MAX_SIZE))
            except Exception:
                pass  # draft is a JPEG-only optimization; ignore where unsupported
            _write_thumbnail(img, dest_path)
        return True
    except Exception as e:
        logging.warning(f"Thumbnail backfill failed for {src_path}: {e}")
        return False
