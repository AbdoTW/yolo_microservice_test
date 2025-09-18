#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from shared.rabbitmq_client import RabbitMQClient

def test_rabbitmq_connection():
    print("Testing RabbitMQ connection...")
    
    # Create client
    client = RabbitMQClient()
    
    # Test connection
    if client.connect():
        print("✅ Successfully connected to RabbitMQ!")
        
        # Test queue declaration
        try:
            client.declare_queue('test_queue')
            print("✅ Successfully declared test queue!")
            
            # Test message publishing
            test_message = {"test": "Hello RabbitMQ!", "timestamp": "2024-09-18"}
            if client.publish_message('test_queue', test_message):
                print("✅ Successfully published test message!")
            else:
                print("❌ Failed to publish test message")
            
        except Exception as e:
            print(f"❌ Error during queue operations: {e}")
        
        client.close()
        print("✅ Connection closed successfully")
        return True
    else:
        print("❌ Failed to connect to RabbitMQ")
        print("Make sure RabbitMQ server is running:")
        print("  sudo systemctl status rabbitmq-server")
        return False

if __name__ == "__main__":
    test_rabbitmq_connection()