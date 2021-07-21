# camera.sh is a script to capture a video 
# Display camera output to display, and optionally saves an H264 capture at requested bitrate
# usage: raspiVid [options]
# Image parameter commands:
#		-w, --width : Set image width . Default 1920

#		-h, --height : Set image height . Default 1080

#		-b, --bitrate : Set bitrate. Use bits per second (e.g. 10MBits/s would be -b 10000000)

#		-o, --output : Output filename (to write to stdout, use '-o -')

#		-v, --verbose : Output verbose information during run

#		-t, --timeout : Time (in ms) before takes picture and shuts down. If not specified, set to 5s

#		-d, --demo : Run a demo mode (cycle through range of camera options, no capture)

#		-fps, --framerate : Specify the frames per second to record

#		-e, --penc : Display preview image *after* encoding (shows compression artifacts)

#		-sh, --sharpness : Set image sharpness (-100 to 100)

#		-co, --contrast : Set image contrast (-100 to 100)

#		-br, --brightness : Set image brightness (0 to 100)

#		-sa, --saturation : Set image saturation (-100 to 100)

#		-ISO, --ISO : Set capture ISO

#		-vs, --vstab : Turn on video stablisation

#		-ev, --ev : Set EV compensation

#		-ex, --exposure : Set exposure mode (see Notes)

#		-awb, --awb : Set AWB mode (see Notes) 

#		-ifx, --imxfx : Set image effect (see Notes)

#		-cfx, --colfx : Set colour effect (U:V)

#		-mm, --metering : Set metering mode (see Notes)

#		-rot, --rotation : Set image rotation (0-359)

#		-hf, --hflip : Set horizontal flip

#		-vf, --vflip : Set vertical flip

# Notes:

#Exposure mode options :
#off,auto,night,nightpreview,backlight,spotlight,sports,snow,beach,verylong,fixedfps,antishake,fireworks

#AWB mode options :
#off,auto,sun,cloud,shade,tungsten,fluorescent,incandescent,flash,horizon

#Image Effect mode options :
#none,negative,solarise,sketch,denoise,emboss,oilpaint,hatch,gpen,pastel,watercolour,film,blur,saturation,colourswap,washedout,posterise,colourpoint,colourbalance,cartoon

#Metering Mode options :
#average,spot,backlit,matrix


timeout="10000"
filename="video.$(date +%Y%m%d%H%M%S).h264"
raspivid -t $timeout  -hf -vf -o $filename
sshpass -p 12qwaszx sudo scp $filename  fuego@192.168.3.232:~/lc_video_data/

