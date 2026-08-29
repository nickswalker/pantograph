# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application
- **Development**: `uv run python wsgi.py` (runs on localhost:5001)
- **Production**: `docker compose up` (requires .env file with URL_KEY)

### Environment Setup
- **Install uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh` (if not already installed)
- **Install Dependencies**: `uv sync`
- **Generate URL Key**: `echo "URL_KEY=$(openssl rand -base64 32)" > ./.env`
- **OAuth Configuration**: Add Google/GitHub OAuth credentials to `.env`:
  ```
  GOOGLE_CLIENT_ID=your_google_client_id
  GOOGLE_CLIENT_SECRET=your_google_client_secret
  GITHUB_CLIENT_ID=your_github_client_id
  GITHUB_CLIENT_SECRET=your_github_client_secret
  ```

### Authentication System
- **OAuth Integration**: Google and GitHub sign-in using Authlib + loginpass
- **Admin Access**: Navigate to `/admin` after OAuth authentication
- **Sign In**: Visit `/login` to choose OAuth provider (Google/GitHub)
- **User Management**: Automatic user creation on first OAuth login
- **Admin Users**: Set by email address in `handle_authorize()` function
- **Database**: SQLite database `relay_photos.db` stores OAuth user accounts

### Photo Processing Script
- **Process uploaded photos**: `uv run python scripts/time_relay_photos.py <image_directory> <geojson_file> --start-timestamp "YYYY-MM-DD HH:MM:SS"`
  - Assigns photos to nearest GeoJSON points based on GPS coordinates
  - Creates symlinks and generates JSON reports with exchange times

## Architecture Overview

### Core Application (wsgi.py)
- **Flask web application** serving photo upload/gallery interface with OAuth authentication
- **Security-based routing**: Team URLs use HMAC-SHA256 hashes to prevent unauthorized access
- **OAuth authentication**: Google/GitHub sign-in using Authlib and loginpass
- **Admin interface**: Protected admin panel at `/admin` for team management and statistics
- **EXIF metadata extraction**: Capture time and GPS coordinates from uploaded images
- **File handling**: Supports JPEG, PNG, HEIC, and other formats with deduplication
- **Image limits**: 23 photos max per team, 10MB file size limit

### Key Components
- **Team folders**: Each team gets a directory in `./uploads/` (folder name = team display name)
- **Metadata extraction**: `get_exif_data()` and `get_gps_data()` parse image EXIF for timestamps and GPS
- **HEIC support**: Uses pillow-heif for Apple HEIC image format conversion

### Database Models (models.py)
- **User**: Stores OAuth user accounts with email, name, avatar_url, provider info, and admin status
- **SQLAlchemy**: ORM with automatic table creation and Flask-Login integration
- **OAuth Fields**: provider, provider_id, created_at, last_login for user management

### Frontend (templates/team_page.html)
- **Interactive gallery**: Bootstrap-styled cards with image previews and metadata
- **MapLibre integration**: Shows photo locations on interactive map with markers
- **HEIC browser support**: Client-side conversion for browsers that don't support HEIC
- **Real-time preview**: Shows EXIF data before upload using ExifReader library

### Data Processing (scripts/time_relay_photos.py)
- **Geospatial analysis**: Matches photos to relay points using haversine distance calculation
- **Manual overrides**: Supports numbered files (e.g., "140.jpg") or timestamp files ("140.txt")
- **Report generation**: Creates JSON reports with exchange times relative to start timestamp
- **Symlink management**: Creates point-numbered symlinks for organization

### Configuration
- **Secret management**: Supports environment variables, Docker secrets, or local config files
- **Docker deployment**: Multi-stage build with volume mounting for persistent uploads
- **Network setup**: Expects external 'web_network' for reverse proxy integration

### File Structure
- `uploads/[team_name]/`: Team photo directories
- `templates/`: Jinja2 HTML templates
- `scripts/`: Utility scripts for photo processing
- `*.geojson`: Relay point coordinates for photo-to-location matching