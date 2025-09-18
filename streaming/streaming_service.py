import os
import json
import threading
import time
import uuid
import subprocess
from datetime import datetime
from typing import List, Dict, Any
from collections import deque
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
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
        self.frame_buffer = deque(maxlen=100)  # Buffer for frames
        self.stats = {
            "total_frames": 0,
            "total_humans": 0,
            "current_human_count": 0,
            "processing_status": "idle",
            "start_time": None,
            "current_video": None
        }
        self.stats_lock = threading.Lock()
        self.is_consuming = False
        self.consumer_thread = None
        self.loop = None
        self.broadcast_queue = asyncio.Queue()
        
        # Ensure directories exist
        os.makedirs(config.UPLOADS_DIR, exist_ok=True)
        os.makedirs(config.TEMP_FRAMES_DIR, exist_ok=True)
    
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
            
            # Update stats - use the correct field name from detection service
            human_count = data.get("human_count", 0)  # Detection service sends "human_count"
            with self.stats_lock:
                self.stats["total_frames"] += 1
                self.stats["current_human_count"] = human_count
                self.stats["total_humans"] += human_count
            
            # Prepare WebSocket message
            websocket_message = {
                "type": "frame_update",
                "frame_data": data.get("processed_frame", ""),
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
                "human_count": human_count
            }
            
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
        """Stop RabbitMQ consumption"""
        if self.rabbitmq_client:
            self.rabbitmq_client.close()
        self.is_consuming = False
    
    def update_processing_status(self, status: str, video_name: str = None):
        """Update processing status"""
        with self.stats_lock:
            self.stats["processing_status"] = status
            if video_name:
                self.stats["current_video"] = video_name
            if status == "processing" and not self.stats["start_time"]:
                self.stats["start_time"] = datetime.now().isoformat()
    
    def reset_stats(self):
        """Reset processing statistics"""
        with self.stats_lock:
            self.stats = {
                "total_frames": 0,
                "total_humans": 0,
                "current_human_count": 0,
                "processing_status": "idle",
                "start_time": None,
                "current_video": None
            }
    
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

# Add CORS middleware
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
            "buffer_size": len(streaming_service.frame_buffer)
        }
    
    return JSONResponse(status_info)

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

# Health check endpoint
@app.get("/health")
async def health_check():
    return JSONResponse({"status": "healthy", "service": "streaming"})

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)