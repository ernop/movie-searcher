"""
VLC Configuration Optimization Module

This module provides functionality to optimize VLC's configuration for faster
startup times. It can modify VLC's vlcrc configuration file with user consent.

IMPORTANT: This module modifies user's VLC settings. Always:
1. Create a backup before modifying
2. Provide a way to restore original settings
3. Only run with explicit user opt-in
"""

import os
import shutil
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def get_vlcrc_path():
    """
    Get the path to VLC's configuration file (vlcrc) based on OS.
    
    Returns:
        Path object to vlcrc file, or None if not found
    """
    if os.name == 'nt':  # Windows
        # Windows: %APPDATA%\vlc\vlcrc
        appdata = os.environ.get('APPDATA')
        if appdata:
            vlcrc = Path(appdata) / 'vlc' / 'vlcrc'
            return vlcrc
    else:
        # Linux/Mac: ~/.config/vlc/vlcrc
        config_home = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        vlcrc = Path(config_home) / 'vlc' / 'vlcrc'
        return vlcrc
    
    return None


def get_vlcrc_backup_path():
    """Get the path for the vlcrc backup file."""
    vlcrc = get_vlcrc_path()
    if vlcrc:
        return vlcrc.with_suffix('.vlcrc.backup')
    return None


def check_vlcrc_status():
    """
    Check the current status of VLC configuration.
    
    Returns:
        dict with:
        - exists: bool - whether vlcrc file exists
        - path: str - path to vlcrc
        - backup_exists: bool - whether backup file exists
        - backup_path: str - path to backup file
        - is_optimized: bool - whether file appears to already have optimizations
        - size: int - file size in bytes
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()
    
    status = {
        "exists": False,
        "path": str(vlcrc) if vlcrc else None,
        "backup_exists": False,
        "backup_path": str(backup) if backup else None,
        "is_optimized": False,
        "size": 0
    }
    
    if vlcrc and vlcrc.exists():
        status["exists"] = True
        status["size"] = vlcrc.stat().st_size
        
        # Check if already optimized by looking for our marker comment
        try:
            content = vlcrc.read_text(encoding='utf-8', errors='ignore')
            status["is_optimized"] = "# Movie Searcher Optimization" in content
        except Exception:
            pass
    
    if backup and backup.exists():
        status["backup_exists"] = True
    
    return status


# Optimization settings to apply to vlcrc
# Format: (key, value, description)
VLC_OPTIMIZATION_SETTINGS = [
    # Performance optimizations
    ("file-caching", "300", "Reduced file caching for faster local file playback"),
    ("input-fast-seek", "1", "Fast (but less accurate) seeking"),
    ("metadata-network-access", "0", "Disable network metadata lookups"),
    ("auto-preparse", "0", "Disable automatic file preparsing"),
    ("media-library", "0", "Disable media library"),
    
    # UI optimizations
    ("video-title-show", "0", "Disable on-screen video title"),
    ("qt-privacy-ask", "0", "Skip privacy dialog"),
    ("qt-updates-notif", "0", "Disable update notifications"),
    ("album-art", "0", "Disable album art fetching"),
    
    # Note: Hardware acceleration is NOT included here as it can cause issues
    # on some systems. It's offered as a separate opt-in option via command line.
]


def create_backup():
    """
    Create a backup of the current vlcrc file.
    
    Returns:
        dict with success status and message
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()
    
    if not vlcrc or not vlcrc.exists():
        return {
            "success": False,
            "message": "VLC configuration file not found. VLC may not have been run yet."
        }
    
    try:
        shutil.copy2(vlcrc, backup)
        return {
            "success": True,
            "message": f"Backup created at: {backup}",
            "backup_path": str(backup)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to create backup: {e}"
        }


def restore_backup():
    """
    Restore vlcrc from backup.
    
    Returns:
        dict with success status and message
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()
    
    if not backup or not backup.exists():
        return {
            "success": False,
            "message": "No backup file found to restore from."
        }
    
    try:
        shutil.copy2(backup, vlcrc)
        return {
            "success": True,
            "message": "VLC configuration restored from backup."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to restore backup: {e}"
        }


def apply_optimizations():
    """
    Apply fast-startup optimizations to vlcrc file.
    Creates a backup first if one doesn't exist.
    
    Returns:
        dict with success status, message, and details of changes made
    """
    vlcrc = get_vlcrc_path()
    
    if not vlcrc:
        return {
            "success": False,
            "message": "Could not determine VLC configuration path for this OS."
        }
    
    # Ensure vlc config directory exists
    vlcrc.parent.mkdir(parents=True, exist_ok=True)
    
    # Create backup if vlcrc exists and backup doesn't
    backup = get_vlcrc_backup_path()
    if vlcrc.exists() and backup and not backup.exists():
        backup_result = create_backup()
        if not backup_result["success"]:
            return {
                "success": False,
                "message": f"Failed to create backup before optimization: {backup_result['message']}"
            }
    
    # Read existing content or start fresh
    if vlcrc.exists():
        try:
            content = vlcrc.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to read vlcrc: {e}"
            }
    else:
        lines = []
    
    # Parse existing settings into a dict
    settings = {}
    setting_lines = {}  # Track which line each setting is on
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()
            settings[key] = value
            setting_lines[key] = i
    
    # Apply optimizations
    changes_made = []
    
    for key, value, description in VLC_OPTIMIZATION_SETTINGS:
        old_value = settings.get(key)
        
        if old_value != value:
            if key in setting_lines:
                # Update existing line
                line_num = setting_lines[key]
                lines[line_num] = f"{key}={value}"
                changes_made.append(f"Updated {key}: {old_value} → {value} ({description})")
            else:
                # Add new setting
                lines.append(f"{key}={value}")
                changes_made.append(f"Added {key}={value} ({description})")
            
            settings[key] = value
    
    # Add marker comment if not present
    marker = "# Movie Searcher Optimization"
    if marker not in '\n'.join(lines):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.insert(0, f"{marker} applied on {timestamp}")
        lines.insert(1, "# Original settings backed up. Use Movie Searcher settings to restore.")
        lines.insert(2, "")
    
    # Write back
    try:
        vlcrc.write_text('\n'.join(lines), encoding='utf-8')
        return {
            "success": True,
            "message": f"Applied {len(changes_made)} optimizations to VLC configuration.",
            "changes": changes_made,
            "vlcrc_path": str(vlcrc)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to write vlcrc: {e}"
        }


def remove_optimizations():
    """
    Remove Movie Searcher optimizations from vlcrc.
    If a backup exists, restores from backup.
    Otherwise, removes the specific settings we added.
    
    Returns:
        dict with success status and message
    """
    backup = get_vlcrc_backup_path()
    
    # If backup exists, restore from it
    if backup and backup.exists():
        result = restore_backup()
        if result["success"]:
            # Optionally delete the backup after successful restore
            # backup.unlink()  # Uncomment to delete backup after restore
            pass
        return result
    
    # No backup - try to reset just our settings
    vlcrc = get_vlcrc_path()
    
    if not vlcrc or not vlcrc.exists():
        return {
            "success": True,
            "message": "VLC configuration file not found. Nothing to remove."
        }
    
    try:
        content = vlcrc.read_text(encoding='utf-8', errors='ignore')
        lines = content.split('\n')
        
        # Get list of our optimization keys
        opt_keys = {key for key, _, _ in VLC_OPTIMIZATION_SETTINGS}
        
        # Filter out our settings and marker comments
        new_lines = []
        for line in lines:
            stripped = line.strip()
            
            # Skip our marker comments
            if "Movie Searcher Optimization" in stripped:
                continue
            if "Original settings backed up" in stripped:
                continue
            
            # Skip our optimization settings
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.partition('=')[0].strip()
                if key in opt_keys:
                    continue
            
            new_lines.append(line)
        
        vlcrc.write_text('\n'.join(new_lines), encoding='utf-8')
        
        return {
            "success": True,
            "message": "Removed Movie Searcher optimizations from VLC configuration."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to remove optimizations: {e}"
        }


def get_optimization_info():
    """
    Get information about what optimizations would be applied.
    
    Returns:
        dict with optimization details
    """
    return {
        "settings": [
            {
                "key": key,
                "value": value,
                "description": description
            }
            for key, value, description in VLC_OPTIMIZATION_SETTINGS
        ],
        "description": """
These optimizations modify VLC's global configuration to reduce startup time:

1. **File Caching**: Reduced from 1200ms to 300ms for faster local file playback
2. **Fast Seeking**: Uses faster (but less frame-accurate) seeking when jumping to timestamps
3. **Metadata**: Disables network lookups for metadata and album art
4. **Preparsing**: Disables automatic file scanning
5. **Media Library**: Disables VLC's built-in media library
6. **UI Elements**: Disables on-screen title display and update notifications

Note: These changes affect ALL VLC usage, not just launches from Movie Searcher.
A backup of your original settings is created before applying changes.
        """.strip(),
        "notes": [
            "Hardware acceleration is NOT included as it can cause issues on some systems",
            "Changes affect all VLC usage system-wide",
            "A backup is created before any changes",
            "You can restore original settings at any time"
        ]
    }


if __name__ == "__main__":
    # Test the module
    print("VLC Configuration Optimizer")
    print("=" * 40)
    
    status = check_vlcrc_status()
    print(f"\nCurrent status:")
    print(f"  vlcrc exists: {status['exists']}")
    print(f"  vlcrc path: {status['path']}")
    print(f"  Is optimized: {status['is_optimized']}")
    print(f"  Backup exists: {status['backup_exists']}")
    
    print("\nOptimizations that would be applied:")
    for key, value, desc in VLC_OPTIMIZATION_SETTINGS:
        print(f"  {key}={value} - {desc}")
