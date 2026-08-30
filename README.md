This project is a recording and camera tracking solution. It is two independent systems. The first part uses two quadrature encoders built into the Favero reels and the second is a Raspberry Pi that uses the Bluetooth (BLE) from a Skewered Fencing machine to create an overlay for fencing recording.

Camera Tracking

Reel Hardware

An explanation of how the quadrature encoding works can be found in this Youtube Video.

The tracking system uses quadrature encoding, a series of four black matte vinyl wrap  ⅛ circle segments that are adhered to the bottom of the drum of the Favero reel. The other four ⅛ circle segments are left blank since the aluminum is reflective enough on its own.

Two TCRT5000 Infrared Sensors are placed 32 mm apart from each other and held in place by a 3D printed holder. They are connected via a PCB using the Gerber File and the schematic. These sensors are powered by and relay data back to the circuit board attached to the same Gerber File as above. The main components for the board are a Wemos D1 Mini and a 0.96 inch OLED screen along with a few 10k Ω resistors, 0.1 and 100 μF capacitors, pushbuttons and connectors. For the 0.96 inch OLED, ensure that the VCC and GND are in the correct order and match the PCB. There seems to be two styles and one of them has the two pins flipped. The system also uses an 18650 Battery, a TP4056 charging module, a battery holder and a power switch. This is enclosed in a 3D printed holder that is meant to be screwed into the back of the Favero reel using a tapped, threaded connection to hold 8 mm M3 screws. The power switch is designed so that it can either charge the battery or power circuit board. To directly power the circuit board, use the USB on the Wemos to power the system.

The Servo used is a DS3218 and sits atop a tripod and holds the manually operated USB camera. The D1 Wemos that controls the camera is not mounted on a PCB

Reel Coding

The code for the Wemos can be found above. The Left or Right reel is determined by the line:

outgoingData.senderID = 1; //1 For Left and 2 for Right

Update the line with a 2, for the Right Reel. The distance from the reel to the camera is assumed to be about 250. The units are arbitrary but are displayed on the OLED when the reel is extended. You can either work with the assumption, set it every time that you power up the reel or hardcode in a better value by changing the line of code:

int hypotenuse = 250;

The coding for the Servo Camera can be found here.

Video Recording and Replay

The video recording system is relatively straightforward and relies heavily on the work done already by Augusto Roman. The Skewered Fencing box transmits its data using Bluetooth (BLE). The Raspberry Pi receives the BLE and then displays the data as an overlay on the video from the webcam that is plugged into the Raspberry Pi. The Raspberry Pi uses a display screen and is powered by a battery bank.

The Raspberry Pi records the videos automatically once there is a change in the data, such as a scoring light and continues on for 5 minutes after the last light was on. It also automatically starts a new bout when the score is set to 0 - 0. The names and team logos can be adjusted and bouts are taken off the Raspberry Pi using a USB stick. The clips are saved and then exported. 

The recording works by continuously recording and then saving the time stamps. If all the bouts are exported then only the clips of touches are saved. If a specific bout is exported it will show the entire bout, not solely the touches.

It is not designed for livestreaming, though it could be modified to do so without too much trouble.

The Raspberry Pi is set up in a way that I find convenient and it is meant so that anyone who uses it, can modify the code to suit their needs.

Future Considerations:

The code is meant to be easily adapted and there is little expectation that everyone will keep the code standard. It is designed to be adapted to the user. A few of the ideas that I may implement later. 

Livestreaming or uploading directly from the Raspberry Pi. It is very much possible to directly upload from the Pi to a Youtube channel, even livestreaming is feasible. My biggest hesitancy for this is the numerous high level fencing tournaments with poor quality streams. I don’t want to have to rely on the local wifi and feel that recording with a later upload would yield a better product. And very few people particularly care if the fencing bout is live or a few hours old. Most of the live data is consumed by friend or parents and that comes from the FencingTimeLive results.

Adding favorites or clips to review. A simple feature that uses something like cycling through “match count” or some other little used button on the remote, or even adding a second to the score clock, this could be a signal to the Raspberry Pi to tag that specific clip for a “Favorites” or quick look up feature and then be de-tagged later.
The skewered fencing machine also has blade contact and a waterfall display. There might be a clever way to display this info on the fencing overlay.

Subtle danger music that plays when a bout is 4-4, or 14-14, maybe victory music when the final point is awarded. Could also tie it to a button. 

Audio, currently there really isn’t any audio tied into the Raspberry Pi. There really isn’t any particular reason why other than I have not done it yet.
