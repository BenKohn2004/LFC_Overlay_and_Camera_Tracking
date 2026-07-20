import socket, struct, time

FMT = "<B6s4I12?32s"   # msgType, mac, R score, L score, sec, min, 12 flags, name
FLAGS = ["Green_Light","Red_Light","White_Green","White_Red","Yellow_Green","Yellow_Red",
         "YCard_Green","YCard_Red","RCard_Green","RCard_Red","Prio_Left","Prio_Right"]

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 4210))
s.settimeout(10)
print("listening on 0.0.0.0:4210 ...")

n = 0
start = time.time()
while n < 8 and time.time() - start < 15:
    try:
        data, addr = s.recvfrom(1024)
    except socket.timeout:
        print("timeout: no packet in 10 s")
        break
    n += 1
    if len(data) != struct.calcsize(FMT):
        print(f"[{n}] {addr[0]} unexpected size {len(data)}")
        continue
    f = struct.unpack(FMT, data)
    msg_type, mac = f[0], f[1]
    r_score, l_score, secs, mins = f[2], f[3], f[4], f[5]
    flags = [name for name, v in zip(FLAGS, f[6:18]) if v]
    name = f[18].split(b"\x00")[0].decode(errors="replace")
    print(f"[{n}] from {addr[0]}  type=0x{msg_type:02X}  box='{name}'  "
          f"L {l_score} - R {r_score}  clock {mins}:{secs:02d}  "
          f"flags={flags or 'none'}")
print(f"done: {n} packets in {time.time()-start:.1f} s")
