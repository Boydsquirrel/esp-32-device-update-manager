import network
import time
import os

FILE = "networks.txt"
KEY = b'SquirR3LSFOL'   # CHANGE THIS to your own random string


# xor encryption 
def xor_encrypt(data: str) -> str:
    d = data.encode()
    enc = bytes([d[i] ^ KEY[i % len(KEY)] for i in range(len(d))])
    return enc.hex()

def xor_decrypt(hex_string: str) -> str:
    enc = bytes.fromhex(hex_string)
    dec = bytes([enc[i] ^ KEY[i % len(KEY)] for i in range(len(enc))])
    return dec.decode()


# ====== NETWORK FILE HANDLING ======
def load_saved_networks():
    if FILE not in os.listdir():
        return {}

    nets = {}
    with open(FILE, "r") as f:
        for line in f:
            if "," in line:
                ssid, enc_pwd = line.strip().split(",", 1)
                try:
                    pwd = xor_decrypt(enc_pwd)
                    nets[ssid] = pwd
                except:
                    pass  # skip corrupt entries

    return nets


def save_network(ssid, pwd):
    nets = load_saved_networks()
    nets[ssid] = pwd
    with open(FILE, "w") as f:
        for s, p in nets.items():
            f.write(f"{s},{xor_encrypt(p)}\n")


# ====== SCANNING ======
def scan_networks():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    raw = wlan.scan()

    ssids = []
    for item in raw:
        name = item[0].decode()
        if name not in ssids:
            ssids.append(name)

    return ssids


# ====== AUTO CONNECT ======
def try_auto_connect():
    saved = load_saved_networks()
    nets = scan_networks()

    for ssid in nets:
        if ssid in saved:
            print(f"Auto-connecting to {ssid}…")
            if connect(ssid, saved[ssid]):
                print("Auto-connect succeeded")
                return True

    print("No saved networks available. Going manual.")
    return False


# ====== CONNECT HANDLER ======
def connect(ssid, pwd):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, pwd)

    for _ in range(20):
        if wlan.isconnected():
            print("Connected!")
            print("IP:", wlan.ifconfig()[0])
            return True
        time.sleep(0.5)

    print("Failed to connect")
    return False


# ====== MANUAL SELECT MODE ======
def manual_mode():
    print("\n=== WiFi Scanner ===")
    nets = scan_networks()

    if not nets:
        print("Bruh no networks found")
        return

    for i, ssid in enumerate(nets):
        print(f"[{i}] {ssid}")

    choice = input("\nPick network number: ").strip()
    if not choice.isdigit() or int(choice) >= len(nets):
        print("Invalid choice lol")
        return

    ssid = nets[int(choice)]
    pwd = input(f"Password for {ssid}: ")

    if connect(ssid, pwd):
        save_network(ssid, pwd)


# ====== MAIN ENTRY ======
def wifi_manager():
    print("Booting WiFi manager…")

    if not try_auto_connect():
        manual_mode()
        


