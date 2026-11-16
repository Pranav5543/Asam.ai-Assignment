import sys, os, json; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hwqa
def test_allowlist_blocks_arbitrary():
    for bad in [("rm", "-rf", "/"), ("lscpu", "; id"), ("dmidecode", "-t", "1")]:
        try: hwqa.run(bad); assert False
        except PermissionError: pass
def test_slots_parsing(monkeypatch=None):
    t17 = "Memory Device\n\tLocator: DIMM A\n\tSize: 16 GB\n\tType: DDR4\n\n" "Memory Device\n\tLocator: DIMM B\n\tSize: No Module Installed\n"
    t16 = "Physical Memory Array\n\tMaximum Capacity: 64 GB\n\tNumber Of Devices: 2\n"
    hwqa.run = lambda a: (t17 if a[-1] == "17" else t16, None)
    d = hwqa.memory_slots()["data"]
    assert (d["slots_used"], d["slots_total"], d["headroom_gb"]) == (1, 2, 48)
def test_missing_root_is_honest():
    hwqa.run = lambda a: (None, "root permission required")
    r = hwqa.memory_slots(); assert not r["ok"] and "cannot be determined" in r["error"]
def test_storage_nvme():
    hwqa.run = lambda a: (json.dumps({"blockdevices": [{"name": "nvme0n1", "size": "512110190592", "type": "disk", "rota": False, "tran": None, "model": "X"}]}), None)
    assert hwqa.storage()["data"][0]["kind"] == "NVMe SSD"
if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
