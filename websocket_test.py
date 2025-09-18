import asyncio
import websockets
import json
import base64
from datetime import datetime

async def test_websocket():
    uri = "ws://localhost:8003/ws"
    frame_count = 0
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"[{datetime.now()}] 🔗 Connected to WebSocket")
            print("📺 Listening for processed frames with bounding boxes...\n")
            
            # Listen for 30 seconds to catch some frames
            timeout = 30
            start_time = asyncio.get_event_loop().time()
            
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    data = json.loads(message)
                    
                    if data.get('type') == 'frame_update':
                        frame_count += 1
                        human_count = data.get('human_count', 0)
                        timestamp = data.get('timestamp', 'N/A')
                        frame_data = data.get('frame_data', '')
                        
                        print(f"🎬 Frame #{frame_count}")
                        print(f"   👥 Humans detected: {human_count}")
                        print(f"   ⏰ Timestamp: {timestamp}")
                        print(f"   🖼️  Frame data size: {len(frame_data)} chars (base64)")
                        
                        if human_count > 0:
                            print(f"   ✅ HUMANS DETECTED WITH BOUNDING BOXES!")
                        print("-" * 50)
                    
                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    await websocket.send("ping")
                except Exception as e:
                    print(f"❌ Error: {e}")
                    break
            
            print(f"\n🎯 Test Complete!")
            print(f"📊 Total frames received: {frame_count}")
            if frame_count > 0:
                print("✅ SUCCESS: WebSocket streaming is working!")
            else:
                print("⚠️  No frames received - check if processing is still running")
                    
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())
