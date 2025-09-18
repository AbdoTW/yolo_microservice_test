import pika
import json
import sys
sys.path.append('.')
from shared.rabbitmq_client import RabbitMQClient

def debug_callback(ch, method, properties, body):
    try:
        data = json.loads(body.decode('utf-8'))
        print("=== RAW MESSAGE FROM DETECTION SERVICE ===")
        print(f"Keys in message: {list(data.keys())}")
        print(f"Full message: {json.dumps(data, indent=2)}")
        print("=" * 50)
        
        # Check what detection count field is called
        for key, value in data.items():
            if 'count' in key.lower() or 'detection' in key.lower():
                print(f"Potential count field: {key} = {value}")
        
    except Exception as e:
        print(f"Error parsing message: {e}")
        print(f"Raw body: {body}")

# Create consumer
rabbitmq = RabbitMQClient()
if rabbitmq.connect():
    print("Listening for detection results...")
    rabbitmq.consume_messages('detection_results', debug_callback)
else:
    print("Failed to connect to RabbitMQ")
