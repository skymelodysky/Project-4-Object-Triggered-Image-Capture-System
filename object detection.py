import time
import gc
import board
import busio
import digitalio
import wiznet
from adafruit_wiznet5k.adafruit_wiznet5k import WIZNET5K
import adafruit_wiznet5k.adafruit_wiznet5k_socketpool as socketpool
import adafruit_ov2640
import adafruit_hcsr04

print("W6300 Object Detection Camera")

MY_MAC = "00:08:DC:03:04:05"
#MY_MAC = "00:01:02:03:04:05"

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
led.value = False

ethernetRst = digitalio.DigitalInOut(board.W5K_RST)
ethernetRst.direction = digitalio.Direction.OUTPUT

cs = digitalio.DigitalInOut(board.W5K_CS)

# Reset W6300
ethernetRst.value = False
time.sleep(1)
ethernetRst.value = True
time.sleep(1)

# SPI setup
spi_bus = wiznet.PIO_SPI(
    board.W5K_SCK, 
    quad_io0=board.W5K_MOSI, 
    quad_io1=board.W5K_MISO, 
    quad_io2=board.W5K_IO2, 
    quad_io3=board.W5K_IO3
)

# Initialize Ethernet
eth = WIZNET5K(spi_bus, cs, is_dhcp=True, mac=MY_MAC, debug=False)
ip_address = eth.pretty_ip(eth.ip_address)
print(f"IP Address: {ip_address}")

# Camera setup
print("Initializing camera...")
i2c = busio.I2C(board.GP9, board.GP8)

cam = adafruit_ov2640.OV2640(
    i2c,
    data_pins=[board.GP0, board.GP1, board.GP2, board.GP3,
              board.GP4, board.GP5, board.GP6, board.GP7],
    clock=board.GP10,
    vsync=board.GP12,
    href=board.GP11,
    reset=board.GP13,
    mclk_frequency=20_000_000,
)

# Configure camera
cam.size = adafruit_ov2640.OV2640_SIZE_SVGA
cam.colorspace = adafruit_ov2640.OV2640_COLOR_JPEG
cam.test_pattern = False
time.sleep(2)

print("Camera configured: SVGA JPEG")

# Ultrasonic sensor setup
sonar = adafruit_hcsr04.HCSR04(
    trigger_pin=board.GP27, 
    echo_pin=board.GP26,
    timeout=0.5
) 

# Frame buffer
frame_buffer = bytearray(40 * 1024)

# Detection threshold (20 cm)
DETECTION_THRESHOLD = 20.0

# Store the latest detected image and distance
latest_detection_image = None
current_distance = None
show_image = False
last_detection_time = 0
detection_cooldown = 1.0

print(f"\n✅ Object Detection System Ready!")
print(f"   Connect to: http://{ip_address}")
print(f"   Detection distance: <{DETECTION_THRESHOLD}cm")



def measure_distance():
    """Measure distance using ultrasonic sensor"""
    try:
        distance = sonar.distance
        if 2 <= distance <= 400:
            return distance
    except RuntimeError:
        pass
    return None

def capture_image():
    """Capture and return JPEG image data"""
    try:
        jpeg_data = cam.capture(frame_buffer)
        if jpeg_data:
            return bytes(jpeg_data)
    except Exception as e:
        print(f"Capture error: {e}")
    return None

def send_html_response(conn, client_ip):
    """Send HTML page"""
    global current_distance, latest_detection_image, show_image
    
    # Handle None distance
    distance_str = f"{current_distance:.1f} cm" if current_distance is not None else "-- cm"
    
    # Check if we should show the image
    show_image = current_distance is not None and current_distance < DETECTION_THRESHOLD
    
    # Border color based on detection
    border_color = "red" if show_image else "#ccc"
    
    if show_image:
        text = f"""
         <img src="/latest.jpg" alt="Camera Image">
        """
    else:
        text = f"""
        <div id="placeholder">
            <div>
                <h3>No Object Detected</h3>
                <p>Image will appear when object is within {DETECTION_THRESHOLD}cm</p>
                <p>Current distance: {distance_str}</p>
            </div>
        </div>
        """
        
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Object Detection Camera</title>
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center; margin: 20px; }}
        h1 {{ color: #333; }}
        #distance {{ 
            font-size: 24px; 
            font-weight: bold; 
            padding: 10px; 
            margin: 20px auto; 
            width: 200px;
            border-radius: 10px;
            background: {'#ffcccc' if show_image else '#ccffcc'};
        }}
        img {{ 
            border: 3px solid {border_color}; 
            min-width: 60%;
            margin: 20px auto;
            display: block;
            min-height: 500px;
            background: #f0f0f0;
            transform: rotate(180deg);
        }}
        .status {{ 
            font-size: 20px; 
            font-weight: bold; 
            color: {'red' if show_image else 'green'};
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <h1>Object Detection Camera</h1>
    <p>IP: {ip_address}</p>
    
    <div id="distance">
        {distance_str}
    </div>
    
    <div class="status">
        {'🚨 OBJECT DETECTED!' if show_image else '✅ Monitoring...'}
    </div>
    """ + text + f"""
    <script>
        // Auto-refresh every 1.5 seconds
        setTimeout(function() {{
            location.reload();
        }}, 2000);
    </script>
</body>
</html>"""
    
    response = (f"HTTP/1.1 200 OK\r\n"
               f"Content-Type: text/html; charset=utf-8\r\n"
               f"Connection: close\r\n"
               f"Content-Length: {len(html)}\r\n"
               f"\r\n{html}").encode()
    
    try:
        conn.send(response)
        print(f"  Distance: {distance_str}")
        return True
    except Exception as e:
        print(f"  Failed to send HTML: {e}")
        return False

def send_image_response(conn):
    """Send JPEG image with 2048 byte chunks"""
    global latest_detection_image
    
    if latest_detection_image:
        image_length = len(latest_detection_image)
        
        response = (f"HTTP/1.1 200 OK\r\n"
                   f"Content-Type: image/jpeg\r\n"
                   f"Content-Length: {image_length}\r\n"
                   f"Cache-Control: no-cache\r\n"
                   f"Connection: close\r\n"
                   f"\r\n").encode()
        
        try:
            conn.send(response)
            
            # Send in 2048 byte chunks
            chunk_size = 2048
            for i in range(0, image_length, chunk_size):
                end = min(i + chunk_size, image_length)
                conn.send(latest_detection_image[i:end])
            
            print(f"  Image sent: {image_length/1024:.1f}KB")
        
        except Exception as e:
            print(f"  Image send error (client may have closed): {e}")
    else:
        try:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nNo image available"
            conn.send(response)
        except Exception as e:
            print(f"  Could not send 404: {e}")

# Create server socket ONCE at startup
print("\n⏳ Creating server socket...")
pool = socketpool.SocketPool(eth)
server = pool.socket()
server.bind((ip_address, 80))
server.listen(1)
print(f"✅ Server ready at http://{ip_address}")
print("Waiting for connections...")

last_dhcp_check = time.monotonic()
last_distance_check = time.monotonic()
connection_active = False

# Main loop - SINGLE SERVER SOCKET
try:
    while True:
        current_time = time.monotonic()
        
        # DHCP maintenance (every 30 seconds)
        if current_time - last_dhcp_check > 30:
            try:
                eth.maintain_dhcp_lease()
                print("  DHCP lease maintained")
            except Exception as e:
                print(f"  DHCP error: {e}")
            last_dhcp_check = current_time
        
        # Check distance regularly (every 0.5 seconds)
        
        if current_time - last_distance_check > 0.5:
            distance = measure_distance()
            current_distance = distance
            
            if distance and distance < DETECTION_THRESHOLD:
                if current_time - last_detection_time > detection_cooldown:
                    # Capture image
                    led.value = True
                    image_data = capture_image()
                    
                    if image_data:
                        if image_data[0] == 0xFF and image_data[1] == 0xD8:
                            latest_detection_image = image_data
                            show_image = True
                            last_detection_time = current_time
                            print(f"🚨 Detected: {distance:.1f}cm")
                    
                    time.sleep(0.1)
                    led.value = False
            elif distance is None or distance >= DETECTION_THRESHOLD:
                show_image = False
                latest_detection_image = None
                gc.collect()
            
            last_distance_check = current_time
        
        # Handle client connections with timeout
        try:
            server.settimeout(0.1)  # Short timeout to check regularly
        except:
            pass
        
        try:
            conn, addr = server.accept()
            client_ip = addr[0]
            if client_ip == "0.0.0.0" or addr[1] == 0:
                conn.close()
                continue
            
            if not connection_active:
                print(f"\n📡 Client connected: {client_ip}")
                connection_active = True
            
            # Set timeout for receiving request
            try:
                conn.settimeout(1.0)
            except:
                break
            
            try:
                # Receive request
                data = conn.recv(1024)
                
                if data:
                    # Parse request
                    if b'GET /latest.jpg' in data:
                        send_image_response(conn)
                    else:
                        # Main page or any request
                        send_html_response(conn, client_ip)
                
            except Exception as e:
                # Timeout or error receiving
                pass
            
            # Close connection after handling
            conn.close()
            
        except Exception as e:
            if "timeout" not in str(e):
                # Reset connection flag if error (not timeout)
                connection_active = False
                print(e)
        
        # Small delay
        time.sleep(0.01)
        
except KeyboardInterrupt:
    print("\n\n🛑 Server stopped by user")

finally:
    led.value = False
    try:
        server.close()
    except:
        pass
    print("\n✅ System stopped")
    