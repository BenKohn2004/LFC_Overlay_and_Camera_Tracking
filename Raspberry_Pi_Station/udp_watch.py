import socket, struct, time

FMT = "<B6s4I12?32s"
FLAGS = ["Green","Red","WhiteG","WhiteR","YellowG","YellowR",
         "YCardG","YCardR","RCardG","RCardR","PrioL","PrioR"]

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 4210))
s.settimeout(3)
print("watching for state changes (20 s) ...")

last = None
start = time.time()
count = 0
while time.time() - start < 20:
    try:
        data, addr = s.recvfrom(1024)
    except socket.timeout:
        print("  (no packets for 3 s)")
        continue
    count += 1
    if data == last:
        continue
    last = data
    f = struct.unpack(FMT, data)
    flags = [n for n, v in zip(FLAGS, f[6:18]) if v]
    name = f[18].split(b"\x00")[0].decode(errors="replace")
    print(f"t={time.time()-start:5.2f}s  '{name}'  L {f[3]} - R {f[2]}  "
          f"clock {f[5]}:{f[4]:02d}  flags={flags or 'none'}")
print(f"done: {count} packets total")
