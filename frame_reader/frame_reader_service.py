import cv2
import os
import sys
import time
import base64
import uuid
from datetime import datetime
import numpy as np

# Add parent directory to path to import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.rabbitmq_client import RabbitMQClient
from frame_reader.config import *

class FrameReaderService:
    def __init__(self):
        self.rabbitmq_client = RabbitMQClient(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            username=RABBITMQ_USERNAME,
            password=RABBITMQ_PASSWORD
        )
        self.setup_directories()
    
    def setup_directories(self):
        """Create necessary directories if they don't exist"""
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        os.makedirs(TEMP_FRAMES_DIR, exist_ok=True)
    
    def resize_frame(self, frame):
        """Resize frame to max dimensions for faster processing"""
        height, width = frame.shape[:2]
        
        if width > MAX_FRAME_WIDTH or height > MAX_FRAME_HEIGHT:
            # Calculate aspect ratio
            aspect_ratio = width / height
            
            if width > height:
                new_width = MAX_FRAME_WIDTH
                new_height = int(new_width / aspect_ratio)
            else:
                new_height = MAX_FRAME_HEIGHT
                new_width = int(new_height * aspect_ratio)
            
            frame = cv2.resize(frame, (new_width, new_height))
        
        return frame
    
    def frame_to_base64(self, frame):
        """Convert OpenCV frame to base64 string"""
        try:
            _, buffer = cv2.imencode('.jpg', frame)
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            return frame_base64
        except Exception as e:
            print(f"Error converting frame to base64: {e}")
            return None
    
    def process_video(self, video_path):
        """Process video file and extract frames"""
        print(f"Starting to process video: {video_path}")
        
        # Check if file exists
        if not os.path.exists(video_path):
            print(f"Error: Video file not found: {video_path}")
            return False
        
        # Connect to RabbitMQ
        if not self.rabbitmq_client.connect():
            print("Failed to connect to RabbitMQ")
            return False
        
        # Open video file
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            print(f"Error: Could not open video file: {video_path}")
            return False
        
        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        print(f"Video properties: FPS={fps}, Total Frames={total_frames}, Duration={duration:.2f}s")
        
        # Calculate frame skip interval
        frame_skip = max(1, int(fps / FRAME_RATE)) if fps > 0 else 1
        
        frame_count = 0
        processed_count = 0
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Skip frames based on desired frame rate
                if frame_count % frame_skip == 0:
                    # Resize frame
                    frame = self.resize_frame(frame)
                    
                    # Convert to base64
                    frame_base64 = self.frame_to_base64(frame)
                    
                    if frame_base64:
                        # Create message
                        message = {
                            'frame_id': str(uuid.uuid4()),
                            'timestamp': datetime.now().isoformat(),
                            'frame_data': frame_base64,
                            'frame_number': frame_count,
                            'video_path': video_path,
                            'frame_width': frame.shape[1],
                            'frame_height': frame.shape[0]
                        }
                        
                        # Send to RabbitMQ
                        if self.rabbitmq_client.publish_message(RAW_FRAMES_QUEUE, message):
                            processed_count += 1
                            print(f"Sent frame {processed_count} (frame #{frame_count})")
                        else:
                            print(f"Failed to send frame {frame_count}")
                
                frame_count += 1
                
                # Add small delay to avoid overwhelming the system
                time.sleep(0.01)
        
        except KeyboardInterrupt:
            print("Processing interrupted by user")
        except Exception as e:
            print(f"Error during video processing: {e}")
        finally:
            cap.release()
            self.rabbitmq_client.close()
        
        print(f"Video processing completed. Processed {processed_count} frames out of {frame_count} total frames")
        return True

def main():
    # Simple test
    service = FrameReaderService()
    
    # Check if video path is provided as argument
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        # Ask user for video path
        video_path = input("Enter path to video file: ").strip()
    
    # Check if file has supported format
    file_ext = os.path.splitext(video_path)[1].lower()
    if file_ext not in SUPPORTED_FORMATS:
        print(f"Error: Unsupported video format. Supported formats: {SUPPORTED_FORMATS}")
        return
    
    # Process video
    service.process_video(video_path)

if __name__ == "__main__":
    main()