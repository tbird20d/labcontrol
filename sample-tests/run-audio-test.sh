#!/bin/sh
# run the audio playback test using settings for rpi3-2
#  board = rpi3-2
#  device = default:CARD=Creation (= PCM device on DUT)
#  device_id = usb-audio (=lab name for DUT audio source)
#  997-tone.wav = file to use for test

# FIXTHIS - need to specify tone frequency (or let test cycle it)

./audio-playback-test.sh -v rpi3-2 default:CARD=Creation usb-audio ./997-tone.wav
# Result: play, recording starts, play stops, recording stops,
#  download works, trim works, compare works
