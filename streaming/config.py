import cv2

# RabbitMQ Settings
RABBITMQ_HOST = 'localhost'
RABBITMQ_PORT = 5672
RABBITMQ_USERNAME = 'guest'
RABBITMQ_PASSWORD = 'guest'

# Queue Names
RAW_FRAMES_QUEUE = 'raw_frames'
DETECTION_RESULTS_QUEUE = 'detection_results'

# File Paths
UPLOADS_DIR = 'uploads'
TEMP_FRAMES_DIR = 'temp_frames'
PROCESSED_VIDEOS_DIR = 'processed_videos'  # New directory for processed videos

# Supported video formats
SUPPORTED_FORMATS = ['.mp4', '.avi', '.mov', '.mkv', '.webm']

# Server Settings
HOST = '0.0.0.0'
PORT = 8003


# Video Processing Settings
PROCESSED_VIDEOS_DIR = 'processed_videos'

# Video codec preferences (in order of preference)
VIDEO_CODECS = [
    ('H264', cv2.VideoWriter_fourcc(*'H264')),  # Best quality and compatibility
    ('XVID', cv2.VideoWriter_fourcc(*'XVID')),  # Good fallback
    ('MP4V', cv2.VideoWriter_fourcc(*'mp4v')),  # Basic fallback
    ('MJPG', cv2.VideoWriter_fourcc(*'MJPG'))   # Last resort
]

# Video output settings
OUTPUT_VIDEO_FPS = 20.0  # Frames per second for output video
OUTPUT_VIDEO_QUALITY = 90  # JPEG quality for frames (0-100)



