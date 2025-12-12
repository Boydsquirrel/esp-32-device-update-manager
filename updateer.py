import network, time, urequests, machine, os

VERSION_FILE = "version.txt"
UPDATE_JSON  = "https://raw.githubusercontent.com/Boydsquirrel/esp-32-device-update-manager/main/version.json"
BASE_URL     = "https://raw.githubusercontent.com/Boydsquirrel/esp-32-device-update-manager/main/"

def ver(v):
    return tuple(map(int, v.split(".")))

def wait_for_wifi(timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    print("Connecting to WiFi…")

    for _ in range(timeout * 2):
        if wlan.isconnected():
            print("Connected:", wlan.ifconfig()[0])
            return wlan
        time.sleep(0.5)

    print("WiFi failed.")
    return None

def get_local_version():
    if VERSION_FILE not in os.listdir():
        with open(VERSION_FILE, "w") as f:
            f.write("0.0")
        return "0.0"
    return open(VERSION_FILE).read().strip() or "0.0"

def save_local_version(v):
    with open(VERSION_FILE, "w") as f:
        f.write(v)

def download_file(url, filename):
    print("Downloading:", filename)
    try:
        r = urequests.get(url, timeout=5)

        if r.status_code != 200:
            print("HTTP error:", r.status_code)
            r.close()
            return False

        tmp = filename + ".tmp"
        with open(tmp, "w") as f:
            f.write(r.text)

        os.rename(tmp, filename)
        r.close()
        print("Saved:", filename)
        return True

    except Exception as e:
        print("Failed:", e)
        return False

def check_for_update():
    print("Checking for updates…")

    # fetch update json
    try:
        r = urequests.get(UPDATE_JSON, timeout=5)
        data = r.json()
        r.close()
    except Exception as e:
        print("Couldn't fetch version.json:", e)
        return False

    server_ver = str(data.get("version", "0.0"))
    files_list = data.get("files", ["main.py"])

    local_ver = get_local_version()
    print("Local:", local_ver, "Server:", server_ver)

    if ver(local_ver) >= ver(server_ver):
        print("Up to date.")
        return False

    print("New update found!")
    for file_name in files_list:
        if not download_file(BASE_URL + file_name, file_name):
            print("Update failed on:", file_name)
            return False

    save_local_version(server_ver)
    print("Update done. Rebooting…")
    time.sleep(1)
    machine.reset()
    return True

def run_updater():
    wlan = wait_for_wifi()
    if wlan:
        check_for_update()
        wlan.active(False)
    else:
        print("Skipping update, no WiFi.")
        


