#WebRTC call recorder
Simple API driven service for audio-calls with dialog recording. Depends on [aiortc](https://github.com/aiortc/aiortc) library.

##Working algorithm
1. External application (EA) handle authentication, authorization and receive call requests from authorized clients.
2. On call request EA made API call for room initialization with clients id and room id.
3. EA sends clients id and room id from step 2 to clients and thay connect to service with their id to dedicated room.
4. Service handle api call, create room and wait for clients connection
5. After all described clients connected service starts to proxy webrtc streams and record them to file.
6. Service handles disconnect by data channel message which means properly disconnection by button.
7. When all callers leave the room, service made request on webhook url. It's post request with disconnect reasons for each user, call time and record file attached.

##Settings description

##API description

