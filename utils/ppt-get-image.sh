# image.sh is a script to capture a image.
# Runs camera for specific time, and take JPG capture at end if requested
#usage: raspiStill [options]
# Image parameter options:
#		-w, --width : Set image width

#		-h, --height : Set image height

#		-q, --quality : Set jpeg quality <0 to 100>

#		-r, --raw : Add raw bayer data to jpeg metadata

#		-o, --output : Output filename (to write to stdout, use '-o -'). If not specified, no file is saved

#		-v, --verbose : Output verbose information during run

#		-t, --timeout : Time (in ms) before takes picture and shuts down (if not specified, set to 5s)

#		-th, --thumb : Set thumbnail parameters (x:y:quality)

#		-d, --demo : Run a demo mode (cycle through range of camera options, no capture)

#		-e, --encoding : Encoding to use for output file (jpg, bmp, gif, png)

#		-x, --exif : EXIF tag to apply to captures (format as 'key=value')

#		-tl, --timelapse : Timelapse mode. Takes a picture every ms

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

filename="image$(date +%Y%m%d%H%M%S).jpg"
sshpass -p raspberry ssh pi@192.168.3.72 raspistill -hf -vf -o $filename
echo $filename
sshpass -p 12qwaszx sudo scp $filename  fuego@192.168.3.232:/home/fuego/labcontrol
sshpass -p raspberry ssh -o StrictHostKeyChecking=no pi@192.168.3.72 sshpass -p 12qwaszx sudo scp $filename fuego@192.168.3.232:~/labcontrol
sshpass -p 12qwaszx sudo mv $filename latest_image.jpg
