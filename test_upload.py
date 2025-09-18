# test_upload.py
import requests
import os

def test_upload():
    url = "http://localhost:8003/api/upload"
    
    # Check if file exists
    video_file = "test_video.mp4"
    if not os.path.exists(video_file):
        print(f"Video file {video_file} not found!")
        print("Available files:")
        for f in os.listdir("."):
            if f.endswith(('.mp4', '.avi', '.mov')):
                print(f"  - {f}")
        return None
    
    try:
        with open(video_file, "rb") as f:
            files = {"file": (video_file, f, "video/mp4")}
            response = requests.post(url, files=files)
        
        print("Response status:", response.status_code)
        print("Response:", response.json())
        return response.json()
    
    except Exception as e:
        print(f"Upload failed: {e}")
        return None

if __name__ == "__main__":
    result = test_upload()
    if result and 'filename' in result:
        print(f"Success! Uploaded as: {result['filename']}")