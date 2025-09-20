import os
import json
import threading
import time
import uuid
import subprocess
import cv2
import numpy as np
import base64
from datetime import datetime
from typing import List, Dict, Any
from collections import deque
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import sys
sys.path.append('..')
from shared.rabbitmq_client import RabbitMQClient
import config

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.lock = threading.Lock()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self.lock:
            self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        # Clean up disconnected clients
        with self.lock:
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

class StreamingService:
    def __init__(self):
        self.websocket_manager = WebSocketManager()
        self.rabbitmq_client = None
        self.frame_buffer = deque(maxlen=100)
        self.stats = {
            "total_frames": 0,  
            "total_humans": 0,
            "current_human_count": 0,
            "current_frame_number": 0,
            "processing_status": "idle",
            "start_time": None,
            "current_video": None
        }
        self.stats_lock = threading.Lock()
        self.is_consuming = False
        self.consumer_thread = None
        self.loop = None
        self.broadcast_queue = asyncio.Queue()
        
        # Video processing for saving
        self.processed_frames = []
        self.video_writer = None
        self.output_video_path = None
        self.last_frame_time = None
        self.finalization_timer = None
        
        # Ensure directories exist
        os.makedirs(config.UPLOADS_DIR, exist_ok=True)
        os.makedirs(config.TEMP_FRAMES_DIR, exist_ok=True)
        os.makedirs(config.PROCESSED_VIDEOS_DIR, exist_ok=True)
    
    def setup_rabbitmq(self):
        """Initialize RabbitMQ connection"""
        try:
            self.rabbitmq_client = RabbitMQClient(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT,
                username=config.RABBITMQ_USERNAME,
                password=config.RABBITMQ_PASSWORD
            )
            return self.rabbitmq_client.connect()
        except Exception as e:
            print(f"RabbitMQ setup failed: {e}")
            return False
    
    def handle_detection_result(self, ch, method, properties, body):
        """Handle incoming detection results from RabbitMQ"""
        try:
            data = json.loads(body.decode('utf-8'))
            
            # Check if this indicates end of video (frame_reader sends this signal)
            if data.get("end_of_video", False):
                self.finalize_processed_video()
                print("🎬 Video processing completed - saved processed video")
                
                # CRITICAL: Send completion signal to frontend
                completion_message = {
                    "type": "processing_complete",
                    "message": "Video processing completed successfully",
                    "timestamp": datetime.now().isoformat(),
                    "total_frames": data.get("total_processed_frames", self.stats["total_frames"]),
                    "video_ready": True
                }
                
                # Broadcast completion to all connected clients
                try:
                    if self.loop and not self.loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self.broadcast_queue.put(completion_message), 
                            self.loop
                        )
                except Exception as e:
                    print(f"Failed to broadcast completion message: {e}")
                
                # Update status to completed
                with self.stats_lock:
                    self.stats["processing_status"] = "completed"
                
                return  # Don't process this as a regular frame
            
            # Update stats for regular frames
            human_count = data.get("human_count", 0)
            frame_number = data.get("frame_number", 0)
            
            with self.stats_lock:
                self.stats["total_frames"] += 1
                self.stats["current_human_count"] = human_count
                self.stats["total_humans"] += human_count
                self.stats["current_frame_number"] = frame_number
            
            # Prepare WebSocket message
            websocket_message = {
                "type": "frame_update",
                "frame_data": data.get("processed_frame", ""),
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
                "human_count": human_count,
                "frame_number": frame_number
            }
            
            # Save processed frame for video creation
            self.save_processed_frame(data.get("processed_frame", ""))
            
            # Add to buffer
            self.frame_buffer.append(websocket_message)
            
            # Put message in queue for async broadcasting
            try:
                if self.loop and not self.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast_queue.put(websocket_message), 
                        self.loop
                    )
            except Exception as e:
                print(f"Failed to queue message for broadcast: {e}")
            
        except Exception as e:
            print(f"Error handling detection result: {e}")
    
    def start_finalization_timer(self):
        """Start or reset the finalization timer"""
        # Cancel existing timer if running
        if self.finalization_timer:
            self.finalization_timer.cancel()
        
        # Start new timer - finalize video if no frames for 10 seconds
        self.finalization_timer = threading.Timer(10.0, self.auto_finalize_video)
        self.finalization_timer.daemon = True
        self.finalization_timer.start()
    
    def auto_finalize_video(self):
        """Auto-finalize video when no more frames are coming"""
        try:
            if self.video_writer is not None and self.stats["processing_status"] == "processing":
                print("🎬 No more frames detected - auto-finalizing video...")
                self.finalize_processed_video()
                print("✅ Processed video automatically saved!")
        except Exception as e:
            print(f"Error in auto-finalization: {e}")

    def save_processed_frame(self, base64_frame: str):
        """Save processed frame for video compilation"""
        try:
            if not base64_frame:
                return
            
            # Decode base64 frame
            frame_data = base64.b64decode(base64_frame)
            frame_array = np.frombuffer(frame_data, np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
            
            if frame is None:
                print("Warning: Failed to decode frame")
                return
            
            # Initialize video writer on first frame
            if self.video_writer is None:
                self.initialize_video_writer(frame)
            
            # Write frame to video
            if self.video_writer is not None and self.video_writer.isOpened():
                self.video_writer.write(frame)
            else:
                print("Warning: Video writer not available")
                
        except Exception as e:
            print(f"Error saving processed frame: {e}")
    
    def initialize_video_writer(self, first_frame):
        """Initialize video writer for processed video"""
        try:
            if self.stats["current_video"]:
                # Create output filename
                base_name = os.path.splitext(self.stats["current_video"])[0]
                self.output_video_path = os.path.join(
                    config.PROCESSED_VIDEOS_DIR, 
                    f"{base_name}_processed.mp4"
                )
                
                # Get frame dimensions
                height, width = first_frame.shape[:2]
                
                # Use H.264 codec for better compatibility
                fourcc = cv2.VideoWriter_fourcc(*'H264')  # Changed from 'mp4v'
                
                # Try H264, fallback to XVID if not available
                self.video_writer = cv2.VideoWriter(
                    self.output_video_path,
                    fourcc,
                    20.0,  # Reduced FPS for more stable video
                    (width, height)
                )
                
                # Verify video writer is opened
                if not self.video_writer.isOpened():
                    print("H264 codec failed, trying XVID...")
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    self.video_writer = cv2.VideoWriter(
                        self.output_video_path,
                        fourcc,
                        20.0,
                        (width, height)
                    )
                
                if self.video_writer.isOpened():
                    print(f"✅ Video writer initialized: {self.output_video_path}")
                    print(f"   Dimensions: {width}x{height}, Codec: {fourcc}")
                else:
                    print(f"❌ Failed to initialize video writer")
                    self.video_writer = None
                    
        except Exception as e:
            print(f"Error initializing video writer: {e}")
            self.video_writer = None
    
    def finalize_processed_video(self):
        """Finalize and save the processed video"""
        try:
            if self.video_writer is not None:
                print("Finalizing processed video...")
                
                # Properly release the video writer
                self.video_writer.release()
                self.video_writer = None
                
                # Verify the file was created and has content
                if self.output_video_path and os.path.exists(self.output_video_path):
                    file_size = os.path.getsize(self.output_video_path)
                    if file_size > 0:
                        print(f"✅ Processed video saved: {self.output_video_path}")
                        print(f"   File size: {file_size / 1024 / 1024:.2f} MB")
                    else:
                        print(f"❌ Video file is empty: {self.output_video_path}")
                        return None
                else:
                    print(f"❌ Video file not found: {self.output_video_path}")
                    return None
                
                # Update status to completed
                with self.stats_lock:
                    self.stats["processing_status"] = "completed"
                
                return self.output_video_path
            else:
                print("No video writer to finalize")
                return None
                
        except Exception as e:
            print(f"Error finalizing video: {e}")
            return None
    
    def start_consuming(self):
        """Start consuming messages from RabbitMQ in a separate thread"""
        if self.is_consuming:
            return
        
        if not self.setup_rabbitmq():
            raise Exception("Failed to setup RabbitMQ connection")
        
        self.is_consuming = True
        self.consumer_thread = threading.Thread(target=self._consume_loop, daemon=True)
        self.consumer_thread.start()
    
    def _consume_loop(self):
        """RabbitMQ consumption loop"""
        try:
            self.rabbitmq_client.consume_messages(
                config.DETECTION_RESULTS_QUEUE, 
                self.handle_detection_result
            )
        except Exception as e:
            print(f"Consumer loop error: {e}")
            self.is_consuming = False
    
    def stop_consuming(self):
        """Stop RabbitMQ consumption and finalize video"""
        if self.rabbitmq_client:
            self.rabbitmq_client.close()
        self.is_consuming = False
        
        # Finalize processed video when stopping
        if self.video_writer is not None:
            self.finalize_processed_video()
    
    def update_processing_status(self, status: str, video_name: str = None):
        """Update processing status"""
        with self.stats_lock:
            self.stats["processing_status"] = status
            if video_name:
                self.stats["current_video"] = video_name
            if status == "processing" and not self.stats["start_time"]:
                self.stats["start_time"] = datetime.now().isoformat()
    
    def reset_stats(self):
        """Reset processing statistics and video processing"""
        with self.stats_lock:
            self.stats = {
                "total_frames": 0,
                "total_humans": 0,
                "current_human_count": 0,
                "current_frame_number": 0,
                "processing_status": "idle",
                "start_time": None,
                "current_video": None
            }
        
        # Properly clean up video processing
        if self.video_writer is not None:
            try:
                self.video_writer.release()
            except:
                pass
            self.video_writer = None
        
        self.processed_frames = []
        self.output_video_path = None
        print("🔄 Stats and video processing state reset")
    
    def trigger_frame_reader(self, video_path: str):
        """Trigger frame reader service"""
        try:
            # Run frame_reader_service.py as subprocess
            frame_reader_path = os.path.join("..", "frame_reader", "frame_reader_service.py")
            subprocess.Popen([
                "python", frame_reader_path, video_path
            ], cwd=os.path.dirname(__file__))
            return True
        except Exception as e:
            print(f"Failed to trigger frame reader: {e}")
            return False

    async def broadcast_worker(self):
        """Background task to handle WebSocket broadcasting"""
        while True:
            try:
                message = await self.broadcast_queue.get()
                await self.websocket_manager.broadcast(message)
                self.broadcast_queue.task_done()
            except Exception as e:
                print(f"Broadcast worker error: {e}")

"""
StreamingService Class
----------------------

function name : __init__
benefit       : Initialize service state (WebSockets, RabbitMQ client, stats, frame buffers, directories).
return        : None

function name : setup_rabbitmq
benefit       : Connect to RabbitMQ using config values.
return        : bool (True if connected successfully, False otherwise)

function name : handle_detection_result
benefit       : Handle incoming detection results from RabbitMQ (update stats, save frames, broadcast updates).
return        : None

function name : start_finalization_timer
benefit       : Start/reset a 10s timer to auto-finalize video if no frames are received.
return        : None

function name : auto_finalize_video
benefit       : Automatically finalize video if frames stop arriving.
return        : None

function name : save_processed_frame
benefit       : Decode base64 frame → OpenCV image → append to output video.
return        : None

function name : initialize_video_writer
benefit       : Initialize OpenCV VideoWriter for saving processed frames as a video file.
return        : None

function name : finalize_processed_video
benefit       : Release video writer, verify saved file, update status.
return        : str or None (path to saved video or None if failed)

function name : start_consuming
benefit       : Start RabbitMQ message consumption in a background thread.
return        : None

function name : _consume_loop
benefit       : Internal loop to receive messages from RabbitMQ and call handler.
return        : None

function name : stop_consuming
benefit       : Stop RabbitMQ consumption and finalize any pending video.
return        : None

function name : update_processing_status
benefit       : Update stats with new processing status and optionally the current video name.
return        : None

function name : reset_stats
benefit       : Reset counters, clear video writer, reset processing state.
return        : None

function name : trigger_frame_reader
benefit       : Launch the frame reader microservice as a subprocess for a given video.
return        : bool (True if started successfully, False otherwise)

function name : broadcast_worker
benefit       : Async worker that sends messages from broadcast queue to WebSocket clients.
return        : None (coroutine, runs indefinitely)
"""



# Global service instance
streaming_service = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler"""
    global streaming_service
    # Startup
    streaming_service.loop = asyncio.get_event_loop()
    streaming_service.start_consuming()
    
    # Start broadcast worker
    broadcast_task = asyncio.create_task(streaming_service.broadcast_worker())
    
    yield
    
    # Shutdown
    broadcast_task.cancel()
    streaming_service.stop_consuming()

app = FastAPI(title="Video Detection Streaming Service", lifespan=lifespan)
"""lifespan
It’s a modern, cleaner replacement for startup/shutdown events:
Before the app starts → you can connect to a database, load ML models, initialize queues, etc.
After the app stops → you can close DB connections, free memory, disconnect message brokers, etc.
"""

# Add CORS middleware, Middleware is code that runs before and after every request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize service instance after app creation
streaming_service = StreamingService()



@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload video file and trigger processing"""
    try:
        # Validate file format
        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in config.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported format. Supported: {config.SUPPORTED_FORMATS}"
            )
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(config.UPLOADS_DIR, unique_filename)
        
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # Reset stats for new video
        streaming_service.reset_stats()
        streaming_service.update_processing_status("uploaded", unique_filename)
        
        return JSONResponse({
            "message": "Video uploaded successfully",
            "filename": unique_filename,
            "file_path": file_path,
            "status": "uploaded"
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post("/api/start-stream")
async def start_stream(data: dict):
    """Start video processing pipeline"""
    try:
        filename = data.get("filename")
        if not filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        
        file_path = os.path.join(config.UPLOADS_DIR, filename)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Video file not found")
        
        # Update status
        streaming_service.update_processing_status("processing", filename)
        
        # Trigger frame reader
        if not streaming_service.trigger_frame_reader(file_path):
            streaming_service.update_processing_status("error")
            raise HTTPException(status_code=500, detail="Failed to start processing")
        
        return JSONResponse({
            "message": "Video processing started",
            "filename": filename,
            "status": "processing"
        })
        
    except HTTPException:
        raise
    except Exception as e:
        streaming_service.update_processing_status("error")
        raise HTTPException(status_code=500, detail=f"Failed to start stream: {str(e)}")

@app.get("/api/stats")
async def get_stats():
    """Get current detection statistics"""
    with streaming_service.stats_lock:
        stats_copy = streaming_service.stats.copy()
    
    return JSONResponse(stats_copy)

@app.get("/api/status")
async def get_status():
    """Get current processing status"""
    with streaming_service.stats_lock:
        status_info = {
            "processing_status": streaming_service.stats["processing_status"],
            "current_video": streaming_service.stats["current_video"],
            "is_consuming": streaming_service.is_consuming,
            "connected_clients": len(streaming_service.websocket_manager.active_connections),
            "buffer_size": len(streaming_service.frame_buffer),
            "processed_video_ready": streaming_service.output_video_path is not None and 
                                   os.path.exists(streaming_service.output_video_path) if streaming_service.output_video_path else False
        }
    
    return JSONResponse(status_info)

@app.post("/api/finalize-video")
async def finalize_video():
    """Manually finalize the processed video"""
    try:
        output_path = streaming_service.finalize_processed_video()
        if output_path:
            return JSONResponse({
                "message": "Video finalized successfully",
                "output_path": output_path,
                "status": "completed"
            })
        else:
            return JSONResponse({
                "message": "No video to finalize or finalization failed",
                "status": "error"
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Finalization failed: {str(e)}")

@app.get("/api/download/{filename}")
async def download_processed_video(filename: str):
    """Download processed video file"""
    try:
        # Extract base name without extension and add processed suffix
        base_name = os.path.splitext(filename)[0]
        processed_filename = f"{base_name}_processed.mp4"
        file_path = os.path.join(config.PROCESSED_VIDEOS_DIR, processed_filename)
        
        # Check if file exists
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Processed video not found")
        
        # Check if file has content
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise HTTPException(status_code=404, detail="Processed video file is empty")
        
        print(f"📥 Downloading: {file_path} ({file_size / 1024 / 1024:.2f} MB)")
        
        # Return file with proper headers
        return FileResponse(
            path=file_path,
            filename=processed_filename,
            media_type='video/mp4',
            headers={
                "Content-Disposition": f"attachment; filename={processed_filename}",
                "Content-Length": str(file_size),
                "Cache-Control": "no-cache"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Download error: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time frame streaming"""
    await streaming_service.websocket_manager.connect(websocket)
    
    try:
        # Send any buffered frames to new client
        for frame_data in list(streaming_service.frame_buffer):
            await websocket.send_json(frame_data)
        
        # Keep connection alive and handle client messages
        while True:
            try:
                # Wait for client messages (ping/pong, etc.)
                data = await websocket.receive_text()
                # Echo back for connection health check
                await websocket.send_json({"type": "pong", "message": "Connection alive"})
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"WebSocket error: {e}")
                break
                
    except WebSocketDisconnect:
        pass
    finally:
        streaming_service.websocket_manager.disconnect(websocket)
        
        
@app.get("/api/video-status/{filename}")
async def check_video_status(filename: str):
    """Check if processed video is ready for download"""
    try:
        base_name = os.path.splitext(filename)[0]
        processed_filename = f"{base_name}_processed.mp4"
        file_path = os.path.join(config.PROCESSED_VIDEOS_DIR, processed_filename)
        
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            return JSONResponse({
                "exists": True,
                "file_size": file_size,
                "file_size_mb": round(file_size / 1024 / 1024, 2),
                "ready": file_size > 0,
                "path": processed_filename
            })
        else:
            return JSONResponse({
                "exists": False,
                "ready": False
            })
            
    except Exception as e:
        return JSONResponse({
            "exists": False,
            "ready": False,
            "error": str(e)
        })

# Health check endpoint
@app.get("/health")
async def health_check():
    return JSONResponse({"status": "healthy", "service": "streaming"})


"""
API Endpoints Documentation
---------------------------

function name : upload_video
benefit       : Upload a video file, validate format, save to uploads folder, reset stats, update status.
return        : JSONResponse (message, filename, file_path, status)

function name : start_stream
benefit       : Start video processing pipeline, trigger frame reader, update status.
return        : JSONResponse (message, filename, status)

function name : get_stats
benefit       : Get current detection statistics safely (thread-safe copy).
return        : JSONResponse (stats dict)

function name : get_status
benefit       : Get current processing status (status, video name, clients, buffer size, video readiness).
return        : JSONResponse (status info dict)

function name : finalize_video
benefit       : Manually finalize video processing and save output file.
return        : JSONResponse (message, output_path, status)

function name : download_processed_video
benefit       : Download processed video file if it exists and is valid.
return        : FileResponse (video file with headers) or HTTPException

function name : websocket_endpoint
benefit       : WebSocket endpoint for real-time frame streaming and client communication.
return        : None (bi-directional WebSocket stream)

function name : check_video_status
benefit       : Check if the processed video exists and is ready for download.
return        : JSONResponse (file existence, size, readiness)

function name : health_check
benefit       : Basic health check endpoint for monitoring service availability.
return        : JSONResponse ({status: "healthy", service: "streaming"})
"""


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)







