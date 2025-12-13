import updateer
import wifi
import network
import utime
import buttons
import machine
import time
import os
from machine import Pin, lightsleep

#variables
settings = {}
wlan = network.WLAN(network.STA_IF)
t = utime.localtime()
settings_file = open("settings.txt")
timer = 0
boot = True

#first boot
def first_boot():
    print("welcome to the Micromate!")
    print(f"the time is {t[3]:02d}:{t[4]:02d} does that sound right?")
    print("we are now going to connect the device to the internet! this is used for updating the machineand for syncing")
    wifi_true = input("would you like to connect to wifi? y/n:")
    if wifi_true.strip().lower() in ("y", "yes"):
        wifi.wifi_manager()
    else:
        print("ok we will skip it for now")
    print("setup complete")

with open("settings.txt", "r") as f:
    for line in f:
        if "=" in line:
            key, value = line.strip().split("=", 1)
            settings[key] = value

theme_mode = settings["solid_theme"]
brightness = settings["brightness"]
print(f"the time is {t[3]:02d}:{t[4]:02d}:{t[5]:02d}")# prints time
FLAG_FILE = "firstboot.flag"

def write_flag_once():
    if FLAG_FILE not in os.listdir():
        print("first boot")
        with open(FLAG_FILE, "x") as f:
            f.write("1")
        first_boot()
    else:
        pass  
        print("boot finished")

write_flag_once()
#update
updateer.run_updater()
print("finished updating")

print("now booted up")
while boot:
    if buttons.button_input() != 0: #autosleep
        print("Button pressed!")
    else:
        timer += 1
        if timer == 2400: #2400 because we are sleeping 0.1 so its divided by 10
            print("timer hit 240 seconds")
            print("Sleeping for 0.5 sec...")
            lightsleep(500)  # ms
            if buttons.button_input() != 0:
                timer = 0
        else:
            time.sleep(0.1)
