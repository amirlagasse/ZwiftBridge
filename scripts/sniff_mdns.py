"""Passive mDNS listener: log which service types the iPad queries for."""
import socket, struct, sys, time
from collections import Counter

IPAD = sys.argv[1] if len(sys.argv) > 1 else None
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 60

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
except Exception: pass
s.bind(("", 5353))
mreq = struct.pack("4sl", socket.inet_aton("224.0.0.251"), socket.INADDR_ANY)
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
s.settimeout(1.0)

def names(data):
    """Yield question names from a DNS message."""
    try:
        qd = struct.unpack("!H", data[4:6])[0]
        i = 12
        for _ in range(qd):
            parts = []
            while i < len(data):
                ln = data[i]
                if ln == 0: i += 1; break
                if ln & 0xC0: i += 2; break
                parts.append(data[i+1:i+1+ln].decode("utf-8","replace")); i += ln+1
            i += 4
            if parts: yield ".".join(parts)
    except Exception: pass

seen = Counter()
end = time.time() + DURATION
print(f"listening {DURATION}s for mDNS queries" + (f" from {IPAD}" if IPAD else ""))
while time.time() < end:
    try: data, addr = s.recvfrom(9000)
    except socket.timeout: continue
    if IPAD and addr[0] != IPAD: continue
    for n in names(data):
        if n.startswith("_"): seen[n] += 1

print("\nservice types queried:")
for n, c in seen.most_common(40):
    mark = "  <== US" if "openbikecontrol" in n else ""
    print(f"  {c:4}x  {n}{mark}")
if not seen: print("  (none seen)")
