import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Upload, Download, Wifi, WifiOff, AlertCircle, CheckCircle, Clock } from 'lucide-react';
import './App.css';

const App = () => {
  // State management
  const [uploadStatus, setUploadStatus] = useState('idle'); // idle, uploaded, processing, completed, error
  const [isConnected, setIsConnected] = useState(false);
  const [currentFrame, setCurrentFrame] = useState(null);
  const [stats, setStats] = useState({
    current_human_count: 0,
    total_humans: 0,
    total_frames: 0,
    processing_status: 'idle',
    current_video: null
  });
  const [uploadedFile, setUploadedFile] = useState(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Refs
  const fileInputRef = useRef(null);
  const wsRef = useRef(null);
  const statsIntervalRef = useRef(null);

  // Configuration
  const API_BASE = 'http://localhost:8003';
  const WS_URL = 'ws://localhost:8003/ws';
  const SUPPORTED_FORMATS = ['mp4', 'avi', 'mov', 'mkv', 'webm'];

  // Utility functions
  const showError = (message) => {
    setError(message);
    setTimeout(() => setError(''), 5000);
  };

  const showSuccess = (message) => {
    setSuccess(message);
    setTimeout(() => setSuccess(''), 3000);
  };

  const validateFile = (file) => {
    if (!file) return 'No file selected';
    
    const fileExtension = file.name.split('.').pop().toLowerCase();
    if (!SUPPORTED_FORMATS.includes(fileExtension)) {
      return `Unsupported format. Please use: ${SUPPORTED_FORMATS.join(', ').toUpperCase()}`;
    }
    
    // Check file size (limit to 500MB)
    const maxSize = 500 * 1024 * 1024; // 500MB in bytes
    if (file.size > maxSize) {
      return 'File size too large. Maximum size is 500MB';
    }
    
    return null;
  };

  // WebSocket functions
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      wsRef.current = new WebSocket(WS_URL);
      
      wsRef.current.onopen = () => {
        console.log('WebSocket connected');
        setIsConnected(true);
      };

      wsRef.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          if (data.type === 'frame_update') {
            setCurrentFrame(`data:image/jpeg;base64,${data.frame_data}`);
            // Update current human count from WebSocket data
            setStats(prev => ({
              ...prev,
              current_human_count: data.human_count || 0
            }));
          }
          
          // CRITICAL: Handle processing completion
          if (data.type === 'processing_complete') {
            console.log('Video processing completed!', data);
            setUploadStatus('completed');
            showSuccess('Video processing completed! You can now download the processed video.');
            stopStatsPolling();
            
            // Update final stats
            setStats(prev => ({
              ...prev,
              processing_status: 'completed',
              total_frames: data.total_frames || prev.total_frames
            }));
            
            // Optional: Close WebSocket after completion
            if (wsRef.current) {
              wsRef.current.close();
            }
          }

        } catch (error) {
          console.error('Error parsing WebSocket message:', error);
        }
      };

      wsRef.current.onclose = () => {
        console.log('WebSocket disconnected');
        setIsConnected(false);
        
        // Auto-reconnect if processing (but not if completed)
        if (uploadStatus === 'processing') {
          setTimeout(connectWebSocket, 2000);
        }
      };

      wsRef.current.onerror = (error) => {
        console.error('WebSocket error:', error);
        setIsConnected(false);
      };

    } catch (error) {
      console.error('Failed to connect WebSocket:', error);
      setIsConnected(false);
    }
  }, [uploadStatus]);

  // API functions
  const uploadFile = async (file) => {
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Upload failed' }));
        throw new Error(errorData.detail || 'Upload failed');
      }

      const result = await response.json();
      setUploadedFile({
        filename: result.filename,
        originalName: file.name
      });
      setUploadStatus('uploaded');
      showSuccess('File uploaded successfully!');
      
      // Auto-start processing
      await startProcessing(result.filename);
      
    } catch (error) {
      console.error('Upload error:', error);
      showError(error.message || 'Upload failed');
      setUploadStatus('error');
    }
  };

  const startProcessing = async (filename) => {
    try {
      const response = await fetch(`${API_BASE}/api/start-stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ filename: filename }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Processing failed to start' }));
        throw new Error(errorData.detail || 'Processing failed to start');
      }

      setUploadStatus('processing');
      connectWebSocket();
      startStatsPolling();
      showSuccess('Processing started!');

    } catch (error) {
      console.error('Start processing error:', error);
      showError(error.message || 'Failed to start processing');
      setUploadStatus('error');
    }
  };

  const fetchStats = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/stats`);
      if (response.ok) {
        const data = await response.json();
        console.log('Stats update:', data); // Debug log
        setStats(data);

        // Update upload status based on backend status
        if (data.processing_status === 'completed' && uploadStatus === 'processing') {
          setUploadStatus('completed');
          showSuccess('Video processing completed!');
          stopStatsPolling();
        } else if (data.processing_status === 'processing') {
          setUploadStatus('processing');
        } else if (data.processing_status === 'error') {
          setUploadStatus('error');
          stopStatsPolling();
        }
      }
    } catch (error) {
      console.error('Error fetching stats:', error);
    }
  };

  const downloadProcessedVideo = async () => {
    if (!uploadedFile?.filename || uploadStatus !== 'completed') return;

    try {
      // First check if video is ready
      const statusResponse = await fetch(`${API_BASE}/api/video-status/${uploadedFile.filename}`);
      const statusData = await statusResponse.json();
      
      if (!statusData.ready) {
        showError('Processed video is not ready yet or file is empty');
        return;
      }
      
      console.log(`Downloading video: ${statusData.file_size_mb}MB`);
      
      // Download the file
      const response = await fetch(`${API_BASE}/api/download/${uploadedFile.filename}`);
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Download failed' }));
        throw new Error(errorData.detail || 'Download failed');
      }
      
      // Get the blob
      const blob = await response.blob();
      
      // Verify blob has content
      if (blob.size === 0) {
        throw new Error('Downloaded file is empty');
      }
      
      // Create download link
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = `processed_${uploadedFile.originalName}`;
      
      // Trigger download
      document.body.appendChild(a);
      a.click();
      
      // Cleanup
      setTimeout(() => {
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }, 100);
      
      showSuccess(`Download started! (${statusData.file_size_mb}MB)`);

    } catch (error) {
      console.error('Download error:', error);
      showError(error.message || 'Failed to download processed video');
    }
  };


  const checkVideoReady = async () => {
    if (!uploadedFile?.filename) return false;
    
    try {
      const response = await fetch(`${API_BASE}/api/video-status/${uploadedFile.filename}`);
      const data = await response.json();
      return data.ready;
    } catch (error) {
      console.error('Error checking video status:', error);
      return false;
    }
  };

  // Polling functions
  const startStatsPolling = () => {
    stopStatsPolling(); // Clear any existing interval
    fetchStats(); // Fetch immediately
    statsIntervalRef.current = setInterval(fetchStats, 2000);
  };

  const stopStatsPolling = () => {
    if (statsIntervalRef.current) {
      clearInterval(statsIntervalRef.current);
      statsIntervalRef.current = null;
    }
  };

  // Event handlers
  const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (!file) return;

    const validationError = validateFile(file);
    if (validationError) {
      showError(validationError);
      return;
    }

    uploadFile(file);
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const handleDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    
    const files = event.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      const validationError = validateFile(file);
      if (validationError) {
        showError(validationError);
        return;
      }

      uploadFile(file);
    }
  };

  // Status indicator component
  const StatusIndicator = () => (
    <div className="flex items-center gap-2">
      {uploadStatus === 'idle' && <Clock className="w-4 h-4 text-gray-400" />}
      {uploadStatus === 'uploaded' && <CheckCircle className="w-4 h-4 text-green-500" />}
      {uploadStatus === 'processing' && (
        <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
      )}
      {uploadStatus === 'completed' && <CheckCircle className="w-4 h-4 text-green-600" />}
      {uploadStatus === 'error' && <AlertCircle className="w-4 h-4 text-red-500" />}
      <span className="font-medium capitalize text-sm">{uploadStatus}</span>
    </div>
  );

  const ConnectionStatus = () => (
    <div className="flex items-center gap-2">
      {isConnected ? (
        <>
          <Wifi className="w-4 h-4 text-green-500" />
          <span className="text-green-600 text-sm">Connected</span>
        </>
      ) : (
        <>
          <WifiOff className="w-4 h-4 text-red-500" />
          <span className="text-red-600 text-sm">Disconnected</span>
        </>
      )}
    </div>
  );

  // Cleanup on component unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      stopStatsPolling();
    };
  }, []);

  // Auto-connect WebSocket and start polling on mount
  useEffect(() => {
    connectWebSocket();
    startStatsPolling();
  }, [connectWebSocket]);

  return (
    <div className="min-h-screen bg-gray-100 p-6">
      <div className="max-w-full mx-auto">
        <header className="mb-8 px-6">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Real-time Video Detection Service
          </h1>
          <p className="text-gray-600">
            Upload videos for human detection analysis with live streaming results
          </p>
        </header>

        {/* Error/Success Messages */}
        <div className="px-6">
        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-red-500" />
            <span className="text-red-700">{error}</span>
          </div>
        )}

        {success && (
          <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-green-500" />
            <span className="text-green-700">{success}</span>
          </div>
        )}
        </div>

        <div className="bg-white shadow-sm border-t border-gray-200">
          {/* Compact Control Panel */}
          <div className="bg-white border-b border-gray-200 p-4">
            <div className="flex flex-wrap items-center justify-between gap-4 max-w-7xl mx-auto px-6">
              <div className="flex items-center gap-4">
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileSelect}
                  accept={SUPPORTED_FORMATS.map(format => `.${format}`).join(',')}
                  style={{ display: 'none' }}
                />
                <button 
                  onClick={handleUploadClick}
                  disabled={uploadStatus === 'processing'}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  <Upload className="w-4 h-4 inline mr-2" />
                  Upload
                </button>
                <StatusIndicator />
                <ConnectionStatus />
              </div>
              <div className="flex items-center gap-4 text-sm">
                <span><strong>{stats.current_human_count}</strong> Current</span>
                <span><strong>{stats.total_humans}</strong> Total</span>
                <span><strong>{stats.total_frames}</strong> Frames</span>
                <button 
                  onClick={downloadProcessedVideo}
                  disabled={uploadStatus !== 'completed' || !uploadedFile}
                  className={`px-3 py-1 rounded font-medium transition-colors ${
                    uploadStatus === 'completed' && uploadedFile
                      ? 'bg-green-600 hover:bg-green-700 text-white' 
                      : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  }`}
                >
                  <Download className="w-4 h-4 inline mr-1" />
                  {uploadStatus === 'completed' && uploadedFile ? 'Download' : 
                   uploadStatus === 'processing' ? 'Processing...' :
                   'Download'}
                </button>
              </div>
            </div>
          </div>

          {/* Video Display */}
          <div 
            className="bg-black flex items-center justify-center relative h-screen-70 w-full"
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          >
            {currentFrame ? (
              <img 
                src={currentFrame} 
                alt="Processed frame with detections" 
                className="max-w-full max-h-full object-contain"
              />
            ) : (
              <div className="text-white text-center">
                <Upload className="w-16 h-16 mx-auto mb-4 opacity-50" />
                <p className="text-lg">Drop video file here or click Upload</p>
                <p className="text-sm opacity-75">
                  Supports: {SUPPORTED_FORMATS.join(', ').toUpperCase()}
                </p>
                <p className="text-xs opacity-50 mt-2">
                  Real-time detection with bounding boxes will appear here
                </p>
              </div>
            )}
            {uploadStatus === 'processing' && (
              <div className="absolute top-4 left-4 bg-red-600 text-white px-3 py-1 rounded-full text-sm font-medium">
                ● LIVE
              </div>
            )}
            {stats.current_video && (
              <div className="absolute bottom-4 left-4 bg-black bg-opacity-75 text-white px-3 py-1 rounded text-sm">
                {stats.current_video}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;