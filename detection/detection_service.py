import cv2
import os
import sys
import base64
import json
import numpy as np
from datetime import datetime
from ultralytics import YOLO

# Add parent directory to path to import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.rabbitmq_client import RabbitMQClient
from detection.config import *

class DetectionService:
    def __init__(self):
        self.rabbitmq_client = RabbitMQClient(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            username=RABBITMQ_USERNAME,
            password=RABBITMQ_PASSWORD
        )
        self.model = None
        self.setup_directories()
        self.load_yolo_model()
    
    def setup_directories(self):
        """Create necessary directories if they don't exist"""
        os.makedirs(MODELS_DIR, exist_ok=True)
    
    def load_yolo_model(self):
        """Load YOLO model"""
        print("Loading YOLO model...")
        try:
            # This will download the model if it doesn't exist
            self.model = YOLO(YOLO_MODEL)
            print(f"✅ YOLO model '{YOLO_MODEL}' loaded successfully")
        except Exception as e:
            print(f"❌ Error loading YOLO model: {e}")
            sys.exit(1)
    
    def base64_to_frame(self, base64_string):
        """Convert base64 string back to OpenCV frame"""
        try:
            # Decode base64 string
            img_data = base64.b64decode(base64_string)
            
            # Convert bytes to numpy array
            nparr = np.frombuffer(img_data, np.uint8)
            
            # Decode image
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            return frame
        except Exception as e:
            print(f"Error converting base64 to frame: {e}")
            return None
    
    def frame_to_base64(self, frame):
        """Convert OpenCV frame to base64 string"""
        try:
            _, buffer = cv2.imencode('.jpg', frame)
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            return frame_base64
        except Exception as e:
            print(f"Error converting frame to base64: {e}")
            return None
    
    def detect_humans(self, frame):
        """Detect humans in frame using YOLO"""
        try:
            # Run YOLO detection
            results = self.model(frame, verbose=False)
            
            detections = []
            human_count = 0
            
            # Process results
            for result in results:
                boxes = result.boxes
                if boxes is not None:
                    for box in boxes:
                        # Get class ID
                        class_id = int(box.cls[0])
                        
                        # Check if it's a person (class_id = 0)
                        if class_id == PERSON_CLASS_ID:
                            confidence = float(box.conf[0])
                            
                            # Check confidence threshold
                            if confidence >= CONFIDENCE_THRESHOLD:
                                # Get bounding box coordinates
                                x1, y1, x2, y2 = box.xyxy[0].tolist()
                                
                                detection = {
                                    'class': 'person',
                                    'confidence': confidence,
                                    'bbox': [int(x1), int(y1), int(x2-x1), int(y2-y1)],  # [x, y, width, height]
                                    'xyxy': [int(x1), int(y1), int(x2), int(y2)]  # [x1, y1, x2, y2]
                                }
                                
                                detections.append(detection)
                                human_count += 1
            
            return detections, human_count
            
        except Exception as e:
            print(f"Error during detection: {e}")
            return [], 0
    
    def draw_detections(self, frame, detections):
        """Draw bounding boxes and labels on frame"""
        try:
            for detection in detections:
                x1, y1, x2, y2 = detection['xyxy']
                confidence = detection['confidence']
                
                # Draw bounding box
                cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, BOX_THICKNESS)
                
                # Create label
                label = f"Person {confidence:.2f}"
                
                # Get text size
                (text_width, text_height), baseline = cv2.getTextSize(
                    label, cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, FONT_THICKNESS
                )
                
                # Draw label background
                cv2.rectangle(
                    frame,
                    (x1, y1 - text_height - baseline - 5),
                    (x1 + text_width, y1),
                    BOX_COLOR,
                    -1
                )
                
                # Draw label text
                cv2.putText(
                    frame,
                    label,
                    (x1, y1 - baseline - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    FONT_SCALE,
                    (0, 0, 0),  # Black text
                    FONT_THICKNESS
                )
            
            # Draw human count
            count_text = f"Humans: {len(detections)}"
            cv2.putText(
                frame,
                count_text,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),  # Yellow text
                2
            )
            
            return frame
            
        except Exception as e:
            print(f"Error drawing detections: {e}")
            return frame
    
    def process_frame_message(self, channel, method, properties, body):
        """Process incoming frame message from RabbitMQ"""
        try:
            # Parse message
            message = json.loads(body)
            
            frame_id = message.get('frame_id')
            frame_data = message.get('frame_data')
            timestamp = message.get('timestamp')
            frame_number = message.get('frame_number')
            
            print(f"Processing frame {frame_id} (#{frame_number})")
            
            # Convert base64 to frame
            frame = self.base64_to_frame(frame_data)
            
            if frame is None:
                print(f"❌ Failed to decode frame {frame_id}")
                return
            
            # Detect humans
            detections, human_count = self.detect_humans(frame)
            
            # Draw detections on frame if enabled
            if DRAW_BOXES:
                frame = self.draw_detections(frame, detections)
            
            # Convert processed frame back to base64
            processed_frame_base64 = self.frame_to_base64(frame)
            
            if processed_frame_base64 is None:
                print(f"❌ Failed to encode processed frame {frame_id}")
                return
            
            # Create result message
            result_message = {
                'frame_id': frame_id,
                'timestamp': timestamp,
                'original_timestamp': timestamp,
                'processed_timestamp': datetime.now().isoformat(),
                'frame_number': frame_number,
                'processed_frame': processed_frame_base64,
                'detections': detections,
                'human_count': human_count,
                'detection_summary': {
                    'total_detections': len(detections),
                    'persons_detected': human_count
                }
            }
            
            # Send to detection results queue
            if self.rabbitmq_client.publish_message(DETECTION_RESULTS_QUEUE, result_message):
                print(f"✅ Processed frame {frame_id}: Found {human_count} humans")
            else:
                print(f"❌ Failed to send result for frame {frame_id}")
        
        except Exception as e:
            print(f"❌ Error processing frame message: {e}")
    
    def start_consuming(self):
        """Start consuming messages from raw_frames queue"""
        print("🚀 Starting Detection Service...")
        print(f"Consuming from queue: {RAW_FRAMES_QUEUE}")
        print(f"Publishing to queue: {DETECTION_RESULTS_QUEUE}")
        print(f"YOLO model: {YOLO_MODEL}")
        print(f"Confidence threshold: {CONFIDENCE_THRESHOLD}")
        print("-" * 50)
        
        # Connect to RabbitMQ
        if not self.rabbitmq_client.connect():
            print("❌ Failed to connect to RabbitMQ")
            return False
        
        try:
            # Start consuming messages
            self.rabbitmq_client.consume_messages(
                RAW_FRAMES_QUEUE,
                self.process_frame_message
            )
        except KeyboardInterrupt:
            print("\n🛑 Detection service stopped by user")
        except Exception as e:
            print(f"❌ Error during message consumption: {e}")
        finally:
            self.rabbitmq_client.close()
            print("🔌 RabbitMQ connection closed")

def main():
    """Main function"""
    print("🔍 Human Detection Service")
    print("=" * 40)
    
    # Create and start detection service
    service = DetectionService()
    service.start_consuming()

if __name__ == "__main__":
    main()