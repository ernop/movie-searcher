"""
Subtitle parsing and burning functionality for Movie Searcher.
"""
import os
import re
import logging
from pathlib import Path

# Import PIL at module level so it's available in subprocesses
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None
    ImageDraw = None
    ImageFont = None

logger = logging.getLogger(__name__)


def parse_srt_at_timestamp(srt_path, timestamp_seconds):
    """Parse SRT file and return subtitle text at given timestamp
    
    Returns:
        str or None: Subtitle text if found at timestamp, None otherwise
    """
    try:
        # Try different encodings to read the SRT file
        content = None
        encodings = ['utf-8', 'latin-1', 'windows-1252', 'iso-8859-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(srt_path, 'r', encoding=encoding) as f:
                    content = f.read()
                break  # Successfully read with this encoding
            except (UnicodeDecodeError, LookupError):
                continue
        
        if content is None:
            # If all encodings fail, try with errors='ignore'
            with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        
        # Parse SRT format: 
        # Number
        # HH:MM:SS,mmm --> HH:MM:SS,mmm
        # Text (can be multiline)
        # Empty line
        pattern = r'(?:\d+\s*\n)?(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.+?)(?=\n\n|\n\d+\s*\n\d{2}:|\Z)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for match in matches:
            start_h, start_m, start_s, start_ms = int(match[0]), int(match[1]), int(match[2]), int(match[3])
            end_h, end_m, end_s, end_ms = int(match[4]), int(match[5]), int(match[6]), int(match[7])
            start_sec = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000
            end_sec = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000
            
            # Check if timestamp falls within this subtitle's time range
            if start_sec <= timestamp_seconds <= end_sec:
                text = match[8].strip()
                # Clean up HTML tags and excessive newlines
                text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
                text = re.sub(r'\n+', '\n', text)  # Normalize newlines
                return text.strip()
        
        return None
    except Exception as e:
        logger.error(f"Error parsing SRT file {srt_path}: {e}")
        return None


def burn_subtitle_text_onto_image(image_path, subtitle_text):
    """Burn subtitle text onto an image using PIL/Pillow - standard subtitle appearance
    
    Args:
        image_path: Path to image file
        subtitle_text: Text to overlay (can be multiline)
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not PIL_AVAILABLE:
        logger.error(f"PIL/Pillow not available - cannot burn subtitles. Please install Pillow: pip install Pillow")
        return False
    
    try:
        
        # Open image
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        draw = ImageDraw.Draw(img)
        
        # Calculate dynamic font size based on image height (video resolution)
        # Industry standards (FEPSS, Venice Film Festival, Capital Captions):
        # - Font size: 4.6% to 5.6% of screen height (FEPSS: 50-60px for 1080p)
        # - Line height/subtitle area: ~8% of screen height (BBC standard)
        # We use 5.5% to match upper end of professional standards
        image_height = img.size[1]
        
        # Calculate font size as percentage of image height (5.5% matches FEPSS upper range)
        # This ensures subtitles scale proportionally with video resolution
        font_size = int(image_height * 0.055)
        
        # Set reasonable bounds to avoid extremes
        # Minimum: 20px for very low-res videos (e.g., 360p) - matches Channel 4 SD standard
        # Maximum: 80px for very high-res videos (e.g., 4K) - allows proper scaling
        font_size = max(20, min(80, font_size))
        
        font = None
        
        # Try standard subtitle fonts in order
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/verdana.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
        ]
        
        # Load font
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except Exception as e:
                    logger.debug(f"Failed to load font {font_path}: {e}")
                    continue
        
        # Fallback to default font if no TrueType font found
        if not font:
            font = ImageFont.load_default()
        
        # Handle multiline text - split by newlines
        lines = subtitle_text.split('\n')
        lines = [line.strip() for line in lines if line.strip()]  # Remove empty lines
        
        if not lines:
            logger.warning(f"No text to burn after splitting: '{subtitle_text}'")
            return False
        
        # Get text dimensions for each line
        line_heights = []
        line_widths = []
        for line in lines:
            try:
                # Use textbbox (modern PIL)
                bbox = draw.textbbox((0, 0), line, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                line_widths.append(w)
                line_heights.append(h)
            except Exception as e:
                logger.error(f"Failed to measure text size: {e}")
                return False
        
        # Calculate total height and max width
        # Line spacing also scales with resolution (proportional to font size)
        line_spacing = max(3, int(font_size * 0.1))  # 10% of font size, minimum 3px
        total_height = sum(line_heights) + (len(lines) - 1) * line_spacing
        max_width = max(line_widths) if line_widths else 0
        
        # Position at bottom center (standard subtitle position)
        # Bottom margin scales with resolution (about 2% of image height, minimum 20px)
        bottom_margin = max(20, int(image_height * 0.02))
        x = (img.size[0] - max_width) // 2
        y = img.size[1] - total_height - bottom_margin
        
        # Draw each line
        current_y = y
        for i, line in enumerate(lines):
            line_w = line_widths[i]
            line_x = (img.size[0] - line_w) // 2  # Center each line individually
            
            # Draw black outline (standard subtitle outline)
            # Outline thickness scales with font size (about 4% of font size, minimum 1px)
            outline_range = max(1, int(font_size * 0.04))
            for x_offset in range(-outline_range, outline_range + 1):
                for y_offset in range(-outline_range, outline_range + 1):
                    if x_offset != 0 or y_offset != 0:  # Skip center position
                        draw.text((line_x + x_offset, current_y + y_offset), line, font=font, fill='black')
            
            # Draw white text on top (standard subtitle color)
            draw.text((line_x, current_y), line, font=font, fill='white')
            
            # Move to next line
            current_y += line_heights[i] + line_spacing
        
        # Save the modified image
        img.save(image_path)
        logger.info(f"Successfully burned subtitle text onto {image_path}: '{subtitle_text[:50].replace(chr(10), ' ')}...'")
        return True
    except Exception as e:
        logger.error(f"Error burning subtitle text onto {image_path}: {e}", exc_info=True)
        return False

