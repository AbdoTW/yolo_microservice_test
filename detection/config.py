# Detection Service Configuration

# RabbitMQ Settings
RABBITMQ_HOST = 'localhost'
RABBITMQ_PORT = 5672
RABBITMQ_USERNAME = 'guest'
RABBITMQ_PASSWORD = 'guest'

# Queue Names
RAW_FRAMES_QUEUE = 'raw_frames'
DETECTION_RESULTS_QUEUE = 'detection_results'

# YOLO Settings
YOLO_MODEL = 'models/yolov8n.pt'  # Nano version for faster processing
CONFIDENCE_THRESHOLD = 0.5
PERSON_CLASS_ID = 0  # In YOLO, 'person' class has ID 0

# Detection Settings
MAX_DETECTIONS = 100  # Maximum detections per frame
DRAW_BOXES = True
BOX_COLOR = (0, 255, 0)  # Green color for bounding boxes
BOX_THICKNESS = 2
FONT_SCALE = 0.5
FONT_THICKNESS = 1

# Model Directory
MODELS_DIR = 'models'